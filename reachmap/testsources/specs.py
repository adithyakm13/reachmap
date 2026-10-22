"""Playwright and Cypress specs in a configured repository, as a test source that needs no test-management system.

A spec file is read at a pinned commit and tokenised with the JavaScript lexer, so a `goto` in a comment or in a string is not a
call. A `page.goto(...)` (Playwright) or `cy.visit(...)` (Cypress) whose first argument is a string or a template literal is a
visit; the address is cut to its path (scheme, host, query and fragment dropped) and its segments are matched against the screen
routes of the M7 route table: a literal segment equals a route's static segment or fills a parameter, a substitution of a template
(`${id}`) fills a route parameter and never a static segment, a static route beats a parameter route, the number of segments has to
agree (a trailing `*` takes the rest). A visit to a route that is a screen of the run is a suggestion for that screen, with the spec
line as pinned evidence and the name of the nearest `test(` or `it(` before it as the title. Nothing about a visit is a claim about
what the spec checks.

An address that is not a literal (a variable, a concatenation, an object, a template that starts with a substitution, a call) is
recorded as `spec_url_partial` (non-blocking) and never matched. A route table that is not whole, a route whose path could not be
evaluated and a spec file that does not lex cleanly make the list partial (`Found.cut`). Specs that cannot be read at all, or a
repository whose primary branch cannot be read, are `SourceError`.

The commit: the changed repository is read at the head of the change (specs travel with the code), any other at the head of its
primary branch as of `--as-of`. The default spec files are `e2e/**` and `tests/**` for Playwright and `cypress/e2e/**` and
`cypress/integration/**` for Cypress; `globs` in the configuration replaces them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Sequence
from urllib.parse import unquote

from reachmap import config, redact
from reachmap.adapters import Route
from reachmap.adapters.js.lexer import Token, tokenize, unquote as unquote_string
from reachmap.gitlayer import ReadFailed
from reachmap.incomplete import record
from reachmap.model import Evidence, Suggestion
from reachmap.testsources import match
from reachmap.testsources.base import Context, Found, SourceError, clean_title

__all__ = ["Parsed", "Partial", "Specs", "Visit", "match_routes", "spec_matcher", "visits"]

Segment = str | None  # a literal segment, or None for a parameter (a substitution of a template)
EXTENSIONS = "{ts,tsx,js,jsx,mjs,cjs}"
DEFAULT_GLOBS = {
    "playwright": (f"e2e/**/*.{{spec,test}}.{EXTENSIONS}", f"tests/**/*.{{spec,test}}.{EXTENSIONS}"),
    "cypress": (f"cypress/e2e/**/*.{{cy,spec}}.{EXTENSIONS}", f"cypress/integration/**/*.{{cy,spec}}.{EXTENSIONS}"),
}


@dataclass(frozen=True, slots=True)
class Visit:
    line: int  # the line of the address (the call's own line when the address is on it)
    segments: tuple[Segment, ...]
    detail: str  # that line of the file, as written
    title: str  # the nearest test or it before it ("" when there is none)


@dataclass(frozen=True, slots=True)
class Partial:
    line: int
    reason: str
    detail: str
    title: str


@dataclass(frozen=True, slots=True)
class Parsed:
    visits: tuple[Visit, ...]
    partial: tuple[Partial, ...]
    problems: tuple[str, ...]  # what the lexer could not make sense of: visits after it may be missing
    tests: int = 0  # the `test("name", ...)` and `it("name", ...)` calls: a file with tests and no visit may visit through a helper


# ------------------------------------------------------------------------------ reading a file

_OPEN, _CLOSE = ("(", "[", "{"), (")", "]", "}")
_TEST_MODIFIERS = ("only", "skip", "fixme", "fails", "slow")
_HOST = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://[^/?#]*")


def _is_call(toks: Sequence[Token], i: int, framework: str) -> bool:
    t = toks[i]
    name = "goto" if framework == "playwright" else "visit"
    if t.kind != "ident" or t.value != name or i + 1 >= len(toks) or toks[i + 1].value != "(" or toks[i + 1].kind != "punct":
        return False
    if i == 0 or toks[i - 1].kind != "punct" or toks[i - 1].value not in (".", "?."):
        return False
    return framework == "playwright" or (i >= 2 and toks[i - 2].kind == "ident" and toks[i - 2].value == "cy")


def _argument(toks: Sequence[Token], start: int) -> list[Token]:
    """The tokens of the first argument of the call whose `(` is at `start - 1`: up to the top level comma or the closing bracket."""
    out: list[Token] = []
    depth = 0
    for t in toks[start:]:
        if t.kind == "punct" and depth == 0 and t.value in (",", ")"):
            break
        if t.kind == "punct" and t.value in _OPEN:
            depth += 1
        elif t.kind == "punct" and t.value in _CLOSE:
            depth -= 1
        elif t.kind == "template":
            depth += (1 if t.value.endswith("${") else 0) - (1 if t.value.startswith("}") else 0)
        out.append(t)
    return out


def _template_pieces(arg: Sequence[Token]) -> list[str | None] | None:
    """The literal text and the substitutions (None) of an argument that is exactly one template literal, else None."""
    pieces: list[str | None] = []
    open_outer, inner, done = False, 0, False
    for t in arg:
        if done:
            return None
        if not open_outer:
            if t.kind != "template" or not t.value.startswith("`"):
                return None
            ends = t.value.endswith("${")
            pieces.append(t.value[1:-2] if ends else t.value[1:-1])
            if ends:
                pieces.append(None)
                open_outer, inner = True, 0
            else:
                done = True
        elif t.kind == "template" and t.value.startswith("}") and inner == 0:
            ends = t.value.endswith("${")
            pieces.append(t.value[1:-2] if ends else t.value[1:-1])
            if ends:
                pieces.append(None)
            else:
                open_outer, done = False, True
        elif t.kind == "punct" and t.value in _OPEN:
            inner += 1
        elif t.kind == "punct" and t.value in _CLOSE:
            inner -= 1
        elif t.kind == "template":
            inner += (1 if t.value.endswith("${") else 0) - (1 if t.value.startswith("}") else 0)
    return pieces if done and not open_outer else None


def _segments(pieces: Sequence[str | None]) -> tuple[Segment, ...]:
    """The path of an address as segments: scheme, host, query and fragment are not part of it; a segment that holds a substitution
    is a parameter."""
    parts: list[str | None] = []
    for n, p in enumerate(pieces):
        if p is None:
            parts.append(None)
            continue
        if n == 0:
            p = _HOST.sub("", p, count=1)
        cut = min((p.find(c) for c in "?#" if c in p), default=-1)
        parts.append(p if cut < 0 else p[:cut])
        if cut >= 0:
            break
    out: list[Segment] = []
    cur, has_param = "", False

    def close() -> None:
        nonlocal cur, has_param
        if has_param:
            out.append(None)
        elif cur == "..":
            if out:
                out.pop()
        elif cur and cur != ".":
            out.append(cur)
        cur, has_param = "", False

    for p in parts:
        if p is None:
            has_param = True
            continue
        for k, part in enumerate(p.split("/")):
            if k:
                close()
            cur += part
    close()
    return tuple(out)


def _classify(arg: Sequence[Token]) -> tuple[tuple[Segment, ...] | None, str]:
    """(segments, "") for a literal address, (None, why) for one that is not."""
    first = arg[0]
    if first.kind == "punct" and first.value == "{":
        return None, "the address is an object"
    if first.kind == "string" and len(arg) == 1:
        return _segments([unquote_string(first.value)]), ""
    if first.kind == "template":
        pieces = _template_pieces(arg)
        if pieces is not None:
            if pieces[0] == "" and len(pieces) > 1:
                return None, "the address starts with a substitution"
            if len(pieces) > 1 and pieces[1] is None and re.fullmatch(r"[A-Za-z][A-Za-z0-9+.-]*://", pieces[0] or ""):
                return None, "the host is a substitution"
            return _segments(pieces), ""
    depth = 0
    for t in arg:
        if t.kind == "punct" and t.value in _OPEN:
            depth += 1
        elif t.kind == "punct" and t.value in _CLOSE:
            depth -= 1
        elif t.kind == "punct" and t.value == "+" and depth == 0:
            return None, "the address is built by concatenation"
    if all(t.kind == "ident" or (t.kind == "punct" and t.value in (".", "?.")) for t in arg):
        return None, "the address is a variable"
    return None, "the address is not a literal"


def _titles(toks: Sequence[Token]) -> list[tuple[int, str]]:
    """(token index, name) of every `test("name", ...)` and `it("name", ...)` (`.only`, `.skip` and the like included)."""
    out: list[tuple[int, str]] = []
    for i, t in enumerate(toks):
        if t.kind != "ident" or t.value not in ("test", "it"):
            continue
        j = i + 1
        if j + 1 < len(toks) and toks[j].value == "." and toks[j + 1].value in _TEST_MODIFIERS:
            j += 2
        if j + 1 < len(toks) and toks[j].value == "(" and toks[j].kind == "punct":
            a = toks[j + 1]
            if a.kind == "string":
                out.append((i, unquote_string(a.value)))
            elif a.kind == "template" and a.value.startswith("`") and a.value.endswith("`") and not a.value.endswith("${"):
                out.append((i, a.value[1:-1]))
    return out


def visits(text: str, framework: str) -> Parsed:
    """The visits of one spec file: `page.goto` for `playwright`, `cy.visit` for `cypress`. Comments and strings are not code."""
    if framework not in DEFAULT_GLOBS:
        raise ValueError(f"unknown framework {framework!r}; use playwright or cypress")
    lex = tokenize(text)
    toks = lex.tokens
    lines = text.split("\n")
    titles = _titles(toks)
    found: list[Visit] = []
    partial: list[Partial] = []
    for i, t in enumerate(toks):
        if not _is_call(toks, i, framework):
            continue
        arg = _argument(toks, i + 2)
        if not arg:
            continue
        line = arg[0].line
        detail = lines[line - 1].rstrip("\r") if 0 < line <= len(lines) else ""
        title = next((name for index, name in reversed(titles) if index < i), "")
        segments, why = _classify(arg)
        if segments is None:
            partial.append(Partial(line, why, detail, title))
        else:
            found.append(Visit(line, segments, detail, title))
    return Parsed(tuple(found), tuple(partial), lex.problems, len(titles))


# ------------------------------------------------------------------------------ routes and files


def _route_parts(path: str) -> list[str]:
    return [p for p in path.split("/") if p]


def _same(a: str, b: str) -> bool:
    return unquote(a).lower() == unquote(b).lower()


def match_routes(segments: Sequence[Segment], routes: Sequence[Route]) -> list[Route]:
    """The screen routes a visit to `segments` reaches: the most specific ones (the most static segments matched; a route with a
    splat after a route with none), more than one only when equally specific. A route with a part that could not be evaluated, and
    an API route, are never matched."""
    best: list[tuple[tuple[int, int], Route]] = []
    for r in routes:
        if r.kind != "screen" or r.partial or "<unknown" in r.path:
            continue
        parts = _route_parts(r.path)
        splat = bool(parts) and parts[-1] == "*"
        fixed = parts[:-1] if splat else parts
        if len(segments) < len(fixed) or (not splat and len(segments) != len(fixed)):
            continue
        static, ok = 0, True
        for seg, part in zip(segments, fixed):
            if part.startswith(":"):
                continue  # a parameter takes a literal and a parameter
            if seg is None or not _same(seg, part):
                ok = False  # a spec parameter is never a static segment
                break
            static += 1
        if ok:
            best.append(((static, 0 if splat else 1), r))
    if not best:
        return []
    top = max(rank for rank, _ in best)
    out: list[Route] = []
    for rank, r in best:
        if rank == top and all(r.path != x.path for x in out):
            out.append(r)
    return out


def spec_matcher(framework: str, globs: Sequence[str] | None) -> Callable[[str], bool]:
    """Whether a repository path is a spec file: the configured `globs`, or the framework's defaults."""
    from reachmap.adapters.react_router import glob_regex

    regexes = [glob_regex(g) for g in (globs or DEFAULT_GLOBS[framework])]
    return lambda path: any(rx.match(path) for rx in regexes)


