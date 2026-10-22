"""The route table of a Spring application at one commit: the controllers (`@RestController`, `@Controller`, a type with a class level
`@RequestMapping`), their handler methods (`@GetMapping`, `@PostMapping`, `@PutMapping`, `@DeleteMapping`, `@PatchMapping`,
`@RequestMapping(method = ...)`), and the full path of each route: the context path of the application configuration, the class level path
and the method level path, joined the way Spring joins them. Handlers and a class level mapping that a controller inherits from a supertype
of the repository (an interface, a base class) are routes of the controller.

A path is a literal, a constant of the project, a Kotlin template, a sum of those, an array of them, or a property placeholder; what cannot
be evaluated is written `<unknown:SOURCE>`, the route is `partial` and nothing is guessed. A mapping may have several paths and several
methods: one route for each pair. No method is `ANY`.

What it does not do (limits, docs/briefs/M16-spring.md): component scanning (a controller outside the packages the application scans is
taken as mounted), request conditions (`params`, `headers`, `consumes`, `produces`), a meta annotation of a meta annotation, functional
routes (`RouterFunction`), WebFlux router DSL, Jakarta REST (`@Path`), a servlet registered by hand.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field, replace
from typing import Any

from reachmap.adapters import Route, normalize_path, third_party
from reachmap.adapters.base import citable, dedupe, evidence_at, record_quoted
from reachmap.adapters.jvm import is_jvm_path
from reachmap.adapters.jvm.syntax import Annotation, Parsed, Unit
from reachmap.adapters.spring import records  # noqa: F401  (registers the incompleteness kinds of the adapter)
from reachmap.adapters.spring.expr import Evaluator, Str, split_items
from reachmap.adapters.spring.props import PATH_KEYS, Config, Entry, read_config
from reachmap.incomplete import record
from reachmap.model import Evidence, Read

__all__ = ["Composed", "FileFacts", "Handler", "Table", "TypeFacts", "spring_name"]

MAPPING_METHODS = {"GetMapping": "GET", "PostMapping": "POST", "PutMapping": "PUT", "DeleteMapping": "DELETE", "PatchMapping": "PATCH"}
CONTROLLERS = frozenset({"RestController", "Controller"})
SPRING_NAMES = frozenset(CONTROLLERS | {"RequestMapping"} | set(MAPPING_METHODS))
HTTP_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "TRACE"})
CANDIDATE_PATTERNS = ["Controller", "Mapping", "RouterFunction", "ws.rs"]
CONFIG_NAME = re.compile(r"^(?:application|bootstrap)(?:-[^.]+)?\.(?:properties|ya?ml)$")
_SEGMENT_REST = re.compile(r"^\{\*([^{}]+)\}$")


def _param(seg: str) -> tuple[str, str] | None:
    """(name, constraint) of a segment that is exactly one `{name}` or `{name:regex}` (the regex may hold braces: `{id:[0-9]{3}}`)."""
    if not (seg.startswith("{") and seg.endswith("}")):
        return None
    depth = 0
    for i, c in enumerate(seg):
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and i != len(seg) - 1:
                return None
    if depth != 0:
        return None
    name, _sep, constraint = seg[1:-1].partition(":")
    if not name.strip() or "{" in name or "*" in name:
        return None
    return name.strip(), constraint


def _is_spring(qualified: str) -> bool:
    """Whether a dotted name is under a package registered as the installed framework of this kind (`reachmap.adapters.third_party`)."""
    installed = third_party.names_for("spring")[0]
    return any(qualified.startswith(p + ".") for p in installed)


def spring_name(parsed: Parsed, ann: Annotation) -> str | None:
    """The Spring annotation `ann` is (`RestController`, `GetMapping`...), told by what the file imports and never by the name alone: a
    qualified name under `org.springframework`, a single-type import (an alias too), or a wildcard import of a package of Spring."""
    q = ann.qualified
    if "." in q:
        return ann.name if _is_spring(q) and ann.name in SPRING_NAMES else None
    for r in parsed.imports:
        if not r.wildcard and not r.static and r.local == ann.name:
            last = r.module.rsplit(".", 1)[-1]
            return last if _is_spring(r.module) and last in SPRING_NAMES else None
    if ann.name in SPRING_NAMES and any(r.kind == "wildcard" and _is_spring(r.module + ".") for r in parsed.imports):
        return ann.name
    return None


@dataclass(slots=True)
class Handler:
    """A method that answers requests: `unit` is the method that has the mapping (in `path`); `runs` the unit that runs when it is called
    (the controller's own method when it overrides an interface's)."""

    path: str
    unit: Unit
    ann: Annotation
    canon: str
    runs_path: str
    runs: Unit


@dataclass(slots=True)
class TypeFacts:
    unit: Unit
    controller: bool
    class_map: Annotation | None
    handlers: list[tuple[Unit, Annotation, str]] = field(default_factory=list)


@dataclass(slots=True)
class FileFacts:
    path: str
    parsed: Parsed
    types: list[TypeFacts] = field(default_factory=list)


@dataclass(slots=True)
class Composed:
    """One route and where it comes from: the handler that answers it and the files its evidence is in."""

    route: Route
    handler: Handler
    controller_path: str
    controller: str  # dotted name of the controller type
    unknown: tuple[tuple[str, str, int], ...]
    keys: tuple[str, ...]  # property keys its path was made of


class Table:
    def __init__(self, adapter: Any, sha: str) -> None:
        self.adapter = adapter
        self.language = adapter.language
        self.sha = sha
        self.repo = adapter.repo
        self._complete = True
        self.reasons: list[str] = []
        self.global_hole = False  # something that could hold a route anywhere was not read
        self.hole_paths: set[str] = set()  # the files that were not read: what could be in them was not listed
        self.candidates: set[str] = set()
        self._carried: dict[int, tuple[str, Parsed, Unit, Annotation]] = {}  # a project annotation (by id) to the Spring annotation it carries, with where it is declared
        self._config_said: set[tuple[str, str]] = set()
        self.facts: dict[str, FileFacts | None] = {}
        self.entry_files: set[str] = set()
        self._config: Config | None = None
        self._meta: dict[tuple[str, str], list[tuple[str, Annotation, str, Parsed, Unit]]] = {}
        self._composed: list[Composed] | None = None
        self.ev = Evaluator(self)
        self.keys_used: set[str] = set()
        self._build()

    def incomplete(self, why: str, path: str | None = None) -> None:
        """The table is not whole. `path` is the file that was not read (the routes it may hold are not known); without one, anything may be missing."""
        self._complete = False
        if path is None:
            self.global_hole = True
        else:
            self.hole_paths.add(path)
        if why not in self.reasons:
            self.reasons.append(why)
            self.adapter.problem(f"route table at {self.sha[:12]}: {why}")

    @property
    def complete(self) -> bool:
        """Whether the table was read completely: asked after the routes are composed, because what composing finds out (a name that more
        than one file declares, a package that could not be listed) is part of the answer."""
        self.composed()
        return self._complete

    def hole_covers(self, route: Route) -> bool:
        """Whether something that was not read may hold `route` (or may have held it, for a route the other version does not show): the
        table has a hole anywhere, the route's file or a file its evidence cites was not read, or a file that was not read names the controller."""
        if self.global_hole:
            return True
        if not self.hole_paths:
            return False
        cited = {route.file, *(e.path for e in route.evidence)}
        if cited & self.hole_paths:
            return True
        controller = route.handler.split(".")[0]
        for p in self.hole_paths:
            read = self.language.file_text(self.sha, p)
            if not read.is_found or controller in read.text:
                return True
        return False

    @property
    def target(self) -> Any:
        return self.adapter.target

    # ---- finding and reading files

    def _search(self, patterns: list[str]) -> list[str]:
        """The files that spell one of the patterns (the candidates that are new), with what could not be searched said."""
        res = self.language.search(self.sha, patterns)
        shown = " | ".join(patterns)
        if res is None:
            self.incomplete("the tree could not be read at this commit")
            return []
        if res.state == Read.UNREADABLE:
            self.incomplete(f"the search for {shown!r} was unreadable: {res.detail}")
            return []
        if res.truncated_by == "examine_cap":
            self.incomplete(f"search_truncated: the search for {shown!r} examined {res.examined} of {res.total} files")
        for link, why in res.not_searched:
            if is_jvm_path(link):
                self.incomplete(f"{link} is a {why}: git grep does not search it, so routes in it may be missed", link)
                record_quoted(self.target, "read_unreadable", f"{link} at {self.sha[:12]}", f"a {why}: the search does not look in it, so a route in it may be missed", "", once=True)
        new: list[str] = []
        for m in res.partial_matches:
            if not is_jvm_path(m.path) or self.language.is_test_path(m.path):
                continue
            if not citable(m.path):  # a name that no evidence can cite: its routes cannot be listed, and that is said
                self.incomplete(f"{m.path!r} cannot be cited as evidence (its name holds a backslash or bytes that are not UTF-8): routes in it were not listed", m.path)
                record_quoted(self.target, "read_unreadable", f"{m.path} at {self.sha[:12]}", "the file name cannot be cited as evidence, so the routes in it were not listed", "", once=True)
                continue
            if m.path not in self.candidates:
                self.candidates.add(m.path)
                new.append(m.path)
        return new

    def _build(self) -> None:
        for path in sorted(self._search(list(CANDIDATE_PATTERNS))):
            self.facts_of(path)
        # a controller that is made of the project's own annotations spells neither word: look for the files that use an annotation type
        # that carries a Spring one (one level, as `meta` reads it)
        carriers = sorted({u.name for f in list(self.facts.values()) if f is not None for u in f.parsed.units() if u.kind == "annotation" and any(spring_name(f.parsed, a) for a in u.annotations)})
        if carriers:
            for path in sorted(self._search(carriers)):
                self.facts_of(path)
        for path, f in list(self.facts.items()):
            if f is None:
                continue
            for t in f.types:
                if t.controller or t.handlers:
                    self.entry_files.add(path)
                if t.controller:
                    for st in self.language.supers_of(self.sha, path, f.parsed, t.unit):  # a base class or an interface that holds mappings
                        sf = self.facts_of(st.path)
                        if sf is not None and any(x.handlers or x.class_map is not None for x in sf.types):
                            self.entry_files.add(st.path)

    def facts_of(self, path: str) -> FileFacts | None:
        if path in self.facts:
            return self.facts[path]
        self.facts[path] = None
        if not self.language.file_text(self.sha, path).is_found:
            self.incomplete(f"{path} could not be read", path)
            return None
        parsed = self.language.parsed(self.sha, path)
        if parsed is None:
            self.incomplete(f"{path} could not be read", path)
            return None
        if parsed.problems:
            self.incomplete(f"{path} did not parse ({parsed.problems[0]}): controllers and mappings in it were not read", path)
            record_quoted(self.target, "parse_degraded", path, "no route is claimed from it, so routes may be missing", parsed.problems[0], blocks_selection=True, once=True)
            return None
        self.facts[path] = self._extract(path, parsed)
        return self.facts[path]

    def meta(self, path: str, parsed: Parsed, ann: Annotation) -> list[tuple[str, Annotation, str, Parsed, Unit]]:
        """The Spring annotations a project annotation carries (one level: `@interface ApiController` annotated `@RestController`, a `@Get` annotated
        `@GetMapping`): what makes a class a controller or a method a handler when the name of the annotation is the project's own, with the
        file, the parse and the unit where it is declared. The path and the `method` of a composed mapping are those of the usage when it has
        them, else those the project annotation was declared with."""
        key = (path, ann.qualified)
        if key not in self._meta:
            found: list[tuple[str, Annotation, str, Parsed, Unit]] = []
            hit = self.language.resolve_type(self.sha, path, parsed, ann.qualified)
            if hit is not None:
                f, top = hit
                p = self.language.parsed(self.sha, f)
                unit = next((d for d in p.declarations if d.name == top and d.kind == "annotation"), None) if p is not None and not p.problems else None
                if unit is not None:
                    found = [(n, ma, f, p, unit) for ma in unit.annotations if (n := spring_name(p, ma))]
            self._meta[key] = found
        return self._meta[key]

    def _extract(self, path: str, parsed: Parsed) -> FileFacts:
        out = FileFacts(path, parsed)
        for unit in parsed.units():
            if not unit.is_type:
                continue
            canon: dict[str, Annotation] = {}
            for a in unit.annotations:
                n = spring_name(parsed, a)
                if n:
                    canon[n] = a
                    continue
                for mn, ma, mpath, mparsed, munit in self.meta(path, parsed, a):
                    if mn in CONTROLLERS:
                        canon[mn] = a
                    elif mn == "RequestMapping":
                        canon.setdefault("RequestMapping", a)  # the usage carries the path, else the project annotation does
                        self._carried[id(a)] = (mpath, mparsed, munit, ma)
            class_map = canon.get("RequestMapping")
            controller = (any(n in CONTROLLERS for n in canon) or class_map is not None) and unit.kind in ("class", "object") and "abstract" not in unit.modifiers
            handlers: list[tuple[Unit, Annotation, str]] = []
            for m in unit.children:
                if m.kind != "method":
                    continue
                for a in m.annotations:
                    n = spring_name(parsed, a)
                    found = [(n, None)] if n else [(mn, (mpath, mparsed, munit, ma)) for mn, ma, mpath, mparsed, munit in self.meta(path, parsed, a)]
                    for mn, carried in found:
                        if mn in MAPPING_METHODS or mn == "RequestMapping":
                            if carried is not None:
                                self._carried[id(a)] = carried
                            handlers.append((m, a, mn))
                            break
            out.types.append(TypeFacts(unit, controller, class_map, handlers))
        self._unread_styles(path, parsed)
        return out

    def _unread_styles(self, path: str, parsed: Parsed) -> None:
        """Functional routes (`RouterFunction`) and Jakarta REST (`@Path`) make routes this adapter does not read: said, so a file that serves
        routes that way is not taken for a file with none."""
        functional = any(t.kind == "ident" and t.value in ("RouterFunction", "RouterFunctions") for t in parsed.code)
        jaxrs = any(f"{r.module}.".startswith(("javax.ws.rs.", "jakarta.ws.rs.")) and ".client." not in f"{r.module}." for r in parsed.imports)  # the client API makes no route
        if not (functional or jaxrs):
            return
        what = "functional routes (RouterFunction)" if functional else "Jakarta REST resources (@Path)"
        self.incomplete(f"{path} serves {what}, which this adapter does not read", path)
        record_quoted(self.target, "route_style_unread", path, f"{what} are not read, so the routes of the file are not listed", "", blocks_selection=True, once=True)

    # ---- configuration

    def config(self) -> Config:
        if self._config is None:
            cfg = Config()
            for f in sorted(self.language.tree(self.sha)):
                if CONFIG_NAME.match(posixpath.basename(f)) and not self.language.is_test_path(f):
                    read = self.language.file_text(self.sha, f)
                    if not read.is_found:
                        self.incomplete(f"{f} could not be read: a context path in it was not read")
                        continue
                    sub = read_config(f, read.text)
                    cfg.entries += sub.entries
                    cfg.unread += sub.unread
            self._config = cfg
        return self._config

    def module_of(self, path: str) -> str:
        return self.language.module_of(self.sha, path)

    def _config_unread(self, key: str, entry: Entry, why: str) -> Entry:
        """`key` is set, to a value that is not known: the entry says so (`unread`), and the table and the report say why, once."""
        if (key, entry.path) not in self._config_said:
            self._config_said.add((key, entry.path))
            self.incomplete(f"{entry.path}: {why}; whether it sets {key} was not established")
            record_quoted(self.target, "config_unread", entry.path, "whether it sets a path for the routes was not established", why, blocks_selection=True, once=True)
        return replace(entry, unread=True)

    def config_lookup(self, path: str):  # noqa: ANN201 - a key to an Entry or None, for the configuration that applies to a file
        """The lookup of a property for the controllers of `path`: the entry that applies, None when nothing sets the key, and an entry marked
        `unread` when the key is set to something that is not known (a profile gives another value, a part of a file this reader does not
        understand may set it, another module holds the only configuration, two modules disagree). Never the value of a place that may not apply."""
        cfg = self.config()
        module = self.module_of(path)

        def lookup(key: str) -> Entry | None:
            for p, why in cfg.unread:
                e = next((e for e in cfg.entries if e.unread and e.path == p and (key == e.key or key.startswith(e.key + "."))), None)
                if e is not None:
                    return self._config_unread(key, e, why)
            entries = [e for e in cfg.get(key) if e.profile is None]
            mine = [e for e in entries if self.module_of(e.path) == module]
            if mine:
                use = mine
            elif entries and module:  # this module has a configuration of its own or none; another module's is not its
                return self._config_unread(key, entries[0], f"the module {module} has no application configuration of its own and another module has one")
            else:
                use = entries
            profiled = [e for e in cfg.profiled(key) if self.module_of(e.path) == module or not module]
            if not use:
                if profiled:
                    return self._config_unread(key, profiled[0], f"the key is set only under the profile {profiled[0].profile}")
                return None
            use.sort(key=lambda e: (e.path.endswith((".yml", ".yaml")), e.path, -e.line))  # properties files win over yaml; the last value of a file wins
            chosen = use[0]
            if any(e.value != chosen.value for e in use if self.module_of(e.path) != self.module_of(chosen.path)):
                return self._config_unread(key, chosen, "the modules that set the key disagree")
            if any(p.value != chosen.value for p in profiled):
                return self._config_unread(key, chosen, f"the profile {next(p.profile for p in profiled if p.value != chosen.value)} gives another value")
            return chosen

        return lookup

    def prefix(self, path: str) -> Str:
        """What the application configuration puts before every route of a file: the context path and the servlet path."""
        lookup = self.config_lookup(path)
        out = Str()
        override = self.adapter.context_path
        for key in PATH_KEYS:
            if key == "server.servlet.context-path" and override is not None:
                out += self._piece(Str(override))
                continue
            entry = lookup(key)
            if entry is None:
                continue
            self.keys_used.add(key)
            if entry.unread:
                out += Str(f"/<unknown:{key}>", ((key, entry.path, entry.line),), ((entry.path, entry.line),), (key,))
                continue
            out += Str(self._piece(Str(entry.value)).value, (), ((entry.path, entry.line),), (key,))
        return out

    @staticmethod
    def _piece(s: Str) -> Str:
        v = s.value
        if v and not v.startswith("/"):
            v = "/" + v
        return Str(v, s.unknown, s.lines, s.keys)

    # ---- the routes

    def _paths(self, path: str, parsed: Parsed, unit: Unit, ann: Annotation) -> list[Str]:
        """The paths a mapping annotation gives (`value` and `path` are aliases); `[Str()]` for none (the path of the class or the root)."""
        out: list[Str] = []
        for i, (name, a, b) in enumerate(ann.args):
            if name in ("value", "path") or (name is None and i == 0):
                out += self.ev.values(path, parsed, unit, a, b)
        if out:
            return out
        carried = self._carried.get(id(ann))
        if carried is None:
            return [Str()]
        mpath, mparsed, munit, mann = carried  # a project annotation that is a mapping: the path it was declared with, when the usage gives none
        given = self._paths(mpath, mparsed, munit, mann)
        out = [Str("", (), ((mpath, mann.line),)) + s for s in given]
        if any(name not in (None, "value", "path") for name, _a, _b in ann.args) and any(t.kind == "ident" and t.value == "AliasFor" for t in mparsed.code[munit.t0:munit.t1]):
            out = [s + Str("", (("@AliasFor", path, ann.line),)) for s in out]  # an attribute of the usage may be an alias of the path: not read
        return out

    def _methods(self, path: str, parsed: Parsed, ann: Annotation, canon: str) -> tuple[list[str], list[tuple[str, str, int]]]:
        """The HTTP methods a mapping answers: those of the annotation's name or of `method = ...`; `ANY` for none. A method that is not a
        constant of `RequestMethod` is `<unknown:SOURCE>` (and said)."""
        if canon in MAPPING_METHODS:
            return [MAPPING_METHODS[canon]], []
        for name, a, b in ann.args:
            if name != "method":
                continue
            methods: list[str] = []
            unknown: list[tuple[str, str, int]] = []
            for x, y in self._items(parsed, a, b):
                chain = [t.value for t in parsed.code[x:y] if t.kind == "ident"]
                if y - x >= 1 and chain and chain[-1] in HTTP_METHODS and all(t.kind == "ident" or t.value == "." for t in parsed.code[x:y]):
                    methods.append(chain[-1])
                else:
                    src = " ".join(parsed.source(x, y - 1).split()) if y > x else "?"
                    methods.append(f"<unknown:{src}>")
                    unknown.append((f"method = {src}", path, parsed.code[x].line if y > x else ann.line))
            return methods or ["ANY"], unknown
        carried = self._carried.get(id(ann))
        if carried is not None and canon == "RequestMapping":
            mpath, mparsed, _munit, mann = carried  # the methods the project annotation was declared with
            return self._methods(mpath, mparsed, mann, "RequestMapping")
        return ["ANY"], []

    @staticmethod
    def _items(parsed: Parsed, a: int, b: int) -> list[tuple[int, int]]:
        code = parsed.code
        if b - a >= 2 and code[a].value in "{[" and parsed.match.get(a) == b - 1:
            a, b = a + 1, b - 1
        elif b - a >= 3 and code[a].value in ("arrayOf", "listOf") and code[a + 1].value == "(" and parsed.match.get(a + 1) == b - 1:
            a, b = a + 2, b - 1
        return split_items(parsed, a, b)

    def convert(self, s: Str, path: str, line: int) -> Str:
        """A Spring path pattern as a route path: `{id}` and `{id:\\d+}` are parameters, `{*rest}` and `**` take the rest (`{rest:path}`),
        `*` is one segment (`{wildcard}`); a segment that mixes text and a pattern (`{id}.json`, `v{n}`) is not understood: `<unknown:SEGMENT>`."""
        segs: list[str] = []
        unknown = list(s.unknown)
        for seg in s.value.split("/"):
            if seg == "":
                segs.append(seg)
                continue
            if "<unknown:" in seg:
                segs.append(seg)
                continue
            m = _SEGMENT_REST.match(seg)
            if m:
                segs.append("{" + m.group(1) + ":path}")
            elif seg == "**":
                segs.append("{path:path}")
            elif seg == "*":
                segs.append("{wildcard}")
            else:
                got = _param(seg)
                if got is not None:
                    segs.append("{" + got[0] + (":path" if re.search(r"\.[*+]|/", got[1]) else "") + "}")
                elif any(c in seg for c in "{}*?"):
                    segs.append(f"<unknown:{seg}>")
                    unknown.append((seg, path, line))
                else:
                    segs.append(seg)
        return Str("/".join(segs), tuple(unknown), s.lines, s.keys)

    def composed(self) -> list[Composed]:
        if self._composed is not None:
            return self._composed
        out: list[Composed] = []
        for path in sorted(self.facts):
            f = self.facts[path]
            if f is None:
                continue
            for t in f.types:
                if t.controller:
                    out += self._routes_of(path, f, t)
        out.sort(key=lambda c: (c.route.path, c.route.method, c.route.file, c.route.handler))
        self._composed = out
        for path, why in sorted(self.language.gaps.get(self.sha, {}).items()):
            # a file of a package that was searched for a supertype or a constant, and could not be read: the type may be declared there
            self.incomplete(f"{path}: {why}; a supertype or a constant of a route may be declared there")
            record_quoted(self.target, "parse_degraded", path, "a type it declares may be a supertype of a controller or a constant of a route path, so routes may be missing", why,
                          blocks_selection=True, once=True)
        for where, written, files in sorted(self.language.ambiguities.get(self.sha, {}).values()):
            # a name that more than one file declares and nothing tells which: its constant is unknown, its supertype was not followed
            self.incomplete(f"{written} in {where} could be declared by any of {files}: what it gives a route was not established", where)
            record_quoted(self.target, "type_ambiguous", written, "more than one file declares it and nothing tells which one the code means, so the routes that use it are not whole", f"{where}: {files}",
                          blocks_selection=True, once=True)
        why = self.language.package_index(self.sha)[1]
        if why is not None:
            self.incomplete(f"{why}: a file whose folders do not spell its package may hold a supertype or a constant of a route")
        return out

    def _routes_of(self, path: str, f: FileFacts, t: TypeFacts) -> list[Composed]:
        handlers: list[Handler] = [Handler(path, u, a, n, path, u) for u, a, n in t.handlers]
        class_map: tuple[str, Annotation, Unit] | None = (path, t.class_map, t.unit) if t.class_map is not None else None
        own_names = {u.name for u, _a, _n in t.handlers}  # the methods the controller maps itself
        for st in self.language.supers_of(self.sha, path, f.parsed, t.unit):
            sf = self.facts_of(st.path)
            if sf is None:
                continue
            for x in sf.types:
                if x.unit is not st.unit:
                    continue
                if class_map is None and x.class_map is not None:
                    class_map = (st.path, x.class_map, x.unit)
                for u, a, n in x.handlers:
                    if u.name in own_names:
                        continue
                    over = next((c for c in t.unit.children if c.kind == "method" and c.name == u.name), None)  # overridden without a mapping of its own
                    handlers.append(Handler(st.path, u, a, n, path if over is not None else st.path, over if over is not None else u))
        prefix = self.prefix(path)
        out: list[Composed] = []
        class_paths: list[Str] = [Str()]
        class_methods: list[str] = []
        class_unknown: list[tuple[str, str, int]] = []
        if class_map is not None:
            cpath, cann, cunit = class_map
            cparsed = self.facts[cpath].parsed if self.facts.get(cpath) is not None else self.language.parsed(self.sha, cpath)
            class_paths = self._paths(cpath, cparsed, cunit, cann)
            cm, class_unknown = self._methods(cpath, cparsed, cann, "RequestMapping")
            class_methods = [] if cm == ["ANY"] else cm
        for h in handlers:
            hf = self.facts.get(h.path)
            if hf is None:
                continue
            method_paths = self._paths(h.path, hf.parsed, h.unit, h.ann)
            methods, m_unknown = self._methods(h.path, hf.parsed, h.ann, h.canon)
            if class_methods:  # the methods of the class and of the handler are added (a handler with none takes the class's)
                methods = list(class_methods) if methods == ["ANY"] else list(dict.fromkeys([*class_methods, *methods]))
            for cp in class_paths:
                for mp in method_paths:
                    for method in methods:
                        out.append(self._route(path, t, h, class_map, prefix, cp, mp, method, m_unknown + class_unknown))
        return [c for c in out if c is not None]  # type: ignore[misc]

    def _route(self, path: str, t: TypeFacts, h: Handler, class_map: tuple[str, Annotation, Unit] | None, prefix: Str, cp: Str, mp: Str,
               method: str, extra_unknown: list[tuple[str, str, int]]) -> Composed | None:
        full = prefix + self._piece(cp) + self._piece(mp)
        full = self.convert(full, h.path, h.ann.line)
        unknown = tuple(full.unknown) + tuple(extra_unknown)
        value = normalize_path(full.value or "/")
        pairs: list[tuple[str, int]] = [(h.path, h.ann.line)]
        pairs += list(mp.lines)
        if class_map is not None:
            pairs.append((class_map[0], class_map[1].line))
        pairs += list(cp.lines)
        pairs += list(prefix.lines)
        evidence: list[Evidence] = []
        seen: set[tuple[str, int]] = set()
        for n, (p, line) in enumerate(pairs):
            if (p, line) in seen:
                continue
            seen.add((p, line))
            fp = self.facts.get(p)
            parsed = fp.parsed if fp is not None else self.language.parsed(self.sha, p)
            ev = evidence_at(self.repo, p, self.sha, parsed, line) if parsed is not None else None
            if ev is None:  # a route nothing can cite is not listed, and that is said (never a table that is whole without it)
                self.incomplete(f"{p}:{line} could not be cited as evidence of {method} {value}: the route was not listed", h.runs_path)
                return None
            if not evidence:
                ev = replace(ev, claim=f"route:{method} {value}:{h.path}")
            evidence.append(ev)
        runner = h.runs.dotted
        route = Route(
            repo=self.repo, kind="api_route", method=method, path=value, file=h.runs_path, handler=runner, target=h.runs_path,
            module=posixpath.basename(self.module_of(h.runs_path)), evidence=tuple(evidence),
            partial=bool(unknown) or "<unknown" in method,
        )
        self.keys_used.update(prefix.keys + cp.keys + mp.keys)
        return Composed(route, h, path, t.unit.dotted, unknown, tuple(dict.fromkeys(prefix.keys + cp.keys + mp.keys)))

    def routes(self) -> list[Route]:
        return [c.route for c in self.composed()]
