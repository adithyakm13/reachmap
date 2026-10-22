"""The Express entry-point adapter: the API routes a change reaches, with their full mounted paths.

An app is a module level `const app = express()` (`require("express")()` too), a router `express.Router(...)`, `Router(...)`
of `import { Router } from "express"` or the router `express-promise-router` makes. A route is `app.get(path, ...handlers)`
(`post`, `put`, `delete`, `patch`, `options`, `head`, `all`), `router.route(path).get(...).post(...)`, and a mount is
`app.use(prefix, router)` or `app.use(router)`, across files: the router is found through the import (`import`, `require`,
`export default`, `export const`, `module.exports = router`, `module.exports = { router }`, `exports.router`), a router mounted on a
router goes on to the app. A route is composed on the router it is declared on, following mounts up to an app: the path is the
mount prefixes from the app down, then the route's own path, `:id` is written `{id}` (the form the http edge matches), `*` last
is `{rest:path}`, `:id?` is two routes. The evidence of a route is the line that declares it (the `.get(` line; for a `.route(path)`
chain also the `.route(` line, where the path is) and then each `use` line from the router outwards.

A router that no mount carries up to an app has no route (`router_unmounted`, only when a change reaches a handler on it). A
path that is not a literal or a constant is kept with the unknown part marked and `route_path_partial` is recorded when a change
reaches it. What cannot be read is said and blocks: a mount of something that may be a router and cannot be resolved
(`mount_unresolved`), a router built where this does not read (a function: `routes_unread`), an app handed to a function that
adds routes (`routes_unread`), a file that does not lex (`parse_degraded`), a search that was cut. Limits: docs/briefs/M16-express.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from reachmap.adapters import Reached, Route, normalize_path
from reachmap.adapters.base import record_quoted
from reachmap.adapters.express import common
from reachmap.adapters.js.lexer import unquote
from reachmap.adapters.express.source import Src, Str, build_consts, path_pieces
from reachmap.model import Evidence

HTTP = frozenset({"get", "post", "put", "delete", "patch", "options", "head"})
CHAINED = HTTP | {"all"}
FACTORIES = {"express": ("app", "router"), "express-promise-router": (None, "router")}
PATTERNS = ["express"]
GLOBALS = frozenset({
    "console", "JSON", "Object", "Promise", "Reflect", "Array", "Math", "process", "module", "global", "window", "Number", "String", "Symbol", "Error", "Map", "Set",
    "setTimeout", "setInterval", "setImmediate", "queueMicrotask", "require", "exports", "Buffer", "URL",
})

Ref = tuple[str, str]  # (file, variable of a router or an app)


@dataclass(slots=True)
class Recv:
    path: str
    var: str
    kind: str  # app | router
    line: int


@dataclass(slots=True)
class Call:
    name: str
    line: int  # the line of the method's name
    open: int
    args: list[tuple[int, int]]
    end: int = 0  # the line of the closing parenthesis
    refs: frozenset[str] = frozenset()  # what the arguments spell
    members: dict[str, frozenset[str] | None] = field(default_factory=dict)


@dataclass(slots=True)
class Stmt:
    """A chain of calls on a name at module level: `app.get(...)`, `router.route(...).get(...).post(...)`, `app.use(...)`."""

    path: str
    recv: str
    calls: list[Call]
    first: int
    last: int
    start: int
    end: int
    refs: frozenset[str]
    kind: str  # route | chain | use | listen | other
    mount: bool = False  # a `use` that mounts at least one router (set when it is linked)
    members: dict[str, frozenset[str] | None] = field(default_factory=dict)  # for each name the statement spells: the members it uses (`ctrl.list`), None when it is also used as a whole

    @property
    def key(self) -> tuple[str, int]:
        return (self.path, self.start)


@dataclass(slots=True)
class Facts:
    path: str
    src: Src
    recvs: dict[str, Recv] = field(default_factory=dict)
    stmts: list[Stmt] = field(default_factory=list)
    strays: list[int] = field(default_factory=list)  # lines of a router or app built where this does not read
    passed: list[tuple[str, int, int]] = field(default_factory=list)  # (the receiver, the token of the callee, the line) of a receiver handed to a call
    ctors: list[int] = field(default_factory=list)  # tokens of every constructor call
    consts: dict[str, tuple[int, int] | None] = field(default_factory=dict)
    other_refs: frozenset[str] = frozenset()
    import_lines: dict[int, frozenset[str]] = field(default_factory=dict)  # the lines of an import statement to the names it binds
    wiring: frozenset[int] = frozenset()


@dataclass(frozen=True, slots=True)
class _Link:
    stmt: Stmt
    line: int
    parent: Ref
    child: Ref
    prefix: Str
    partial: bool


@dataclass(frozen=True, slots=True)
class _Option:
    leading: Str
    evidence: tuple[tuple[str, int], ...]  # the mount lines from the router outwards
    stmts: tuple[tuple[str, int], ...]  # the mount statements (file, first line)
    app: bool
    top: Ref
    refs: tuple[Ref, ...]
    partial: bool


@dataclass(slots=True)
class _Composed:
    stmt: Stmt
    method: str
    router: Ref
    path: Str
    option: _Option
    route: Route
    call: int  # the index of the method's call in the statement
    spans: tuple[tuple[int, int], ...]  # the lines that belong to the route: its statement


def ctor_at(src: Src, i: int, express: frozenset[str], routers: frozenset[str]) -> tuple[str, int] | None:
    """The constructor call that starts at token i: (`app` or `router`, the index of the call's `(`), else None."""
    j = i
    if src.w(j) == "new":
        j += 1
    if src.w(j) is None or src.p(j - 1) in (".", "?."):
        return None
    word = src.t[j].value
    if word in express and word not in routers:
        if src.p(j + 1) == "(":
            return "app", j + 1
        if src.p(j + 1) == "." and src.w(j + 2) == "Router" and src.p(j + 3) == "(":
            return "router", j + 3
    if word in routers and src.p(j + 1) == "(":
        return "router", j + 1
    if word == "require" and src.p(j + 1) == "(" and src.t[j + 2].kind == "string" and src.p(j + 3) == ")":
        spec = src.t[j + 2].value[1:-1]
        kinds = FACTORIES.get(spec)
        if kinds is not None:
            if src.p(j + 4) == "(" and kinds[0] is not None:
                return "app", j + 4
            if src.p(j + 4) == "(" and kinds[1] is not None and kinds[0] is None:
                return "router", j + 4
            if src.p(j + 4) == "." and src.w(j + 5) == "Router" and src.p(j + 6) == "(":
                return "router", j + 6
    return None


def constructors(src: Src, upstream: Any = None) -> tuple[frozenset[str], frozenset[str]]:
    """The local names of what `express` is bound to, and of the router factories. `upstream(spec, imported)` says what an import of a file of the
    repository that only re-exports the package really is (`export { Router } from "express"`)."""
    express: set[str] = set()
    routers: set[str] = set()
    for local, b in src.bindings().items():
        spec, imported = upstream(b.spec, b.imported) if upstream is not None and b.spec.startswith(".") else (b.spec, b.imported)
        if spec == "express" and imported in ("default", "*", "*cjs"):
            express.add(local)
        elif spec == "express" and imported == "Router":
            routers.add(local)
        elif spec == "express-promise-router" and imported in ("default", "*cjs"):
            routers.add(local)
    return frozenset(express), frozenset(routers)


def extract(path: str, parsed: Any, upstream: Any = None) -> Facts:
    """What one file declares: its apps and routers, its route and mount statements, and where it builds a router that this
    does not read. Nothing is resolved here but what `upstream` says about the framework's own names."""
    src = Src(parsed, path)
    facts = Facts(path, src)
    facts.consts = build_consts(src)
    express, routers = constructors(src, upstream)
    accounted: set[int] = set()
    for d in parsed.declarations:
        if d.kind != "variable" or d.init_tok < 0:
            continue
        got = ctor_at(src, d.init_tok, express, routers)
        if got is not None and src.module_scope(d.init_tok):
            kind, op = got
            if src.match(op) >= src.strip(d.init_tok, d.end_tok)[1]:
                accounted.add(d.init_tok)
                facts.recvs[d.name] = Recv(path, d.name, kind, d.start)
    for i in range(src.n):
        if src.w(i - 1) == "new":
            continue  # `new Router()`: the `new` token reads the whole constructor
        got = ctor_at(src, i, express, routers)
        if got is not None:
            facts.ctors.append(i)
            if i not in accounted:
                facts.strays.append(src.line(i))
    names = set(facts.recvs) | set(src.bindings())
    claimed: set[int] = set()
    for i in range(src.n):
        if i in claimed or src.t[i].kind != "ident" or src.p(i - 1) in (".", "?.") or src.p(i + 1) != "." or src.w(i + 2) is None or src.p(i + 3) != "(":
            continue
        if src.t[i].value not in names or not src.module_scope(i) or src.in_declaration(i):
            continue
        calls: list[Call] = []
        j = i + 1
        last = i
        while src.p(j) == "." and src.w(j + 1) is not None and src.p(j + 2) == "(":
            close = src.match(j + 2)
            calls.append(Call(src.t[j + 1].value, src.line(j + 1), j + 2, src.args(j + 2), src.line(close), src.refs(j + 2, close), members_of(src, j + 2, close)))
            last = close
            j = close + 1
        first = calls[0]
        if first.name == "route" and any(c.name in CHAINED for c in calls[1:]):
            kind = "chain"
        elif first.name in CHAINED and not (first.name == "get" and len(first.args) < 2):
            kind = "route"
        elif first.name == "use":
            kind = "use"
        elif first.name == "listen":
            kind = "listen"
        else:
            kind = "other"
        stmt = Stmt(path, src.t[i].value, calls, i, last, src.line(i), src.line(last), src.refs(i, last), kind, members=members_of(src, i, last))
        facts.stmts.append(stmt)
        claimed.update(range(i, last + 1))
    # what the rest of the module level mentions: statements that run when the file loads and are not routes
    imports: dict[int, set[str]] = {}
    for rec in parsed.imports:
        if rec.kind == "import" and rec.bindings:
            for n in range(rec.start_line, rec.line + 1):
                imports.setdefault(n, set()).update(b.local for b in rec.bindings)
        elif rec.kind in ("import", "reexport", "export_star"):
            for n in range(rec.start_line, rec.line + 1):
                imports.setdefault(n, set())
    facts.import_lines = {n: frozenset(v) for n, v in imports.items()}
    wiring: set[int] = set()
    for i in range(src.n):
        if i in claimed or not src.module_scope(i) or src.in_declaration(i):
            continue
        starts = (src.w(i) == "export") or (src.w(i) in ("module", "exports") and src.p(i + 1) == "." and src.p(i - 1) not in (".", "?."))
        if starts:
            end = src.stmt_end(i)
            wiring.update(range(i, end + 1))
    facts.wiring = frozenset(wiring)
    other: set[str] = set()
    for i in range(src.n):
        t = src.t[i]
        if t.kind != "ident" or i in claimed or i in wiring or t.line in facts.import_lines or not src.module_scope(i) or src.in_declaration(i):
            continue
        if src.p(i - 1) in (".", "?."):
            continue
        other.add(t.value)
    facts.other_refs = frozenset(other)
    # a name that may be a receiver handed to a call as an argument: the callee may add routes to it (said when the name does resolve to one)
    for i in range(src.n):
        if src.t[i].kind != "ident" or src.t[i].value not in names or src.p(i - 1) in (".", "?."):
            continue
        callee = _callee_of_argument(src, i)
        if callee is not None and src.module_scope(callee):
            facts.passed.append((src.t[i].value, callee, src.line(i)))
    return facts


def callee_root(src: Src, c: int) -> tuple[str, Any]:
    """Where a callee that ends at token c starts: ("name", the root identifier), ("require", the specifier) for `require("m")` and `require("m").f`, or
    ("unknown", None) for a call result, an index or anything else (`routes.v1.mount` is rooted at `routes`)."""
    k = c
    while True:
        if src.w(k) is not None:
            if src.p(k - 1) in (".", "?."):
                k -= 2
                continue
            return "name", src.t[k].value
        if src.p(k) == ")":
            op = src._open_of(k)
            if src.w(op - 1) == "require" and src.t[op + 1].kind == "string" and src.p(op - 2) not in (".", "?."):
                return "require", unquote(src.t[op + 1].value)
        return "unknown", None


def members_of(src: Src, a: int, b: int) -> dict[str, frozenset[str] | None]:
    """For each name tokens a..b spell (not after a `.`): the members used through it (`ctrl.list`, `ctrl.get`), or None when it is
    also used any other way (a call, a value, `ctrl["x"]`): then every member counts."""
    found: dict[str, set[str] | None] = {}
    for k in range(a, min(b, src.n - 1) + 1):
        if src.t[k].kind != "ident" or src.p(k - 1) in (".", "?."):
            continue
        name = src.t[k].value
        if src.p(k + 1) in (".", "?.") and src.w(k + 2) is not None:
            cur = found.setdefault(name, set())
            if cur is not None:
                cur.add(src.t[k + 2].value)
        else:
            found[name] = None
    return {n: (frozenset(v) if v is not None else None) for n, v in found.items()}


def _callee_of_argument(src: Src, i: int) -> int | None:
    """If the identifier at i is an argument of a call, the token index of the callee's last token (an identifier, or the `)` of
    `require("m")` in `require("m")(app)`); None when it is not an argument or the call is a method of a router or an app."""
    k = i - 1
    if src.p(k) not in ("(", ","):
        return None
    depth = 0
    while k >= 0:
        if src.is_close(k):
            depth += 1
        elif src.is_open(k):
            if depth == 0:
                break
            depth -= 1
        k -= 1
    if k < 1 or src.p(k) != "(":
        return None
    c = k - 1
    if src.w(c) is not None:
        if src.p(c - 1) in (".", "?.") and src.w(c) in CHAINED | {"use", "route", "listen"}:
            return None  # a call that mounts or routes: the statements are read
        return c
    if src.p(c) == ")":
        return c
    return None


class _Table(common.TableBase):
    """The apps, routers, routes and mounts of one repository at one commit, and the routes composed from them."""

    def __init__(self, adapter: "ExpressAdapter", sha: str) -> None:
        super().__init__(adapter, sha)
        self.mounts_of: dict[Ref, list[_Link]] = {}
        self._up: dict[Ref, list[_Option]] = {}
        self._composed: list[_Composed] | None = None
        self._registers: dict[str, bool] = {}
        self.entry_files: set[str] = set()
        self._build()

    def extract(self, path: str, parsed: Any) -> Facts:
        return extract(path, parsed, lambda spec, imported: self.upstream(path, spec, imported))

    def _build(self) -> None:
        self.candidates |= self.adapter.grep(self, PATTERNS)
        for path in sorted(self.candidates):
            self.facts_of(path)
        # a router or an app is imported under any name by the files that add routes to it or mount it: the importers of each file that declares one
        # are candidates too, and so are the importers of a file that only re-exports it (a barrel), to a fixed point (the importer search says when it
        # could not be complete)
        queue = [f.path for f in self.facts.values() if f is not None and f.recvs]
        searched: set[str] = set()
        while queue:
            batch = sorted(set(queue) - searched)
            queue = []
            if not batch:
                break
            self.language.prefetch_importers(batch, self.sha)
            for path in batch:
                searched.add(path)
                res = self.language.importers(path, self.sha)
                if not res.complete:
                    self.incomplete(f"the importers of {path} were not established completely: " + "; ".join(res.reasons), path)
                for e in res.edges:
                    if self.adapter.is_source(e.importer_path) and self.adapter._citable(e.importer_path):
                        self.candidates.add(e.importer_path)
                        if e.kind in ("reexport", "export_star"):
                            queue.append(e.importer_path)
        for path in sorted(self.candidates):
            self.facts_of(path)
        self._link()
        self.entry_files = {
            f.path for f in self.facts.values()
            if f is not None and (f.recvs or any(s.kind in ("route", "chain", "use") and self.resolve_name(f.path, s.recv) for s in f.stmts))
        }

    # ---- resolving names to routers

    def export_ref(self, path: str, name: str, depth: int = 0) -> list[Ref]:
        facts = self.facts_of(path)
        if facts is None or depth > self.DEPTH:
            return []
        wanted = ("default", "*cjs") if name in ("default", "*cjs") else (name,)
        out: list[Ref] = []
        for e in facts.src.exports():
            if e.name not in wanted:
                continue
            if e.source is not None:
                target = self.module_file(path, e.source)
                if target is not None:
                    out += self.export_ref(target, e.source_name or e.name, depth + 1)
            elif e.local is not None:
                out += self.resolve_name(path, e.local, depth + 1)
        if not out and name not in ("default", "*cjs"):  # `export * from "./x"`
            for spec in facts.src.parsed.export_stars:
                target = self.module_file(path, spec)
                if target is not None:
                    out += self.export_ref(target, name, depth + 1)
        return out

    def resolve_name(self, path: str, name: str, depth: int = 0) -> list[Ref]:
        facts = self.facts_of(path)
        if facts is None or depth > self.DEPTH:
            return []
        if name in facts.recvs:
            return [(path, name)]
        b = facts.src.bindings().get(name)
        if b is None:
            return []
        target = self.module_file(path, b.spec)
        if target is None or b.imported == "*":
            return []
        return self.export_ref(target, b.imported, depth + 1)

    def resolve(self, path: str, a: int, b: int, depth: int = 0) -> list[Ref]:
        """The routers or apps an expression names: a name, an attribute of a namespace (`users.router`), a `require()` call
        with or without an attribute."""
        facts = self.facts_of(path)
        if facts is None:
            return []
        src = facts.src
        a, b = src.strip(a, b)
        if a > b:
            return []
        if a == b and src.w(a) is not None:
            return self.resolve_name(path, src.t[a].value, depth)
        if src.w(a) == "require" and src.p(a + 1) == "(" and src.t[a + 2].kind == "string" and src.p(a + 3) == ")":
            target = self.module_file(path, unquote(src.t[a + 2].value))
            if target is None:
                return []
            if a + 3 == b:
                return self.export_ref(target, "*cjs", depth + 1)
            if src.p(a + 4) == "." and src.w(a + 5) is not None and a + 5 == b:
                return self.export_ref(target, src.t[a + 5].value, depth + 1)
            return []
        if src.w(a) is not None and src.p(a + 1) == "." and src.w(a + 2) is not None and a + 2 == b:
            bind = facts.src.bindings().get(src.t[a].value)
            if bind is None:
                return []
            target = self.module_file(path, bind.spec)
            if target is None:
                return []
            if bind.imported in ("*", "*cjs", "default"):
                return self.export_ref(target, src.t[b].value, depth + 1)
        return []

    # ---- paths

    def alternatives(self, path: str, a: int, b: int) -> list[tuple[Str, bool]]:
        """The paths a route or a mount argument stands for: a string, a template, a sum, a constant, a list of them; a regular
        expression or anything else is an unknown segment. Each is converted to the `{param}` form."""
        facts = self.facts_of(path)
        assert facts is not None
        src = facts.src
        a, b = src.strip(a, b)
        if src.p(a) == "[" and src.match(a) == b:
            out: list[tuple[Str, bool]] = []
            for x, y in src.args(a):
                out += self.alternatives(path, x, y)
            return out or [(src.unknown(a, b) + Str(), True)]
        if a == b and src.t[a].kind == "regex":
            return [(Str("/" + src.unknown(a, b).value, src.unknown(a, b).unknown), True)]
        return path_pieces(self.eval(path, a, b))

    # ---- what may be a router

    def registers_routes(self, path: str, rng: tuple[int, int] | None = None) -> bool:
        """Whether the file (or the token range) calls `.get(`, `.post(`, ..., `.use(`, `.route(` with arguments on something: it
        may add routes to a router or an app it is handed."""
        key = f"{path}:{rng}"
        if key in self._registers:
            return self._registers[key]
        facts = self.facts_of(path)
        out = False
        if facts is not None:
            src = facts.src
            lo, hi = rng if rng is not None else (0, src.n - 1)
            for i in range(lo, hi + 1):
                if src.p(i) == "." and src.w(i + 1) in (CHAINED | {"use", "route"}) and src.p(i + 2) == "(":
                    n = len(src.args(i + 2))
                    if src.w(i + 1) in ("use", "route") or n >= 2:
                        out = True
                        break
        self._registers[key] = out
        return out

    def _decl_range(self, facts: Facts, name: str) -> tuple[int, int] | None:
        for d in facts.src.parsed.declarations:
            if d.name == name:
                return d.start_tok, d.end_tok
        return None

    def may_be_router(self, path: str, a: int, b: int) -> bool:
        """Whether an argument of `use` that did not resolve to a router could still be one: a call or a name that leads to a repository
        file or declaration that builds or declares routers. Middleware from a package, a function written in place, and code that
        builds no router are not."""
        facts = self.facts_of(path)
        if facts is None:
            return True
        src = facts.src
        a, b = src.strip(a, b)
        if src.w(a) is None or src.w(a) in ("function", "async") or any(src.p(j) == "=>" for j in range(a, b + 1)):
            return False  # a function written in place, or something that is not a name or a call
        if src.w(a) == "require" and src.p(a + 1) == "(" and src.t[a + 2].kind == "string":
            target = self.module_file(path, unquote(src.t[a + 2].value))
            other = self.facts_of(target) if target is not None else None
            return target is not None and (other is None or bool(other.ctors))
        root = src.t[a].value
        bind = facts.src.bindings().get(root)
        if bind is not None:
            target = self.module_file(path, bind.spec)
            if target is None:
                return False  # a package: middleware
            other = self.facts_of(target)
            return other is None or bool(other.ctors)  # a file that could not be read may be a router; one that builds routers somewhere may be one of them
        rng = self._decl_range(facts, root)
        if rng is not None:
            return any(i in range(rng[0], rng[1] + 1) for i in facts.ctors)
        return False

    # ---- the table

    def _link(self) -> None:
        loaded = [f for f in self.facts.values() if f is not None]
        for f in loaded:
            for line in f.strays:
                self.unresolved = True
                self.incomplete(f"{f.path}:{line} builds a router or an app where routes are not read", f.path)
                record_quoted(self.target, "routes_unread", f"{f.path}:{line}",
                              "a router or an app built outside module level (a function, a class) is not read, so the routes it serves are missing", f.src.parsed.line_text(line).strip(), once=True)
            for name, callee, line in f.passed:
                if not self.resolve_name(f.path, name):
                    continue  # not an app or a router
                verdict = self._registrar(f, callee)
                if verdict == "no":
                    continue
                self.unresolved = True
                self.incomplete(f"{f.path}:{line} hands {name} to something that may add routes to it", f.path)
                fixed = ("the app or router is handed to a function or a method that adds routes to it; those routes are not read" if verdict == "yes"
                         else "the app or router is handed to something this cannot resolve, which may add routes to it; those routes are not read")
                record_quoted(self.target, "routes_unread", f"{f.path}:{line}", fixed, f.src.parsed.line_text(line).strip(), once=True)
        for f in loaded:
            for s in f.stmts:
                if s.kind != "use":
                    continue
                parents = self.resolve_name(f.path, s.recv)
                if not parents:
                    continue
                self._link_use(f, s, parents)

    def _registrar(self, f: Facts, callee: int) -> str:
        """Whether what a receiver is handed to adds routes to it: `yes` (the callee is a function, a method of a module or of an object that calls `.get(`, `.use(` or
        `.route(` with arguments), `no` (a package, a global, code that does not), or `unknown` (a root this cannot resolve: undecidable means affected)."""
        src = f.src
        kind, root = callee_root(src, callee)
        if kind == "unknown":
            return "unknown"
        if kind == "require":
            target = self.module_file(f.path, root)
            return "no" if target is None else ("yes" if self.registers_routes(target) else "no")
        if root in GLOBALS:
            return "no"
        b = src.bindings().get(root)
        if b is not None:
            target = self.module_file(f.path, b.spec)
            return "no" if target is None else ("yes" if self.registers_routes(target) else "no")
        for d in src.parsed.declarations:
            if d.name != root:
                continue
            if d.kind == "variable" and d.init_tok >= 0 and not (src.p(d.init_tok) == "{" or src.w(d.init_tok) in ("function", "async", "class") or any(src.p(k) == "=>" for k in range(d.init_tok, d.end_tok + 1))):
                return "unknown"  # the result of a call, an alias: what it is cannot be read here
            return "yes" if self.registers_routes(f.path, (d.start_tok, d.end_tok)) else "no"
        return "unknown"

    def _looks_like_path(self, f: Facts, a: int, b: int) -> bool:
        """Whether the first argument of `use` is a path and not middleware: a string, a template, a regular expression, a list, a sum with
        a string in it, or a name that is a constant string. (A call such as `auth("admin")` holds a string, but not at its own level.)"""
        src = f.src
        a, b = src.strip(a, b)
        if src.p(a) == "[" and src.match(a) == b:
            return True
        j = a
        while j <= b:
            if src.t[j].kind in ("string", "regex") or (src.t[j].kind == "template"):
                return True
            if src.is_open(j):
                j = src.match(j) + 1
                continue
            j += 1
        if a == b and src.w(a) is not None:
            return self.const(f.path, src, src.t[a].value, 0) is not None
        parts = src._split_plus(a, b)
        return len(parts) > 1 and all(x == y and src.w(x) is not None and self.const(f.path, src, src.t[x].value, 0) is not None for x, y in parts)

    def export_kind(self, path: str, name: str, depth: int = 0) -> str | None:
        """What a file exports under `name`: `function` (a function, a class, a constant that is a function), `other`, or None when it cannot be told."""
        facts = self.facts_of(path)
        if facts is None or depth > self.DEPTH:
            return None
        wanted = ("default", "*cjs") if name in ("default", "*cjs") else (name,)
        for e in facts.src.exports():
            if e.name not in wanted:
                continue
            if e.source is not None:
                target = self.module_file(path, e.source)
                return self.export_kind(target, e.source_name or name, depth + 1) if target is not None else None
            if e.value is not None:
                return "function" if any(facts.src.p(k) == "=>" or facts.src.w(k) == "function" for k in range(e.value[0], e.value[1] + 1)) else "other"
            local = e.local
            if local is not None:
                for d in facts.src.parsed.declarations:
                    if d.name == local:
                        if d.kind in ("function", "class"):
                            return "function"
                        if d.kind == "variable" and d.init_tok >= 0 and (facts.src.w(d.init_tok) in ("function", "async") or any(facts.src.p(k) == "=>" for k in range(d.init_tok, d.end_tok + 1))):
                            return "function"
                        return "other"
        return None

    def _first_argument(self, f: Facts, a: int, b: int) -> str:
        """What the first argument of `use` is when more follow: `router`, `path` (a literal, a template, a list, a constant), `middleware` (a function written
        in place, anything from a package, a function of the repository, a call), or `unknown` (a member of an object, a name that is not a function and not a
        constant this can read: a prefix that is not visible, which is an unknown segment and not nothing)."""
        src = f.src
        if self.resolve(f.path, a, b):
            return "router"
        if self._looks_like_path(f, a, b):
            return "path"
        x, y = src.strip(a, b)
        if src.w(x) is None or src.w(x) in ("function", "async", "class") or any(src.p(j) == "=>" for j in range(x, y + 1)):
            return "middleware"
        root = src.t[x].value
        b0 = src.bindings().get(root)
        if b0 is not None and self.module_file(f.path, b0.spec) is None:
            return "middleware"  # from a package
        if src.p(y) == ")":
            return "middleware"  # a call: `auth()`, `express.json()`; a prefix made by a call is read as middleware (a limit)
        if x == y:
            if b0 is not None:
                kind = self.export_kind(self.module_file(f.path, b0.spec) or "", b0.imported)
                return "middleware" if kind == "function" else "unknown"
            for d in src.parsed.declarations:
                if d.name == root and d.kind in ("function", "class"):
                    return "middleware"
        return "unknown"

    def _link_use(self, f: Facts, s: Stmt, parents: list[Ref]) -> None:
        call = s.calls[0]
        args = list(call.args)
        prefixes: list[tuple[Str, bool]] = [(Str(), False)]
        if args:
            a, b = args[0]
            kind = self._first_argument(f, a, b) if len(args) > 1 or self._looks_like_path(f, a, b) else "child"
            if kind == "path":
                prefixes = self.alternatives(f.path, a, b)
                args = args[1:]
            elif kind == "unknown":
                x, y = f.src.strip(a, b)
                value = f.src.unknown(x, y)
                prefixes = [(Str("/" + value.value, value.unknown), True)]
                args = args[1:]
        children: list[tuple[int, int]] = []
        for a, b in args:
            if f.src.p(a) == "[" and f.src.match(a) == b:
                children += f.src.args(a)
            else:
                children.append((a, b))
        for a, b in children:
            refs = self.resolve(f.path, a, b)
            if not refs:
                if self.may_be_router(f.path, a, b):
                    s.mount = True  # a mount of something this could not read: a change to what it names reaches the routes below it, which are not known (said below)
                    self.unresolved = True
                    self.incomplete(f"{f.path}:{call.line} mounts something that could not be resolved to a router", f.path)
                    record_quoted(self.target, "mount_unresolved", f"{f.path}:{call.line}",
                                  "what it mounts could not be resolved to a router, so a router that looks unmounted may be mounted here", f.src.parsed.line_text(call.line).strip(), once=True)
                continue
            s.mount = True
            for parent in parents:
                for child in refs:
                    for prefix, partial in prefixes:
                        self.mounts_of.setdefault(child, []).append(_Link(s, call.line, parent, child, prefix, partial))

    def recv(self, ref: Ref) -> Recv:
        facts = self.facts[ref[0]]
        assert facts is not None
        return facts.recvs[ref[1]]

    def up(self, ref: Ref, stack: tuple[Ref, ...] = ()) -> list[_Option]:
        """Every way up from a router or an app: through each mount that carries it, to an app or to a router that nothing mounts."""
        if ref in self._up:
            return self._up[ref]
        out: list[_Option] = []
        for link in self.mounts_of.get(ref, ()):
            if link.parent in stack or link.parent == ref:
                continue  # a cycle of mounts
            for o in self.up(link.parent, stack + (ref,)):
                out.append(_Option(o.leading + link.prefix, ((link.stmt.path, link.line),) + o.evidence, (link.stmt.key,) + o.stmts, o.app, o.top, (ref,) + o.refs, o.partial or link.partial))
        if not out:
            out.append(_Option(Str(), (), (), self.recv(ref).kind == "app", ref, (ref,), False))
        if not stack:
            self._up[ref] = out
        return out

    def _handler(self, f: Facts, args: list[tuple[int, int]]) -> tuple[str, str]:
        """(the handler's name, the file that defines it): the last argument, flattened one level for a list."""
        src = f.src
        if not args:
            return "<none>", f.path
        a, b = args[-1]
        a, b = src.strip(a, b)
        if src.p(a) == "[" and src.match(a) == b:
            inner = src.args(a)
            if inner:
                a, b = src.strip(*inner[-1])
        chain = [src.t[k].value for k in range(a, b + 1) if src.t[k].kind == "ident"]
        simple = all(src.t[k].kind == "ident" or src.p(k) in (".", "?.") for k in range(a, b + 1))
        if not simple:
            if any(src.p(k) == "=>" for k in range(a, b + 1)) or src.w(a) in ("function", "async"):
                return "<anonymous>", f.path
            name = chain[0] + "(...)" if chain else "<anonymous>"
            return name, f.path
        name = chain[-1] if chain else "<anonymous>"
        bind = src.bindings().get(chain[0]) if chain else None
        target = self.module_file(f.path, bind.spec) if bind is not None else None
        return name, target or f.path

    def stmt_routes(self, f: Facts, s: Stmt) -> list[tuple[str, int, int, list[tuple[Str, bool]], list[int], list[tuple[int, int]]]]:
        """The routes one statement declares: (method, the index of its call, the line of the call, the paths, extra lines of evidence,
        the handler arguments)."""
        out = []
        if s.kind == "route":
            for n, c in enumerate(s.calls):  # `r.get(...).post(...)`: every call that names a path and a handler is a route
                if c.name in CHAINED and len(c.args) >= 2:
                    method = "ANY" if c.name == "all" else c.name.upper()
                    a, b = c.args[0]
                    out.append((method, n, c.line, self.alternatives(f.path, a, b), [], c.args[1:]))
        elif s.kind == "chain":
            head = s.calls[0]
            if not head.args:
                return out
            alts = self.alternatives(f.path, *head.args[0])
            for n, c in enumerate(s.calls):
                if n > 0 and c.name in CHAINED:
                    method = "ANY" if c.name == "all" else c.name.upper()
                    out.append((method, n, c.line, alts, [head.line] if head.line != c.line else [], c.args))
        return out

    def composed(self) -> list[_Composed]:
        """Every route with every way up (`app` or not): the routes that end at an app are `Route`s."""
        if self._composed is not None:
            return self._composed
        out: list[_Composed] = []
        for f in sorted((f for f in self.facts.values() if f is not None), key=lambda x: x.path):
            for s in f.stmts:
                if s.kind not in ("route", "chain"):
                    continue
                refs = self.resolve_name(f.path, s.recv)
                if not refs:
                    continue
                for method, idx, line, alts, extra, handler_args in self.stmt_routes(f, s):
                    handler, target = self._handler(f, handler_args)
                    for ref in refs:
                        for piece, partial in alts:
                            for o in self.up(ref):
                                full = o.leading + piece
                                pairs = ((f.path, line),) + tuple((f.path, x) for x in extra) + o.evidence
                                route = self._route(f, method, full, pairs, handler, target, o.app, partial or o.partial)
                                if route is not None:
                                    out.append(_Composed(s, method, ref, full, o, route, idx, ((s.start, s.end),)))
        self._composed = out
        return out

    def _route(self, f: Facts, method: str, full: Str, pairs: tuple[tuple[str, int], ...], handler: str, target: str, app: bool, partial: bool) -> Route | None:
        text = normalize_path(full.value)
        evidence: list[Evidence] = []
        for n, (p, line) in enumerate(pairs):
            ev = self.evidence(p, line, f"route:{method} {text}:{f.path}" if n == 0 else None)
            if ev is None:
                return None
            evidence.append(ev)
        return Route(
            repo=self.adapter.repo, kind="api_route", method=method, path=text, file=f.path, handler=handler, target=target,
            module=self.adapter.module_of(f.path, self.sha), evidence=tuple(evidence), partial=partial or bool(full.unknown),
        )

    def routes(self) -> list[Route]:
        return [c.route for c in self.composed() if c.option.app]


class ExpressAdapter(common.Shared):
    """Express entry points of one repository, built on its JavaScript adapter (which reads the files, parses them, resolves
    specifiers and lists the tree). Use one adapter per run, as the language adapters are."""

    kind = "express"
    WHAT = "express() or Router()"

    def __init__(self, repo: str, language: Any, *, service_marker: str | None = None, record_to: Any = None) -> None:
        super().__init__(repo, language, service_marker=service_marker, record_to=record_to)
        self._tables: dict[str, _Table] = {}

    @classmethod
    def from_config(cls, repo: Any, language: Any, *, record_to: Any = None) -> "ExpressAdapter":
        return cls(repo.name, language, service_marker=getattr(repo, "service_marker", None), record_to=record_to)

    def table(self, sha: str) -> _Table:
        if sha not in self._tables:
            self._tables[sha] = _Table(self, sha)
        return self._tables[sha]

    # ---- what the walk asks

    def is_entry_file(self, path: str, sha: str) -> bool:
        """A file that declares an app or a router, or adds routes or mounts to one."""
        return path in self.table(sha).entry_files

    def structural_names(self, path: str, sha: str) -> frozenset[str]:
        """The names a file exports its apps and routers under: whoever imports one of them mounts it, which is no way for a change to
        reach anything else (a helper exported next to them is)."""
        facts = self.table(sha).facts_of(path)
        if facts is None:
            return frozenset()
        return frozenset(e.name for e in facts.src.exports() if e.local in facts.recvs)

    def unmounted(self, sha: str) -> list[tuple[str, str]]:
        """The routers (file, variable) that have routes and that nothing mounts up to an app."""
        table = self.table(sha)
        refs = {c.router for c in table.composed()}
        return sorted(ref for ref in refs if table.recv(ref).kind == "router" and not any(o.app for o in table.up(ref)))

    # ---- what a chain reaches

    def entry_points_reached(self, chains: Iterable[Any], sha: str | None = None) -> list[Reached]:
        """The API routes the chains reach: one `Area` per route, with the chain of Evidence from the change to the handler (the
        imports on the way, or the changed line) followed by the route's own evidence, and whether it was established completely."""
        hits: list[common.Hit] = []
        seen = list(chains)
        for chain in seen:
            table = self.table(chain.sha)
            for comp, hops in self._reached(table, chain):
                route = comp.route
                if not comp.option.app:
                    hits.append(common.Hit(route, (), True, (), chain, (), unmounted=comp.option.top))
                    continue
                evidence = self.chain_of(hops, route)
                hits.append(common.Hit(route, evidence, chain.complete and table.complete, tuple(chain.reasons) + tuple(table.reasons), chain, comp.path.unknown))
        return self.assemble(hits, any(self.table(c.sha).unresolved for c in seen))

    def _reached(self, table: _Table, chain: Any) -> list[tuple[_Composed, tuple[Evidence, ...]]]:
        facts = table.facts_of(chain.entry)
        if facts is None:
            return []
        reach = chain.reach
        locals_ = set(reach.locals)
        whole = not reach.complete or facts.src.parsed.uses_dynamic_access()
        if locals_ & facts.other_refs:
            whole = True
        namespaces = {n for n, b in facts.src.bindings().items() if b.imported == "*" and n in locals_}
        names = chain.names

        def uses(refs: frozenset[str], members: dict[str, frozenset[str] | None]) -> bool:
            for name in refs & locals_:
                mem = members.get(name)
                if names is not None and name in namespaces and mem is not None and not (mem & names):
                    continue  # `ctrl.get` of a namespace import when only `list` of the module is reached
                return True
            return False

        hit: dict[tuple[str, int], set[int] | None] = {}  # a statement to the calls of it that are reached (None: all of them)

        def add(key: tuple[str, int], calls: set[int] | None) -> None:
            if key in hit and hit[key] is None:
                return
            if calls is None or key not in hit:
                hit[key] = calls if calls is None else set(calls)
            else:
                hit[key] |= calls  # type: ignore[operator]

        for s in facts.stmts:
            if s.kind == "listen":
                continue
            if len(s.calls) > 1 and s.kind in ("chain", "route"):  # a chain of calls: the call that spells the name (all of them for the receiver and the `.route(path)` call)
                head = s.calls[0]
                if s.recv in locals_ or (s.kind == "chain" and uses(head.refs, head.members)):
                    add(s.key, None)
                else:
                    idx = {n for n, c in enumerate(s.calls) if (n > 0 or s.kind == "route") and uses(c.refs, c.members)}
                    if idx:
                        add(s.key, idx)
            elif uses(s.refs, s.members):
                if s.kind == "route" or s.mount:
                    add(s.key, None)
                else:
                    whole = True
        if chain.edge is None:
            more, other = self._direct(table, chain, facts)
            for key, calls in more.items():
                add(key, calls)
            whole = whole or other
        out: list[tuple[_Composed, tuple[Evidence, ...]]] = []
        for c in table.composed():
            here = c.stmt.path == chain.entry
            through = any(e.path == chain.entry for e in c.route.evidence)
            if not (here or through):
                continue
            mine = here and c.stmt.key in hit and (hit[c.stmt.key] is None or c.call in hit[c.stmt.key])  # type: ignore[operator]
            via = any(k in hit and hit[k] is None for k in c.option.stmts)
            if not (whole or mine or via):
                continue
            hops = chain.hops
            if chain.direct:
                call = c.stmt.calls[c.call]
                spans = [(call.line, call.end), (c.stmt.start, c.stmt.start)] if here else []
                hops = self.direct_hop(chain, c.route, spans)
                if not hops and chain.hops:
                    hops = chain.hops
            out.append((c, tuple(hops)))
        return out

    def _direct(self, table: _Table, chain: Any, facts: Facts) -> tuple[dict[tuple[str, int], set[int] | None], bool]:
        """What the changed lines of a statement outside every declaration touch: (the statements and the calls of them, whether some
        changed line could change anything the file does when it loads). A changed import line reaches the statements that spell the
        names it binds; a changed line in a chain statement reaches the call it is in (the whole chain when it is the `.route(path)`
        call or the receiver)."""
        hit: dict[tuple[str, int], set[int] | None] = {}
        other = False
        imported: set[str] = set()

        def put(stmt: Stmt, line: int) -> None:
            nonlocal other
            if stmt.kind == "listen":
                return
            if stmt.kind not in ("route", "chain") and not stmt.mount:
                other = True  # a middleware or a setting: it applies to what the file serves, which is not narrowed
                return
            if len(stmt.calls) == 1 or stmt.kind not in ("chain", "route"):
                hit[stmt.key] = None
                return
            idx = [n for n, c in enumerate(stmt.calls) if c.line <= line <= c.end]
            if not idx or (stmt.kind == "chain" and idx == [0]) or hit.get(stmt.key, set()) is None:
                hit[stmt.key] = None
            else:
                hit.setdefault(stmt.key, set()).update(idx)  # type: ignore[union-attr]

        before = self.language.parsed(chain.pre_sha, chain.before_path) if chain.pre_sha else None
        pre = self.table(chain.pre_sha).extract(chain.before_path, before) if before is not None and not before.problems else None
        for s in chain.changed:
            if not s.is_module:
                continue
            for n in s.added_lines:
                if n not in facts.src.parsed.code_lines:
                    continue  # a comment or a blank line changes nothing
                st = next((x for x in facts.stmts if x.start <= n <= x.end), None)
                if st is not None:
                    put(st, n)
                elif n in facts.import_lines:
                    imported |= facts.import_lines[n]
                elif n in facts.src.parsed.code_lines and not self._wiring_line(facts, n):
                    other = True
            for n in s.removed_lines:
                if pre is None:
                    other = True
                    continue
                if n not in pre.src.parsed.code_lines:
                    continue
                st = next((x for x in pre.stmts if x.start <= n <= x.end), None)
                if st is not None:
                    twin = next((x for x in facts.stmts if self._same(pre, st, facts, x)), None)
                    if twin is not None:
                        pre_idx = [k for k, c in enumerate(st.calls) if c.line <= n <= c.end]
                        if len(st.calls) == 1 or st.kind not in ("chain", "route") or not pre_idx or (st.kind == "chain" and pre_idx == [0]):
                            hit[twin.key] = None
                        else:  # a call of a chain: the call of the same method in the other version (gone: its route is a removed route)
                            same = [k for k, c in enumerate(twin.calls) if c.name == st.calls[pre_idx[0]].name and (k > 0 or st.kind == "route")]
                            if same and hit.get(twin.key, set()) is not None:
                                hit.setdefault(twin.key, set()).update(same)  # type: ignore[union-attr]
                    elif st.kind not in ("route", "chain") and not (st.kind == "use" and self._pre_mounts(table, pre, st)):
                        other = True  # a removed middleware, setting or other statement: undecidable means affected
                elif n in pre.import_lines:
                    imported |= pre.import_lines[n]
                elif n in pre.src.parsed.code_lines and not self._wiring_line(pre, n):
                    other = True
        if imported:
            for st in facts.stmts:
                if st.refs & imported:
                    if st.kind in ("route", "chain") or st.mount:
                        hit[st.key] = None
                    elif st.kind != "listen":
                        other = True
            if facts.other_refs & imported:
                other = True
        return hit, other

    @staticmethod
    def _wiring_line(facts: Facts, n: int) -> bool:
        return any(facts.src.line(i) == n for i in facts.wiring)

    @staticmethod
    def _same(pre: Facts, a: Stmt, facts: Facts, b: Stmt) -> bool:
        """The same statement in both versions: the same receiver, the same first call and the same first argument."""
        if (a.recv, a.calls[0].name, a.kind) != (b.recv, b.calls[0].name, b.kind):
            return False
        ta = pre.src.text(*a.calls[0].args[0]) if a.calls[0].args else ""
        tb = facts.src.text(*b.calls[0].args[0]) if b.calls[0].args else ""
        return ta == tb

    @staticmethod
    def _pre_mounts(table: _Table, pre: Facts, st: Stmt) -> bool:
        """Whether a removed `use` statement mounted a router (its routes are then removed routes, which `routes_changed` says)."""
        call = st.calls[0]
        for a, b in call.args:
            names = pre.src.refs(a, b)
            if names & (set(pre.src.bindings()) | set(pre.recvs)):
                return True
        return False


__all__ = ["ExpressAdapter"]
