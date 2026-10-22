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
`ast`; a file that does not parse claims nothing and records a blocking `parse_degraded`. Limits are in
docs/briefs/M7-entry-points.md.
"""

from __future__ import annotations

import ast
import posixpath
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Iterator

from reachmap import config
from reachmap.adapters import Reached, Route, diff_routes, guard_route_changes, normalize_path
from reachmap.adapters.base import api_area, citable, dedupe, direct_hop, evidence_at, import_lines, module_changes, record_quoted
from reachmap.adapters.python import PyAdapter
from reachmap.incomplete import record
from reachmap.model import Evidence, Read

__all__ = ["FastApiAdapter"]

METHODS = frozenset({"get", "post", "put", "delete", "patch", "options", "head", "trace"})
CONSTRUCTORS = frozenset({"APIRouter", "FastAPI"})
CANDIDATE_PATTERNS = ["APIRouter", "FastAPI", "include_router", ".mount("]
MOUNT_CALLS = frozenset({"include_router", "mount"})
COMPOUND = (ast.If, ast.Try, getattr(ast, "TryStar", ast.Try), ast.With, ast.AsyncWith, ast.For, ast.AsyncFor, ast.While, getattr(ast, "Match", ast.If))
DEPTH = 6  # how far a name is followed through imports and constants

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
class _Facts:
    path: str
    parsed: Any
    routers: dict[str, _Router] = field(default_factory=dict)
    handlers: list[_Handler] = field(default_factory=list)
    mounts: list[_Mount] = field(default_factory=list)
    strays: list[int] = field(default_factory=list)  # lines of mounts that are not module level statements
    from_binding: dict[str, tuple[str, int, str]] = field(default_factory=dict)  # local name to (module, level, name)
    module_binding: dict[str, tuple[str, int]] = field(default_factory=dict)  # local name to (module, level)
    consts: dict[str, ast.expr | None] = field(default_factory=dict)  # None: assigned more than once


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


def _names_in(*exprs: ast.expr | None) -> set[str]:
    return {n.id for e in exprs if e is not None for n in ast.walk(e) if isinstance(n, ast.Name)}


def _callee(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _statements(body: list[ast.stmt]) -> Iterator[ast.stmt]:
    """The statements at module level, through `if`, `try`, `with`, `for` and `while` blocks (a mount in one of those
    runs when the module is imported)."""
    for stmt in body:
        yield stmt
        if isinstance(stmt, COMPOUND):
            for attr in ("body", "orelse", "finalbody"):
                yield from _statements(getattr(stmt, attr, None) or [])
            for handler in getattr(stmt, "handlers", ()):
                yield from _statements(handler.body)
            for case in getattr(stmt, "cases", ()):
                yield from _statements(case.body)


def _kw(call: ast.Call, name: str) -> ast.expr | None:
    return next((k.value for k in call.keywords if k.arg == name), None)


class _Table:
    """The routers, handlers and mounts of one repository at one commit, and the routes composed from them."""

    def __init__(self, adapter: "FastApiAdapter", sha: str) -> None:
        self.adapter = adapter
        self.language: PyAdapter = adapter.language
        self.sha = sha
        self.facts: dict[str, _Facts | None] = {}
        self.complete = True
        self.reasons: list[str] = []
        self.candidates: set[str] = set()
        self.mounts_of: dict[Ref, list[_Link]] = {}
        self.unresolved = False  # a mount that could not be read: a router that looks unmounted may be mounted there
        self._up: dict[Ref, list[_Option]] = {}
        self._composed: list[_Composed] | None = None
        self.entry_files: set[str] = set()
        self._build()

    def incomplete(self, why: str) -> None:
        self.complete = False
        if why not in self.reasons:
            self.reasons.append(why)
            self.adapter.problem(f"route table at {self.sha[:12]}: {why}")

    @property
    def target(self) -> Any:
        return self.adapter.target

    # ---- finding and reading files

    def _grep(self, patterns: list[str]) -> None:
        """One `git grep` for all of the names (a line that holds any of them): the files that may declare routes."""
        res = self.language.search(self.sha, patterns)
        shown = " | ".join(patterns)
        if res is None:
            self.incomplete("the tree could not be read at this commit")
            return
        if res.state == Read.UNREADABLE:
            self.incomplete(f"the search for {shown!r} was unreadable: {res.detail}")
            return
        if res.truncated_by == "examine_cap":
            self.incomplete(f"search_truncated: the search for {shown!r} examined {res.examined} of {res.total} files")
        for link, why in res.not_searched:
            if link.endswith(".py"):
                self.incomplete(f"{link} is a {why}: git grep does not search it, so routes in it may be missed")
        for m in res.partial_matches:
            if m.path.endswith(".py") and not self.language.is_test_path(m.path) and citable(m.path):
                self.candidates.add(m.path)

    def facts_of(self, path: str) -> _Facts | None:
        if path in self.facts:
            return self.facts[path]
        self.facts[path] = None
        parsed = self.language.parsed(self.sha, path) if self.language.file_text(self.sha, path).is_found else None
        if parsed is None:
            self.incomplete(f"{path} could not be read")
            return None
        if parsed.problems:
            self.incomplete(f"{path} did not parse ({parsed.problems[0]}): routes and mounts in it were not read")
            record_quoted(self.target, "parse_degraded", path, "no route or mount is claimed from it, so routes may be missing", parsed.problems[0], blocks_selection=True)
            return None
        self.facts[path] = self._extract(path, parsed)
        return self.facts[path]

    def _extract(self, path: str, parsed: Any) -> _Facts:
        facts = _Facts(path, parsed)
        for rec in parsed.imports:
            if rec.kind == "from":
                for n in rec.names:
                    facts.from_binding[n.asname or n.name] = (rec.module, rec.level, n.name)
            elif rec.kind == "import" and rec.names:
                nm = rec.names[0]
                if nm.asname:
                    facts.module_binding[nm.asname] = (nm.name, 0)
                else:
                    first = nm.name.split(".")[0]
                    facts.module_binding[first] = (first, 0)
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
                if isinstance(value, ast.Call) and _callee(value.func) in CONSTRUCTORS:
                    facts.routers[name] = _Router(path, name, _callee(value.func) or "", _kw(value, "prefix"), node.lineno)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                decos = [x for x in (self._deco(deco) for deco in node.decorator_list) if x is not None]
                if decos:
                    facts.handlers.append(_Handler(path, node.name, d.start, d.end, decos))
        seen: set[int] = set()
        for stmt in _statements(parsed.tree.body):
            call = stmt.value if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call) else None
            if call is None or not isinstance(call.func, ast.Attribute) or call.func.attr not in MOUNT_CALLS:
                continue
            seen.add(id(call))
            if call.func.attr == "include_router":
                child = call.args[0] if call.args else _kw(call, "router")
                facts.mounts.append(_Mount(path, call.lineno, "include_router", call.func.value, child, _kw(call, "prefix")))
            else:
                prefix = call.args[0] if call.args else _kw(call, "path")
                child = call.args[1] if len(call.args) > 1 else _kw(call, "app")
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
            node = _kw(deco, "methods")
            if node is None:
                methods = ["GET"]
            elif isinstance(node, (ast.List, ast.Tuple, ast.Set)) and all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.elts):
                methods = [e.value.upper() for e in node.elts]  # type: ignore[union-attr]
            else:
                return _Deco(deco.func.value, [], deco.args[0] if deco.args else _kw(deco, "path"), deco.lineno, ast.unparse(node))
        else:
            return None
        return _Deco(deco.func.value, methods, deco.args[0] if deco.args else _kw(deco, "path"), deco.lineno)

    # ---- resolving names to routers

    def _modules(self, path: str, module: str, level: int) -> tuple[str, ...]:
        return self.language.resolve_module(path, self.sha, module, level)

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

    def eval(self, path: str, expr: ast.expr | None, depth: int = 0) -> _Str:
        if expr is None:
            return _Str()
        if isinstance(expr, ast.Constant):
            if isinstance(expr.value, str):
                return _Str(expr.value)
            if expr.value is None:
                return _Str()
        elif isinstance(expr, ast.JoinedStr):
            out = _Str()
            for v in expr.values:
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    out += _Str(v.value)
                elif isinstance(v, ast.FormattedValue) and v.conversion == -1 and v.format_spec is None:
                    out += self.eval(path, v.value, depth + 1)
                else:
                    out += self._unknown(path, v)
            return out
        elif isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
            return self.eval(path, expr.left, depth + 1) + self.eval(path, expr.right, depth + 1)
        elif isinstance(expr, ast.Name) and depth < DEPTH:
            facts = self.facts_of(path)
            if facts is not None:
                if facts.consts.get(expr.id) is not None:
                    return self.eval(path, facts.consts[expr.id], depth + 1)
                if expr.id not in facts.consts and expr.id in facts.from_binding:
                    module, level, name = facts.from_binding[expr.id]
                    for g in self._modules(path, module, level):
                        other = self.facts_of(g)
                        if other is not None and other.consts.get(name) is not None:
                            return self.eval(g, other.consts[name], depth + 1)
        return self._unknown(path, expr)

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
        self._grep(CANDIDATE_PATTERNS)
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
        evidence: list[Evidence] = []
        for n, (p, line) in enumerate(pairs):
            facts = self.facts.get(p)
            ev = evidence_at(self.adapter.repo, p, self.sha, facts.parsed, line) if facts is not None else None
            if ev is None:
                return None
            if n == 0:  # the decorator: the route's own line (the mounts after it are not claimed)
                ev = replace(ev, claim=f"route:{method} {normalize_path(full.value)}:{h.path}")
            evidence.append(ev)
        return Route(
            repo=self.adapter.repo, kind="api_route", method=method, path=normalize_path(full.value), file=h.path, handler=h.name, target=h.path,
            module=self.adapter.module_of(h.path, self.sha), evidence=tuple(evidence), partial=bool(full.unknown),
        )

    def routes(self) -> list[Route]:
        return [c.route for c in self.composed() if c.option.app]


class FastApiAdapter:
    """FastAPI entry points of one repository, built on its Python adapter (which reads the files, parses them and
    resolves modules). Use one adapter per run, as the language adapters are."""

    kind = "fastapi"

    def __init__(self, repo: str, language: PyAdapter, *, record_to: Any = None) -> None:
        self.repo = repo
        self.language = language
        self._target = record_to
        self._tables: dict[str, _Table] = {}
        self.problems: list[str] = []  # what could not be established (a route table that is not whole, an unreadable file)

    def problem(self, why: str) -> None:
        if why not in self.problems:
            self.problems.append(why)

    @classmethod
    def from_config(cls, repo: config.Repo, language: PyAdapter, *, record_to: Any = None) -> "FastApiAdapter":
        return cls(repo.name, language, record_to=record_to)

    @property
    def target(self) -> Any:
        return self._target if self._target is not None else self.language.target

    def _table(self, sha: str) -> _Table:
        if sha not in self._tables:
            self._tables[sha] = _Table(self, sha)
        return self._tables[sha]

    def module_of(self, path: str, sha: str) -> str:
        """The service the file belongs to: the name of the nearest folder holding the service marker."""
        marker = self.language.service_marker
        if not marker:
            return ""
        files = self.language.tree(sha)
        folder = posixpath.dirname(path)
        while folder:
            if f"{folder}/{marker}" in files:
                return posixpath.basename(folder)
            folder = posixpath.dirname(folder)
        return ""

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

    def is_entry_file(self, path: str, sha: str) -> bool:
        """A file that declares handlers, routers or mounts."""
        return path in self._table(sha).entry_files

    def routes(self, repo: str | None, sha: str) -> list[Route]:
        """Every API route of the repository at `sha`, with its full path and evidence."""
        if repo is not None and repo != self.repo:
            raise ValueError(f"this adapter reads {self.repo}, not {repo}")
        return self._table(sha).routes()

    def unmounted(self, sha: str) -> list[tuple[str, str]]:
        """The routers (file, variable) that declare handlers and that nothing mounts up to an app."""
        table = self._table(sha)
        refs = {c.router for c in table.composed()}
        return sorted(ref for ref in refs if table.router(ref).kind == "APIRouter" and not any(o.app for o in table.up(ref)))

    def routes_changed(self, before: str | None, after: str) -> tuple[list[Route], list[Route]]:
        """(removed, added) routes between two commits by method and full path, each with the evidence of its own
        version. Without a pre-change version nothing is computed: removals cannot be (the change set recorded it)."""
        if before is None:
            return [], []
        old, new = self._table(before), self._table(after)
        removed, added = diff_routes(old.routes(), new.routes())
        # a route of a file that did not parse was not read, it was not deleted; one the base could not show is not new (guard_route_changes)
        return guard_route_changes(self.target, self.repo, removed, added, base_ok=old.complete, head_ok=new.complete, why="; ".join((old.reasons if not old.complete else []) + (new.reasons if not new.complete else []))[:300])

    withholds_unread = True  # `routes_changed` guards itself: the http edge does not guard it again

    def routes_complete(self, sha: str) -> tuple[bool, list[str]]:
        """Whether the route table at `sha` was read completely, and why not."""
        table = self._table(sha)
        return table.complete, list(table.reasons)

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
        found: dict[tuple[Any, ...], dict[str, Any]] = {}
        order: list[tuple[Any, ...]] = []
        unmounted: dict[str, None] = {}
        chains_seen = list(chains)
        for chain in chains_seen:
            table = self._table(chain.sha)
            for comp in self._reached(table, chain):
                if not comp.option.app:
                    top = comp.option.top
                    unmounted.setdefault(f"{top[0]}:{top[1]}")
                    continue
                route = comp.route
                key = (route.method, route.path, route.file, route.handler, route.evidence)
                hops = chain.hops
                if chain.direct:
                    hops = self._direct_hop(chain, comp)
                evidence = tuple(dedupe(list(hops) + list(route.evidence)))
                entry = found.setdefault(key, {"route": route, "chains": [], "sources": [], "complete": True, "reasons": [], "unknown": comp.path.unknown})
                if evidence not in entry["chains"]:
                    entry["chains"].append(evidence)
                    entry["sources"].append(chain)
                if not (chain.complete and table.complete):
                    entry["complete"] = False
                    entry["reasons"] += [r for r in tuple(chain.reasons) + tuple(table.reasons) if r not in entry["reasons"]]
                if key not in order:
                    order.append(key)
        for subject in unmounted:
            if any(self._table(chain.sha).unresolved for chain in chains_seen):
                self.problem(f"{subject} may be mounted by a mount that could not be resolved: it is not said to be unmounted")
                continue  # that claim cannot be made while a mount of the repository is unresolved (the blocking record says so)
            record(self.target, "router_unmounted", subject,
                   detail="no route is claimed for its handlers: no mount carries it up to an app (a mount in a comment is not a mount)")
        out: list[Reached] = []
        for key in order:
            e = found[key]
            route: Route = e["route"]
            for expr, path, line in e["unknown"]:
                record_quoted(self.target, "route_path_partial", f"{path}:{line}",
                              "a prefix of the route is not a string literal or a constant; it is kept with the part marked and nothing is guessed", expr)
            area = api_area(self.repo, route, e["chains"])
            out.append(Reached(area, (route,), e["complete"], tuple(e["reasons"]), tuple(e["sources"])))
        out.sort(key=lambda r: (r.area.label, r.routes[0].file))
        return out

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
        mount_names = {m.line: _names_in(m.parent, m.child, m.prefix) for m in facts.mounts}
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
