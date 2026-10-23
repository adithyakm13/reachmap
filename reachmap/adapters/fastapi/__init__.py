"""The FastAPI entry-point adapter: the API routes a change reaches, with their full mounted paths.

A handler is a module-level function decorated `@<router>.<method>(path, ...)` (get, post, put, patch, delete,
options, head, trace, `api_route` with `methods=`, `websocket`), where `<router>` is a module-level name bound to
`APIRouter(...)` (with its own `prefix=`) or `FastAPI(...)`. Mounts are `<parent>.include_router(<child>, prefix=...)`
and `<parent>.mount(path, <child app>)` calls at module level; the child is resolved by import (aliased imports, module
attributes such as `routers.items.router`, names re-exported by another module). A route is composed on the router it
is declared on, following mounts up to an app (`FastAPI()`): the path is the app's mounts, the routers' own prefixes and
the decorator's path, joined in the order the framework joins them; several mount paths give several routes. The
evidence of a route is the decorator, then (from the router outwards) the constructor line of a router that has a prefix
and each mount line.

A router that no mount carries up to an app has no route: the handlers on it claim nothing and a non-blocking
`router_unmounted` names it (`<path>:<router variable>`, the one nothing mounts). A mount in a comment is not a mount
(this reads the syntax tree, not text). A prefix that is a variable, an f-string or a sum of strings is evaluated
when everything in it is a literal or a module constant (this file's, or an imported one); a part that cannot be
evaluated is written `<unknown:EXPR>` in the path, the route is `partial` and `route_path_partial` is recorded. A mount
made anywhere but module level, or of something that cannot be resolved to a router, is a blocking `mount_unresolved`:
routers that look unmounted may be mounted there.

Files are found with `git grep` (a cut or failed search makes everything incomplete, never empty) and parsed with
`ast`; a file that does not parse claims nothing and records a blocking `parse_degraded`. That reading, the
constant evaluation, the route evidence and the routes / routes_changed flow are shared with the Django adapter
(`reachmap.adapters.python_routes`); this file keeps what is FastAPI's: routers, decorators, mounts and how a route is composed.
Limits are in docs/briefs/M7-entry-points.md.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any, Iterable

from reachmap import config
from reachmap.adapters import Reached, Route, normalize_path
from reachmap.adapters.base import citable, dedupe, direct_hop, import_lines, module_changes, record_quoted
from reachmap.adapters.python import PyAdapter
from reachmap.adapters.python_routes import DEPTH, FileFacts, Gathered, Shared, TableBase, callee, kw, literal_strings, names_in, nearest_folder, route_evidence, statements
from reachmap.model import Evidence

__all__ = ["FastApiAdapter"]

METHODS = frozenset({"get", "post", "put", "delete", "patch", "options", "head", "trace"})
CONSTRUCTORS = frozenset({"APIRouter", "FastAPI"})
CANDIDATE_PATTERNS = ["APIRouter", "FastAPI", "include_router", ".mount("]
MOUNT_CALLS = frozenset({"include_router", "mount"})

Ref = tuple[str, str]  # (file, variable of a router or an app)


@dataclass(frozen=True, slots=True)
class _Str:
    """A string made of known text and unknown parts (`unknown`: the expression, the file and the line)."""

    value: str = ""
    unknown: tuple[tuple[str, str, int], ...] = ()

    def __add__(self, other: "_Str") -> "_Str":
        return _Str(self.value + other.value, self.unknown + other.unknown)

    def __bool__(self) -> bool:
        return bool(self.value)


@dataclass(slots=True)
class _Router:
    path: str
    var: str
    kind: str  # APIRouter | FastAPI
    prefix: ast.expr | None
    line: int


@dataclass(slots=True)
class _Deco:
    """One route decorator: the router expression, the methods, the path expression, the decorator's line."""

    router: ast.expr
    methods: list[str]
    path: ast.expr | None
    line: int
    unknown_methods: str | None = None


