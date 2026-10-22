"""The URL table of a Django project at one commit: the root URLconf, the `include()` chains below it, the routes they compose with their evidence,
the views and DRF viewsets behind the routes, and the units (URLconfs and routers) nothing mounts.

`Table` reads files through the Python adapter (which reads at a pinned commit, parses, and resolves modules) and never imports the project.
A part that cannot be read or understood is never an absence: it makes the table incomplete (`complete`, `reasons`, and the records made on the
way, blocking when a route may be missing because of it). See `reachmap.adapters.django` for the rules.
"""

from __future__ import annotations

import ast
import fnmatch
import posixpath
import re
from dataclasses import dataclass, field, replace
from typing import Any

from reachmap.adapters import Route, normalize_path
from reachmap.adapters.base import record_quoted
from reachmap.adapters.django import known
from reachmap.adapters.django.facts import (
    ActionInfo, BaseRef, ClassInfo, Element, Facts, PATH_FUNCS, RouterDecl, Register, Span, callee, class_info, extract, kw, names_in, refs_in, span_of,
)
from reachmap.adapters.django.urlpath import convert_regex, convert_route, has_marker, marker, substitute
from reachmap.adapters.python_routes import DEPTH, TableBase, route_evidence

__all__ = ["Comp", "Handler", "Table", "WHOLE"]

PENDING = object()  # incomplete(): the file could not be read, and where it matters is said when it is known whether an include carries it
GREP = ["urlpatterns", "include(", "Router(", ".register(", "ROOT_URLCONF", "DJANGO_SETTINGS_MODULE", "import *"]
WHOLE = None  # members_reached: the whole class


_REGEX_SYNTAX = re.compile(r"[()\[\]|\\^$*+?{}.]")


def seg(*parts: "Str") -> "Str":
    """A path the way DRF's routers write theirs: the parts with a slash between them and one at the end (nothing for no part)."""
    out = Str()
    for part in parts:
        if not part.empty:
            out = out + (Str("/") if not out.empty else Str()) + part
    return out + Str("/") if not out.empty else out


Ref = tuple[str, str]  # (file, variable)
Notes = tuple[tuple[str, str, int], ...]  # parts of a path that are not literals, or are regexes: (text, file, line)


# ------------------------------------------------------------------------------ strings


@dataclass(frozen=True, slots=True)
class Str:
    """A string made of known text and unknown parts: `value` holds a stand-in (`marker(i)`) for the ith unknown part, `unknown` the parts as
    (source text, file, line)."""

    value: str = ""
    unknown: tuple[tuple[str, str, int], ...] = ()

    def __add__(self, other: "Str") -> "Str":
        shifted = other.value
        n = len(self.unknown)
        if n and other.unknown:
            for i in range(len(other.unknown) - 1, -1, -1):
                shifted = shifted.replace(marker(i), marker(i + n))
        return Str(self.value + shifted, self.unknown + other.unknown)

    @property
    def empty(self) -> bool:
        return not self.value and not self.unknown

    def text(self) -> str:
        return normalize_path("/" + substitute(self.value, tuple(u[0] for u in self.unknown)))


# ------------------------------------------------------------------------------ resolved things


@dataclass(frozen=True, slots=True)
class Handler:
    """What a route runs: the top-level declaration (`top`), the method of a class that answers (`member`), the file and the lines they are
    in. `label` is what `Route.handler` says."""

    file: str
    top: str
    member: str | None
    start: int
    end: int

    @property
    def label(self) -> str:
        return f"{self.top}.{self.member}" if self.member else self.top


@dataclass(slots=True)
class Piece:
    """One handler of a view: the method it answers, what runs, the class the route names (for inheritance), and what is not known."""

    method: str
    handler: Handler
    view: Ref | None = None
    name: str | None = None  # the url name, when DRF gives one
    methods_unknown: bool = False


@dataclass(slots=True)
class Comp:
    """One route as composed: `spans` are the places that say it (the route's own first, then each include from the router outwards), `files`
    every file whose code the route depends on, `app` whether the way up ends at the root URLconf, `top` the unit at the top of the way up."""

    route: Route
    method: str
    handler: Handler
    view: Ref | None
    spans: tuple[Span, ...]
    files: frozenset[str]
    app: bool
    top: Ref
    notes: tuple[tuple[str, str, int], ...]  # the parts of the path that are not literals or are regexes: (text, file, line)
    ns: tuple[str, ...]
    name: str | None
    methods_unknown: bool


@dataclass(frozen=True, slots=True)
class Target:
    """What an `include()` carries: a URLconf module (`file`), a router (`file`, `name`), or a list of elements (`expr` in `file`'s scope)."""

    kind: str  # module | router | list
    file: str = ""
    name: str = ""
    expr: ast.expr | None = None
    app_name: str | None = None
    elements: tuple[Element, ...] = ()


@dataclass(slots=True)
class Res:
    """What a name or an attribute chain denotes: definitions (`targets`: file and name), modules (files), and why there is nothing."""

    targets: list[Ref] = field(default_factory=list)
    modules: list[str] = field(default_factory=list)
    external: bool = False

    def __bool__(self) -> bool:
        return bool(self.targets or self.modules)

    def merge(self, other: "Res") -> None:
        self.targets += [t for t in other.targets if t not in self.targets]
        self.modules += [m for m in other.modules if m not in self.modules]
        self.external = self.external or other.external


@dataclass(slots=True)
class RouteSpec:
    """A route a router makes: the path (relative to where the router is mounted), the method, what runs and the spans that say it."""

    path: Str
    piece: Piece
    spans: tuple[Span, ...]
    name: str | None
    notes: Notes = ()


