"""Token level reading of one JavaScript or TypeScript file, for the Express and NestJS adapters.

Everything works on the tokens of the JavaScript adapter's parser (`reachmap.adapters.js.syntax.Parsed`), so a word in a
comment or a string is never code. This is not a parser of the language: it answers the narrow questions the two adapters
ask (the arguments of a call, the identifiers a range of tokens spells, whether a token is at module level, what a
`require()` binds, what a file exports, what a string, a template or a sum of strings evaluates to) and says "unknown" for
the rest. Line numbers are 1-based.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from reachmap.adapters.js.lexer import Token, unquote

__all__ = [
    "Bind", "Export", "Src", "Str", "build_consts", "convert_path", "path_pieces", "unknown_whole",
]

COMPOUND_WORDS = frozenset({"if", "for", "while", "switch", "catch", "with"})
COMPOUND_BEFORE = frozenset({"else", "try", "finally", "do", "catch"})
TYPE_BRACE_PUNCT = frozenset({":", "|", "&", "<", ",", "(", "[", "=>", "=", "?"})
TYPE_BRACE_WORDS = frozenset({"extends", "implements", "keyof", "typeof", "is", "asserts", "infer", "readonly", "new", "as", "satisfies", "in", "of"})


@dataclass(frozen=True, slots=True)
class Str:
    """A string made of known text and unknown parts (`unknown`: the expression, the file and the line of each)."""

    value: str = ""
    unknown: tuple[tuple[str, str, int], ...] = ()

    def __add__(self, other: "Str") -> "Str":
        return Str(self.value + other.value, self.unknown + other.unknown)

    def __bool__(self) -> bool:
        return bool(self.value)


@dataclass(frozen=True, slots=True)
class Bind:
    """What a local name is bound to by an import or a `require`: `imported` is a name, `default`, `*` (the module as a
    namespace) or `*cjs` (whatever `module.exports` is: `const x = require("m")`)."""

    spec: str
    imported: str
    line: int


@dataclass(frozen=True, slots=True)
class Export:
    """One thing a file exports: `local` is the name it is declared under in the file (None when it is not a plain
    name), `source` the specifier of a re-export and `source_name` the name taken from it."""

    name: str
    local: str | None
    line: int
    source: str | None = None
    source_name: str | None = None
    value: tuple[int, int] | None = None  # for a CommonJS member that is not a name (`exports.P = "/api"`, `{ P: "/api" }`): the tokens of its value


class Src:
    """The token helpers of one file."""

    def __init__(self, parsed: Any, path: str) -> None:
        self.parsed = parsed
        self.path = path
        self.t: list[Token] = parsed.tokens
        self.n = len(self.t)
        self._scope: list[bool] | None = None
        self._in_decl: list[bool] | None = None
        self._bindings: dict[str, Bind] | None = None
        self._exports: list[Export] | None = None

    # ---- tokens

    def w(self, i: int) -> str | None:
        return self.parsed.w(i)

    def p(self, i: int) -> str | None:
        return self.parsed.p(i)

    def line(self, i: int) -> int:
        return self.t[i].line if 0 <= i < self.n else 0

    def is_open(self, i: int) -> bool:
        t = self.t[i]
        return (t.kind == "punct" and t.value in ("(", "[", "{")) or (t.kind == "template" and t.value.endswith("${"))

    def is_close(self, i: int) -> bool:
        t = self.t[i]
        return (t.kind == "punct" and t.value in (")", "]", "}")) or (t.kind == "template" and t.value.startswith("}"))

    def match(self, i: int) -> int:
        return self.parsed.matching(i)

    def text(self, a: int, b: int) -> str:
        """The source text of tokens a..b (inclusive), whitespace folded."""
        if a > b or a >= self.n:
            return ""
        b = min(b, self.n - 1)
        return " ".join(self.parsed.text[self.t[a].start:self.t[b].end].split())

    def args(self, open_idx: int) -> list[tuple[int, int]]:
        """The top-level arguments of the call or list that opens at `open_idx` as inclusive token ranges."""
        close = self.match(open_idx)
        out: list[tuple[int, int]] = []
        start = open_idx + 1
        j = open_idx + 1
        while j < close:
            if self.is_open(j):
                j = self.match(j) + 1
                continue
            if self.p(j) == ",":
                if start <= j - 1:
                    out.append((start, j - 1))
                start = j + 1
            j += 1
        if start <= close - 1:
            out.append((start, close - 1))
        return out

    def refs(self, a: int, b: int) -> frozenset[str]:
        """The identifiers spelled in tokens a..b except those after a `.`."""
        return frozenset(self.t[k].value for k in range(a, min(b, self.n - 1) + 1) if self.t[k].kind == "ident" and self.p(k - 1) not in (".", "?."))

    def strip(self, a: int, b: int) -> tuple[int, int]:
        """The range without a type assertion (`x as const`, `x as string`), the parentheses around the whole of it and a trailing `;`."""
        while True:
            while b >= a and self.p(b) == ";":
                b -= 1
            cut = b
            j = a
            while j <= b:
                if self.is_open(j):
                    j = self.match(j) + 1
                    continue
                if self.w(j) in ("as", "satisfies") and j > a:
                    cut = j - 1
                    break
                j += 1
            b = cut
            if b > a and self.p(a) == "(" and self.match(a) == b:
                a, b = a + 1, b - 1
                continue
            return a, b

    def body_open(self, i: int) -> int | None:
        """From the token after a method's parameter list, the `{` that opens its body (the return type may hold braces of
        its own); None when there is no body (an overload, an abstract member, a property)."""
        j = i
        while j < self.n:
            if self.p(j) == "{":
                prev = self.t[j - 1] if j > 0 else None
                if prev is not None and ((prev.kind == "punct" and prev.value in TYPE_BRACE_PUNCT) or (prev.kind == "ident" and prev.value in TYPE_BRACE_WORDS)):
                    j = self.match(j) + 1
                    continue
                return j
            if self.p(j) == ";" or self.p(j) == "}" or self.p(j) == "=":
                return None
            if self.is_open(j):
                j = self.match(j) + 1
                continue
            j += 1
        return None

    # ---- scopes

    def _compound(self, i: int) -> bool:
        """Whether the `{` at i opens a block that runs when the module loads (an `if`, a loop, a `try`, a bare block): the
        statements in it are module level. A function body, a class body and an object literal are not."""
        if i == 0:
            return True
        prev = self.t[i - 1]
        if prev.kind == "punct":
            if prev.value in (";", "{", "}"):
                return True
            if prev.value == ")":
                m = self._open_of(i - 1)
                word = self.w(m - 1) if m >= 1 else None
                if word == "await" and m >= 2:
                    word = self.w(m - 2)
                return word in COMPOUND_WORDS
            return False
        return prev.kind == "ident" and prev.value in COMPOUND_BEFORE

    def _open_of(self, close: int) -> int:
        depth = 0
        for k in range(close, -1, -1):
            if self.is_close(k):
                depth += 1
            if self.is_open(k):
                depth -= 1
                if depth == 0:
                    return k
        return 0

    def module_scope(self, i: int) -> bool:
        """Whether token i is at module level: not inside a call, a list, a function, a class or an object (statements
        in `if`, loops, `try` and bare blocks count: they run when the module loads)."""
        if self._scope is None:
            flags = [True] * self.n
            stack: list[bool] = []
            for k, t in enumerate(self.t):
                closer, opener = self.is_close(k), self.is_open(k)
                if closer and stack:
                    stack.pop()
                flags[k] = all(stack)
                if opener:
                    stack.append(t.kind == "punct" and t.value == "{" and self._compound(k))
            self._scope = flags
        return self._scope[i]

    def in_declaration(self, i: int) -> bool:
        """Whether token i is inside a top-level declaration (a function, a class, a constant)."""
        if self._in_decl is None:
            flags = [False] * self.n
            for d in self.parsed.declarations:
                for k in range(max(d.start_tok, 0), min(d.end_tok, self.n - 1) + 1):
                    flags[k] = True
            self._in_decl = flags
        return self._in_decl[i]

    # ---- bindings

    def bindings(self) -> dict[str, Bind]:
        """The names the file binds to a module: by `import` and by `require` (`const x = require("m")`,
        `const { a, b: c } = require("m")`, `const x = require("m").a`)."""
        if self._bindings is not None:
            return self._bindings
        out: dict[str, Bind] = {}
        for rec in self.parsed.imports:
            if rec.kind == "import":
                for b in rec.bindings:
                    out[b.local] = Bind(rec.specifier, b.imported, rec.line)
        for i in range(self.n):
            if self.w(i) != "require" or self.p(i + 1) != "(" or self.p(i - 1) in (".", "?.") or self.t[i + 2].kind != "string" or self.p(i + 3) != ")":
                continue
            spec = unquote(self.t[i + 2].value)
            after = i + 4
            member = self.w(after + 1) if self.p(after) == "." and self.p(after + 2) != "(" else None
            if self.p(i - 1) != "=":
                continue
            left = i - 2
            if self.w(left) is not None and self.w(left - 1) in ("const", "let", "var"):
                out[self.t[left].value] = Bind(spec, member or "*cjs", self.t[i].line)
            elif self.p(left) == "}":  # const { a, b: c } = require("m")
                open_idx = self._open_of(left)
                if self.w(open_idx - 1) in ("const", "let", "var"):
                    for a, b in self.args(open_idx):
                        names = [self.t[k].value for k in range(a, b + 1) if self.t[k].kind == "ident"]
                        if self.p(a + 1) == ":" and len(names) >= 2:
                            out[names[1]] = Bind(spec, names[0], self.t[i].line)
                        elif names:
                            out[names[0]] = Bind(spec, names[0], self.t[i].line)
            elif self.p(left) == ":" or self.w(left) is not None:
                # a type annotation between the name and `=`: `const x: T = require("m")`
                k = left
                while k > 0 and self.w(k - 1) not in ("const", "let", "var") and self.p(k) != ";":
                    k -= 1
                if self.w(k - 1) in ("const", "let", "var") and self.w(k) is not None:
                    out[self.t[k].value] = Bind(spec, member or "*cjs", self.t[i].line)
        self._bindings = out
        return out

    # ---- exports

    def exports(self) -> list[Export]:
        """What the file exports: ES exports, and the CommonJS forms `module.exports = x`, `module.exports = { a, b: c }`,
        `module.exports.a = x`, `exports.a = x` and `module.exports = require("m")`. The CommonJS value is named `*cjs`."""
        if self._exports is not None:
            return self._exports
        out: list[Export] = []
        for name, ent in self.parsed.exports.items():
            local = ent.local if ent.local not in (None, "*") else None
            out.append(Export(name, local, ent.line, ent.source, ent.local if ent.source else None))
        for i in range(self.n):
            if not self.module_scope(i) or self.p(i - 1) in (".", "?."):
                continue
            if self.w(i) == "module" and self.p(i + 1) == "." and self.w(i + 2) == "exports":
                j = i + 3
            elif self.w(i) == "exports" and self.p(i + 1) == ".":
                j = i + 1
                if self.w(j + 1) is not None and self.p(j + 2) == "=" and self.p(j + 3) != "=":
                    out.append(self._member_export(self.t[j + 1].value, j + 3, self.t[i].line))
                continue
            else:
                continue
            if self.p(j) == "=" and self.p(j + 1) != "=":  # module.exports = ...
                k = j + 1
                if self.w(k) == "require" and self.p(k + 1) == "(" and self.t[k + 2].kind == "string":
                    out.append(Export("*cjs", None, self.t[i].line, unquote(self.t[k + 2].value), "*cjs"))
                elif self.p(k) == "{":
                    for a, b in self.args(k):
                        if self.p(a) == "." and self.p(a + 1) == ".":
                            continue
                        key = self.t[a]
                        if key.kind not in ("ident", "string"):
                            continue
                        name = unquote(key.value) if key.kind == "string" else key.value
                        if a == b:
                            out.append(Export(name, name, key.line))
                        elif self.p(a + 1) == ":" and a + 2 == b and self.w(b) is not None:
                            out.append(Export(name, self.t[b].value, key.line))
                        elif self.p(a + 1) == ":":
                            out.append(Export(name, None, key.line, value=(a + 2, b)))
                        else:
                            out.append(Export(name, None, key.line))
                    out.append(Export("*cjs", None, self.t[i].line))
                elif self.w(k) is not None and self._single(k):
                    out.append(Export("*cjs", self.t[k].value, self.t[i].line))
                else:
                    out.append(Export("*cjs", None, self.t[i].line))
            elif self.p(j) == "." and self.w(j + 1) is not None and self.p(j + 2) == "=" and self.p(j + 3) != "=":
                out.append(self._member_export(self.t[j + 1].value, j + 3, self.t[i].line))
        self._exports = out
        return out

    def _member_export(self, name: str, k: int, line: int) -> Export:
        """`exports.name = <expression starting at k>`: a name when the expression is one, else its tokens (a literal a constant may be)."""
        if self.w(k) is not None and self._single(k):
            return Export(name, self.t[k].value, line)
        return Export(name, None, line, value=(k, self.stmt_end(k - 1)))

    def stmt_end(self, i: int) -> int:
        """The last token of the statement that holds token i (the `;`, or the token before a line break that ends it)."""
        j = i + 1
        while j < self.n:
            if self.is_open(j):
                j = self.match(j) + 1
                continue
            if self.p(j) == ";":
                return j - 1
            if self.t[j].newline_before and self.p(j) not in (",", ".", "(", "?", ":", "=", "+", "-", "*", "&&", "||", "=>") and self.w(j) not in ("from", "as"):
                return j - 1
            j += 1
        return self.n - 1

    def _single(self, k: int) -> bool:
        """Whether the identifier at k is the whole right hand side of its statement."""
        return self.p(k + 1) in (";", None) or self.t[k + 1].newline_before or self.p(k + 1) == "}"

    # ---- strings

    def eval(self, a: int, b: int, ctx: Any, depth: int = 0) -> Str:
        """The string tokens a..b make: literals, templates, sums (`+`), and names `ctx.const(name, depth)` evaluates. A part
        that cannot be evaluated is `<unknown:EXPR>` with its place."""
        a, b = self.strip(a, b)
        if a > b:
            return Str()
        if a == b:
            t = self.t[a]
            if t.kind == "string" or (t.kind == "template" and not t.value.endswith("${")):
                return Str(unquote(t.value))
            if t.kind == "ident" and depth < 6:
                got = ctx.const(self.path, self, t.value, depth)
                if got is not None:
                    return got
        if self.t[a].kind == "template" and self.t[a].value.endswith("${") and self.t[b].kind == "template" and self.t[b].value.endswith("`"):
            out = Str(self._chunk(self.t[a].value[1:-2]))
            j = a + 1
            while j <= b:
                k = j
                while k <= b and not (self.t[k].kind == "template" and self.t[k].value.startswith("}") and self._depth_zero(j, k)):
                    k += 1
                if k > b:
                    return self.unknown(a, b)
                out += self.eval(j, k - 1, ctx, depth + 1)
                chunk = self.t[k].value
                if chunk.endswith("${"):
                    out += Str(self._chunk(chunk[1:-2]))
                    j = k + 1
                else:
                    out += Str(self._chunk(chunk[1:-1]))
                    break
            return out
        parts = self._split_plus(a, b)
        if len(parts) > 1:
            out = Str()
            for x, y in parts:
                out += self.eval(x, y, ctx, depth + 1)
            return out
        return self.unknown(a, b)

    @staticmethod
    def _chunk(raw: str) -> str:
        return unquote("`" + raw + "`")

    def _depth_zero(self, a: int, k: int) -> bool:
        """Whether no bracket opened between a and k is still open at k."""
        depth = 0
        for x in range(a, k):
            if self.is_open(x):
                depth += 1
            elif self.is_close(x):
                depth -= 1
        return depth == 0

    def _split_plus(self, a: int, b: int) -> list[tuple[int, int]]:
        parts: list[tuple[int, int]] = []
        start = a
        j = a
        while j <= b:
            if self.is_open(j):
                j = self.match(j) + 1
                continue
            if self.p(j) == "+" and j > start:
                parts.append((start, j - 1))
                start = j + 1
            j += 1
        parts.append((start, b))
        return parts

    def unknown(self, a: int, b: int) -> Str:
        src = self.text(a, b)
        if len(src) > 60:
            src = src[:57] + "..."
        return Str(f"<unknown:{src}>", ((src, self.path, self.line(a)),))


def build_consts(src: "Src") -> dict[str, tuple[int, int] | None]:
    """The constants a file declares at its top level: a name to the tokens of its initializer, None when the name is declared more than once (one
    rule for both adapters: a name assigned twice is unknown)."""
    out: dict[str, tuple[int, int] | None] = {}
    seen: dict[str, int] = {}
    for d in src.parsed.declarations:
        if d.kind != "variable" or d.init_tok < 0:
            continue
        seen[d.name] = seen.get(d.name, 0) + 1
        out[d.name] = src.strip(d.init_tok, d.end_tok) if seen[d.name] == 1 else None
    return out


# ------------------------------------------------------------------------------ paths

_PARAM = re.compile(r"^:([A-Za-z_$][\w$]*)(\([^)]*\))?([?*+]?)$")


def unknown_whole(text: str) -> bool:
    return text.startswith("<unknown:") and text.endswith(">") and text.count("<unknown:") == 1


def convert_path(text: str) -> list[tuple[str, bool]]:
    """An Express path as the `{param}` form the http edge matches: `[(path, partial)]`, more than one when an optional
    parameter (`:id?`) makes two URLs. `*` last is the rest of the path, a pattern after a parameter is dropped (the route fits
    more than it serves), any other pattern in a segment is an unknown segment (partial)."""
    variants: list[tuple[list[str], bool]] = [([], False)]
    held: list[str] = []

    def hold(m: "re.Match[str]") -> str:
        held.append(m.group())
        return f"\x00{len(held) - 1}\x00"

    text = re.sub(r"<unknown:[^>]*>", hold, text)
    texts = _groups(text)
    if len(texts) > 1:  # Express 5 optional groups: `/users{/:id}` is `/users` and `/users/:id`
        out: list[tuple[str, bool]] = []
        for t in texts:
            for path, partial in convert_path(re.sub("\x00(\\d+)\x00", lambda m: held[int(m.group(1))], t)):
                if (path, partial) not in out:
                    out.append((path, partial))
        return out
    parts = text.split("/")
    for n, seg in enumerate(parts):
        last = n == len(parts) - 1
        if seg == "":
            continue
        if "\x00" in seg:
            seg = re.sub("\x00(\\d+)\x00", lambda m: held[int(m.group(1))], seg)
            variants = [(v + [seg], True) for v, _p in variants]
            continue
        m = _PARAM.match(seg)
        if m:
            name, pattern, mod = m.groups()
            if mod == "?":
                variants = [(v, p) for v, p in variants] + [(v + ["{" + name + "}"], p or bool(pattern)) for v, p in variants]
            elif mod in ("*", "+"):  # what `*` and `+` after a parameter mean differs between Express 4 and 5: the route is partial
                variants = [(v + ["{" + name + ":path}"], True) for v, p in variants]
            else:
                variants = [(v + ["{" + name + "}"], p or bool(pattern)) for v, p in variants]  # a pattern is dropped: the route fits more than it serves
            continue
        if seg == "*" and last:
            variants = [(v + ["{rest:path}"], p) for v, p in variants]
            continue
        if re.search(r"[:()*+?\[\]\\^$|]", seg):
            variants = [(v + [f"<unknown:{seg}>"], True) for v, _p in variants]
            continue
        variants = [(v + [seg], p) for v, p in variants]
    out: list[tuple[str, bool]] = []
    for v, p in variants:
        path = "/" + "/".join(v)
        if (path, p) not in out:
            out.append((path, p))
    return out


def _groups(text: str) -> list[str]:
    """The paths an Express 5 optional group `{...}` (one level) stands for: with and without its content."""
    m = re.search(r"\{([^{}]*)\}", text)
    if m is None:
        return [text]
    out: list[str] = []
    for t in (text[: m.start()] + text[m.end():], text[: m.start()] + m.group(1) + text[m.end():]):
        for x in _groups(t):
            if x not in out:
                out.append(x)
    return out


def path_pieces(value: Str) -> list[tuple[Str, bool]]:
    """A path string (a route's or a mount's) as pieces: each variant of `convert_path` with the unknown parts it keeps. A path
    that is unknown as a whole stands for a segment, `/<unknown:EXPR>`."""
    text = value.value
    if unknown_whole(text) or (not text.startswith("/") and "<unknown:" in text and text.count("/") == 0):
        return [(Str("/" + text, value.unknown), True)]
    out: list[tuple[Str, bool]] = []
    for path, partial in convert_path(text):
        out.append((Str(path, value.unknown), partial or bool(value.unknown)))
    return out