@dataclass(slots=True)
class _Handler:
    path: str
    name: str
    start: int
    end: int
    decos: list[_Deco]


@dataclass(slots=True)
class _Mount:
    path: str
    line: int
    kind: str  # include_router | mount
    parent: ast.expr
    child: ast.expr | None
    prefix: ast.expr | None


@dataclass(frozen=True, slots=True)
class _Link:
    """A mount that resolved: `child` is carried by `parent`."""

    mount: _Mount
    parent: Ref
    child: Ref


@dataclass(slots=True)
class _Facts(FileFacts):
    routers: dict[str, _Router] = field(default_factory=dict)
    handlers: list[_Handler] = field(default_factory=list)
    mounts: list[_Mount] = field(default_factory=list)
    strays: list[int] = field(default_factory=list)  # lines of mounts that are not module level statements


@dataclass(frozen=True, slots=True)
class _Option:
    """One way up from a router: `leading` is what the ancestors put before the router's own prefix, `evidence`
    the (file, line) pairs from the router outwards, `app` whether the way ends at an app, `top` the router or app
    it ends at, `refs` the routers passed (this one first)."""

    leading: _Str
    evidence: tuple[tuple[str, int], ...]
    app: bool
    top: Ref
    refs: tuple[Ref, ...]


@dataclass(slots=True)
class _Composed:
    handler: _Handler
    deco: _Deco
    method: str
    router: Ref
    path: _Str
    option: _Option
    route: Route


