"""What one Python file says about URLs, read from its syntax tree: the assignments that build `urlpatterns` (and any other list a pattern list
is made of), the imports that name a module or a view, the constants a route string may be made of, the DRF routers and their `register` calls,
the `ROOT_URLCONF` assignments, the calls to `include` that are not at module level, and the classes a view may be.

Nothing here resolves a name across files: that is the table's job (`table.py`). Nothing here imports Django or the project.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any

from reachmap.adapters.django import known
from reachmap.adapters.python_routes import FileFacts, callee, kw, literal_strings, names_in, statements

__all__ = [
    "ActionInfo", "BaseRef", "ClassInfo", "Element", "Facts", "PATH_FUNCS", "RouterDecl", "Register", "Span", "callee", "extract", "refs_in", "kw",
    "names_in", "statements",
]

PATH_FUNCS = {"path": False, "re_path": True, "url": True}  # callee name, and whether the route is a regular expression
FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _chain(node: ast.expr) -> list[str] | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return parts[::-1]
    return None


def refs_in(*nodes: ast.AST | None) -> frozenset[str]:
    """Every dotted reference spelled in the nodes, each with its prefixes: `views.Hello.as_view` is `views`, `views.Hello` and the whole of it.
    A bare name is its own reference."""
    out: set[str] = set()
    for node in nodes:
        if node is None:
            continue
        for n in ast.walk(node):
            if isinstance(n, (ast.Name, ast.Attribute)):
                chain = _chain(n)  # type: ignore[arg-type]
                if chain:
                    out.update(".".join(chain[:i]) for i in range(1, len(chain) + 1))
    return frozenset(out)


@dataclass(frozen=True, slots=True)
class Span:
    """A place in a file that says something about a route: a `path()` call, a `router.register()` call, a router's constructor, an `@action`
    decorator. `key` is the dump of its syntax (a call that is written the same is the same key in another version of the file), `names` the names
    spelled in it and `refs` its dotted references (what a change that arrives by an import is compared with)."""

    file: str
    kind: str  # path | include | register | router | action | urls
    start: int
    end: int
    key: str
    names: frozenset[str]
    refs: frozenset[str]

    def covers(self, line: int) -> bool:
        return self.start <= line <= self.end


def span_of(file: str, kind: str, node: ast.AST) -> Span:
    return Span(file, kind, node.lineno, node.end_lineno or node.lineno, ast.dump(node), names_in(node), refs_in(node))  # type: ignore[attr-defined]


@dataclass(slots=True)
class Element:
    """One element of a `urlpatterns` list: a route, an include, the URLs of a router, or something that was not understood (`why`).
    `span.file` is the file whose names the expressions are written in."""

    kind: str  # route | include | router_urls | unread
    span: Span
    regex: bool = False
    route: ast.expr | None = None
    view: ast.expr | None = None
    name: ast.expr | None = None
    target: ast.expr | None = None
    namespace: ast.expr | None = None
    expr: ast.expr | None = None
    why: str = ""


@dataclass(frozen=True, slots=True)
class RouterDecl:
    """`router = routers.DefaultRouter(...)`: `cls` is the class name (`DefaultRouter`, `SimpleRouter`) or None when it is not a router class
    the adapter knows; `root_view` whether the class adds an API root view."""

    file: str
    var: str
    cls: str | None
    root_view: bool
    span: Span


@dataclass(frozen=True, slots=True)
class Register:
    """`router.register(prefix, viewset, basename)` at module level."""

    file: str
    router: ast.expr
    prefix: ast.expr | None
    viewset: ast.expr | None
    basename: ast.expr | None
    span: Span


@dataclass(frozen=True, slots=True)
class BaseRef:
    """A base class: in the repository (`file`, `name`) or outside it (`name` only)."""

    kind: str  # repo | external
    name: str
    file: str = ""


@dataclass(frozen=True, slots=True)
class ActionInfo:
    """An `@action(...)` method of a viewset: the expression of `detail` (a constant for the table to evaluate), `methods` (None: not a list of
    literals), the url path and name."""

    name: str
    detail_expr: ast.expr | None
    methods: tuple[str, ...] | None
    url_path: str
    url_name: str
    span: Span
    node: ast.AST


@dataclass(slots=True)
class ClassInfo:
    file: str
    name: str
    node: ast.ClassDef
    start: int
    end: int
    members: dict[str, ast.AST]
    bases: list[BaseRef] = field(default_factory=list)
    attrs: dict[str, ast.expr] = field(default_factory=dict)
    actions: list[ActionInfo] = field(default_factory=list)


@dataclass(slots=True)
class Facts(FileFacts):
    stars: list[tuple[str, int]] = field(default_factory=list)
    assigns: dict[str, list[tuple[str, ast.expr]]] = field(default_factory=dict)  # name to [(set | aug | append | extend, expr)]
    declared: set[str] = field(default_factory=set)
    kinds: dict[str, str] = field(default_factory=dict)  # a declared name to function | class | variable
    routers: dict[str, RouterDecl] = field(default_factory=dict)
    registers: list[Register] = field(default_factory=list)
    root_assigns: list[tuple[int, ast.expr]] = field(default_factory=list)
    settings_names: list[str] = field(default_factory=list)  # the modules DJANGO_SETTINGS_MODULE is set to, written as literals
    strays: list[int] = field(default_factory=list)
    spans: list[Span] = field(default_factory=list)  # every path, include, register and router call at module level

    @property
    def has_patterns(self) -> bool:
        return "urlpatterns" in self.assigns


def _action_info(span_file: str, node: ast.AST, deco: ast.Call) -> ActionInfo:
    name = node.name  # type: ignore[attr-defined]
    detail = kw(deco, "detail")
    meth = kw(deco, "methods")
    methods: tuple[str, ...] | None
    if meth is None:
        methods = ("GET",)
    elif (named := literal_strings(meth)) is not None:
        methods = tuple(sorted({m.upper() for m in named}))
    else:
        methods = None
    up, un = kw(deco, "url_path"), kw(deco, "url_name")
    url_path = up.value if isinstance(up, ast.Constant) and isinstance(up.value, str) else name
    url_name = un.value if isinstance(un, ast.Constant) and isinstance(un.value, str) else name.replace("_", "-")
    return ActionInfo(name, detail, methods, url_path, url_name, span_of(span_file, "action", deco), node)


def class_info(file: str, decl_node: ast.ClassDef, start: int, end: int, bases: list[BaseRef]) -> ClassInfo:
    members: dict[str, ast.AST] = {}
    attrs: dict[str, ast.expr] = {}
    counts: dict[str, int] = {}
    actions: list[ActionInfo] = []
    for stmt in decl_node.body:
        if isinstance(stmt, FUNCS):
            members.setdefault(stmt.name, stmt)
            for deco in stmt.decorator_list:
                if isinstance(deco, ast.Call) and callee(deco.func) == "action":
                    actions.append(_action_info(file, stmt, deco))
        elif isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
            value = stmt.value
            for t in targets:
                if isinstance(t, ast.Name) and value is not None:
                    counts[t.id] = counts.get(t.id, 0) + 1
                    attrs[t.id] = value
    attrs = {k: v for k, v in attrs.items() if counts[k] == 1}
    return ClassInfo(file, decl_node.name, decl_node, start, end, members, bases, attrs, actions)


def extract(path: str, parsed: Any) -> Facts:
    """The facts of a parsed file (see the module docstring)."""
    facts = Facts(path, parsed)
    facts.stars = facts.bind_imports()
    for d in parsed.declarations:
        for name in d.names:
            facts.declared.add(name)
            facts.kinds.setdefault(name, d.kind)
    counts: dict[str, int] = {}
    for stmt in statements(parsed.tree.body):
        _statement(facts, stmt, counts)
    for name, entries in facts.assigns.items():
        facts.consts[name] = entries[0][1] if len(entries) == 1 and entries[0][0] == "set" and counts.get(name, 0) == 1 else None
    seen_stray: set[int] = set()
    for scope in (n for n in ast.walk(parsed.tree) if isinstance(n, (*FUNCS, ast.ClassDef, ast.Lambda))):
        for node in ast.walk(scope):
            if isinstance(node, ast.Call) and callee(node.func) == "include" and id(node) not in seen_stray:
                seen_stray.add(id(node))
                facts.strays.append(node.lineno)
    facts.strays = sorted(set(facts.strays))
    facts.settings_names = _settings_names(parsed.tree)
    return facts


SETTINGS_ENV = "DJANGO_SETTINGS_MODULE"


def _is_str(node: ast.AST | None, value: str | None = None) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str) and (value is None or node.value == value)


def _settings_names(tree: ast.AST) -> list[str]:
    """The literal module names DJANGO_SETTINGS_MODULE is given anywhere in the file: `os.environ.setdefault(KEY, "x")`, `os.environ[KEY] = "x"`,
    `os.environ.update({KEY: "x"})`, `os.putenv(KEY, "x")`."""
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            args = list(node.args)
            for i, a in enumerate(args[:-1]):
                if _is_str(a, SETTINGS_ENV) and _is_str(args[i + 1]):
                    out.append(args[i + 1].value)  # type: ignore[attr-defined]
            for a in args:
                if isinstance(a, ast.Dict):
                    out += [v.value for k, v in zip(a.keys, a.values) if _is_str(k, SETTINGS_ENV) and _is_str(v)]  # type: ignore[attr-defined]
        elif isinstance(node, ast.Assign) and _is_str(node.value):
            for t in node.targets:
                if isinstance(t, ast.Subscript) and _is_str(t.slice, SETTINGS_ENV):
                    out.append(node.value.value)  # type: ignore[attr-defined]
    return out


def _router_decl(file: str, var: str, value: ast.expr, stmt: ast.stmt) -> RouterDecl | None:
    if not isinstance(value, ast.Call):
        return None
    name = callee(value.func)
    if name is None or not name.endswith("Router"):
        return None
    return RouterDecl(file, var, name if name in known.ROUTER_CLASSES else None, known.ROUTER_CLASSES.get(name, False), span_of(file, "router", value))


def _statement(facts: Facts, stmt: ast.stmt, counts: dict[str, int]) -> None:
    file = facts.path
    value: ast.expr | None = None
    if isinstance(stmt, ast.Assign):
        value = stmt.value
        for t in stmt.targets:
            if isinstance(t, ast.Name):
                _assign(facts, t.id, "set", stmt.value, counts)
                if t.id == "ROOT_URLCONF":
                    facts.root_assigns.append((stmt.lineno, stmt.value))
                decl = _router_decl(file, t.id, stmt.value, stmt)
                if decl is not None:
                    facts.routers[t.id] = decl
    elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.value is not None:
        value = stmt.value
        _assign(facts, stmt.target.id, "set", stmt.value, counts)
        if stmt.target.id == "ROOT_URLCONF":
            facts.root_assigns.append((stmt.lineno, stmt.value))
        decl = _router_decl(file, stmt.target.id, stmt.value, stmt)
        if decl is not None:
            facts.routers[stmt.target.id] = decl
    elif isinstance(stmt, ast.AugAssign) and isinstance(stmt.target, ast.Name) and isinstance(stmt.op, ast.Add):
        value = stmt.value
        _assign(facts, stmt.target.id, "aug", stmt.value, counts)
        if stmt.target.id == "ROOT_URLCONF":
            facts.root_assigns.append((stmt.lineno, stmt.value))
    elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        call = stmt.value
        value = call
        if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name) and call.func.attr in ("append", "extend") and call.args:
            _assign(facts, call.func.value.id, call.func.attr, call.args[0], counts)
        elif isinstance(call.func, ast.Attribute) and call.func.attr == "register":
            prefix = call.args[0] if call.args else kw(call, "prefix")
            viewset = call.args[1] if len(call.args) > 1 else kw(call, "viewset")
            basename = call.args[2] if len(call.args) > 2 else (kw(call, "basename") or kw(call, "base_name"))
            facts.registers.append(Register(file, call.func.value, prefix, viewset, basename, span_of(file, "register", call)))
    if value is not None:
        for node in ast.walk(value):
            if isinstance(node, ast.Call) and callee(node.func) in PATH_FUNCS:
                facts.spans.append(span_of(file, "path", node))
        if isinstance(value, ast.Call) and callee(value.func) in ("register",) and isinstance(value.func, ast.Attribute):
            facts.spans.append(span_of(file, "register", value))
        decl = None
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            decl = facts.routers.get(stmt.targets[0].id)
        if decl is not None:
            facts.spans.append(decl.span)


def _assign(facts: Facts, name: str, op: str, value: ast.expr, counts: dict[str, int]) -> None:
    if op == "set":
        counts[name] = counts.get(name, 0) + 1
    facts.assigns.setdefault(name, []).append((op, value))
