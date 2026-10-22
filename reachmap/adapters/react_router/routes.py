"""Routes of a React Router routes file, read from tokens (never from the text of comments or strings).

Two forms are read: JSX `<Route path="..." element={<Page />} />` (attributes in any order, single or multi-line,
`element={<Page prop={x} />}`, `Component={Page}`, wrapped pages such as `<React.Suspense><Page /></React.Suspense>`,
nested `<Route>` children) and object routes (`{ path, element | Component | lazy, index, children }` in an array,
as `createBrowserRouter([...])` takes). `compose` joins child paths to their parents' (a relative child path joins the
parent's, an absolute one stands alone, an index or layout route takes the parent's path) and works out which routes
are leaves (a parent route renders its children; only a leaf is a screen of its own).

What a route renders is a list of names (JSX tag names, a `Component` identifier) and of dynamic `import("...")`
specifiers; mapping a name to a file is the adapter's job (it knows the imports and the aliases). The parser also
reads the import bindings of the file and the declarations that hold a dynamic import (`const Page = React.lazy(() =>
import("./Page"))`).

This is not a JSX parser. A path that is not a string literal is kept as source text (`path_expr`), never guessed. A
file whose tokens do not balance is a problem and claims no route.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from reachmap.adapters import normalize_path
from reachmap.adapters.js.lexer import Token, unquote
from reachmap.adapters.js.syntax import Parsed

__all__ = ["Composed", "RouteDecl", "RoutesParse", "compose", "parse_routes"]

ROUTE = "Route"
CREATORS = frozenset({"createBrowserRouter", "createHashRouter", "createMemoryRouter", "useRoutes", "createRoutesFromElements"})
# A word before `<` after which it still starts an element rather than comparing
KEYWORD_BEFORE_ELEMENT = frozenset({"return", "default", "yield", "await", "else", "case", "typeof", "void", "of", "in", "do"})


@dataclass(slots=True)
class RouteDecl:
    """One route as written. `own_lines` are the lines of the route's own tag (an object route's own properties),
    not those of its children; `first_line` and `last_line` the whole span. `tags` are the names the element renders
    (`Layout`, `React.Suspense`, `pages.Home`) in source order; `lazy` the specifiers and lines of a dynamic `import()`
    in the element, `Component` or `lazy` property."""

    index: int
    parent: int | None
    form: str  # jsx | object
    path: str | None  # a string literal path
    path_expr: str | None  # the source of a path that is not a literal
    is_index: bool
    first_line: int
    last_line: int
    own_lines: frozenset[int]
    tags: tuple[str, ...]
    lazy: tuple[tuple[str, int], ...]
    children: list[int] = field(default_factory=list)


@dataclass(slots=True)
class RoutesParse:
    routes: list[RouteDecl]
    imports: dict[str, tuple[str, str, int]]  # local name to (specifier, imported name, line of the specifier)
    lazies: dict[str, tuple[str, int]]  # local name to (specifier, line of the import() call)
    problems: list[str]


@dataclass(frozen=True, slots=True)
class Composed:
    """A route with its full path. `chain` are the indexes of the routes from the outermost ancestor to this one;
    `tags` and `lazy` are everything the chain renders (ancestors first). `unknown` lists the path expressions that
    could not be evaluated (the full path then holds `<unknown:EXPR>`)."""

    decl: RouteDecl
    path_full: str
    is_leaf: bool
    chain: tuple[int, ...]
    tags: tuple[str, ...]
    lazy: tuple[tuple[str, int], ...]
    partial: bool
    unknown: tuple[str, ...]

    @property
    def index(self) -> int:
        return self.decl.index


def parse_routes(parsed: Parsed) -> RoutesParse:
    problems = list(parsed.problems)
    imports, lazies = _bindings(parsed)
    routes: list[RouteDecl] = []
    if not problems:
        _Scanner(parsed, routes).run()
    return RoutesParse(routes if not problems else [], imports, lazies, problems)


def _bindings(parsed: Parsed) -> tuple[dict[str, tuple[str, str, int]], dict[str, tuple[str, int]]]:
    imports: dict[str, tuple[str, str, int]] = {}
    for rec in parsed.imports:
        if rec.kind != "import":
            continue
        for b in rec.bindings:
            imports[b.local] = (rec.specifier, b.imported, rec.line)
    lazies: dict[str, tuple[str, int]] = {}
    toks = parsed.tokens
    for d in parsed.declarations:
        if d.kind != "variable" or d.init_tok < 0:
            continue
        for k in range(d.init_tok, min(d.end_tok, len(toks) - 1) + 1):
            hit = _dynamic_import(parsed, k)
            if hit is not None:
                lazies[d.name] = hit
                break
    return imports, lazies


def _dynamic_import(parsed: Parsed, k: int) -> tuple[str, int] | None:
    """`import("spec")` with a plain string at token k: (specifier, line)."""
    toks = parsed.tokens
    if toks[k].kind == "ident" and toks[k].value == "import" and parsed.p(k + 1) == "(" and parsed.p(k - 1) not in (".", "?."):
        arg = toks[k + 2]
        if arg.kind == "string" or (arg.kind == "template" and arg.value.startswith("`") and arg.value.endswith("`") and len(arg.value) >= 2):
            if parsed.p(k + 3) == ")":
                return unquote(arg.value), arg.line
    return None


def _literal(tok: Token) -> str | None:
    if tok.kind == "string" and len(tok.value) >= 2 and tok.value[-1] == tok.value[0]:
        return unquote(tok.value)
    if tok.kind == "template" and tok.value.startswith("`") and tok.value.endswith("`") and len(tok.value) >= 2:
        return unquote(tok.value)
    return None


class _Scanner:
    def __init__(self, parsed: Parsed, out: list[RouteDecl]) -> None:
        self.p = parsed
        self.t = parsed.tokens
        self.out = out
        self.consumed: set[int] = set()  # openers of object routes already read as children

    # ---- shared helpers

    def _value_before(self, i: int) -> bool:
        """Whether the token before `<` ends a value (so the `<` compares), as opposed to starting an element."""
        prev = self.t[i - 1] if i > 0 else None
        if prev is None:
            return False
        if prev.kind in ("number", "string", "regex"):
            return True
        if prev.kind == "template":
            return not prev.value.endswith("${")
        if prev.kind == "ident":
            return prev.value not in KEYWORD_BEFORE_ELEMENT
        return prev.value in (")", "]")

    def _tag_at(self, i: int) -> tuple[str, int] | None:
        """A JSX opening tag `<Name.Part` at token i: (dotted name, index after the name)."""
        t = self.t
        if not (t[i].kind == "punct" and t[i].value == "<" and t[i + 1].kind == "ident") or self._value_before(i):
            return None
        name = t[i + 1].value
        j = i + 2
        while self.p.p(j) == "." and t[j + 1].kind == "ident":
            name += "." + t[j + 1].value
            j += 2
        return name, j

    def _expr_info(self, a: int, b: int) -> tuple[list[str], list[tuple[str, int]]]:
        """What tokens a..b (inclusive) render: JSX tag names and dynamic imports; a lone identifier is a component."""
        tags: list[str] = []
        lazy: list[tuple[str, int]] = []
        if a == b and self.t[a].kind == "ident":
            return [self.t[a].value], lazy
        for k in range(a, b + 1):
            hit = self._tag_at(k)
            if hit is not None:
                name = hit[0]
                if name[0].isupper() or "." in name:
                    tags.append(name)
            imp = _dynamic_import(self.p, k)
            if imp is not None:
                lazy.append(imp)
        return tags, lazy

    def _lines(self, a: int, b: int, skip: tuple[int, int] | None = None) -> frozenset[int]:
        lines: set[int] = set()
        for k in range(a, b + 1):
            if skip is not None and skip[0] < k < skip[1]:
                continue
            tok = self.t[k]
            lines.update(range(tok.line, tok.end_line + 1))
        return frozenset(lines)

    def _path_of(self, a: int, b: int) -> tuple[str | None, str | None]:
        """A path given as tokens a..b: (literal, None), or (None, source text) when it is not a literal."""
        if a == b:
            lit = _literal(self.t[a])
            if lit is not None:
                return lit, None
        src = self.p.text[self.t[a].start:self.t[b].end].replace("\r", "")
        return None, " ".join(src.split())

    # ---- driver

    def run(self) -> None:
        self._jsx()
        self._objects()
        self.out.sort(key=lambda r: (r.first_line, r.index))
        remap = {r.index: n for n, r in enumerate(self.out)}
        for r in self.out:
            r.index = remap[r.index]
            r.parent = remap[r.parent] if r.parent is not None else None
            r.children = sorted(remap[c] for c in r.children)

    # ---- JSX

    def _jsx(self) -> None:
        t, n = self.t, len(self.t)
        stack: list[RouteDecl] = []
        i = 0
        while i < n:
            hit = self._tag_at(i)
            if hit is not None and hit[0] == ROUTE:
                decl, end_tok, closed = self._open_tag(i, hit[1], stack[-1] if stack else None)
                if decl is not None:
                    if closed:
                        decl.last_line = t[end_tok].line
                    else:
                        stack.append(decl)
                i = end_tok + 1
                continue
            if t[i].kind == "punct" and t[i].value == "<" and self.p.p(i + 1) == "/" and self.p.w(i + 2) == ROUTE and self.p.p(i + 3) == ">":
                if stack:
                    stack.pop().last_line = t[i + 3].line
                i += 4
                continue
            i += 1

    def _open_tag(self, start: int, j: int, parent: RouteDecl | None) -> tuple[RouteDecl | None, int, bool]:
        t, p = self.t, self.p
        n = len(t)
        path: str | None = None
        path_expr: str | None = None
        is_index = False
        tags: list[str] = []
        lazy: list[tuple[str, int]] = []
        while j < n:
            tok = t[j]
            if tok.kind == "punct" and tok.value == "/" and p.p(j + 1) == ">":
                return self._make_jsx(start, j + 1, parent, path, path_expr, is_index, tags, lazy), j + 1, True
            if tok.kind == "punct" and tok.value == ">":
                return self._make_jsx(start, j, parent, path, path_expr, is_index, tags, lazy), j, False
            if tok.kind == "punct" and tok.value == "{":  # {...spread}
                j = p.matching(j) + 1
                continue
            if tok.kind == "ident":
                name = tok.value
                j += 1
                while p.p(j) == "-" and t[j + 1].kind == "ident":  # aria-label style names
                    name += "-" + t[j + 1].value
                    j += 2
                if p.p(j) != "=":
                    if name == "index":
                        is_index = True
                    continue
                j += 1
                value = t[j]
                if value.kind == "string":
                    a = b = j
                    j += 1
                elif value.kind == "punct" and value.value == "{":
                    close = p.matching(j)
                    a, b = j + 1, close - 1
                    j = close + 1
                    if a > b:
                        continue
                else:  # an element as an attribute value or something else: the tag cannot be read
                    return None, j, False
                if name == "path":
                    path, path_expr = self._path_of(a, b)
                elif name == "index":
                    is_index = not (a == b and t[a].value == "false")
                elif name in ("element", "Component"):
                    found, imp = self._expr_info(a, b)
                    tags += found
                    lazy += imp
                continue
            j += 1
        return None, n - 1, False

    def _make_jsx(self, start: int, end: int, parent: RouteDecl | None, path: str | None, path_expr: str | None, is_index: bool,
                  tags: list[str], lazy: list[tuple[str, int]]) -> RouteDecl:
        decl = RouteDecl(
            index=len(self.out), parent=parent.index if parent is not None else None, form="jsx", path=path, path_expr=path_expr,
            is_index=is_index, first_line=self.t[start].line, last_line=self.t[end].line, own_lines=self._lines(start, end),
            tags=tuple(tags), lazy=tuple(lazy),
        )
        self.out.append(decl)
        if parent is not None:
            parent.children.append(decl.index)
        return decl

    # ---- object routes

    def _objects(self) -> None:
        t, p = self.t, self.p
        n = len(t)
        for k in range(n):
            if k in self.consumed or not (t[k].kind == "punct" and t[k].value == "{"):
                continue
            if not (p.p(k - 1) in ("[", ",", "(") or (p.p(k - 1) is None and k == 0)):
                continue
            close = p.matching(k)
            if close <= k or not self._looks_like_route(k, close):
                continue
            self._object(k, close, None)

    def _props(self, k: int, close: int) -> list[tuple[str, int, int]]:
        """The top-level properties of the object whose `{` is at k: (key, first value token, last value token)."""
        t, p = self.t, self.p
        out: list[tuple[str, int, int]] = []
        j = k + 1
        while j < close:
            key_tok = t[j]
            if key_tok.kind in ("ident", "string") and p.p(j + 1) == ":":
                key = unquote(key_tok.value) if key_tok.kind == "string" else key_tok.value
                a = j + 2
                e = a
                while e < close:
                    tk = t[e]
                    if tk.kind == "punct" and tk.value in ("(", "[", "{") or (tk.kind == "template" and tk.value.endswith("${")):
                        e = p.matching(e) + 1
                        continue
                    if tk.kind == "punct" and tk.value == ",":
                        break
                    e += 1
                out.append((key, a, e - 1))
                j = e + 1
                continue
            if key_tok.kind == "punct" and key_tok.value in ("(", "[", "{"):
                j = p.matching(j) + 1
                continue
            j += 1
        return out

    def _looks_like_route(self, k: int, close: int) -> bool:
        keys = {key for key, _a, _b in self._props(k, close)}
        if {"path", "index"} & keys:
            return bool({"element", "Component", "lazy", "children"} & keys)
        return "children" in keys and bool({"element", "Component", "lazy"} & keys)  # a layout route has no path

    def _object(self, k: int, close: int, parent: RouteDecl | None) -> RouteDecl:
        t, p = self.t, self.p
        self.consumed.add(k)
        path: str | None = None
        path_expr: str | None = None
        is_index = False
        tags: list[str] = []
        lazy: list[tuple[str, int]] = []
        children: tuple[int, int] | None = None
        for key, a, b in self._props(k, close):
            if b < a:
                continue
            if key == "path":
                path, path_expr = self._path_of(a, b)
            elif key == "index":
                is_index = not (a == b and t[a].value == "false")
            elif key in ("element", "Component", "lazy"):
                found, imp = self._expr_info(a, b)
                if key == "lazy" and a != b:
                    found = []  # `lazy: () => import(...)` renders what the imported module exports, not a tag here
                tags += found
                lazy += imp
            elif key == "children" and t[a].kind == "punct" and t[a].value == "[":
                children = (a, p.matching(a))
        decl = RouteDecl(
            index=len(self.out), parent=parent.index if parent is not None else None, form="object", path=path, path_expr=path_expr,
            is_index=is_index, first_line=t[k].line, last_line=t[close].line, own_lines=self._lines(k, close, children), tags=tuple(tags), lazy=tuple(lazy),
        )
        self.out.append(decl)
        if parent is not None:
            parent.children.append(decl.index)
        if children is not None:
            a, b = children
            j = a + 1
            while j < b:
                if t[j].kind == "punct" and t[j].value == "{":
                    end = p.matching(j)
                    if end > j and self._looks_like_route(j, end):
                        self._object(j, end, decl)
                    j = end + 1
                else:
                    j += 1
        return decl


def _join(parent: str, child: str) -> str:
    if child.startswith("/"):
        return child
    if parent in ("", "/"):
        return "/" + child
    return parent.rstrip("/") + "/" + child  # an empty child is the parent's own path (`normalize_path` drops the slash)


def compose(res: RoutesParse) -> list[Composed]:
    """Every route with its full path, in source order. A route without a path (an index or a layout route) has the
    path of its parent; a path that is not a literal is written `<unknown:EXPR>` and makes the route partial."""
    by = {r.index: r for r in res.routes}
    cache: dict[int, tuple[str, tuple[int, ...], tuple[str, ...], tuple[tuple[str, int], ...], tuple[str, ...]]] = {}

    def full(r: RouteDecl) -> tuple[str, tuple[int, ...], tuple[str, ...], tuple[tuple[str, int], ...], tuple[str, ...]]:
        if r.index in cache:
            return cache[r.index]
        if r.parent is None:
            base, chain, tags, lazy, unknown = "", (), (), (), ()
        else:
            base, chain, tags, lazy, unknown = full(by[r.parent])
        unk = unknown
        if r.path is not None and r.parent is None and r.path and not r.path.startswith("/"):
            # a relative path with nothing above it in this file (`<Routes>` inside a component that a route mounts
            # elsewhere): where it is mounted is not known here, and it is not claimed to be the root
            path = "/<unknown:mount>/" + r.path
            unk = unknown + ("mount",)
        elif r.path is not None:
            path = _join(base, r.path)
        elif r.path_expr is not None:
            marker = f"<unknown:{r.path_expr}>"
            path = base.rstrip("/") + "/" + marker if base else marker
            unk = unknown + (r.path_expr,)
        else:
            path = base if base else "/"
        out = (normalize_path(path), chain + (r.index,), tags + r.tags, lazy + r.lazy, unk)
        cache[r.index] = out
        return out

    result = []
    for r in res.routes:
        path, chain, tags, lazy, unknown = full(r)
        result.append(Composed(r, path, not r.children, chain, tags, lazy, bool(unknown), unknown))
    return result