class _Table(TableBase):
    """The routers, handlers and mounts of one repository at one commit, and the routes composed from them."""

    UNPARSED = "routes and mounts in it were not read"
    DEGRADED = "no route or mount is claimed from it, so routes may be missing"
    PATTERNS = CANDIDATE_PATTERNS
    Str = _Str

    def __init__(self, adapter: "FastApiAdapter", sha: str) -> None:
        super().__init__(adapter, sha)
        self.mounts_of: dict[Ref, list[_Link]] = {}
        self._up: dict[Ref, list[_Option]] = {}
        self._composed: list[_Composed] | None = None
        self._build()

    # ---- reading a file

    def read_facts(self, path: str, parsed: Any) -> _Facts:
        facts = _Facts(path, parsed)
        facts.bind_imports()
        counts: dict[str, int] = {}
        for d in parsed.declarations:
            node = d.node
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
                if len(d.names) != 1 or value is None:
                    continue
                name = d.names[0]
                counts[name] = counts.get(name, 0) + 1
                facts.consts[name] = value if counts[name] == 1 else None
                if isinstance(value, ast.Call) and callee(value.func) in CONSTRUCTORS:
                    facts.routers[name] = _Router(path, name, callee(value.func) or "", kw(value, "prefix"), node.lineno)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                decos = [x for x in (self._deco(deco) for deco in node.decorator_list) if x is not None]
                if decos:
                    facts.handlers.append(_Handler(path, node.name, d.start, d.end, decos))
        seen: set[int] = set()
        for stmt in statements(parsed.tree.body):
            call = stmt.value if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call) else None
            if call is None or not isinstance(call.func, ast.Attribute) or call.func.attr not in MOUNT_CALLS:
                continue
            seen.add(id(call))
            if call.func.attr == "include_router":
                child = call.args[0] if call.args else kw(call, "router")
                facts.mounts.append(_Mount(path, call.lineno, "include_router", call.func.value, child, kw(call, "prefix")))
            else:
                prefix = call.args[0] if call.args else kw(call, "path")
                child = call.args[1] if len(call.args) > 1 else kw(call, "app")
                facts.mounts.append(_Mount(path, call.lineno, "mount", call.func.value, child, prefix))
        for node in ast.walk(parsed.tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in MOUNT_CALLS and id(node) not in seen:
                facts.strays.append(node.lineno)
        return facts

    @staticmethod
    def _deco(deco: ast.expr) -> _Deco | None:
        if not (isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute)):
            return None
        attr = deco.func.attr
        if attr in METHODS:
            methods = [attr.upper()]
        elif attr == "websocket":
            methods = ["WEBSOCKET"]
        elif attr == "api_route":
            node = kw(deco, "methods")
            if node is None:
                methods = ["GET"]
            elif (named := literal_strings(node)) is not None:
                methods = [m.upper() for m in named]
            else:
                return _Deco(deco.func.value, [], deco.args[0] if deco.args else kw(deco, "path"), deco.lineno, ast.unparse(node))
        else:
            return None
        return _Deco(deco.func.value, methods, deco.args[0] if deco.args else kw(deco, "path"), deco.lineno)

    # ---- resolving names to routers

    def resolve_name(self, path: str, name: str, depth: int = 0) -> list[Ref]:
        facts = self.facts_of(path)
        if facts is None:
            return []
        if name in facts.routers:
            return [(path, name)]
        if depth < DEPTH and name in facts.from_binding:  # re-exported: follow the import
            module, level, original = facts.from_binding[name]
            out: list[Ref] = []
            for g in self._modules(path, module, level):
                out += self.resolve_name(g, original, depth + 1)
            return out
        return []

    def resolve(self, path: str, expr: ast.expr | None, depth: int = 0) -> list[Ref]:
        """The routers or apps an expression names: a name (local, imported, aliased) or an attribute chain on a
        module (`routers.items.router`, `items_mod.router`)."""
        if isinstance(expr, ast.Name):
            return self.resolve_name(path, expr.id, depth)
        if not isinstance(expr, ast.Attribute):
            return []
        chain: list[str] = []
        cur: ast.expr = expr
        while isinstance(cur, ast.Attribute):
            chain.append(cur.attr)
            cur = cur.value
        if not isinstance(cur, ast.Name):
            return []
        chain.reverse()
        facts = self.facts_of(path)
        if facts is None:
            return []
        bases: list[tuple[str, int]] = []
        if cur.id in facts.from_binding:
            module, level, name = facts.from_binding[cur.id]
            bases.append((f"{module}.{name}" if module else name, level))  # `from pkg import mod` names a submodule
        if cur.id in facts.module_binding:
            bases.append(facts.module_binding[cur.id])
        out: list[Ref] = []
        for module, level in bases:
            dotted = ".".join([module, *chain[:-1]])
            for g in self._modules(path, dotted, level):
                out += self.resolve_name(g, chain[-1], depth + 1)
        return out

    # ---- evaluating prefixes

    @staticmethod
    def _unknown(path: str, expr: ast.AST) -> _Str:
        try:
            src = ast.unparse(expr.value if isinstance(expr, ast.FormattedValue) else expr)
        except Exception:  # noqa: BLE001 - an unparsable node is still unknown, not a crash
            src = "?"
        src = " ".join(src.split())
        return _Str(f"<unknown:{src}>", ((src, path, getattr(expr, "lineno", 1)),))

    # ---- the table

    def _build(self) -> None:
        self.search()
        for path in sorted(self.candidates):
            self.facts_of(path)
        # a router is imported under any name by the files that decorate it: the importers of each file that declares
        # one are candidates too (the importer search says when it could not be complete)
        declaring = [f for f in self.facts.values() if f is not None and f.routers]
        self.language.prefetch_importers([f.path for f in declaring], self.sha)
        for f in declaring:
            res = self.language.importers(f.path, self.sha)
            if not res.complete:
                self.incomplete(f"the importers of {f.path} were not established completely: " + "; ".join(res.reasons))
            for importer in res.paths:
                if importer.endswith(".py") and not self.language.is_test_path(importer) and citable(importer):
                    self.candidates.add(importer)
        for path in sorted(self.candidates):
            self.facts_of(path)
        loaded = [f for f in self.facts.values() if f is not None]
        for f in loaded:
            for line in f.strays:
                self.unresolved = True
                self.incomplete(f"{f.path}:{line} mounts a router outside module level")
                record_quoted(self.target, "mount_unresolved", f"{f.path}:{line}",
                              "a mount made inside a function or another scope is not read, so a router that looks unmounted may be mounted there", f.parsed.line_text(line).strip())
        self._link()
        self.entry_files = {f.path for f in self.facts.values() if f is not None and (f.handlers or f.routers or f.mounts)}

    def _link(self) -> None:
        for f in [f for f in self.facts.values() if f is not None]:
            for m in f.mounts:
                if m.kind == "mount" and (m.child is None or isinstance(m.child, ast.Call)):
                    continue  # app.mount("/static", StaticFiles(...)): what is mounted is not a router or an app
                parents = self.resolve(f.path, m.parent)
                children = self.resolve(f.path, m.child)
                if not parents and m.kind == "mount":
                    continue  # not a call on an app or router of this repository
                if not parents or not children:
                    self.unresolved = True
                    self.incomplete(f"{f.path}:{m.line} mounts something that could not be resolved to a router")
                    record_quoted(self.target, "mount_unresolved", f"{f.path}:{m.line}",
                                  "what it mounts, or what it mounts it on, could not be resolved to a router, so a router that looks unmounted may be mounted here",
                                  f.parsed.line_text(m.line).strip())
                    continue
                for parent in parents:
                    for child in children:
                        self.mounts_of.setdefault(child, []).append(_Link(m, parent, child))

    def router(self, ref: Ref) -> _Router:
        facts = self.facts[ref[0]]
        assert facts is not None
        return facts.routers[ref[1]]

    def piece(self, path: str, expr: ast.expr | None) -> _Str:
        """A prefix or a route path: what `eval` makes of it. FastAPI requires each of them to be empty or to start with
        `/`, so a piece that is unknown as a whole stands for a segment: `/<unknown:EXPR>`."""
        out = self.eval(path, expr)
        if len(out.unknown) == 1 and out.value == f"<unknown:{out.unknown[0][0]}>":
            return _Str("/" + out.value, out.unknown)
        return out

    def own_prefix(self, ref: Ref) -> _Str:
        r = self.router(ref)
        return self.piece(r.path, r.prefix) if r.kind == "APIRouter" else _Str()

    def up(self, ref: Ref, stack: tuple[Ref, ...] = ()) -> list[_Option]:
        """Every way up from a router or app: through each mount that carries it, to an app or to a router that
        nothing mounts. A sub application that is mounted is served only where it is mounted."""
        if ref in self._up:
            return self._up[ref]
        router = self.router(ref)
        out: list[_Option] = []
        for link in self.mounts_of.get(ref, ()):
            if link.parent in stack or link.parent == ref:
                continue  # a cycle of mounts
            m, parent = link.mount, self.router(link.parent)
            own = self.own_prefix(link.parent)
            prefix = self.piece(m.path, m.prefix)
            for o in self.up(link.parent, stack + (ref,)):
                ctor = ((parent.path, parent.line),) if own.value or own.unknown else ()
                out.append(_Option(o.leading + own + prefix, ((m.path, m.line),) + ctor + o.evidence, o.app, o.top, (ref,) + o.refs))
        if not out:
            out.append(_Option(_Str(), (), router.kind == "FastAPI", ref, (ref,)))
        if not stack:
            self._up[ref] = out
        return out

    def composed(self) -> list[_Composed]:
        """Every handler route with every way up (`app` or not): the routes that end at an app are `Route`s."""
        if self._composed is not None:
            return self._composed
        out: list[_Composed] = []
        for f in sorted((f for f in self.facts.values() if f is not None), key=lambda x: x.path):
            for h in f.handlers:
                for deco in h.decos:
                    for ref in self.resolve(f.path, deco.router):
                        own = self.own_prefix(ref)
                        path = self.piece(f.path, deco.path)
                        methods = deco.methods or [f"<unknown:{deco.unknown_methods}>"]
                        if deco.unknown_methods is not None:  # the path is known, the methods are not: partial, said when a change reaches it
                            path = path + _Str("", ((f"methods={deco.unknown_methods}", f.path, deco.line),))
                        for method in methods:
                            for o in self.up(ref):
                                full = o.leading + own + path
                                pairs = ((f.path, deco.line),) + (((self.router(ref).path, self.router(ref).line),) if own.value or own.unknown else ()) + o.evidence
                                route = self._route(h, method, full, pairs, o.app)
                                if route is not None:
                                    out.append(_Composed(h, deco, method, ref, full, o, route))
        self._composed = out
        return out

    def _route(self, h: _Handler, method: str, full: _Str, pairs: tuple[tuple[str, int], ...], app: bool) -> Route | None:
        path = normalize_path(full.value)
        # the decorator is the route's own line (the mounts after it are not claimed)
        evidence = route_evidence(self.adapter.repo, self.sha, pairs, self._parsed_of, f"route:{method} {path}:{h.path}")
        if evidence is None:
            return None
        return Route(
            repo=self.adapter.repo, kind="api_route", method=method, path=path, file=h.path, handler=h.name, target=h.path,
            module=self.adapter.module_of(h.path, self.sha), evidence=evidence, partial=bool(full.unknown),
        )

    def _parsed_of(self, path: str) -> Any:
        facts = self.facts.get(path)
        return facts.parsed if facts is not None else None

    def routes(self) -> list[Route]:
        return [c.route for c in self.composed() if c.option.app]


