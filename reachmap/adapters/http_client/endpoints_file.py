"""The endpoints-file HTTP client adapter (JavaScript and TypeScript).

A frontend keeps the URLs of its backend in one file (`http_client.path`, `src/api/endpoints.ts`): exported string
constants, constants made of other constants (`BASE + "/orders"`), and builders with parameters
(`(id: string) => \\`/api/billing/invoices/${id}\\``), also written over several lines. This adapter reads that file with the
JavaScript tokenizer (never a line regex, so a constant whose string is on the next line is read) and evaluates every
URL statically. A part that cannot be evaluated (an imported base URL, `import.meta.env`, a computed value) is marked
unknown and the URL is partial: it is kept, never matched silently and never dropped.

A client call is a line of client code that uses an endpoint: it imports the constant or builder from the endpoints file
(any alias, or through a namespace import) and refers to it. The files are found with the JavaScript adapter's importer
search, so the same completeness rules apply: a cut or failed search, a file that does not parse, a re-export that hides
users, all make the answer incomplete and say why. The method comes from the call that encloses the use (`apiGet(...)`,
`axios.post(...)`, `fetch(url, { method: "PUT" })`); when it cannot be told the call has no method and matches any.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Sequence

from reachmap import config
from reachmap.adapters.base import evidence_at
from reachmap.adapters.http_client import ClientCall, ClientCalls, ClientUrl, METHODS, url_from_parts
from reachmap.adapters.js.lexer import unquote
from reachmap.model import Evidence

__all__ = ["Endpoint", "EndpointTable", "EndpointsFileClient"]

_ALL_VERBS = {"get": "GET", "post": "POST", "put": "PUT", "patch": "PATCH", "delete": "DELETE", "head": "HEAD", "options": "OPTIONS"}
_BARE_VERBS = {"get": "GET", "post": "POST", "put": "PUT", "patch": "PATCH", "delete": "DELETE"}  # a wrapper name never gives HEAD or OPTIONS
_CLIENT_OBJECTS = frozenset({"axios", "http", "https", "api", "client", "ky", "$http", "got", "superagent"})
_CONFIG_CALLS = frozenset({"fetch", "axios", "request", "ajax", "http", "xhr"})
_STRINGIFY = frozenset({"String", "encodeURIComponent", "encodeURI", "Number", "toString"})
_WORDS = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")


@dataclass(frozen=True, slots=True)
class Endpoint:
    """One endpoint of the endpoints file: a constant (no `params`) or a builder. `atoms` are what the URL is made
    of (`lit`, `param` and `unknown` atoms); `lines` are the lines of its declaration and of the constants it uses
    (the `via` evidence of a call)."""

    name: str
    kind: str  # constant | builder
    params: tuple[str, ...]
    atoms: tuple[tuple[str, str], ...]
    lines: tuple[int, ...]

    @property
    def url(self) -> ClientUrl:
        return url_from_parts(self.atoms)


@dataclass(frozen=True, slots=True)
class EndpointTable:
    """The endpoints of the file at a commit. `state`: found, absent (the configured path is not in the tree) or
    unreadable (the git layer recorded it)."""

    path: str
    sha: str
    state: str
    endpoints: dict[str, Endpoint]
    parsed: Any = None
    problems: tuple[str, ...] = ()


class _Cannot(Exception):
    """An expression this evaluator does not understand: it becomes one unknown atom."""


class _Evaluator:
    """Evaluate the initializers of the declarations of one file, as atoms."""

    def __init__(self, parsed: Any) -> None:
        self.p = parsed
        self.decls = {d.name: d for d in parsed.declarations}
        self.cache: dict[str, Endpoint] = {}
        self.busy: set[str] = set()

    # ---- the file's declarations

    def endpoint(self, name: str) -> Endpoint | None:
        if name in self.cache:
            return self.cache[name]
        d = self.decls.get(name)
        if d is None or name in self.busy:
            return None
        self.busy.add(name)
        try:
            ep = self._declare(d)
        finally:
            self.busy.discard(name)
        if ep is not None:
            self.cache[name] = ep
        return ep

    def _declare(self, d: Any) -> Endpoint | None:
        lines = self._lines(d.start, d.end)
        if d.kind not in ("variable", "function") or d.init_tok < 0:
            return None
        toks = self.p.tokens
        first = toks[d.init_tok]
        is_function = first.kind == "ident" and first.value == "function" or (
            first.kind == "ident" and first.value == "async" and d.init_tok + 1 < len(toks) and toks[d.init_tok + 1].value == "function")
        if is_function:
            fn = self._function(d)
            if fn is None:
                return None  # a function with a body of several statements is a helper, not a URL
            return self._evaluate(d.name, "builder", fn[0], fn[1], fn[2], lines)
        if d.kind == "function" or self._looks_like_arrow(d.init_tok, d.end_tok):
            arrow = self._arrow(d.init_tok, d.end_tok)
            if arrow is None:
                return None
            return self._evaluate(d.name, "builder", arrow[0], arrow[1], arrow[2], lines)
        return self._evaluate(d.name, "constant", (), d.init_tok, self._trim(d.init_tok, d.end_tok + 1), lines)

    def _looks_like_arrow(self, a: int, end_tok: int) -> bool:
        toks = self.p.tokens
        i = a + 1 if toks[a].kind == "ident" and toks[a].value == "async" else a
        if toks[i].kind == "ident" and i + 1 <= end_tok and toks[i + 1].kind == "punct" and toks[i + 1].value == "=>":
            return True
        if toks[i].kind == "punct" and toks[i].value == "(":
            close = self._match(i, "(", ")")
            return 0 < close <= end_tok and any(t.kind == "punct" and t.value == "=>" for t in toks[close + 1:min(close + 40, end_tok + 1)])
        return False

    def _lines(self, start: int, end: int) -> list[int]:
        return [n for n in range(start, end + 1) if self.p.line_text(n).strip()]

    def _evaluate(self, name: str, kind: str, params: tuple[str, ...], a: int, b: int, lines: list[int]) -> Endpoint | None:
        used = set(lines)
        try:
            atoms = self._expr(a, b, {p: None for p in params}, used)
        except _Cannot:
            return None  # an object, an array, a body that is not one expression: not a URL
        return Endpoint(name, kind, params, tuple(_merge(atoms)), tuple(sorted(used)))

    # ---- token helpers

    def _text(self, a: int, b: int) -> str:
        toks = self.p.tokens
        if a >= b or a >= len(toks):
            return ""
        return " ".join(self.p.text[toks[a].start:toks[b - 1].end].split())[:80]

    def _trim(self, a: int, b: int) -> int:
        """The end (exclusive) of the expression in tokens [a, b): without a final `;` and without a trailing
        `as const` / `satisfies T`."""
        toks = self.p.tokens
        while b > a and toks[b - 1].kind == "punct" and toks[b - 1].value == ";":
            b -= 1
        for k in range(a, b):
            if toks[k].kind == "ident" and toks[k].value in ("as", "satisfies") and k > a:
                return k
        return b

    def _match(self, i: int, _openers: str = "", _closers: str = "") -> int:
        """The index of the bracket closing the one at i (tokens), or -1."""
        found = _close_of(self.p.tokens, i)
        return -1 if found is None else found

    def _param_names(self, open_idx: int, close_idx: int) -> tuple[str, ...] | None:
        toks = self.p.tokens
        names: list[str] = []
        depth, expect = 0, True
        for j in range(open_idx + 1, close_idx):
            t = toks[j]
            if t.kind == "punct" and t.value in "([{<":
                if depth == 0 and expect and t.value in "{[":
                    return None  # a destructured parameter: the names are not read
                depth += 1
            elif t.kind == "punct" and t.value in ")]}>":
                depth -= 1
            elif t.kind == "punct" and t.value == "," and depth == 0:
                expect = True
            elif expect and depth == 0:
                if t.kind == "ident" and t.value not in ("readonly",):
                    names.append(t.value)
                    expect = False
                elif t.kind == "punct" and t.value == "...":
                    continue
        return tuple(names)

    def _arrow(self, a: int, end_tok: int) -> tuple[tuple[str, ...], int, int] | None:
        """(params, first token, end token) of the body of `(params) => body` / `x => body`, or None when the
        initializer is not an arrow function."""
        toks = self.p.tokens
        i = a
        if toks[i].kind == "ident" and toks[i].value == "async":
            i += 1
        if i < len(toks) and toks[i].kind == "punct" and toks[i].value == "(":
            close = self._match(i, "(", ")")
            if close < 0:
                return None
            params = self._param_names(i, close)
            j = close + 1
        elif i < len(toks) and toks[i].kind == "ident" and i + 1 < len(toks) and toks[i + 1].value == "=>":
            params, j = (toks[i].value,), i + 1
        else:
            return None
        # a return type annotation sits between `)` and `=>`
        while j <= end_tok and not (toks[j].kind == "punct" and toks[j].value == "=>"):
            if toks[j].kind == "punct" and toks[j].value in ";{":
                return None
            j += 1
        if j > end_tok:
            return None
        body = j + 1
        if params is None:
            return ((), body, self._trim(body, end_tok + 1))
        if toks[body].kind == "punct" and toks[body].value == "{":
            close_body = self._match(body, "{", "}")
            ret = self._return(body + 1, close_body)
            return None if ret is None else (params, ret[0], ret[1])
        return params, body, self._trim(body, end_tok + 1)

    def _return(self, a: int, b: int) -> tuple[int, int] | None:
        """The expression of the only statement `return <expr>;` in tokens [a, b)."""
        toks = self.p.tokens
        if a < b and toks[a].kind == "ident" and toks[a].value == "return":
            return a + 1, self._trim(a + 1, b)
        return None

    def _function(self, d: Any) -> tuple[tuple[str, ...], int, int] | None:
        toks = self.p.tokens
        fn = d.init_tok
        if fn < 0:
            return None
        j = fn
        while j <= d.end_tok and not (toks[j].kind == "punct" and toks[j].value == "("):
            j += 1
        if j > d.end_tok:
            return None
        close = self._match(j, "(", ")")
        params = self._param_names(j, close) if close > 0 else None
        k = close + 1
        while k <= d.end_tok and not (toks[k].kind == "punct" and toks[k].value == "{"):
            k += 1
        if k > d.end_tok:
            return None
        end = self._match(k, "{", "}")
        ret = self._return(k + 1, end) if end > 0 else None
        if ret is None:
            return None
        return (params or (), ret[0], ret[1]) if params is not None else ((), ret[0], ret[1])

    # ---- expressions

    def _expr(self, a: int, b: int, env: dict[str, Any], used: set[int]) -> list[tuple[str, str]]:
        """Atoms of the expression in tokens [a, b): terms joined by `+`."""
        toks = self.p.tokens
        out: list[tuple[str, str]] = []
        i = a
        while i < b:
            atoms, i = self._term(i, b, env, used)
            out += atoms
            if i < b:
                t = toks[i]
                if t.kind == "punct" and t.value == "+":
                    i += 1
                    continue
                raise _Cannot()
        return out

    def _term(self, i: int, b: int, env: dict[str, Any], used: set[int]) -> tuple[list[tuple[str, str]], int]:
        toks = self.p.tokens
        t = toks[i]
        if t.kind == "string":
            return [("lit", unquote(t.value))], i + 1
        if t.kind == "number":
            return [("lit", t.value)], i + 1
        if t.kind == "template":
            return self._template(i, b, env, used)
        if t.kind == "punct" and t.value == "(":
            close = self._match(i, "(", ")")
            if close < 0 or close >= b:
                raise _Cannot()
            return self._expr(i + 1, close, env, used), close + 1
        if t.kind == "ident":
            nxt = toks[i + 1] if i + 1 < b else None
            if nxt is not None and nxt.kind == "punct" and nxt.value == "(":
                close = self._match(i + 1, "(", ")")
                if close < 0 or close >= b:
                    raise _Cannot()
                args = self._split(i + 2, close)
                if t.value in _STRINGIFY and len(args) == 1:
                    return self._expr(args[0][0], args[0][1], env, used), close + 1
                ep = self.endpoint(t.value) if t.value not in env else None
                if ep is not None and ep.kind == "builder" and len(args) == len(ep.params):
                    used.update(ep.lines)
                    sub = {p: self._expr(x, y, env, used) for p, (x, y) in zip(ep.params, args)}
                    return _substitute(ep.atoms, sub), close + 1
                end = self._postfix(close + 1, b)
                return [("unknown", self._text(i, end))], end
            if nxt is not None and nxt.kind == "punct" and nxt.value in (".", "?.", "["):
                end = self._postfix(i + 1, b)
                return [("unknown", self._text(i, end))], end  # a member (`config.base`, `import.meta.env.X`): not evaluated
            if t.value in env:
                return ([("param", t.value)] if env[t.value] is None else list(env[t.value])), i + 1
            ep = self.endpoint(t.value)
            if ep is not None and ep.kind == "constant":
                used.update(ep.lines)
                return list(ep.atoms), i + 1
            return [("unknown", t.value)], i + 1  # an imported or computed name
        raise _Cannot()

    def _postfix(self, i: int, b: int) -> int:
        """The end of a chain of `.name`, `?.name`, `[...]` and `(...)` that starts at token i."""
        toks = self.p.tokens
        while i < b:
            t = toks[i]
            if t.kind == "punct" and t.value in (".", "?.") and i + 1 < b and toks[i + 1].kind == "ident":
                i += 2
            elif t.kind == "punct" and t.value in ("(", "["):
                close = self._match(i, t.value, ")" if t.value == "(" else "]")
                if close < 0 or close >= b:
                    return b
                i = close + 1
            else:
                break
        return i

    def _split(self, a: int, b: int) -> list[tuple[int, int]]:
        """Top-level comma separated ranges in tokens [a, b)."""
        toks = self.p.tokens
        out, depth, start = [], 0, a
        for j in range(a, b):
            t = toks[j]
            if t.kind == "punct" and t.value in "([{":
                depth += 1
            elif t.kind == "punct" and t.value in ")]}":
                depth -= 1
            elif t.kind == "punct" and t.value == "," and depth == 0:
                out.append((start, j))
                start = j + 1
        if start < b:
            out.append((start, b))
        return out

    def _template(self, i: int, b: int, env: dict[str, Any], used: set[int]) -> tuple[list[tuple[str, str]], int]:
        toks = self.p.tokens
        first = toks[i].value
        if not first.endswith("${"):
            return [("lit", unquote(first))], i + 1  # a template with no substitution is a string
        out: list[tuple[str, str]] = [("lit", _piece(first))]
        j = i + 1
        while True:
            depth, k = 1, j
            while k < b:
                t = toks[k]
                if t.kind == "template":
                    if t.value.startswith("}") and t.value.endswith("${"):
                        if depth == 1:
                            break
                    elif t.value.startswith("}"):
                        depth -= 1
                        if depth == 0:
                            break
                    elif t.value.endswith("${"):
                        depth += 1
                k += 1
            if k >= b or toks[k].kind != "template":
                raise _Cannot()
            try:
                out += self._expr(j, k, env, used)
            except _Cannot:
                out.append(("unknown", self._text(j, k)))
            piece = toks[k].value
            out.append(("lit", _piece(piece)))
            if piece.endswith("${"):
                j = k + 1
                continue
            return out, k + 1


def _piece(raw: str) -> str:
    """The text of a template piece: the part between the backtick or `}` and the following `${` or backtick."""
    body = raw
    if body.startswith(("`", "}")):
        body = body[1:]
    if body.endswith("${"):
        body = body[:-2]
    elif body.endswith("`"):
        body = body[:-1]
    return unquote("`" + body + "`")


def _is_url(ep: Endpoint) -> bool:
    """Whether an endpoint's value can be a URL: it starts with a slash or a scheme, or with a part that is not known (a
    base URL). A label, a number or a builder whose result is its first argument is not one."""
    if not ep.atoms:
        return False
    kind, text = ep.atoms[0]
    if kind == "param":
        return False
    return kind == "unknown" or text.startswith(("/", "http://", "https://"))


def _merge(atoms: Sequence[tuple[str, str]]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for kind, text in atoms:
        if kind == "lit" and out and out[-1][0] == "lit":
            out[-1] = ("lit", out[-1][1] + text)
        elif not (kind == "lit" and not text):
            out.append((kind, text))
    return out


def _substitute(atoms: Sequence[tuple[str, str]], sub: dict[str, list[tuple[str, str]]]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for kind, text in atoms:
        if kind == "param" and text in sub:
            out += sub[text]
        else:
            out.append((kind, text))
    return out


# ------------------------------------------------------------------------------ the call sites


def _close_of(tokens: Any, i: int) -> int | None:
    """The index of the bracket that closes the one at token i (`(`, `[` and `{` count alike), or None. -1 is never
    returned: callers test for None or a negative index."""
    depth = 0
    for j in range(i, len(tokens)):
        t = tokens[j]
        if t.kind != "punct":
            continue
        if t.value in "([{":
            depth += 1
        elif t.value in ")]}":
            depth -= 1
            if depth == 0:
                return j
    return None


def _words(name: str) -> list[str]:
    return [w.lower() for w in _WORDS.findall(name.replace("_", " ").replace("-", " "))]


class EndpointsFileClient:
    """The client adapter of a repository whose backend URLs are in one file. Use one per run (the JavaScript
    adapter keeps what it read)."""

    kind = "endpoints-file"

    def __init__(self, repo: str, language: Any, path: str) -> None:
        self.repo = repo
        self.language = language
        self.path = path
        self._tables: dict[str, EndpointTable] = {}
        self._calls: dict[str, ClientCalls] = {}

    @classmethod
    def from_config(cls, repo: config.Repo, language: Any) -> "EndpointsFileClient":
        path = (repo.http_client or {}).get("path") or "src/api/endpoints.ts"
        return cls(repo.name, language, str(path))

    # ---- the endpoints file

    def table(self, sha: str) -> EndpointTable:
        if sha not in self._tables:
            self._tables[sha] = self._read_table(sha)
        return self._tables[sha]

    def _read_table(self, sha: str) -> EndpointTable:
        res = self.language.file_text(sha, self.path)
        if res.is_absent:
            return EndpointTable(self.path, sha, "absent", {}, None, (f"the endpoints file {self.path} is not in the tree at {sha[:12]}",))
        if res.is_unreadable:
            return EndpointTable(self.path, sha, "unreadable", {}, None, (f"the endpoints file {self.path} could not be read at {sha[:12]}: {res.detail}",))
        parsed = self.language.parsed(sha, self.path)
        problems: tuple[str, ...] = ()
        if parsed is None:
            return EndpointTable(self.path, sha, "unreadable", {}, None, (f"the endpoints file {self.path} could not be parsed at {sha[:12]}",))
        if parsed.problems:
            problems = (f"the endpoints file {self.path} did not lex cleanly ({parsed.problems[0]}): a URL in it may be misread",)
        ev = _Evaluator(parsed)
        endpoints: dict[str, Endpoint] = {}
        for d in parsed.declarations:
            if d.exported and d.kind in ("variable", "function"):
                ep = ev.endpoint(d.name)
                if ep is not None and _is_url(ep):
                    for name in d.export_names or (d.name,):
                        endpoints[name] = ep if name == ep.name else Endpoint(name, ep.kind, ep.params, ep.atoms, ep.lines)
        return EndpointTable(self.path, sha, "found", endpoints, parsed, problems)

    def evidence_for(self, sha: str, ep: Endpoint) -> tuple[Evidence, ...]:
        table = self.table(sha)
        if table.parsed is None:
            return ()
        out = [e for n in ep.lines if (e := evidence_at(self.repo, self.path, sha, table.parsed, n)) is not None]
        return tuple(out)

    # ---- the calls

    def calls(self, sha: str) -> ClientCalls:
        if sha not in self._calls:
            self._calls[sha] = self._scan(sha)
        return self._calls[sha]

    def _scan(self, sha: str) -> ClientCalls:
        table = self.table(sha)
        reasons: list[str] = []
        if table.state != "found":
            return ClientCalls(self.repo, sha, (), False, table.problems)
        reasons += table.problems
        found = self.language.importers(self.path, sha)
        if not found.complete:
            reasons += [f"the users of {self.path} were not found completely: {why}" for why in found.reasons]
        by_importer: dict[str, list[Any]] = {}
        for e in found.edges:
            if not self.language.is_test_path(e.importer_path):
                by_importer.setdefault(e.importer_path, []).append(e)
        calls: list[ClientCall] = []
        for importer in sorted(by_importer):
            parsed = self.language.parsed(sha, importer)
            if parsed is None:
                reasons.append(f"{importer} could not be read: the calls in it were not found")
                continue
            if parsed.problems:
                reasons.append(f"{importer} did not lex cleanly ({parsed.problems[0]}): a call in it may be missed")
            for edge in by_importer[importer]:
                if edge.kind in ("reexport", "export_star"):
                    reasons.append(f"{importer} re-exports {self.path}: what imports it from there was not searched")
                    continue
                if edge.kind in ("dynamic", "require") and not edge.bindings:
                    reasons.append(f"{importer} loads {self.path} dynamically: which endpoints it uses was not established")
                    continue
                for b in edge.bindings:
                    if b.type_only:
                        continue
                    if b.imported == "*":
                        for name in sorted(table.endpoints):
                            for u in parsed.find_uses(b.local, member=name):
                                calls += self._call(sha, importer, parsed, table.endpoints[name], name, f"{b.local}.{name}", u)
                    elif b.imported in table.endpoints:
                        for u in parsed.find_uses(b.local):
                            calls += self._call(sha, importer, parsed, table.endpoints[b.imported], b.imported, b.local, u)
        calls.sort(key=lambda c: (c.path, c.line, c.name))
        return ClientCalls(self.repo, sha, tuple(calls), not reasons, tuple(dict.fromkeys(reasons)), len(table.endpoints))

    def _call(self, sha: str, importer: str, parsed: Any, ep: Endpoint, name: str, local: str, use: Any) -> list[ClientCall]:
        ev = evidence_at(self.repo, importer, sha, parsed, use.line)
        if ev is None:
            return []
        return [ClientCall(
            repo=self.repo, path=importer, line=use.line, name=name, local=local, url=ep.url, method=method_of(parsed, use.tok), evidence=ev,
            via=self.evidence_for(sha, ep), enclosing=use.enclosing, form=use.form,
        )]


# ------------------------------------------------------------------------------ the method of a call


def _enclosing_call(parsed: Any, i: int) -> int | None:
    """The `(` of the call whose arguments hold the token i, or None. Object and array literals around the token are
    stepped out of (`axios({ url: X })`)."""
    toks = parsed.tokens
    depth = 0
    for j in range(i - 1, -1, -1):
        t = toks[j]
        if t.kind != "punct":
            continue
        if t.value in (")", "]", "}"):
            depth += 1
        elif t.value in ("(", "[", "{"):
            if depth:
                depth -= 1
                continue
            if t.value == "(":
                prev = toks[j - 1] if j else None
                if prev is not None and (prev.kind == "ident" and prev.value not in ("if", "while", "for", "switch", "return", "typeof", "await")
                                         or (prev.kind == "punct" and prev.value in (">", "?."))):
                    return j
        elif t.value == ";" and depth == 0:
            return None
    return None


def _callee(parsed: Any, open_idx: int) -> list[str]:
    """The names a call is made through, outermost first (`axios.get` is `["axios", "get"]`), past generics
    (`apiGet<Invoice[]>(`); empty when the callee is not a plain name chain."""
    toks = parsed.tokens
    j = open_idx - 1
    if j >= 0 and toks[j].kind == "punct" and toks[j].value == ">":
        depth = 0
        while j >= 0:
            if toks[j].kind == "punct" and toks[j].value == ">":
                depth += 1
            elif toks[j].kind == "punct" and toks[j].value == "<":
                depth -= 1
                if depth == 0:
                    break
            j -= 1
        j -= 1
    names: list[str] = []
    while j >= 0 and toks[j].kind == "ident":
        names.insert(0, toks[j].value)
        if j >= 2 and toks[j - 1].kind == "punct" and toks[j - 1].value in (".", "?.") and toks[j - 2].kind == "ident":
            j -= 2
        else:
            break
    return names


def _config_method(parsed: Any, open_idx: int, close_idx: int) -> tuple[bool, str | None]:
    """Whether the arguments of a call name a `method` and what it is: (found, METHOD or None when it is not a
    string literal). Looks for `method: "POST"` at any depth of an object literal among the arguments."""
    toks = parsed.tokens
    for j in range(open_idx + 1, close_idx):
        t = toks[j]
        if t.kind in ("ident", "string") and unquote(t.value) == "method" and j + 1 < close_idx and toks[j + 1].kind == "punct" and toks[j + 1].value == ":":
            v = toks[j + 2] if j + 2 < close_idx else None
            if v is not None and v.kind in ("string", "template"):
                name = unquote(v.value).upper()
                return True, name if name in METHODS else None
            return True, None
    return False, None


def method_of(parsed: Any, tok: int) -> str | None:
    """The HTTP method of the call that holds the endpoint reference at token `tok`; None when it cannot be told.

    A verb is taken only from a known client shape: a member of a client object (`axios.get`, `http.post`, `api.put`,
    `client.delete`), a wrapper whose last word is exactly Get, Post, Put, Patch or Delete behind at least one other word
    (`apiGet`, `http_post`), and the `method` option of `fetch` or `axios(config)`. Never the end of any other name:
    `fetchWithOptions` is no OPTIONS call and `widgetHead` no HEAD call."""
    open_idx = _enclosing_call(parsed, tok)
    if open_idx is None:
        return None
    chain = _callee(parsed, open_idx)
    if not chain:
        return None
    name = chain[-1]
    if len(chain) >= 2 and chain[-2] in _CLIENT_OBJECTS and name.lower() in _ALL_VERBS:
        return _ALL_VERBS[name.lower()]
    words = _words(name)
    if name not in _CONFIG_CALLS and len(words) >= 2 and words[-1] in _BARE_VERBS:
        return _BARE_VERBS[words[-1]]
    if name in _CONFIG_CALLS or (words and words[-1] in ("fetch", "request")):
        close = _close_of(parsed.tokens, open_idx)
        if close is None:
            return None
        found, method = _config_method(parsed, open_idx, close)
        if found:
            return method
        if name not in ("fetch", "axios"):
            return None  # a wrapper that names no method: it cannot be told
        return None if _options_unknown(parsed, open_idx, close) else "GET"  # fetch(url) and axios(url) with no method are GETs
    return None


def _options_unknown(parsed: Any, open_idx: int, close_idx: int) -> bool:
    """Whether the options argument after the URL could hold a method this adapter did not see: it is a variable
    or an expression, or an object literal with a spread."""
    toks = parsed.tokens
    depth = 0
    for j in range(open_idx + 1, close_idx):
        t = toks[j]
        if t.kind == "punct" and t.value in "([{":
            depth += 1
        elif t.kind == "punct" and t.value in ")]}":
            depth -= 1
        elif depth == 0 and t.kind == "punct" and t.value == ",":
            k = j + 1
            if k >= close_idx:
                return False  # a trailing comma
            if toks[k].kind == "punct" and toks[k].value == "{":
                end = _close_of(parsed.tokens, k) or close_idx
                return any(x.kind == "punct" and x.value == "..." for x in toks[k:end])
            return True
    first = toks[open_idx + 1] if open_idx + 1 < close_idx else None
    if first is not None and first.kind == "punct" and first.value == "{":  # axios({ url, ...}): a config object that names no method
        end = _close_of(parsed.tokens, open_idx + 1) or close_idx
        return any(x.kind == "punct" and x.value == "..." for x in toks[open_idx + 1:end])
    return False