# ------------------------------------------------------------------------------ the source


class Specs:
    """`kind` is `playwright` or `cypress`."""

    def __init__(self, source: config.TestSource, *, kind: str | None = None) -> None:
        self.source = source
        self.options = source.options
        self.kind = kind or source.kind
        self.areas = ()

    @property
    def repo(self) -> str | None:
        value = self.options.get("repo")
        return value if isinstance(value, str) and value else None

    @property
    def label(self) -> str:
        return f"{self.kind}:{self.repo}" if self.repo else self.kind

    def unconfigured(self) -> str | None:
        if self.source.unset_vars:
            return f"the variable {', '.join(self.source.unset_vars)} in the configuration is unset or empty"
        return None if self.repo else "repo is not set: it names the repository the specs are in"

    def suggest(self, infos: Sequence[match.AreaInfo], ctx: Context) -> Found:
        repo = self.repo
        assert repo is not None
        screens = str(self.options.get("screens_repo") or repo)
        run = ctx.get_run()
        git = run.git
        with git.quiet() as sink:  # a read that fails here is this source's answer, not a blocking record of the whole run
            try:
                sha_specs = self._sha(ctx, run, repo)
                sha_screens = self._sha(ctx, run, screens)
                entry = run.entry(screens)
                if entry is None:
                    return Found(unconfigured=f"{screens} declares no entry points the run can read, so there is no route table to match the specs against")
                routes, route_cut = self._routes(entry, screens, sha_screens, sink)
                files = self._files(git, run, repo, sha_specs)
            except ReadFailed as exc:
                raise SourceError(redact.for_record(str(exc))) from None
        screen_routes = [r for r in routes if r.kind == "screen"]
        by_path: dict[str, list[match.AreaInfo]] = {}
        for info in infos:
            if info.area.kind == "screen" and info.area.repo == screens:
                for p in (info.area.label, *info.area.also_mounted):
                    by_path.setdefault(p, []).append(info)
        nothing = []  # why the source had nothing to look at: a source that looked at nothing has not said "none suggested"
        if not files:
            nothing.append(f"no file of {repo} matches the spec globs of this {self.kind} source")
        if not screen_routes:
            nothing.append(f"the route table of {screens} has no screen route, so no visit can be matched to a screen")
        if not by_path:
            nothing.append(f"none of the areas of this run is a screen of {screens}, so no spec was matched")
        if nothing:
            return Found(unconfigured="; ".join(nothing))
        found: list[Suggestion] = []
        partials: list[Any] = []
        cuts = [route_cut] if route_cut else []
        tests = seen = 0
        unmatched: list[str] = []
        for path, text in files:
            parsed = visits(text, self.kind)
            tests += parsed.tests
            seen += len(parsed.visits) + len(parsed.partial)
            if parsed.problems:
                cuts.append(f"{path} did not lex cleanly ({parsed.problems[0]}): visits in it may be missing")
            for p in parsed.partial:
                record(partials, "spec_url_partial", f"{repo} {path}:{p.line}", detail=redact.for_record(f"{p.reason}: {p.detail.strip()}"))
            for v in parsed.visits:
                seen_areas: set[str] = set()
                matched = match_routes(v.segments, screen_routes)
                if not matched:
                    unmatched.append(f"{path}:{v.line}")
                for r in matched:
                    for info in by_path.get(r.path, ()):
                        if info.area.id in seen_areas:
                            continue
                        seen_areas.add(info.area.id)
                        found.append(Suggestion(self.label, f"{path}:{v.line}", clean_title(v.title) or path, (), info.area.id, (Evidence(repo, path, v.line, sha_specs, v.detail),)))
        if tests and not seen:
            cuts.append(f"{tests} test or it calls in {len(files)} spec file(s) and no visit the reader could see: the specs may visit through helpers or page objects")
        if unmatched:
            shown = ", ".join(unmatched[:5]) + (f" and {len(unmatched) - 5} more" if len(unmatched) > 5 else "")
            record(partials, "spec_visit_unmatched", f"{repo} {self.kind} specs", detail=redact.for_record(f"{len(unmatched)} visits match no screen route: {shown}"))
        return Found(tuple(found), tuple(partials), "; ".join(cuts))

    # ---- what it reads

    def _sha(self, ctx: Context, run: Any, name: str) -> str:
        if ctx.origin is not None and ctx.origin[0] == name:
            return ctx.origin[1]
        pin = run.primary(name)
        if pin is None:
            raise SourceError(f"the primary branch of {name} could not be read, so its specs and routes were not")
        return pin.sha

    def _routes(self, entry: Any, screens: str, sha: str, sink: list) -> tuple[list[Route], str]:
        routes = entry.routes(screens, sha)
        cuts = []
        if entry.problems or any(r.blocks_selection for r in sink):
            first = (list(entry.problems) or [r.note() for r in sink if r.blocks_selection])[0]
            cuts.append(f"the route table of {screens} is not whole ({redact.for_record(first, 200)}): visits to a screen missing from it are not matched")
        unevaluated = [r for r in routes if r.partial]
        if unevaluated:
            cuts.append(f"{len(unevaluated)} route(s) of {screens} have a path that could not be evaluated: visits to them are not matched")
        return routes, "; ".join(cuts)

    def _files(self, git: Any, run: Any, repo: str, sha: str) -> list[tuple[str, str]]:
        co = git.checkout(run.remote(repo), sha)
        matcher = spec_matcher(self.kind, self.options.get("globs") or None)
        paths = sorted(p for p in co.files if matcher(p))
        reads = git.files_at(co, paths) if paths else {}
        bad = [(p, r.detail) for p, r in reads.items() if not r.is_found]
        if bad:
            raise SourceError(f"{len(bad)} spec file(s) could not be read; the first is {bad[0][0]}: {redact.for_record(bad[0][1], 200)}")
        return [(p, reads[p].text) for p in paths]