class FastApiAdapter(Shared):
    """FastAPI entry points of one repository, built on its Python adapter (which reads the files, parses them and
    resolves modules). Use one adapter per run, as the language adapters are."""

    kind = "fastapi"
    TABLE = _Table
    UNMOUNTED_DETAIL = "no route is claimed for its handlers: no mount carries it up to an app (a mount in a comment is not a mount)"
    UNMOUNTED_UNSURE = "{subject} may be mounted by a mount that could not be resolved: it is not said to be unmounted"

    @classmethod
    def from_config(cls, repo: config.Repo, language: PyAdapter, *, record_to: Any = None) -> "FastApiAdapter":
        return cls(repo.name, language, record_to=record_to)

    def module_of(self, path: str, sha: str) -> str:
        """The service the file belongs to: the name of the nearest folder holding the service marker."""
        marker = self.language.service_marker
        if not marker:
            return ""
        return nearest_folder(self.language.tree(sha), path, marker) or ""

    # ---- what the walk asks

    def unmatched(self, sha: str) -> list[tuple[str, str]]:
        """The search for an app or a router when it came back whole and found no file that declares handlers, routers or mounts. A search that was
        cut or failed is the table's incompleteness, not an absence."""
        table = self._table(sha)
        if not table.complete or table.entry_files:
            return []
        return [("FastAPI or APIRouter", "no file declares a FastAPI app, a router or a mount, so the routes of the repository were not established")]

    def routing_files(self, sha: str, what: str) -> list[str]:
        return list(self._table(sha).entry_files)

    def unmounted(self, sha: str) -> list[tuple[str, str]]:
        """The routers (file, variable) that declare handlers and that nothing mounts up to an app."""
        table = self._table(sha)
        refs = {c.router for c in table.composed()}
        return sorted(ref for ref in refs if table.router(ref).kind == "APIRouter" and not any(o.app for o in table.up(ref)))

    def structural_names(self, path: str, sha: str) -> frozenset[str]:
        """The routers and apps a file declares: whoever imports one of them mounts it (or decorates its handlers), which
        is not a way a change reaches anything else."""
        facts = self._table(sha).facts_of(path)
        return frozenset(facts.routers) if facts is not None else frozenset()

    # ---- what a chain reaches

    def entry_points_reached(self, chains: Iterable[Any], sha: str | None = None) -> list[Reached]:
        """The API routes the chains reach: one `Area` per route, with the chain of Evidence from the change to the
        handler (the imports on the way, or the changed line) followed by the route's own evidence, and whether it was
        established completely. What could not be established at all (a route table that is not whole) is in `problems`."""
        chains_seen = list(chains)
        gathered = Gathered()
        for chain in chains_seen:
            table = self._table(chain.sha)
            for comp in self._reached(table, chain):
                if not comp.option.app:
                    gathered.unmounted_at(comp.option.top)
                    continue
                route = comp.route
                hops = chain.hops
                if chain.direct:
                    hops = self._direct_hop(chain, comp)
                gathered.add(chain, table, route, tuple(dedupe(list(hops) + list(route.evidence))), unknown=comp.path.unknown)

        def partial(entry: dict[str, Any]) -> None:
            for expr, path, line in entry["unknown"]:
                record_quoted(self.target, "route_path_partial", f"{path}:{line}",
                              "a prefix of the route is not a string literal or a constant; it is kept with the part marked and nothing is guessed", expr)

        return self.settle(gathered, chains_seen, partial)

    def _reached(self, table: _Table, chain: Any) -> list[_Composed]:
        facts = table.facts_of(chain.entry)
        if facts is None:
            return []
        comps = table.composed()
        reach = chain.reach

        def through(c: _Composed) -> bool:
            return c.handler.path == chain.entry or any(p == chain.entry for p, _l in self._pairs(c))

        if reach.whole:  # a statement that runs when the file loads (or a constant a mount takes its prefix from): every route in or through it
            return [c for c in comps if through(c)]
        if chain.edge is not None:
            return [c for c in comps if c.handler.path == chain.entry and c.handler.name in reach.locals]
        changed = list(chain.changed)
        added = {n for s in changed for n in s.added_lines}
        imported, other = self._module_changes(chain, facts, changed)
        mount_names = {m.line: names_in(m.parent, m.child, m.prefix) for m in facts.mounts}
        out = []
        for c in comps:
            here = c.handler.path == chain.entry
            pairs = self._pairs(c)
            if not (here or any(p == chain.entry for p, _l in pairs)):
                continue  # the route neither lives in the changed file nor passes through one of its mounts
            hit = (
                (here and c.handler.name in reach.locals)  # the changed declarations and what uses them, in the file
                or any(ref[0] == chain.entry and ref[1] in reach.locals for ref in (c.router,) + c.option.refs)
                or any(p == chain.entry and line in added for p, line in pairs)
                or other
            )
            if not hit and imported:
                if here:
                    decl = facts.parsed.placed(c.handler.start)
                    hit = decl is not None and bool(facts.parsed.decl_references(decl) & imported)
                hit = hit or any(p == chain.entry and mount_names.get(line, set()) & imported for p, line in pairs)
            if hit:
                out.append(c)
        return out

    def _module_changes(self, chain: Any, facts: _Facts, changed: list[Any]) -> tuple[set[str], bool]:
        """What the changed lines outside every declaration touch: (the names the changed import statements bind,
        whether some line is not an import statement, which could change anything the file does when it loads)."""
        return module_changes(changed, facts.parsed, self._before(chain), import_lines)

    def _before(self, chain: Any) -> Any:
        return self.language.parsed(chain.pre_sha, chain.before_path) if chain.pre_sha else None

    @staticmethod
    def _pairs(c: _Composed) -> list[tuple[str, int]]:
        return [(e.path, e.line) for e in c.route.evidence]

    def _direct_hop(self, chain: Any, comp: _Composed) -> tuple[Evidence, ...]:
        """For a change inside the file: the changed line within the handler when there is one, else the first
        changed line that belongs to the route (a mount, a constructor), else the first changed line."""
        h = comp.handler
        return direct_hop(chain, (h.start, h.end) if h.path == chain.entry else None, self._pairs(comp))