class Table(TableBase):
    """The routes of a repository at one commit. `adapter` supplies the Python adapter, the record target and the options. What it shares with the
    FastAPI table (the search, the facts of a file, module resolution, constants, route evidence) is `TableBase`; what is Django's is the URLconf
    chains, regular expression routes, DRF routers, namespaces and where the table has holes."""

    LABEL = "url table"
    NOUN = "URLs"
    UNPARSED = "the URLs in it were not read"
    DEGRADED = "no route is claimed from it, so routes may be missing"
    PATTERNS = GREP
    Str = Str

    def __init__(self, adapter: Any, sha: str) -> None:
        super().__init__(adapter, sha)
        self.facts: dict[str, Facts | None]  # (set by TableBase)
        self.holes: list[str | None] = []  # where routes may be missing: the path of what could not be read (a prefix), or None for anywhere
        self.called_elsewhere: dict[tuple[str, str], bool] = {}  # the adapter's memo of whether a view is imported by a module that is not a URLconf
        self._unreadable_files: set[str] = set()  # files that could not be read or did not parse
        self._covered: set[str] = set()  # of those, the ones an include of a readable URLconf carries (the hole is that include's prefix)
        self.settings: dict[str, Facts] = {}
        self.roots: list[str] = []
        self.comps: list[Comp] = []
        self.handler_tops: dict[str, set[str]] = {}
        self._classes: dict[Ref, ClassInfo | None] = {}
        self._elements: dict[str, list[Element] | None] = {}
        self._tops: frozenset[str] | None = None
        self._said: set[tuple[Any, ...]] = set()
        self.reached: set[Ref] = set()  # units reached from the root URLconf
        self.covered: set[Ref] = set()  # units composed in any way (from the root, or as the top of something nothing mounts)
        self._build()

    # ---- completeness and records

    def incomplete(self, why: str, scope: Any = None) -> None:
        """The table is not whole. `scope` is the path prefix the unread part is under (routes under it may be missing), None for anywhere, `PENDING` when
        the caller says where later."""
        self.unresolved = True  # whatever could not be read may be what mounts a URLconf that looks unmounted
        if scope is not PENDING:
            self.holes.append(scope if isinstance(scope, str) and "<unknown" not in scope else None)
        super().incomplete(why)

    def hole_covers(self, path: str) -> bool:
        """Whether routes at `path` may be missing from this table: a hole that is anywhere, or a prefix of it."""
        return any(h is None or h in ("", "/") or path == h or path.startswith(h + "/") for h in self.holes)

    def say(self, key: tuple[Any, ...], kind: str, subject: str, fixed: str, quoted: str = "", *, blocking: bool | None = None) -> None:
        if key in self._said:
            return
        self._said.add(key)
        record_quoted(self.target, kind, subject, fixed, quoted, blocks_selection=blocking)

    # ---- finding and reading files

    def not_searched(self, link: str, why: str) -> None:
        record_quoted(self.target, "read_unreadable", link, f"git grep does not search a {why}, so the URLs in it may be missed", blocks_selection=True, once=True)

    def unread(self, path: str, why: str) -> None:
        self._unreadable_files.add(path)
        self.incomplete(why, PENDING)

    def read_facts(self, path: str, parsed: Any) -> Facts:
        return extract(path, parsed)

    def parsed_of(self, path: str) -> Any:
        facts = self.facts_of(path)
        return facts.parsed if facts is not None else None

    # ---- names to things

    def _top_names(self) -> frozenset[str]:
        """Every folder and every module name anywhere in the tree (a package may be mounted from any of them: `apps/` on the path, a vendored copy)."""
        if self._tops is None:
            names: set[str] = set()
            for p in self.language.tree(self.sha):
                if p.endswith(".py"):
                    parts = p.split("/")
                    names.update(parts[:-1])
                    names.add(parts[-1][:-3])
            self._tops = frozenset(names)
        return self._tops

    def _in_repo(self, path: str, module: str, level: int) -> bool:
        """Whether a module a file imports can be one of the repository's: a relative import is, so is one whose first name resolves to a
        module of the repository or is the name of a folder or a module anywhere in the tree (it may be mounted from there)."""
        if level > 0 or not module:
            return True
        first = module.split(".")[0]
        return first in self._top_names() or bool(self._modules(path, first, 0))

    def _submodule(self, module_file: str, attr: str) -> list[str]:
        """The module `attr` of the package whose `__init__.py` is `module_file` (a sibling file or folder), when there is one."""
        if posixpath.basename(module_file) != "__init__.py":
            return []
        folder = posixpath.dirname(module_file)
        tree = self.language.tree(self.sha)
        base = f"{folder}/{attr}" if folder else attr
        return [p for p in (base + ".py", base + "/__init__.py") if p in tree]

    def denote(self, path: str, expr: ast.expr | None, depth: int = 0) -> Res:
        """What a name or an attribute chain in `path` means: the declarations (file and name) and modules (files) it can be, or that it is outside
        the repository (`external`). Imports are followed through re-exports; star imports are searched."""
        out = Res()
        if depth > DEPTH or expr is None:
            return out
        if isinstance(expr, ast.Name):
            return self._denote_name(path, expr.id, depth)
        if isinstance(expr, ast.Attribute):
            chain: list[str] = []
            cur: ast.expr = expr
            while isinstance(cur, ast.Attribute):
                chain.append(cur.attr)
                cur = cur.value
            if not isinstance(cur, ast.Name):
                return out
            chain.reverse()
            res = self._denote_name(path, cur.id, depth)
            for attr in chain:
                nxt = Res(external=res.external and not res.modules and not res.targets)
                for m in res.modules:
                    nxt.merge(self._denote_name(m, attr, depth + 1))
                    for sub in self._submodule(m, attr):
                        if sub not in nxt.modules:
                            nxt.modules.append(sub)
                res = nxt
            return res
        return out

    def _denote_name(self, path: str, name: str, depth: int) -> Res:
        facts = self.facts_of(path)
        out = Res()
        if facts is None or depth > DEPTH:
            return out
        if name in facts.declared:
            out.targets.append((path, name))
            return out
        if name in facts.from_binding:
            module, level, original = facts.from_binding[name]
            found_any = False
            for g in self._modules(path, module, level):
                found_any = True
                out.merge(self._denote_name(g, original, depth + 1))
            dotted = f"{module}.{original}" if module else original
            for g in self._modules(path, dotted, level):  # `from pkg import mod`: the name is a submodule
                found_any = True
                if g not in out.modules:
                    out.modules.append(g)
            if not found_any:
                out.external = not self._in_repo(path, module, level)
            return out
        if name in facts.module_binding:
            module, level = facts.module_binding[name]
            files = self._modules(path, module, level)
            out.modules += list(files)
            if not files:
                out.external = not self._in_repo(path, module, level)
            return out
        for module, level in facts.stars:
            for g in self._modules(path, module, level):
                out.merge(self._denote_name(g, name, depth + 1))
        return out

    @staticmethod
    def _unknown(path: str, expr: ast.AST) -> Str:
        try:
            src = ast.unparse(expr.value if isinstance(expr, ast.FormattedValue) else expr)
        except Exception:  # noqa: BLE001 - an expression that cannot be shown is still unknown, not a crash
            src = "?"
        return Str(marker(0), ((" ".join(src.split()), path, getattr(expr, "lineno", 1)),))

    def literal(self, path: str, expr: ast.expr | None) -> str | None:
        """The string an expression is, when all of it is known."""
        s = self.eval(path, expr)
        return s.value if expr is not None and not s.unknown else None

    # ---- classes and views

    def class_info(self, file: str, name: str) -> ClassInfo | None:
        key = (file, name)
        if key in self._classes:
            return self._classes[key]
        self._classes[key] = None
        facts = self.facts_of(file)
        if facts is None:
            return None
        decl = next((d for d in facts.parsed.declarations if d.name == name and isinstance(d.node, ast.ClassDef)), None)
        if decl is None:
            return None
        bases: list[BaseRef] = []
        for b in decl.node.bases:
            res = self.denote(file, b)
            repo = [t for t in res.targets if self._is_class(*t)]
            if repo:
                bases += [BaseRef("repo", t[1], t[0]) for t in repo]
            else:
                chain = refs_in(b)
                last = b.attr if isinstance(b, ast.Attribute) else b.id if isinstance(b, ast.Name) else ""
                bases.append(BaseRef("external", last or (sorted(chain)[-1] if chain else "?")))
        info = class_info(file, decl.node, decl.start, decl.end, bases)
        self._classes[key] = info
        return info

    def _is_class(self, file: str, name: str) -> bool:
        facts = self.facts_of(file)
        return facts is not None and facts.kinds.get(name) == "class"

    def _kinds_of(self, file: str, name: str) -> str | None:
        facts = self.facts_of(file)
        return facts.kinds.get(name) if facts is not None else None

    def dispatch(self, file: str, name: str, stack: tuple[Ref, ...] = ()) -> tuple[dict[str, Ref | None], bool, bool]:
        """The methods a class view answers: method to (file, `Class.member`) or None (it comes from a framework base), whether a base outside the
        repository that is not known could add more, and whether the class is a view of Django or DRF (it has `View` or one of the generic views
        among its bases). Only a method written in lower case dispatches (`getattr(self, request.method.lower())`); a view that answers `get` answers
        `head` (Django makes it), and every class view answers `options`; a literal `http_method_names` limits all of it."""
        info = self.class_info(file, name)
        if info is None or (file, name) in stack:
            return {}, info is None, False
        found: dict[str, Ref | None] = {}
        unknown = False
        is_view = False
        for b in reversed(info.bases):
            if b.kind == "repo":
                sub, u, v = self.dispatch(b.file, b.name, stack + ((file, name),))
                found.update(sub)
                unknown = unknown or u
                is_view = is_view or v
            elif b.name in known.VIEW_BASES:
                is_view = True
                for m in known.VIEW_BASES[b.name]:
                    found[m] = None
            elif b.name in known.VIEWSET_BASES or b.name in known.MIXIN_ACTIONS or b.name.endswith(known.NEUTRAL_SUFFIX) or b.name in known.BASE_OBJECTS:
                continue
            else:
                unknown = True
        for member in info.members:
            if member in known.HTTP_NAMES:
                found[member.upper()] = (info.file, f"{info.name}.{member}")
        if is_view and known.ANY not in found and not stack:
            if "GET" in found and "HEAD" not in found:
                found["HEAD"] = found["GET"]
            found.setdefault("OPTIONS", None)
        if not stack:
            allowed = self.class_attr(file, name, "http_method_names")
            if allowed is not None:
                names = allowed[1]
                if isinstance(names, (ast.List, ast.Tuple, ast.Set)) and all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in names.elts):
                    keep = {e.value.upper() for e in names.elts}  # type: ignore[union-attr]
                    found = {m: o for m, o in found.items() if m in keep or m == known.ANY}
                else:
                    unknown = True  # which methods the class lets through is not known
        return found, unknown, is_view

    def viewset(self, file: str, name: str, stack: tuple[Ref, ...] = ()) -> tuple[dict[str, tuple[Ref | None, str | None]], list[tuple[ClassInfo, ActionInfo]], bool]:
        """A viewset's actions: action name to ((file, class) that defines it or None, member), the `@action` extras and whether a base that is not
        known could add more."""
        info = self.class_info(file, name)
        if info is None or (file, name) in stack:
            return {}, [], info is None
        actions: dict[str, tuple[Ref | None, str | None]] = {}
        extras: dict[str, tuple[ClassInfo, ActionInfo]] = {}
        unknown = False
        for b in reversed(info.bases):
            if b.kind == "repo":
                sub, ex, u = self.viewset(b.file, b.name, stack + ((file, name),))
                actions.update(sub)
                for c, a in ex:
                    extras[a.name] = (c, a)
                unknown = unknown or u
            elif b.name in known.VIEWSET_BASES:
                for a in known.VIEWSET_BASES[b.name]:
                    actions[a] = (None, None)
            elif b.name in known.MIXIN_ACTIONS:
                for a in known.MIXIN_ACTIONS[b.name]:
                    actions[a] = (None, None)
            elif b.name.endswith(known.NEUTRAL_SUFFIX) or b.name in known.BASE_OBJECTS:
                continue
            else:
                unknown = True
        for member in info.members:
            if member in known.ACTIONS:
                actions[member] = ((info.file, info.name), member)
        for a in info.actions:
            extras[a.name] = (info, a)
            actions.pop(a.name, None)
        return actions, [extras[k] for k in extras], unknown

    def class_attr(self, file: str, name: str, attr: str, stack: tuple[Ref, ...] = ()) -> tuple[ClassInfo, ast.expr] | None:
        info = self.class_info(file, name)
        if info is None or (file, name) in stack:
            return None
        if attr in info.attrs:
            return info, info.attrs[attr]
        for b in info.bases:
            if b.kind == "repo":
                found = self.class_attr(b.file, b.name, attr, stack + ((file, name),))
                if found is not None:
                    return found
        return None

    def ancestors(self, file: str, name: str, stack: tuple[Ref, ...] = ()) -> list[Ref]:
        """The classes of the repository a class inherits from, nearest first."""
        info = self.class_info(file, name)
        if info is None or (file, name) in stack:
            return []
        out: list[Ref] = []
        for b in info.bases:
            if b.kind == "repo" and (b.file, b.name) not in out:
                out.append((b.file, b.name))
                out += [a for a in self.ancestors(b.file, b.name, stack + ((file, name),)) if a not in out]
        return out

    def handler_of(self, file: str, top: str, member: str | None) -> Handler | None:
        facts = self.facts_of(file)
        if facts is None:
            return None
        if member is None:
            decl = next((d for d in facts.parsed.declarations if d.name == top), None)
            return Handler(file, top, None, decl.start, decl.end) if decl is not None else None
        info = self.class_info(file, top)
        node = info.members.get(member) if info is not None else None
        if node is None:  # the member comes from a framework base (a viewset's `list`): the class is what there is to cite
            return Handler(file, top, member, info.start, info.end) if info is not None else None
        start = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", ())])  # type: ignore[attr-defined]
        return Handler(file, top, member, start, node.end_lineno or start)  # type: ignore[attr-defined]

    @staticmethod
    def decorator_methods(node: ast.AST) -> list[str] | None:
        """The methods a function view's own decorators restrict it to (None: no restriction)."""
        found: list[str] | None = None
        for deco in getattr(node, "decorator_list", ()):
            m = Table.methods_of_decorator(deco)
            if m is not None:
                found = m if found is None else [x for x in found if x in m]
        return found

    @staticmethod
    def methods_of_decorator(deco: ast.expr) -> list[str] | None:
        name = callee(deco.func) if isinstance(deco, ast.Call) else callee(deco)
        if name in known.METHOD_DECORATORS and not isinstance(deco, ast.Call):
            return list(known.METHOD_DECORATORS[name])
        if name in ("require_http_methods", "api_view") and isinstance(deco, ast.Call):
            arg = deco.args[0] if deco.args else kw(deco, "request_method_list") or kw(deco, "http_method_names")
            if arg is None:
                return ["GET"] if name == "api_view" else None
            if isinstance(arg, (ast.List, ast.Tuple, ast.Set)) and all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in arg.elts):
                return sorted({e.value.upper() for e in arg.elts})  # type: ignore[union-attr]
            return None
        return None

    def pieces(self, file: str, expr: ast.expr | None, where: Span) -> tuple[list[Piece], str]:
        """The handlers a view expression runs: one per method. The second value says why there are none: `unresolved` (nothing the repository
        defines), or `external` (a view of a package outside it)."""
        wrapper: list[str] | None = None
        actions: ast.expr | None = None
        cls = False
        node = expr
        for _ in range(8):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute) and node.func.attr == "as_view":
                    actions = node.args[0] if node.args and isinstance(node.args[0], ast.Dict) else kw(node, "actions")
                    node = node.func.value
                    cls = True
                    break
                inner: ast.expr | None
                deco = node.func
                if isinstance(deco, ast.Call):  # decorator(args)(view)
                    m = self.methods_of_decorator(deco)
                    inner = node.args[0] if node.args else None
                else:
                    m = self.methods_of_decorator(node)
                    inner = node.args[0] if node.args else None
                    if m is None and inner is not None and callee(node.func) in known.METHOD_DECORATORS:
                        m = list(known.METHOD_DECORATORS[callee(node.func) or ""])
                if inner is None or not isinstance(inner, (ast.Name, ast.Attribute, ast.Call, ast.Lambda)):
                    return [], "unresolved"
                if m is not None:
                    wrapper = m if wrapper is None else [x for x in wrapper if x in m]
                node = inner
                continue
            break
        if isinstance(node, ast.Lambda):
            span = span_of(file, "path", node)
            return [Piece(known.ANY, Handler(file, "<lambda>", None, span.start, span.end))], ""
        res = self.denote(file, node)
        if not res.targets:
            if res.external or (isinstance(node, (ast.Name, ast.Attribute)) and not res.modules and self._external_root(file, node)):
                if isinstance(node, ast.Attribute) and node.attr == "urls":
                    return [], "external"  # `admin.site.urls`: URLs of a package outside the repository, not a view
                name = node.attr if isinstance(node, ast.Attribute) else node.id if isinstance(node, ast.Name) else "?"
                return self._external_view(file, name, cls, wrapper, where), "external"
            return [], "unresolved"
        out: list[Piece] = []
        for tfile, tname in res.targets:
            kind = self._kinds_of(tfile, tname)
            if kind == "class":
                out += self._class_view(tfile, tname, actions, wrapper, file)
            elif kind == "function":
                handler = self.handler_of(tfile, tname, None)
                if handler is None:
                    continue
                facts = self.facts_of(tfile)
                decl = next((d for d in facts.parsed.declarations if d.name == tname), None) if facts is not None else None
                own = self.decorator_methods(decl.node) if decl is not None else None
                methods = own if wrapper is None else wrapper if own is None else [x for x in own if x in wrapper]
                for m in (methods if methods is not None else [known.ANY]):
                    out.append(Piece(m, handler))
            # a variable (a view bound to a name) is not followed: the route is not claimed from it
        return (out, "") if out else ([], "unresolved")

    def _external_root(self, file: str, node: ast.expr) -> bool:
        while isinstance(node, ast.Attribute):
            node = node.value
        return isinstance(node, ast.Name) and bool(self._denote_name(file, node.id, 0).external)

    def _external_view(self, file: str, name: str, cls: bool, wrapper: list[str] | None, where: Span) -> list[Piece]:
        methods = list(known.VIEW_BASES.get(name, (known.ANY,))) if cls else [known.ANY]
        if wrapper is not None:
            methods = list(wrapper) if known.ANY in methods else [m for m in methods if m in wrapper]
        handler = Handler(file, name, None, where.start, where.end)
        return [Piece(m, handler) for m in methods]

    def _class_view(self, file: str, name: str, actions: ast.expr | None, wrapper: list[str] | None, urls_file: str) -> list[Piece]:
        info = self.class_info(file, name)
        if info is None:
            return []
        if isinstance(actions, ast.Dict):  # a viewset's as_view({"get": "list"})
            out: list[Piece] = []
            for k, v in zip(actions.keys, actions.values):
                if isinstance(k, ast.Constant) and isinstance(k.value, str) and isinstance(v, ast.Constant) and isinstance(v.value, str):
                    acts, _extras, _u = self.viewset(file, name)
                    own = acts.get(v.value)
                    defined = own[0] if own and own[0] else None
                    handler = self.handler_of(defined[0], defined[1], v.value) if defined else self.handler_of(file, name, None)
                    if handler is not None:
                        out.append(Piece(k.value.upper(), handler, (file, name)))
            return out
        found, unknown, _view = self.dispatch(file, name)
        out = []
        for method in sorted(found):
            if wrapper is not None and method not in wrapper:
                continue
            origin = found[method]
            if origin is None:
                handler = self.handler_of(file, name, None)
            else:
                handler = self.handler_of(origin[0], origin[1].split(".")[0], origin[1].split(".", 1)[1])
            if handler is not None:
                out.append(Piece(method, handler, (file, name), methods_unknown=unknown))
        if not out and (unknown or not found):
            handler = self.handler_of(file, name, None)
            if handler is not None and (wrapper is None):
                out.append(Piece(known.ANY, handler, (file, name), methods_unknown=unknown))
        return out

    # ---- elements of a pattern list

    def module_elements(self, file: str) -> list[Element] | None:
        """The elements of a module's `urlpatterns` in order (None: the module has none)."""
        if file in self._elements:
            return self._elements[file]
        self._elements[file] = None
        facts = self.facts_of(file)
        if facts is None or not facts.has_patterns:
            return None
        self._elements[file] = out = self.variable_elements(file, "urlpatterns", 0)
        return out

    def variable_elements(self, file: str, name: str, depth: int) -> list[Element]:
        facts = self.facts_of(file)
        out: list[Element] = []
        if facts is None:
            return out
        for op, expr in facts.assigns.get(name, ()):
            if op == "set":
                if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add) and isinstance(expr.left, ast.Name) and expr.left.id == name:
                    out += self.elements(file, expr.right, depth + 1)  # urlpatterns = urlpatterns + [...]
                else:
                    out = self.elements(file, expr, depth + 1)
            elif op == "append":
                out += self.elements(file, expr, depth + 1)
            else:
                out += self.elements(file, expr, depth + 1)
        return out

    def elements(self, file: str, expr: ast.expr | None, depth: int = 0) -> list[Element]:
        if expr is None:
            return []
        if depth > DEPTH:
            return [self._unread(file, expr, "the patterns are nested too deeply")]
        if isinstance(expr, (ast.List, ast.Tuple)):
            out: list[Element] = []
            for e in expr.elts:
                out += self.elements(file, e.value if isinstance(e, ast.Starred) else e, depth + 1)
            return out
        if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
            return self.elements(file, expr.left, depth + 1) + self.elements(file, expr.right, depth + 1)
        if isinstance(expr, ast.Call):
            name = callee(expr.func)
            if name in PATH_FUNCS:
                return [self._path_element(file, expr, PATH_FUNCS[name])]
            if name in known.STATIC_HELPERS:
                return []
            if name in ("format_suffix_patterns", "list", "tuple") and expr.args:
                return self.elements(file, expr.args[0], depth + 1)
            return [self._unread(file, expr, "a call that builds URL patterns")]
        if isinstance(expr, ast.Name):
            facts = self.facts_of(file)
            if facts is not None and expr.id in facts.assigns:
                return self.variable_elements(file, expr.id, depth + 1)
            res = self.denote(file, expr)
            found = [t for t in res.targets if (f := self.facts_of(t[0])) is not None and t[1] in f.assigns]
            if found:
                out = []
                for tfile, tname in found:
                    out += self.variable_elements(tfile, tname, depth + 1)
                return out
            return [self._unread(file, expr, "a name that is not a list of URL patterns of the repository")]
        if isinstance(expr, ast.Attribute):
            routers = self.routers_of(file, expr.value) if expr.attr == "urls" else []
            if routers:
                return [Element("router_urls", span_of(file, "urls", expr), expr=expr)]
            res = self.denote(file, expr)
            found = [t for t in res.targets if (f := self.facts_of(t[0])) is not None and t[1] in f.assigns]
            if found:
                out = []
                for tfile, tname in found:
                    out += self.variable_elements(tfile, tname, depth + 1)
                return out
            return [self._unread(file, expr, "an attribute that is not a router's URLs or a list of URL patterns of the repository")]
        return [self._unread(file, expr, "an expression that builds URL patterns")]

    def _unread(self, file: str, expr: ast.AST, why: str) -> Element:
        return Element("unread", span_of(file, "path", expr), why=why)

    def _path_element(self, file: str, call: ast.Call, regex: bool) -> Element:
        span = span_of(file, "path", call)
        route = call.args[0] if call.args else kw(call, "route") or kw(call, "regex")
        view = call.args[1] if len(call.args) > 1 else kw(call, "view")
        name = kw(call, "name")
        if route is None or view is None:
            return Element("unread", span, why="a route with no path or no view")
        if isinstance(view, ast.Call) and callee(view.func) == "include":
            target = view.args[0] if view.args else kw(view, "arg")
            if target is None:
                return Element("unread", span, why="an include with no argument")
            namespace = kw(view, "namespace")
            if len(view.args) > 1:
                namespace = namespace or view.args[1]
            return Element("include", span, regex, route=route, name=name, target=target, namespace=namespace)
        return Element("route", span, regex, route=route, view=view, name=name)

    # ---- routers

    def routers_of(self, file: str, expr: ast.expr | None) -> list[Ref]:
        """The routers (file, variable) an expression names."""
        res = self.denote(file, expr)
        out: list[Ref] = []
        for tfile, tname in res.targets:
            facts = self.facts_of(tfile)
            if facts is not None and tname in facts.routers:
                out.append((tfile, tname))
        return out

    def registers_of(self, ref: Ref) -> list[Register]:
        """Every `register` call on a router, wherever it is made (a router may be imported and registered on by other files)."""
        out: list[Register] = []
        for path in sorted(self.facts):
            facts = self.facts[path]
            if facts is None:
                continue
            for r in facts.registers:
                if ref in self.routers_of(path, r.router):
                    out.append(r)
        return out

    def router_specs(self, ref: Ref) -> list[RouteSpec] | None:
        """The routes of a router, as DRF's routers make them (None: the router is not one the adapter knows)."""
        facts = self.facts_of(ref[0])
        decl = facts.routers.get(ref[1]) if facts is not None else None
        if decl is None or decl.cls is None:
            return None
        out: list[RouteSpec] = []
        if decl.root_view:
            piece = Piece("GET", Handler(ref[0], "APIRootView", None, decl.span.start, decl.span.end))
            out.append(RouteSpec(Str(), piece, (decl.span,), "api-root"))
        for reg in self.registers_of(ref):
            out += self._register_specs(reg)
        return out

    def _register_specs(self, reg: Register) -> list[RouteSpec]:
        res = self.denote(reg.file, reg.viewset)
        classes = [t for t in res.targets if self._is_class(*t)]
        if not classes:
            self.unresolved = True
            self.incomplete(f"{reg.file}:{reg.span.start} registers a viewset that could not be resolved to a class of the repository")
            self.say(("view", reg.file, reg.span.start), "view_unresolved", f"{reg.file}:{reg.span.start}",
                     "the viewset of this registration was not resolved to a class of the repository, so its routes are not claimed",
                     _line(self.parsed_of(reg.file), reg.span.start))
            return []
        prefix = self.eval(reg.file, reg.prefix) if reg.prefix is not None else Str()
        out: list[RouteSpec] = []
        for file, name in classes:
            actions, extras, unknown = self.viewset(file, name)
            lookup = self._lookup(file, name)
            base = self._basename(reg, file, name)

            def spec(path: Str, method: str, origin: tuple[Ref | None, str | None], action: str | None, spans: tuple[Span, ...], label: str | None,
                     view: Ref = (file, name)) -> RouteSpec | None:
                defined, member = origin
                handler = self.handler_of(defined[0], defined[1], member) if defined is not None and member else self.handler_of(file, name, action)
                if handler is None:
                    return None
                return RouteSpec(path, Piece(method, handler, view, methods_unknown=unknown and defined is None), spans, f"{base}-{label}" if base and label else None)

            def fixed(rows: tuple[tuple[str, str], ...]) -> list[tuple[str, tuple[Ref | None, str | None], str]]:
                return [(m, actions[a], a) for m, a in rows if a in actions]

            list_rows, detail_rows = fixed(known.LIST_ROUTE), fixed(known.DETAIL_ROUTE)
            lookup_str = Str(f"{{{lookup}}}")
            for rows, path, label in ((list_rows, seg(prefix), "list"), (detail_rows, seg(prefix, lookup_str), "detail")):
                for method, origin, action in rows:
                    s = spec(path, method, origin, action, (reg.span,), label)
                    if s is not None:
                        out.append(s)
                if not rows and unknown:  # a base that is not known may give the methods: the route is there, the methods are not known
                    s = spec(path, known.ANY, (None, None), None, (reg.span,), label)
                    if s is not None:
                        out.append(s)
            for cinfo, act in extras:
                detail = self._bool(cinfo.file, act.detail_expr)
                if detail is None:
                    self.unresolved = True
                    self.incomplete(f"{cinfo.file}:{act.span.start} is an action whose detail is not a constant: whether its route is on the list or on an item is not known")
                    self.say(("view", cinfo.file, act.span.start), "view_unresolved", f"{cinfo.file}:{act.span.start}",
                             "whether this action is a route of the list or of one item is not known, so its route is not claimed", _line(self.parsed_of(cinfo.file), act.span.start).strip())
                    continue
                url_path = Str(convert_regex(act.url_path))  # DRF puts the url path in a regex as it is written
                notes: Notes = ((act.url_path, cinfo.file, act.span.start),) if _REGEX_SYNTAX.search(act.url_path) else ()
                path = seg(prefix, lookup_str, url_path) if detail else seg(prefix, url_path)
                for m in act.methods if act.methods is not None else (known.ANY,):
                    s = spec(path, m, ((cinfo.file, cinfo.name), act.name), act.name, (act.span, reg.span), act.url_name)
                    if s is not None:
                        out.append(replace(s, notes=notes))
        return out

    def _bool(self, file: str, expr: ast.expr | None, depth: int = 0) -> bool | None:
        """A constant `True` or `False`, or a name assigned that once in the file (or imported from a module of the repository that assigns it once)."""
        if isinstance(expr, ast.Constant) and isinstance(expr.value, bool):
            return expr.value
        if isinstance(expr, ast.Name) and depth < DEPTH:
            facts = self.facts_of(file)
            if facts is not None and facts.consts.get(expr.id) is not None:
                return self._bool(file, facts.consts[expr.id], depth + 1)
            if facts is not None and expr.id not in facts.assigns and expr.id in facts.from_binding:
                module, level, name = facts.from_binding[expr.id]
                for g in self._modules(file, module, level):
                    other = self.facts_of(g)
                    if other is not None and other.consts.get(name) is not None:
                        return self._bool(g, other.consts[name], depth + 1)
        return None

    def _lookup(self, file: str, name: str) -> str:
        for attr in ("lookup_url_kwarg", "lookup_field"):
            found = self.class_attr(file, name, attr)
            if found is not None:
                text = self.literal(found[0].file, found[1])
                if text:
                    return text
        return "pk"

    def _basename(self, reg: Register, file: str, name: str) -> str | None:
        text = self.literal(reg.file, reg.basename) if reg.basename is not None else None
        if text:
            return text
        found = self.class_attr(file, name, "queryset")
        if found is not None:
            node = found[1]
            while isinstance(node, (ast.Call, ast.Attribute)):
                node = node.func if isinstance(node, ast.Call) else node.value
            if isinstance(node, ast.Name):
                return node.id.lower()
        return None

    # ---- includes

    def include_targets(self, el: Element) -> tuple[list[Target], str]:
        """What an include carries; the second value says why nothing was found: `external` or `unresolved` (empty when there are targets)."""
        file = el.span.file
        expr = el.target
        app_name: str | None = None
        if isinstance(expr, ast.Tuple) and len(expr.elts) == 2 and isinstance(expr.elts[1], ast.Constant) and isinstance(expr.elts[1].value, str) and not isinstance(expr.elts[0], ast.Call):
            app_name = expr.elts[1].value
            expr = expr.elts[0]
        if isinstance(expr, (ast.List, ast.Tuple)):
            return [Target("list", file, expr=expr, app_name=app_name, elements=tuple(self.elements(file, expr)))], ""
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
            files = self._modules(file, expr.value, 0)
            if not files:
                return [], "unresolved" if self._in_repo(file, expr.value, 0) else "external"
            return [self._module_target(f, app_name) for f in files], ""
        if isinstance(expr, ast.Attribute) and expr.attr == "urls":
            refs = self.routers_of(file, expr.value)
            if refs:
                return [Target("router", ref[0], ref[1], app_name=app_name) for ref in refs], ""
        if isinstance(expr, (ast.Name, ast.Attribute)):
            res = self.denote(file, expr)
            out: list[Target] = []
            for m in res.modules:
                out.append(self._module_target(m, app_name))
            for tfile, tname in res.targets:
                facts = self.facts_of(tfile)
                if facts is not None and tname in facts.assigns:
                    out.append(Target("list", tfile, tname, expr=expr, app_name=app_name, elements=tuple(self.variable_elements(tfile, tname, 0))))
            if out:
                return out, ""
            if res.external:
                return [], "external"
        return [], "unresolved"

    def _module_target(self, file: str, app_name: str | None) -> Target:
        facts = self.facts_of(file)
        if app_name is None and facts is not None:
            app_name = self.literal(file, facts.consts.get("app_name"))
        return Target("module", file, "urlpatterns", app_name=app_name)

    # ---- the roots

    def _roots(self) -> None:
        option = self.adapter.root_urlconf
        wanted = self.adapter.settings
        found: list[str] = []
        unknown = False
        for path in sorted(self.candidates):  # the settings files: every file that assigns ROOT_URLCONF (a reference to it is not an assignment)
            facts = self.facts_of(path)
            if facts is None or not facts.root_assigns or (wanted and not _matches(path, wanted)):
                continue
            self.settings[path] = facts
            if option:
                continue
            for _line_no, expr in facts.root_assigns:
                value = self.literal(path, expr)
                files = self._modules(path, value, 0) if value else ()
                if files:
                    found += [f for f in files if f not in found]
                else:
                    unknown = True
        if option:
            files = self._modules("", option, 0)
            found += list(files)
            unknown = not files
        elif not self.settings:
            unknown = True
        self._more_settings(wanted)
        self.roots = found
        if unknown:
            self.incomplete("a ROOT_URLCONF is not a literal that names a module of the repository: its routes are not known" if found else
                            "the root URLconf could not be determined: no literal ROOT_URLCONF names a module of the repository")
            self.unresolved = True
            self.say(("root",), "urlconf_root_unknown", self.adapter.repo,
                     "a ROOT_URLCONF is not a literal that names a module of the repository, so the routes under it are not claimed" if found else
                     "no literal ROOT_URLCONF that names a module of the repository was found, so what is mounted is not known")

    def _more_settings(self, wanted: str | None) -> None:
        """The other settings modules: the ones a script names with DJANGO_SETTINGS_MODULE (a settings file need not assign ROOT_URLCONF: it is a
        base's, or the environment's), and the ones that `import *` a settings module (`settings_prod`, `local`): a change to one is as much a change to
        the settings as a change to the one with the root."""
        named: set[str] = set()
        for path in sorted(self.candidates):
            facts = self.facts_of(path)
            for module in (facts.settings_names if facts is not None else ()):
                named.update(self._modules(path, module, 0))
        for path in sorted(named):
            facts = self.facts_of(path)
            if facts is not None and not (wanted and not _matches(path, wanted)):
                self.settings.setdefault(path, facts)
        grew = True
        while grew:  # a module that star-imports a settings module is one, and so is what star-imports that
            grew = False
            for path in sorted(self.candidates):
                facts = self.facts_of(path)
                if facts is None or path in self.settings or (wanted and not _matches(path, wanted)):
                    continue
                if any(f in self.settings for module, level in facts.stars for f in self._modules(path, module, level)):
                    self.settings[path] = facts
                    grew = True

    # ---- composing

    def _build(self) -> None:
        self.search()
        for path in sorted(self.candidates):
            self.facts_of(path)
        for f in [f for f in self.facts.values() if f is not None]:
            for line in f.strays:
                self.unresolved = True
                self.incomplete(f"{f.path}:{line} includes URLs outside module level")
                self.say(("stray", f.path, line), "mount_unresolved", f"{f.path}:{line}",
                         "an include made inside a function or a class is not read, so a URLconf that looks unmounted may be mounted there", _line(f.parsed, line).strip(), blocking=True)
        self._roots()
        for root in self.roots:
            self._module(root, Str(), (), (), (), (), True, (root, "urlpatterns"))
        self._standalone()
        if any(path not in self._covered for path in self._unreadable_files):
            self.holes.append(None)  # a file that might be a URLconf, mounted by who knows what
        self._index()

    def _units(self) -> list[Ref]:
        out: list[Ref] = []
        for path in sorted(self.facts):
            facts = self.facts[path]
            if facts is None:
                continue
            if facts.has_patterns:
                out.append((path, "urlpatterns"))
            out += [(path, v) for v in sorted(facts.routers)]
        return out

    def _standalone(self) -> None:
        """Everything the roots do not reach is composed on its own, so that a handler on it is known and a change to it can say which unit nothing
        mounts: units nothing includes first, then what is left (a cycle)."""
        units = self._units()
        included: set[Ref] = set()
        for u in units:
            for t in self._children(u):
                if t != u:
                    included.add(t)
        for unit in [u for u in units if u not in included] + [u for u in units if u in included]:
            if unit in self.covered:
                continue
            if unit[1] == "urlpatterns":
                self._module(unit[0], Str(), (), (), (), (), False, unit)
            else:
                self._router(unit, Str(), (), (), (), (), False, unit)

    def _children(self, unit: Ref) -> list[Ref]:
        out: list[Ref] = []
        if unit[1] == "urlpatterns":
            for el in self.module_elements(unit[0]) or ():
                out += self._element_children(el)
        return out

    def _element_children(self, el: Element) -> list[Ref]:
        if el.kind == "router_urls":
            return self.routers_of(el.span.file, el.expr.value) if isinstance(el.expr, ast.Attribute) else []
        if el.kind != "include":
            return []
        targets, _why = self.include_targets(el)
        out: list[Ref] = []
        for t in targets:
            if t.kind == "module":
                out.append((t.file, "urlpatterns"))
            elif t.kind == "router":
                out.append((t.file, t.name))
            else:
                for sub in t.elements:
                    out += self._element_children(sub)
        return out

    def _module(self, file: str, prefix: Str, rx: Notes, trail: tuple[Span, ...], ns: tuple[str, ...], stack: tuple[Ref, ...], app: bool, top: Ref) -> bool:
        unit = (file, "urlpatterns")
        if unit in stack:
            return True  # a cycle of includes ends here
        elements = self.module_elements(file)
        if elements is None:
            return False
        self.covered.add(unit)
        if app:
            self.reached.add(unit)
        for el in elements:
            self._element(el, prefix, rx, trail, ns, stack + (unit,), app, top)
        return True

    def _piece(self, el: Element) -> Str:
        """The path an element adds: the route string converted, with what is not known marked."""
        file = el.span.file
        s = self.eval(file, el.route)
        if el.regex:
            if s.unknown:
                return Str(marker(0), (("re_path " + (ast.unparse(el.route) if el.route is not None else "?"), file, el.span.start),))
            return Str(convert_regex(s.value))
        return Str(convert_route(s.value), s.unknown)

    @staticmethod
    def _regex_note(el: Element) -> Notes:
        """A regex is never claimed exact: its route is partial, and a change that reaches it says so."""
        if not el.regex:
            return ()
        return ((" ".join(ast.unparse(el.route).split()) if el.route is not None else "?", el.span.file, el.span.start),)

    def _element(self, el: Element, prefix: Str, rx: Notes, trail: tuple[Span, ...], ns: tuple[str, ...], stack: tuple[Ref, ...], app: bool, top: Ref) -> None:
        file = el.span.file
        if el.kind == "unread":
            self._unreadable(el, prefix)
            return
        if el.kind == "router_urls":
            refs = self.routers_of(file, el.expr.value) if isinstance(el.expr, ast.Attribute) else []
            for ref in refs:
                self._router(ref, prefix, rx, trail + (el.span,), ns, stack, app, top)
            return
        path = prefix + self._piece(el)  # Django joins the route strings of an include as they are written: `shop` and `items/` make `shopitems/`
        rx = rx + self._regex_note(el)
        if el.kind == "include":
            targets, why = self.include_targets(el)
            if not targets:
                self._unresolved_include(el, why, path)
                return
            for t in targets:
                instance = self._instance(el, t)
                inner = ns + ((instance,) if instance else ())
                if t.kind == "module":
                    if not self._module(t.file, path, rx, trail + (el.span,), inner, stack, app, top):
                        self._covered.add(t.file)  # what it holds is not read: routes under this include may be missing
                        self._unresolved_include(el, "unresolved", path)
                elif t.kind == "router":
                    self._router((t.file, t.name), path, rx, trail + (el.span,), inner, stack, app, top)
                else:
                    for sub in t.elements:
                        self._element(sub, path, rx, trail + (el.span,), inner, stack, app, top)
            return
        pieces, why = self.pieces(file, el.view, el.span)
        if not pieces:
            if why == "external":
                return
            self.unresolved = True
            self.incomplete(f"{file}:{el.span.start} names a view that could not be resolved to code of the repository", path.text())
            self.say(("view", file, el.span.start), "view_unresolved", f"{file}:{el.span.start}",
                     "the view of this route was not resolved to code of the repository, so its route is not claimed", _line(self.parsed_of(file), el.span.start).strip())
            return
        name = self.literal(file, el.name) if el.name is not None else None
        for p in pieces:
            self._comp(path, rx, p, (el.span,), trail, ns, name, app, top)

    def _instance(self, el: Element, target: Target) -> str | None:
        ns = self.literal(el.span.file, el.namespace) if el.namespace is not None else None
        return ns or target.app_name

    def _unreadable(self, el: Element, prefix: Str) -> None:
        self.unresolved = True
        self.incomplete(f"{el.span.file}:{el.span.start} is not understood as URL patterns ({el.why})", prefix.text())
        self.say(("unread", el.span.file, el.span.start), "mount_unresolved", f"{el.span.file}:{el.span.start}",
                 f"{el.why}: it is not read, so a URLconf that looks unmounted may be mounted here",
                 _line(self.parsed_of(el.span.file), el.span.start).strip(), blocking=True)

    def _unresolved_include(self, el: Element, why: str, path: Str) -> None:
        line = _line(self.parsed_of(el.span.file), el.span.start).strip()
        if why == "external":
            self.say(("external", el.span.file, el.span.start), "include_external", f"{el.span.file}:{el.span.start}",
                     "the URLs this include carries are defined outside the repository and are not read", line)
            return
        self.unresolved = True
        self.incomplete(f"{el.span.file}:{el.span.start} includes something that could not be resolved to a URLconf of the repository", path.text())
        self.say(("include", el.span.file, el.span.start), "mount_unresolved", f"{el.span.file}:{el.span.start}",
                 "what it includes could not be resolved to a URLconf of the repository, so a URLconf that looks unmounted may be mounted here", line)

    def _router(self, ref: Ref, prefix: Str, rx: Notes, trail: tuple[Span, ...], ns: tuple[str, ...], stack: tuple[Ref, ...], app: bool, top: Ref) -> None:
        if ref in stack:
            return
        specs = self.router_specs(ref)
        if specs is None:
            self.unresolved = True
            facts = self.facts_of(ref[0])
            decl = facts.routers.get(ref[1]) if facts is not None else None
            line = decl.span.start if decl is not None else 1
            self.incomplete(f"{ref[0]}:{line} makes a router that is not one of the known classes")
            self.say(("router", ref[0], line), "mount_unresolved", f"{ref[0]}:{line}",
                     "this router is not of a class the adapter knows (DefaultRouter, SimpleRouter), so its routes are not claimed",
                     _line(self.parsed_of(ref[0]), line).strip(), blocking=True)
            return
        self.covered.add(ref)
        if app:
            self.reached.add(ref)
        for s in specs:
            self._comp(prefix + s.path, rx + s.notes, s.piece, s.spans, trail, ns, s.name, app, top)

    def _comp(self, path: Str, rx: Notes, piece: Piece, spans: tuple[Span, ...], trail: tuple[Span, ...], ns: tuple[str, ...], name: str | None, app: bool, top: Ref) -> None:
        all_spans = tuple(spans) + tuple(reversed(trail))
        text = path.text()
        method = piece.method
        evidence = route_evidence(self.adapter.repo, self.sha, [(sp.file, sp.start) for sp in all_spans], self.parsed_of, f"route:{method} {text}:{piece.handler.file}")
        if evidence is None:
            return
        view = piece.view
        files = {piece.handler.file, *(sp.file for sp in all_spans)}
        if view is not None:
            files.add(view[0])
            files.update(f for f, _n in self.ancestors(*view))
        notes = tuple(dict.fromkeys(tuple(path.unknown) + rx))
        route = Route(
            repo=self.adapter.repo, kind="api_route", method=method, path=text, file=all_spans[0].file, handler=piece.handler.label, target=piece.handler.file,
            module=self.adapter.module_of(piece.handler.file, self.sha), evidence=evidence, partial=bool(notes), path_approximate=bool(rx),
        )
        self.comps.append(Comp(route, method, piece.handler, view, all_spans, frozenset(files), app, top, notes, ns, piece.name or name, piece.methods_unknown))

    def _index(self) -> None:
        files: set[str] = set(self.settings)
        tops: dict[str, set[str]] = {}
        for c in self.comps:
            files |= c.files
            tops.setdefault(c.handler.file, set()).add(c.handler.top)
            if c.view is not None:
                tops.setdefault(c.view[0], set()).add(c.view[1])
        for f in self.facts.values():
            if f is not None and (f.has_patterns or f.routers):
                files.add(f.path)
        self.entry_files = files
        self.handler_tops = tops

    # ---- the table

    def composed(self) -> list[Comp]:
        return self.comps

    def routes(self) -> list[Route]:
        rows = [c.route for c in self.comps if c.app]
        return sorted(rows, key=lambda r: (r.path, r.method, r.evidence[0].path, r.evidence[0].line))

    def url_names(self) -> dict[str, list[Route]]:
        out: dict[str, list[Route]] = {}
        for c in self.comps:
            if c.app and c.name:
                out.setdefault(":".join((*c.ns, c.name)), []).append(c.route)
        return {k: sorted(v, key=lambda r: (r.path, r.method)) for k, v in sorted(out.items())}

    def unmounted(self) -> list[Ref]:
        return sorted({c.top for c in self.comps if not c.app})


def _line(parsed: Any, number: int) -> str:
    return parsed.line_text(number) if parsed is not None else ""


def _matches(path: str, wanted: str) -> bool:
    """Whether a file is one the `settings` option names: the path itself, a glob, or a folder."""
    wanted = wanted.strip()
    return path == wanted or fnmatch.fnmatch(path, wanted) or path.startswith(wanted.rstrip("/") + "/")
