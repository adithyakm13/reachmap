"""The NestJS entry-point adapter: the API routes a change reaches, with their full paths.

A controller is a class decorated `@Controller(path)` (a string, a constant, a list, `{ path, version }`, nothing), a route a
method decorated `@Get(path)`, `@Post`, `@Put`, `@Patch`, `@Delete`, `@Options`, `@Head`, `@All` (`@Sse` is a GET), the decorators
being the ones `@nestjs/common` exports (a `Get` of another package or another file is not one). The route's path is the global
prefix (`app.setGlobalPrefix("api")`, wherever it is called), the controller's path and the method's path, with `:id` written
`{id}`. A controller is served when a module lists it in `controllers: [...]` and that module is reachable from the module the
application is created with (`NestFactory.create(AppModule)`) through `imports: [...]`; a controller that no module lists, or
that only an unreachable module lists, has no route (`router_unmounted`, subject `<path>:<Class>`). The evidence of a route is the
method's decorator line, the controller's decorator line (when it has a path), the line of the module's `controllers` list that names
the class, and the line of the global prefix call.

What a change reaches, to the method: a changed line inside a method reaches its routes; a name a change reaches (an imported
service, a helper) reaches the methods that spell it, and, when the name arrives through the constructor (`private readonly users:
UsersService`), the methods that use the property (`this.users`); a change to a decorator of the class, to a line of the class that
is in no member, or to the module that lists the controller reaches every route of the controller. A file that creates the application
(`NestFactory.create`, `setGlobalPrefix`) reaches every route (global pipes, guards and the prefix are in it). What cannot be read is
said and blocks: a controller that extends a class with routes (`routes_unread`), `RouterModule.register` (`mount_unresolved`: it
changes paths), a module that imports something this does not resolve to a class (`mount_unresolved`). Limits: docs/briefs/M16-express.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from reachmap.adapters import Reached, Route, normalize_path
from reachmap.adapters.base import record_quoted
from reachmap.adapters.express import common
from reachmap.adapters.express.source import Src, Str, build_consts, path_pieces
from reachmap.model import Evidence

VERBS = {"Get": "GET", "Post": "POST", "Put": "PUT", "Patch": "PATCH", "Delete": "DELETE", "Options": "OPTIONS", "Head": "HEAD", "All": "ANY", "Sse": "GET"}
MODIFIERS = frozenset({"public", "private", "protected", "static", "async", "readonly", "override", "abstract", "declare", "get", "set", "accessor"})
PROPERTY_MODIFIERS = frozenset({"public", "private", "protected", "readonly"})
PATTERNS = ["@nestjs/", "setGlobalPrefix"]
COMMON = "@nestjs/common"
CORE = "@nestjs/core"
DEPTH = 6

Ref = tuple[str, str]  # (file, class name)


@dataclass(slots=True)
class Deco:
    name: str  # as imported from @nestjs/common (`Get`), the name written for anything else
    line: int
    args: list[tuple[int, int]]
    first: int
    last: int
    nest: bool = False  # the name is bound to @nestjs/common


@dataclass(slots=True)
class Member:
    kind: str  # method | constructor | property
    name: str
    decos: list[Deco]
    first: int  # tokens, decorators included
    last: int
    start: int  # lines
    end: int
    params: list[tuple[int, int]] = field(default_factory=list)
    props: dict[str, int] = field(default_factory=dict)  # a constructor's parameter properties: name to the token of its type or decorator


@dataclass(slots=True)
class Klass:
    path: str
    name: str
    decos: list[Deco]
    first: int
    start: int
    end: int
    body: tuple[int, int]
    members: list[Member]
    extends: str | None
    line: int  # the line of `class`

    def member_at(self, line: int) -> Member | None:
        return next((m for m in self.members if m.start <= line <= m.end), None)


@dataclass(slots=True)
class ControllerInfo:
    klass: Klass
    deco: Deco
    paths: list[tuple[Str, bool]]
    versioned: bool


@dataclass(slots=True)
class ModuleInfo:
    klass: Klass
    deco: Deco
    controllers: list[tuple[str, int]]  # (class name, the line that names it)
    imports: list[tuple[str, int]]
    opaque: list[int]  # lines of `imports` entries this cannot name (a spread, an expression)
    providers: list[tuple[frozenset[str], bool]] = field(default_factory=list)  # the providers written as objects: the names each spells, and whether it is a global one (`APP_GUARD`)
    body_refs: frozenset[str] = frozenset()  # the names the class body spells (`configure(consumer)`)


@dataclass(slots=True)
class Create:
    """A `NestFactory.create(Module)` call: the module argument, the variable the application is kept in and the declaration around it."""

    tok: int
    line: int
    arg: tuple[int, int] | None
    var: str | None
    decl: tuple[int, int] | None


@dataclass(slots=True)
class Prefix:
    """A `setGlobalPrefix(...)` call: the tokens of its first argument, the receiver, the declaration around it and whether it has options."""

    line: int
    a: int
    b: int
    recv: str | None
    decl: tuple[int, int] | None
    options: bool


@dataclass(frozen=True, slots=True)
class _Pfx:
    value: Str
    partial: bool
    file: str
    line: int
    options: bool
    root: Ref | None  # the module of the application it is set on, None when it cannot be paired with one


@dataclass(slots=True)
class Facts:
    path: str
    src: Src
    classes: dict[str, Klass] = field(default_factory=dict)
    controllers: dict[str, ControllerInfo] = field(default_factory=dict)
    modules: dict[str, ModuleInfo] = field(default_factory=dict)
    prefixes: list[Prefix] = field(default_factory=list)
    creates: list[Create] = field(default_factory=list)
    versioning: list[tuple[int, str]] = field(default_factory=list)  # (line, kind) of each enableVersioning call: URI, HEADER, MEDIA_TYPE or CUSTOM
    non_http: set[str] = field(default_factory=set)  # variables that keep an application context or a microservice: no HTTP prefix is set on them
    stray_controllers: list[int] = field(default_factory=list)  # lines of a `controllers: [...]` that is in no module (a function that returns a dynamic module)
    consts: dict[str, tuple[int, int] | None] = field(default_factory=dict)
    router_modules: list[int] = field(default_factory=list)  # lines of RouterModule.register
    bootstrap: bool = False
    nest: dict[str, str] = field(default_factory=dict)  # a local name bound to @nestjs/common to what it is there
    other_refs: frozenset[str] = frozenset()


@dataclass(slots=True)
class _Composed:
    controller: Ref
    member: Member
    route: Route
    registered: bool
    reachable: bool
    unknown: tuple[tuple[str, str, int], ...]


class _Table(common.TableBase):
    NOUN = "controllers, modules and the prefix"

    def __init__(self, adapter: "NestAdapter", sha: str) -> None:
        super().__init__(adapter, sha)
        self._composed: list[_Composed] | None = None
        self.entry_files: set[str] = set()
        self.registrations: dict[Ref, list[tuple[Ref, int]]] = {}  # controller to the (module, line of its entry in `controllers`) that list it
        self.reachable: set[Ref] | None = None
        self.imports: dict[Ref, list[Ref]] = {}  # module to the modules it imports
        self.roots: list[Ref] = []  # the modules the applications are created with
        self.reach_sets: dict[Ref, set[Ref]] = {}  # a root to the modules reachable from it
        self.create_root: dict[tuple[str, int], Ref] = {}
        self.uri_versioning = False
        self._build()

    def extract(self, path: str, parsed: Any) -> Facts:
        src = Src(parsed, path)
        facts = Facts(path, src)
        facts.consts = build_consts(src)
        core: dict[str, str] = {}
        namespaces: set[str] = set()
        for local, b in src.bindings().items():
            spec, imported = self.upstream(path, b.spec, b.imported) if b.spec.startswith(".") else (b.spec, b.imported)  # one hop of re-export
            if spec == COMMON and imported not in ("*", "*cjs", "default"):
                facts.nest[local] = imported
            elif spec == CORE and imported not in ("*", "*cjs", "default"):
                core[local] = imported
            if spec == COMMON and imported in ("*", "*cjs"):
                namespaces.add(local)
        for d in parsed.declarations:
            if d.kind != "class":
                continue
            k = self._klass(src, d, facts, namespaces)
            if k is None:
                continue
            facts.classes[k.name] = k
            ctl = next((x for x in k.decos if x.nest and x.name == "Controller"), None)
            if ctl is not None:
                facts.controllers[k.name] = ControllerInfo(k, ctl, [], False)
            mod = next((x for x in k.decos if x.nest and x.name == "Module"), None)
            if mod is not None:
                facts.modules[k.name] = self._module(src, k, mod)
        decl_of = lambda i: next(((d.start_tok, d.end_tok) for d in parsed.declarations if d.start_tok <= i <= d.end_tok), None)  # noqa: E731
        for i in range(src.n):
            if src.w(i) == "setGlobalPrefix" and src.p(i - 1) in (".", "?.") and src.p(i + 1) == "(":
                args = src.args(i + 1)
                if args:
                    recv = src.t[i - 2].value if src.w(i - 2) is not None else None
                    facts.prefixes.append(Prefix(src.line(i), args[0][0], args[0][1], recv, decl_of(i), len(args) > 1))
                facts.bootstrap = True
            elif src.w(i) == "create" and src.p(i - 1) == "." and src.w(i - 2) is not None and src.p(i + 1) == "(":
                holder = src.t[i - 2].value
                if core.get(holder) == "NestFactory":
                    args = src.args(i + 1)
                    j = i - 4  # `const app = await NestFactory.create(...)`
                    if src.w(j) == "await":
                        j -= 1
                    var = None
                    if src.p(j) == "=":
                        var = src.t[j - 1].value if src.w(j - 1) is not None and src.p(j - 2) != ":" else (src.t[j - 3].value if src.p(j - 2) == ":" and src.w(j - 3) is not None else None)
                    facts.creates.append(Create(i, src.line(i), args[0] if args else None, var, decl_of(i)))
                    facts.bootstrap = True
            elif src.w(i) in ("createApplicationContext", "createMicroservice") and src.p(i - 1) == "." and src.w(i - 2) is not None and core.get(src.t[i - 2].value) == "NestFactory":
                j = i - 4 - (1 if src.w(i - 4) == "await" else 0)
                if src.p(j) == "=" and src.w(j - 1) is not None:
                    facts.non_http.add(src.t[j - 1].value)
            elif src.w(i) == "enableVersioning" and src.p(i - 1) in (".", "?.") and src.p(i + 1) == "(":
                kind = "URI"  # the default
                for x, y in src.args(i + 1)[:1]:
                    if src.p(x) == "{":
                        for kx, ky in src.args(x):
                            if src.t[kx].kind in ("ident", "string") and src.t[kx].value.strip("'\"") == "type" and src.p(kx + 1) == ":":
                                last = src.t[ky].value
                                kind = last if last in ("URI", "HEADER", "MEDIA_TYPE", "CUSTOM") else "URI"  # not readable: counts as a segment (partial)
                facts.versioning.append((src.line(i), kind))
                facts.bootstrap = True
            elif src.w(i) == "register" and src.p(i - 1) == "." and src.w(i - 2) is not None and src.p(i + 1) == "(" and core.get(src.t[i - 2].value) == "RouterModule":
                facts.router_modules.append(src.line(i))
        spans = [(k.first, k.body[1]) for k in facts.classes.values() if k.name in facts.modules]
        for i in range(src.n):
            if src.w(i) == "controllers" and src.p(i + 1) == ":" and src.p(i + 2) == "[" and src.p(i - 1) in ("{", ",") and not any(a <= i <= b for a, b in spans):
                facts.stray_controllers.append(src.line(i))
        refs: set[str] = set()
        imports = {n for rec in parsed.imports for n in range(rec.start_line, rec.line + 1)}
        for i in range(src.n):
            t = src.t[i]
            if t.kind == "ident" and src.p(i - 1) not in (".", "?.") and t.line not in imports and not src.in_declaration(i) and src.module_scope(i):
                refs.add(t.value)
        facts.other_refs = frozenset(refs)
        return facts

    # ---- classes

    def _decos(self, src: Src, k: int, facts: Facts, namespaces: set[str]) -> tuple[list[Deco], int]:
        out: list[Deco] = []
        while src.p(k) == "@" and src.w(k + 1) is not None:
            j = k + 1
            chain = [src.t[j].value]
            while src.p(j + 1) == "." and src.w(j + 2) is not None:
                chain.append(src.t[j + 2].value)
                j += 2
            args: list[tuple[int, int]] = []
            last = j
            if src.p(j + 1) == "(":
                args = src.args(j + 1)
                last = src.match(j + 1)
            nest = False
            name = chain[-1]
            if len(chain) == 1 and chain[0] in facts.nest:
                name, nest = facts.nest[chain[0]], True
            elif len(chain) == 2 and chain[0] in namespaces:
                nest = True
            out.append(Deco(name, src.line(k), args, k, last, nest))
            k = last + 1
        return out, k

    def _klass(self, src: Src, d: Any, facts: Facts, namespaces: set[str]) -> Klass | None:
        decos, k = self._decos(src, d.start_tok, facts, namespaces)
        while k <= d.end_tok and src.w(k) != "class":
            k += 1
        if src.w(k) != "class":
            return None
        line = src.line(k)
        name = src.t[k + 1].value if src.w(k + 1) is not None and src.w(k + 1) not in ("extends", "implements") else d.name
        j = k + 1
        extends = None
        body = -1
        while j <= d.end_tok:
            if src.w(j) == "extends" and extends is None and src.w(j + 1) is not None:
                extends = src.t[j + 1].value
            if src.p(j) == "{" and not (src.p(j - 1) in ("<", ",", ":", "=")):
                body = j
                break
            if src.is_open(j):
                j = src.match(j)
            j += 1
        if body < 0:
            return None
        close = src.match(body)
        members = self._members(src, body, close, facts, namespaces)
        return Klass(src.path, name, decos, d.start_tok, d.start, d.end, (body, close), members, extends, line)

    def _members(self, src: Src, body: int, close: int, facts: Facts, namespaces: set[str]) -> list[Member]:
        out: list[Member] = []
        j = body + 1
        while j < close:
            if src.p(j) == ";":
                j += 1
                continue
            decos, k = self._decos(src, j, facts, namespaces)
            first = decos[0].first if decos else j
            while src.w(k) in MODIFIERS and src.p(k + 1) not in ("(", ":", "=", ";", "?", "!", "<") and k < close:
                k += 1
            if src.p(k) == "*":
                k += 1
            name_idx = k
            if src.p(k) == "[":
                k = src.match(k)
            elif src.t[k].kind not in ("ident", "string", "number"):
                j = k + 1  # something this does not understand: step over it
                continue
            name = src.t[name_idx].value if src.t[name_idx].kind == "ident" else src.text(name_idx, k)
            k += 1
            while src.p(k) in ("?", "!"):
                k += 1
            if src.p(k) == "<":
                depth = 0
                while k < close:
                    if src.p(k) == "<":
                        depth += 1
                    elif src.p(k) == ">":
                        depth -= 1
                        if depth == 0:
                            break
                    k += 1
                k += 1
            if src.p(k) == "(":
                params_close = src.match(k)
                params = src.args(k)
                bo = src.body_open(params_close + 1)
                last = src.match(bo) if bo is not None else self._property_end(src, params_close, close)
                kind = "constructor" if name == "constructor" else "method"
                props: dict[str, int] = {}
                if kind == "constructor":
                    for a, b in params:
                        x = a
                        while src.p(x) == "@":  # a decorator of the parameter: `@Inject(TOKEN)`
                            x = (src.match(x + 2) if src.p(x + 2) == "(" else x + 1) + 1
                        mods = False
                        while src.w(x) in PROPERTY_MODIFIERS:
                            mods = True
                            x += 1
                        if mods and src.w(x) is not None:
                            props[src.t[x].value] = a
                out.append(Member(kind, name, decos, first, last, src.line(first), src.line(last), params, props))
            else:
                last = self._property_end(src, k, close)
                out.append(Member("property", name, decos, first, last, src.line(first), src.line(last)))
            j = last + 1
        return out

    @staticmethod
    def _property_end(src: Src, i: int, close: int) -> int:
        j = i
        while j < close:
            if src.is_open(j):
                j = src.match(j) + 1
                continue
            if src.p(j) == ";":
                return j
            if j + 1 < close and src.t[j + 1].newline_before and src.p(j + 1) not in (".", "(", "=", ",", "?", ":", "+", "-", "*", "|", "&"):
                return j
            j += 1
        return min(j, close - 1)

    def _module(self, src: Src, k: Klass, deco: Deco) -> ModuleInfo:
        """A module: the `controllers` and `imports` lists and the providers written as objects, wherever in the class the object is (the decorator, or a static
        method that returns a dynamic module), and the names the class body spells."""
        info = ModuleInfo(k, deco, [], [], [])
        for i in range(k.first, k.body[1] + 1):
            if src.t[i].kind not in ("ident", "string") or src.p(i + 1) != ":" or src.p(i + 2) != "[" or src.p(i - 1) not in ("{", ","):
                continue
            key = src.t[i].value.strip("'\"")
            if key not in ("controllers", "imports", "providers"):
                continue
            for x, y in src.args(i + 2):
                if key == "providers":
                    if src.p(x) == "{":
                        refs = src.refs(x, y)
                        info.providers.append((refs, any(r.startswith("APP_") for r in refs)))
                    continue
                if src.p(x) == "." and src.p(x + 1) == ".":
                    info.opaque.append(src.line(x))
                    continue
                name = self._entry_name(src, x, y)
                if name is None:
                    info.opaque.append(src.line(x))
                else:
                    (info.controllers if key == "controllers" else info.imports).append((name, src.line(x)))
        info.body_refs = src.refs(deco.last + 1, k.body[1])
        return info

    @staticmethod
    def _entry_name(src: Src, a: int, b: int) -> str | None:
        """The class an entry of `controllers` or `imports` names: `Users`, `Users.forRoot(...)`, `forwardRef(() => Users)`."""
        if src.w(a) is None:
            return None
        if src.w(a) == "forwardRef" and src.p(a + 1) == "(":
            inner = src.args(a + 1)
            if inner:
                x, y = inner[0]
                for k in range(x, y + 1):
                    if src.p(k) == "=>" and src.w(k + 1) is not None:
                        return src.t[k + 1].value
            return None
        return src.t[a].value

    # ---- resolving names to classes

    def export_class(self, path: str, name: str, depth: int = 0) -> Ref | None:
        facts = self.facts_of(path)
        if facts is None or depth > self.DEPTH:
            return None
        for e in facts.src.exports():
            if e.name != name:
                continue
            if e.source is not None:
                target = self.module_file(path, e.source)
                if target is not None:
                    got = self.export_class(target, e.source_name or e.name, depth + 1)
                    if got is not None:
                        return got
            elif e.local is not None and e.local in facts.classes:
                return (path, e.local)
        for spec in facts.src.parsed.export_stars:
            target = self.module_file(path, spec)
            if target is not None:
                got = self.export_class(target, name, depth + 1)
                if got is not None:
                    return got
        return None

    def resolve_class(self, path: str, name: str) -> Ref | None:
        facts = self.facts_of(path)
        if facts is None:
            return None
        if name in facts.classes:
            return (path, name)
        b = facts.src.bindings().get(name)
        if b is None or b.imported in ("*", "*cjs"):
            return None
        target = self.module_file(path, b.spec)
        if target is None:
            return None
        return self.export_class(target, "default" if b.imported == "default" else b.imported)

    def alternatives(self, facts: Facts, args: list[tuple[int, int]]) -> list[tuple[Str, bool]]:
        """The paths a decorator or a call stands for: nothing (the empty path), a string, a template, a constant, a list of them."""
        src = facts.src
        if not args:
            return [(Str(), False)]
        a, b = src.strip(*args[0])
        if src.p(a) == "[" and src.match(a) == b:
            out: list[tuple[Str, bool]] = []
            for x, y in src.args(a):
                out += self.alternatives(facts, [(x, y)])
            return out or [(Str(), False)]
        if src.p(a) == "{" and src.match(a) == b:  # `{ path: "x", version: "1" }`
            for x, y in src.args(a):
                if src.t[x].kind in ("ident", "string") and src.t[x].value.strip("'\"") == "path" and src.p(x + 1) == ":":
                    return self.alternatives(facts, [(x + 2, y)])
            return [(Str(), False)]
        value = src.eval(a, b, self)
        pieces = path_pieces(value) if value.value or value.unknown else [(Str(), False)]
        return [(Str(p.value.lstrip("/") and p.value, p.unknown), partial) for p, partial in pieces]

    # ---- the table

    def _build(self) -> None:
        self.candidates |= self.adapter.grep(self, PATTERNS)
        for path in sorted(self.candidates):
            self.facts_of(path)
        loaded = sorted((f for f in self.facts.values() if f is not None), key=lambda f: f.path)
        for f in loaded:
            for name, c in f.controllers.items():
                c.paths = self.alternatives(f, c.deco.args)
                c.versioned = self._versioned(f, c.deco)
                if c.klass.extends is not None:
                    self._inherits(f, c)
            for line in f.stray_controllers:
                self.unresolved = True
                self.incomplete(f"{f.path}:{line} lists controllers outside a module (a function that returns a dynamic module)", f.path)
                record_quoted(self.target, "mount_unresolved", f"{f.path}:{line}",
                              "a list of controllers outside every module class is not read, so a controller that looks unmounted may be mounted by it", f.src.parsed.line_text(line).strip(), once=True)
            for line in f.router_modules:
                self.unresolved = True
                self.incomplete(f"{f.path}:{line} registers module routes with RouterModule: the paths of its controllers are not read", f.path)
                record_quoted(self.target, "mount_unresolved", f"{f.path}:{line}",
                              "RouterModule.register gives the controllers of its modules a path of their own, which is not read, so the paths and what is mounted may differ",
                              f.src.parsed.line_text(line).strip(), once=True)
        for f in loaded:
            for name, m in f.modules.items():
                for cname, line in m.controllers:
                    ref = self.resolve_class(f.path, cname)
                    if ref is not None:
                        self.registrations.setdefault(ref, []).append(((f.path, name), line))
                for line in m.opaque:
                    self.unresolved = True
                    self.incomplete(f"{f.path}:{line} lists a module or a controller this does not name (a spread or an expression)", f.path)
                    record_quoted(self.target, "mount_unresolved", f"{f.path}:{line}",
                                  "an entry of the module's lists is not a class this can name, so a controller that looks unmounted may be mounted here",
                                  f.src.parsed.line_text(line).strip(), once=True)
        self.uri_versioning = any(kind == "URI" for f in loaded for _line, kind in f.versioning)
        self.reachable = self._reachable(loaded)
        self.entry_files = {f.path for f in loaded if f.controllers or f.modules or f.bootstrap}

    def _versioned(self, f: Facts, deco: Deco) -> bool:
        if not deco.args or f.src.p(deco.args[0][0]) != "{":
            return False
        return any(f.src.t[x].kind in ("ident", "string") and f.src.t[x].value.strip("'\"") == "version" for x, _y in f.src.args(deco.args[0][0]))

    def _inherits(self, f: Facts, c: ControllerInfo) -> None:
        base = self.resolve_class(f.path, c.klass.extends or "")
        if base is None:
            return
        bf = self.facts_of(base[0])
        k = bf.classes.get(base[1]) if bf is not None else None
        if k is not None and any(d.nest and d.name in VERBS for m in k.members for d in m.decos):
            self.unresolved = True
            self.incomplete(f"{f.path}:{c.klass.line} extends {base[1]}, whose routes it inherits", f.path)
            record_quoted(self.target, "routes_unread", f"{f.path}:{c.klass.line}",
                          "the controller extends a class that declares routes; the routes it inherits are not read", base[1], once=True)

    def _reachable(self, loaded: list[Facts]) -> set[Ref]:
        """The modules reachable from the ones the application is created with (every module nobody imports when no
        `NestFactory.create` names one)."""
        modules = {(f.path, n) for f in loaded for n in f.modules}
        imports: dict[Ref, list[Ref]] = {}
        for f in loaded:
            for n, m in f.modules.items():
                for cname, line in m.imports:
                    ref = self.resolve_class(f.path, cname)
                    if ref is not None and ref in modules:
                        imports.setdefault((f.path, n), []).append(ref)
                    elif ref is None and self._is_repo_name(f, cname):
                        self.unresolved = True
                        self.incomplete(f"{f.path}:{line} imports {cname}, which this does not resolve to a module", f.path)
                        record_quoted(self.target, "mount_unresolved", f"{f.path}:{line}",
                                      "a module it imports could not be resolved to a class of the repository, so a controller that looks unmounted may be mounted there",
                                      f.src.parsed.line_text(line).strip(), once=True)
        roots: list[Ref] = []
        named = False
        for f in loaded:
            for c in f.creates:
                named = True
                ref = self.resolve_class(f.path, f.src.t[c.arg[0]].value) if c.arg is not None and f.src.w(c.arg[0]) is not None else None
                if ref is not None and ref in modules:
                    roots.append(ref)
                    self.create_root[(f.path, c.tok)] = ref
                else:
                    self.unresolved = True
                    self.incomplete(f"{f.path}:{c.line} creates the application with something this does not resolve to a module", f.path)
                    record_quoted(self.target, "mount_unresolved", f"{f.path}:{c.line}",
                                  "the module the application is created with could not be resolved, so which controllers are mounted was not established",
                                  f.src.parsed.line_text(c.line).strip(), once=True)
        if not named:
            imported = {r for refs in imports.values() for r in refs}
            roots = sorted(modules - imported)
        self.imports = imports
        roots = list(dict.fromkeys(roots))
        self.roots = roots
        if self.unresolved and named and not roots:
            return set(modules)  # nothing to say about what is reachable: every module counts as mounted
        seen: set[Ref] = set()
        for root in roots:
            reach: set[Ref] = set()
            todo = [root]
            while todo:
                r = todo.pop()
                if r in reach:
                    continue
                reach.add(r)
                todo.extend(imports.get(r, ()))
            self.reach_sets[root] = reach
            seen |= reach
        return seen

    def _is_repo_name(self, f: Facts, name: str) -> bool:
        """Whether a name an `imports` list holds is declared in the file or imported from a file of the repository (an
        import of a package, `TypeOrmModule.forRoot()`, is not a module of ours)."""
        if name in f.classes:
            return True
        b = f.src.bindings().get(name)
        return b is not None and self.module_file(f.path, b.spec) is not None

    def _pair(self, f: Facts, p: Prefix) -> Ref | None:
        """The module of the application a `setGlobalPrefix` call is on: the `NestFactory.create` the receiver variable keeps, else the only one in the same
        declaration; None when it cannot be told (a helper that receives the application)."""
        found = [c for c in f.creates if p.recv is not None and c.var == p.recv]
        if not found and p.decl is not None:
            found = [c for c in f.creates if c.decl == p.decl]
        return self.create_root.get((f.path, found[0].tok)) if len(found) == 1 else None

    def prefixes(self) -> list[_Pfx]:
        """Every global prefix, with the module of the application it is set on."""
        out: list[_Pfx] = []
        for f in sorted((f for f in self.facts.values() if f is not None), key=lambda x: x.path):
            for p in f.prefixes:
                if p.recv in f.non_http:
                    continue
                root = self._pair(f, p)
                for piece, partial in self.alternatives(f, [(p.a, p.b)]):
                    out.append(_Pfx(piece, partial, f.path, p.line, p.options, root))
        return out

    def prefixes_for(self, module: Ref, prefixes: list[_Pfx]) -> list[_Pfx | None]:
        """The prefixes a controller of `module` is served under: of each application that serves the module, those set on it and the ones that cannot be paired
        with an application (None: no prefix)."""
        unpaired = [p for p in prefixes if p.root is None]
        out: list[_Pfx | None] = []
        serving = [r for r in self.roots if module in self.reach_sets.get(r, ())]
        for r in serving or [None]:
            mine = [p for p in prefixes if r is not None and p.root == r] + unpaired
            out += mine or [None]
        return list(dict.fromkeys(out))

    def composed(self) -> list[_Composed]:
        if self._composed is not None:
            return self._composed
        out: list[_Composed] = []
        prefixes = self.prefixes()
        for f in sorted((f for f in self.facts.values() if f is not None), key=lambda x: x.path):
            for name, c in f.controllers.items():
                ref = (f.path, name)
                regs = self.registrations.get(ref, [])
                registered = bool(regs)
                for m in c.klass.members:
                    for d in m.decos:
                        if not (d.nest and d.name in VERBS):
                            continue
                        method_alts = self.alternatives(f, d.args)
                        versioned = (c.deco.line if c.versioned else next((x.line for x in m.decos if x.nest and x.name == "Version"), 0)) if self.uri_versioning else 0
                        for cpath, cpartial in c.paths:
                            for mpath, mpartial in method_alts:
                                for reg_mod, reg_line in (regs or [(("", ""), 0)]):
                                    for pfx in self.prefixes_for(reg_mod, prefixes):
                                        out.append(self._compose(f, c, m, d, ref, pfx, cpath, mpath, versioned, reg_mod, reg_line, cpartial or mpartial, registered,
                                                                 registered and reg_mod in (self.reachable or set())))
        self._composed = [c for c in out if c is not None]
        return self._composed

    def _compose(self, f: Facts, c: ControllerInfo, m: Member, d: Deco, ref: Ref, pfx: _Pfx | None, cpath: Str, mpath: Str, versioned: int, reg_mod: Ref, reg_line: int,
                 partial: bool, registered: bool, reachable: bool) -> _Composed | None:
        unknown: tuple[tuple[str, str, int], ...] = ()
        version = Str()
        if versioned:
            version = Str("/<unknown:version>", (("version", f.path, versioned),))
        pre = pfx.value if pfx is not None else Str()
        if pfx is not None:
            partial = partial or pfx.partial
            if pfx.options:
                unknown += (("exclude", pfx.file, pfx.line),)
            if pfx.root is None and len(self.roots) > 1:
                unknown += (("an application this cannot pair the prefix with", pfx.file, pfx.line),)
        seg = lambda s: Str(("/" + s.value.strip("/")) if s.value.strip("/") else "", s.unknown)  # noqa: E731
        full = seg(pre) + version + seg(cpath) + seg(mpath)
        text = normalize_path(full.value or "/")
        method = VERBS[d.name]
        pairs: list[tuple[str, int]] = [(f.path, d.line)]
        if cpath.value or cpath.unknown:
            pairs.append((f.path, c.deco.line))
        if registered and reg_mod[0]:
            pairs.append((reg_mod[0], reg_line))
        if pfx is not None and (pre.value or pre.unknown):
            pairs.append((pfx.file, pfx.line))
        evidence: list[Evidence] = []
        for n, (p, line) in enumerate(pairs):
            ev = self.evidence(p, line, f"route:{method} {text}:{f.path}" if n == 0 else None)
            if ev is None:
                return None
            evidence.append(ev)
        route = Route(
            repo=self.adapter.repo, kind="api_route", method=method, path=text, file=f.path, handler=m.name, target=f.path,
            module=self.adapter.module_of(f.path, self.sha), evidence=tuple(evidence), partial=partial or bool(full.unknown) or bool(versioned) or bool(unknown),
        )
        return _Composed(ref, m, route, registered, reachable, tuple(dict.fromkeys(full.unknown + unknown)))

    def routes(self) -> list[Route]:
        return [c.route for c in self.composed() if c.registered and c.reachable]


class NestAdapter(common.Shared):
    """NestJS entry points of one repository, built on its JavaScript adapter. Use one adapter per run, as the language adapters are."""

    kind = "nestjs"
    WHAT = "@Controller or @Module"

    def __init__(self, repo: str, language: Any, *, service_marker: str | None = None, record_to: Any = None) -> None:
        super().__init__(repo, language, service_marker=service_marker, record_to=record_to)
        self._tables: dict[str, _Table] = {}

    @classmethod
    def from_config(cls, repo: Any, language: Any, *, record_to: Any = None) -> "NestAdapter":
        return cls(repo.name, language, service_marker=getattr(repo, "service_marker", None), record_to=record_to)

    def table(self, sha: str) -> _Table:
        if sha not in self._tables:
            self._tables[sha] = _Table(self, sha)
        return self._tables[sha]

    def changes_hidden_by(self, old: Any, new: Any) -> bool:
        """The routes the head shows are wrong, not only missing, when the file that carried the global prefix at the base could not be read at the head."""
        return bool({f.path for f in old.facts.values() if f is not None and f.prefixes} & new.holes)

    def is_entry_file(self, path: str, sha: str) -> bool:
        return path in self.table(sha).entry_files

    def structural_names(self, path: str, sha: str) -> frozenset[str]:
        """The controllers and modules a file declares: whoever imports one registers it, which the route table already knows."""
        facts = self.table(sha).facts_of(path)
        return frozenset(facts.controllers) | frozenset(facts.modules) if facts is not None else frozenset()

    def unmounted(self, sha: str) -> list[tuple[str, str]]:
        table = self.table(sha)
        return sorted({c.controller for c in table.composed() if not (c.registered and c.reachable)})

    # ---- what a chain reaches

    def entry_points_reached(self, chains: Iterable[Any], sha: str | None = None) -> list[Reached]:
        hits: list[common.Hit] = []
        seen = list(chains)
        for chain in seen:
            table = self.table(chain.sha)
            for comp, hops in self._reached(table, chain):
                route = comp.route
                if not (comp.registered and comp.reachable):
                    hits.append(common.Hit(route, (), True, (), chain, (), unmounted=comp.controller))
                    continue
                hits.append(common.Hit(route, self.chain_of(hops, route), chain.complete and table.complete, tuple(chain.reasons) + tuple(table.reasons), chain, comp.unknown))
        return self.assemble(hits, any(self.table(c.sha).unresolved for c in seen))

    def _reached(self, table: _Table, chain: Any) -> list[tuple[_Composed, tuple[Evidence, ...]]]:
        facts = table.facts_of(chain.entry)
        if facts is None:
            return []
        reach = chain.reach
        src = facts.src
        classes = set(facts.controllers) | set(facts.modules)
        names = set(reach.locals) - classes
        whole = not reach.complete or src.parsed.uses_dynamic_access()
        if facts.bootstrap and (names & facts.other_refs):
            whole = True
        hit_members: dict[str, set[str]] = {}  # controller class to the member names hit
        whole_classes: set[str] = set()
        modules_hit: set[str] = set()
        added, removed, other_whole = self._changed_lines(chain, facts) if chain.edge is None else ([], [], False)
        if other_whole or (facts.bootstrap and (added or removed)):
            whole = True
        for cname, key in removed:
            if key is None:
                whole_classes.add(cname)
            else:
                hit_members.setdefault(cname, set()).add(key)
        for line in added:
            for cname, c in facts.controllers.items():
                k = c.klass
                if not (k.start <= line <= k.end):
                    continue
                m = k.member_at(line)
                if m is None or not self._touch(src, m, line, None, hit_members.setdefault(cname, set())):
                    whole_classes.add(cname)
            for mname, mod in facts.modules.items():
                if mod.klass.start <= line <= mod.klass.end:
                    modules_hit.add(mname)
        for cname, c in facts.controllers.items():
            k = c.klass
            for i in range(k.body[0] + 1, k.body[1]):
                if src.t[i].kind != "ident" or src.t[i].value not in names or src.p(i - 1) in (".", "?."):
                    continue
                m = k.member_at(src.line(i))
                if m is None or not self._touch(src, m, src.line(i), i, hit_members.setdefault(cname, set())):
                    whole_classes.add(cname)
            for d in k.decos:
                if any(src.t[i].kind == "ident" and src.t[i].value in names and src.p(i - 1) not in (".", "?.") for i in range(d.first, d.last + 1)):
                    whole_classes.add(cname)
        for mname, mod in facts.modules.items():
            d = mod.deco
            if any(src.t[i].kind == "ident" and src.t[i].value in names and src.p(i - 1) not in (".", "?.") for i in range(d.first, d.last + 1)):
                # a name the module's decorator spells: a controller it lists is reached by its own file, a plain class provider is not followed
                if any(cname in names for cname, _line in mod.controllers):
                    modules_hit.add(mname)
            for refs, is_global in mod.providers:  # a provider written as an object (`{ provide: APP_GUARD, useClass: Guard }`)
                if refs & names:
                    if is_global:
                        whole = True  # a global guard, interceptor, pipe or filter applies to every route the application serves
                    else:
                        modules_hit.add(mname)
            if mod.body_refs & names:  # `configure(consumer)`, a constructor: what the module does for its controllers
                modules_hit.add(mname)
        for cname in list(hit_members):
            self._close(src, facts.controllers[cname].klass, hit_members[cname])
        out: list[tuple[_Composed, tuple[Evidence, ...]]] = []
        hit_modules = self._modules_below(table, {(facts.path, n) for n in modules_hit})
        for c in table.composed():
            ref = c.controller
            in_file = ref[0] == chain.entry
            via_module = any(m in hit_modules for m, _l in table.registrations.get(ref, ()))
            via_evidence = any(e.path == chain.entry for e in c.route.evidence) and not in_file
            if not (in_file or via_module or via_evidence or (whole and c.registered and c.reachable)):
                continue
            hit = (whole and c.registered and c.reachable) or via_module  # what the application does for every request is done for the controllers it serves
            if in_file:
                if ref[1] in whole_classes or c.member.name in hit_members.get(ref[1], set()):
                    hit = True
            if via_evidence and not via_module and not hit:
                hit = self._evidence_line_changed(c, chain, added)
            if not hit:
                continue
            hops = chain.hops
            if chain.direct:
                spans = [(c.member.start, c.member.end)] if in_file else []
                hops = self.direct_hop(chain, c.route, spans)
                if not hops:
                    hops = chain.hops
            out.append((c, tuple(hops)))
        return out

    @staticmethod
    def _evidence_line_changed(c: _Composed, chain: Any, added: Iterable[int]) -> bool:
        lines = set(added)
        return any(e.path == chain.entry and e.line in lines for e in c.route.evidence)

    def _modules_below(self, table: _Table, modules: set[Ref]) -> set[Ref]:
        """The modules a changed module reaches: itself and the modules it imports, to a fixed point."""
        imports = getattr(table, "imports", {})
        seen: set[Ref] = set()
        todo = list(modules)
        while todo:
            r = todo.pop()
            if r not in seen:
                seen.add(r)
                todo.extend(imports.get(r, ()))
        return seen

    @staticmethod
    def _touch(src: Src, m: Member, line: int, tok: int | None, into: set[str]) -> bool:
        """A touch inside a member of a controller: a method is hit by name; a parameter property of the constructor and a property of
        the class are hit by the property (`.users`: the methods that use `this.users`). False when the touch cannot be tied to a
        member that is narrower than the class (the constructor's body, a parameter that is not a property): the class is hit."""
        if m.kind == "method":
            into.add(m.name)
            return True
        if m.kind == "property":
            into.add("." + m.name)
            return True
        for a, b in m.params:  # the constructor: the parameter the touch is in
            inside = a <= tok <= b if tok is not None else src.line(a) <= line <= src.line(b)
            if not inside:
                continue
            x = a
            while src.p(x) == "@":
                x = (src.match(x + 2) if src.p(x + 2) == "(" else x + 1) + 1
            while src.w(x) in PROPERTY_MODIFIERS:
                x += 1
            if src.w(x) is not None and src.t[x].value in m.props:
                into.add("." + src.t[x].value)
                return True
        return False

    @staticmethod
    def _close(src: Src, k: Klass, hit: set[str]) -> None:
        """Members that use a property that was reached (`this.users`) or call a member that was reached (`this.load()`), to a fixed point."""
        grown = True
        while grown:
            grown = False
            names = {x[1:] if x.startswith(".") else x for x in hit}
            for m in k.members:
                if m.name in hit or m.kind != "method":
                    continue
                if any(src.w(i) == "this" and src.p(i + 1) in (".", "?.") and src.w(i + 2) in names for i in range(m.first, m.last + 1)):
                    hit.add(m.name)
                    grown = True

    def _changed_lines(self, chain: Any, facts: Facts) -> tuple[list[int], list[tuple[str, str | None]], bool]:
        """What the changed lines touch: (the lines of the file at the head that were added, what the removed lines were part of as
        (controller, member key) pairs where a key of None is the whole class, and whether some changed line is outside every class and
        could change anything the file does when it loads). A line that holds no code (a comment, a blank line) changes nothing."""
        added: list[int] = []
        removed: list[tuple[str, str | None]] = []
        other = False
        before = self.language.parsed(chain.pre_sha, chain.before_path) if chain.pre_sha else None
        pre = None
        if before is not None and not before.problems:
            pre = self.table(chain.pre_sha).extract(chain.before_path, before)
        class_lines = {n for c in list(facts.controllers.values()) for n in range(c.klass.start, c.klass.end + 1)}
        class_lines |= {n for m in facts.modules.values() for n in range(m.klass.start, m.klass.end + 1)}
        import_lines = {n for rec in facts.src.parsed.imports for n in range(rec.start_line, rec.line + 1)}
        for s in chain.changed:
            for n in s.added_lines:
                if n not in facts.src.parsed.code_lines:
                    continue
                added.append(n)
                if n not in class_lines and n not in import_lines:
                    other = other or not self._declared(facts, n)
            for n in s.removed_lines:
                if pre is None:
                    other = True
                    continue
                if n not in pre.src.parsed.code_lines:
                    continue
                k = next((c.klass for c in pre.controllers.values() if c.klass.start <= n <= c.klass.end), None)
                if k is None:
                    k2 = next((m.klass for m in pre.modules.values() if m.klass.start <= n <= m.klass.end), None)
                    if k2 is not None:
                        twin = facts.modules.get(k2.name)
                        if twin is not None:
                            added.append(twin.klass.start)
                    elif not any(rec.start_line <= n <= rec.line for rec in pre.src.parsed.imports):
                        other = other or not self._declared(pre, n)
                    continue
                twin_c = facts.controllers.get(k.name)
                if twin_c is None:
                    continue  # the controller is gone: its routes are removed routes, which `routes_changed` says
                m = k.member_at(n)
                if m is None:
                    removed.append((k.name, None))
                    continue
                if not any((x.kind, x.name) == (m.kind, m.name) for x in twin_c.klass.members):
                    continue  # the member is gone: its routes are removed routes
                keys: set[str] = set()
                if self._touch(pre.src, m, n, None, keys):
                    removed += [(k.name, key) for key in keys]
                else:
                    removed.append((k.name, None))
        return added, removed, other

    @staticmethod
    def _declared(facts: Facts, n: int) -> bool:
        """Whether line n belongs to a top-level declaration (a helper function, a constant): the closure of the declarations says what it reaches."""
        return any(d.start <= n <= d.end for d in facts.src.parsed.declarations)


__all__ = ["NestAdapter"]
