"""The Python language adapter.

Given a change, it answers which symbols changed (placed by diff line number, nested scopes and class
members kept), who imports the changed module (every import form, confirmed by resolving the import
through the module map, never by name alone), which importers named-name narrowing excludes, which
call sites survive, which paths are tests, and how many times a name is defined. Every answer is Evidence
pinned to a commit, or an incompleteness record; a list that could not be established completely says
so (`complete=False`) and is never presented as the whole list.

Standard library only: the files are read through the git layer at the pinned commit and analysed with
`ast` (`syntax`) and a module map (`modules`). The shell it shares with the JavaScript adapter is
`reachmap.adapters.base`. See docs/briefs/M6-python-adapter.md, including its implementation notes and the
limits.
"""

from __future__ import annotations

import posixpath
import sys
from dataclasses import dataclass, replace
from typing import Any, Sequence

from reachmap import config
from reachmap.adapters import Binding, CallSite, ImportEdge, MODULE, Reach, Symbol, third_party
from reachmap.adapters.base import (
    AdapterBase, Context, Narrowing, Search, dedupe as _dedupe, ev_list as _ev_list, evidence_at as _evidence, import_claim, record_quoted,
)
from reachmap.adapters.python.modules import ModuleMap, Resolved
from reachmap.adapters.python.syntax import Declaration, ImportRecord, Parsed, Use, parse
from reachmap.errors import ModelError
from reachmap.gitlayer import Git
from reachmap.incomplete import kind_spec, record, register_kind
from reachmap.model import Evidence

__all__ = ["DEFAULT_MARKERS", "PyAdapter", "symbols_for_change"]

try:
    kind_spec("package_init_implicit")
except ModelError:
    register_kind(
        "package_init_implicit",
        "What else runs {subject} was not established, because importing a module inside a package runs the package's __init__ without naming it",
        False,
    )

# Files Python loads through other machinery than an import statement that this adapter does not read.
NOT_ANALYSED = frozenset({".pyi", ".pyx", ".pxd", ".pyw", ".so", ".pyd"})

DEFAULT_MARKERS = ("tests/", "test/", "test_", "_test", "conftest.py")
LANGUAGES = ("python",)
STDLIB = frozenset(getattr(sys, "stdlib_module_names", ()))


def is_py_path(path: str) -> bool:
    return path.endswith(".py")


def _leaf(name: str) -> str:
    return name.rsplit(".", 1)[-1]


# ------------------------------------------------------------------------------ changed symbols


def symbols_for_change(
    repo: str,
    path: str,
    before: str | None,
    after: str | None,
    added: Sequence[int],
    removed: Sequence[int],
    *,
    before_sha: str | None,
    after_sha: str | None,
    status: str = "modified",
    before_path: str | None = None,
    parsed_before: Parsed | None = None,
    parsed_after: Parsed | None = None,
) -> list[Symbol]:
    """The symbols a change touches. Each added line (its number in `after`) is placed in the
    declaration whose span holds it in the after text, each removed line (its number in `before`) in the
    span in the before text; a line is never searched for by its text. `name` is the top-level declaration,
    or `Class.member` for a line inside a class body (`Invoice.settlement_reference`); `nested` lists the
    functions inside the member that hold the line. A changed line outside every declaration that holds
    code is module level (`<module>`); a comment, a blank line or a module docstring there changes nothing.
    If either text does not parse (`Parsed.problems`) every changed line is module level: nothing can be
    trusted to sit in a declaration. `change` is judged on the top-level declaration: a new field in an
    existing class modifies the class; a declaration that is new (or gone) is one symbol, not one per line."""
    pa = parsed_after if parsed_after is not None else (parse(after) if after is not None else None)
    pb = parsed_before if parsed_before is not None else (parse(before) if before is not None else None)
    degraded = bool((pa is not None and pa.problems) or (pb is not None and pb.problems))
    groups: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
    mod: dict[str, list[int]] = {"after": [], "before": []}

    def top_names(parsed: Parsed | None) -> set[str]:
        return {n for d in parsed.declarations for n in d.names} if parsed is not None else set()

    names_after, names_before = top_names(pa), top_names(pb)

    def change_of(top: str, side: str) -> str:
        if status == "added":
            return "added"
        if status == "deleted":
            return "removed"
        if side == "after" and pb is not None and top not in names_before:
            return "added"
        if side == "before" and pa is not None and top not in names_after:
            return "removed"
        return "modified"

    def place(parsed: Parsed, lines: Sequence[int], side: str) -> None:
        for n in sorted(set(lines)):
            placed = parsed.placements(n)
            if not placed:
                if n in parsed.code_lines:
                    mod[side].append(n)
                continue
            for p in placed:  # more than one when declarations share the line (`x = 1; y = 2`)
                top = p.decl.name
                name, nested, kind = p.name, p.nested, p.kind
                if change_of(top, side) != "modified":  # a declaration that is new or gone is one symbol
                    name, nested, kind = top, (), p.decl.kind
                g = groups.setdefault((name, nested), {"after": [], "before": [], "decl_after": None, "decl_before": None, "top": top, "kind": kind, "changes": set()})
                g[side].append(n)
                g["changes"].add(change_of(top, side))
                if g[f"decl_{side}"] is None:
                    g[f"decl_{side}"] = p.decl

    if degraded:  # no line can be trusted to belong to a declaration
        mod["after"] = sorted(set(added)) if pa is not None else []
        mod["before"] = sorted(set(removed)) if pb is not None else []
    else:
        if pa is not None:
            place(pa, added, "after")
        if pb is not None:
            place(pb, removed, "before")

    def decls_named(parsed: Parsed | None, top: str) -> list[Declaration]:
        return [d for d in parsed.declarations if top in d.names] if parsed is not None else []

    out: list[Symbol] = []
    for (name, nested), g in groups.items():
        top = g["top"]
        decl_a = g["decl_after"] or next(iter(decls_named(pa, top)), None)
        decl_b = g["decl_before"] or next(iter(decls_named(pb, top)), None)
        changes = g["changes"]
        change = "added" if changes == {"added"} else "removed" if changes == {"removed"} else "modified"
        side = "after" if decl_a is not None else "before"
        decl = decl_a if side == "after" else decl_b
        assert decl is not None
        evidence: list[Evidence] = []
        if pa is not None and after_sha is not None:
            evidence += _ev_list(repo, path, after_sha, pa, g["after"])
        if pb is not None and before_sha is not None:
            evidence += _ev_list(repo, before_path or path, before_sha, pb, g["before"])
        if not evidence:  # only blank lines changed inside it: cite the declaration itself
            parsed_d, sha_d, path_d = (pa, after_sha, path) if side == "after" else (pb, before_sha, before_path or path)
            if parsed_d is not None and sha_d is not None:
                evidence += _ev_list(repo, path_d, sha_d, parsed_d, [decl.start])
        exports = sorted({n for parsed in (pa, pb) for d in decls_named(parsed, top) for n in d.names})
        out.append(Symbol(
            repo=repo, path=path, name=name, kind=g["kind"], start=decl.start, end=decl.end, exported=bool(exports),
            nested=nested, side=side, change=change, export_names=tuple(exports), evidence=tuple(evidence),
            added_lines=tuple(sorted(set(g["after"]))), removed_lines=tuple(sorted(set(g["before"]))),
        ))
    if mod["after"] or mod["before"]:
        side = "after" if mod["after"] else "before"
        lines = mod[side]
        evidence = []
        if pa is not None and after_sha is not None:
            evidence += _ev_list(repo, path, after_sha, pa, mod["after"])
        if pb is not None and before_sha is not None:
            evidence += _ev_list(repo, before_path or path, before_sha, pb, mod["before"])
        out.append(Symbol(
            repo=repo, path=path, name=MODULE, kind="module", start=min(lines), end=max(lines), exported=False, nested=(),
            side=side, change="modified", evidence=tuple(evidence),
            added_lines=tuple(sorted(set(mod["after"]))), removed_lines=tuple(sorted(set(mod["before"]))),
        ))
    out.sort(key=lambda s: (s.is_module, s.start, s.name, s.nested))
    return out




# ------------------------------------------------------------------------------ the adapter


@dataclass(frozen=True, slots=True)
class _Target:
    """One file an import statement reaches: how (`kind`), which names (None: the whole module) and the
    bindings the statement makes for it."""

    path: str
    kind: str
    names: tuple[str, ...] | None
    bindings: tuple[Binding, ...]


@dataclass(frozen=True, slots=True)
class _Expansion:
    """What one import statement means: the files it reaches, whether it resolved to nothing at all
    (`unresolved`), whether a relative import went above the top level (`beyond`) and the module names
    that more than one file answers to."""

    targets: tuple[_Target, ...]
    unresolved: bool
    beyond: bool = False
    ambiguous: dict[str, tuple[str, ...]] | None = None


class PyAdapter(AdapterBase):
    """Python adapter for one repository (see `AdapterBase`)."""

    languages = LANGUAGES
    DEFAULT_MARKERS = DEFAULT_MARKERS
    NOT_ANALYSED = NOT_ANALYSED
    PROBLEMS_STOP_READING = True
    DYNAMIC_CALLS = "import_module() or __import__()"
    LANGUAGE = "Python adapter"

    def __init__(
        self,
        repo: str,
        remote: str,
        git: Git,
        *,
        service_marker: str | None = None,
        library_roots: Sequence[str] = (),
        test_markers: Sequence[str] | None = None,
        record_to: Any = None,
        examine_cap: int | None = None,
        hub_importers: int | None = None,
        distinct_max: int | None = None,
        framework: str | None = None,
    ) -> None:
        super().__init__(repo, remote, git, test_markers=test_markers, record_to=record_to, examine_cap=examine_cap,
                         hub_importers=hub_importers, distinct_max=distinct_max)
        self.service_marker = service_marker
        self.library_roots = tuple(library_roots)
        self.installed, self.conventions = third_party.names_for(framework)  # per entry-point kind of the repository (`reachmap.adapters.third_party`)
        self._segments: dict[int, frozenset[str]] = {}
        self._unresolved_prefixes: set[str] = set()

    @classmethod
    def from_config(
        cls,
        repo: config.Repo,
        git: Git,
        *,
        limits: config.Limits | None = None,
        remote: str | None = None,
        record_to: Any = None,
        examine_cap: int | None = None,
    ) -> "PyAdapter":
        """The adapter a repository's config selects: `service_marker`, `library_roots`, `tests.markers`,
        and the hub and distinctiveness limits of the workspace."""
        tests = repo.tests or {}
        return cls(
            repo.name,
            remote or repo.remote or "",
            git,
            service_marker=repo.service_marker,
            library_roots=repo.library_roots,
            test_markers=tests.get("markers"),
            record_to=record_to,
            examine_cap=examine_cap,
            hub_importers=limits.hub_importers if limits else None,
            distinct_max=limits.distinct_max if limits else None,
            framework=(repo.entry_points or {}).get("kind"),
        )

    # ---- hooks of the shell

    def _parse_text(self, text: str) -> Parsed:
        return parse(text)

    def _is_code_path(self, path: str) -> bool:
        return is_py_path(path)

    def _prepare_context(self, ctx: Context, sha: str) -> None:
        ctx.modules = ModuleMap.build(  # type: ignore[attr-defined]
            ctx.files, service_marker=self.service_marker, library_roots=self.library_roots,
            read=lambda p: self._read(sha, p), target=self.target, at=sha[:12],
        )

    def _context_gaps(self, ctx: Context) -> list[str]:
        mm = ctx.modules  # type: ignore[attr-defined]
        return [] if mm.complete else ["the module layout could not be read completely: " + "; ".join(mm.reasons)]

    def _build_symbols(self, fc, before, after, added, removed, pre, head, before_path, pb, pa) -> list[Symbol]:
        return symbols_for_change(
            self.repo, fc.path, before, after, added, removed, before_sha=pre, after_sha=head,
            status=fc.status, before_path=before_path, parsed_before=pb, parsed_after=pa,
        )

    def _enrich_symbols(self, fc, symbols, pre, head):
        if posixpath.basename(fc.path) == "__init__.py":
            record(self.target, "package_init_implicit", fc.path,
                   detail="importing a module inside the package runs this file without naming it; only imports that name the package were searched")
        return symbols

    def _distinct_name(self, name: str) -> str | None:
        leaf = _leaf(name)
        return None if leaf.startswith("__") and leaf.endswith("__") else leaf  # a dunder is never a name to link code by

    def _convention(self, leaf: str) -> bool:
        return leaf in self.conventions

    def _definition_lines(self, parsed: Parsed, name: str) -> list[int]:
        return parsed.definition_lines(name)

    def names_for(self, path: str, sha: str) -> tuple[str, ...]:
        """Every dotted module name the file has at `sha` (one per source root that holds it)."""
        ctx = self._context(sha)
        return () if ctx is None else ctx.modules.names_of(path)  # type: ignore[attr-defined]

    # ---- import statements

    def _expand(self, ctx: Context, importer: str, rec: ImportRecord) -> _Expansion:
        """The files an import statement reaches, for the importer."""
        mm: ModuleMap = ctx.modules  # type: ignore[attr-defined]
        type_only = rec.type_only
        targets: list[_Target] = []
        ambiguous: dict[str, tuple[str, ...]] = {}

        def module_part(res: Resolved | None, label: str) -> bool:
            if res is not None and res.ambiguous:
                ambiguous[label] = res.files
            return res is not None and res.found

        if rec.kind in ("import", "dynamic"):
            res = mm.resolve(rec.module, importer)
            found = module_part(res, rec.module)
            bindings: tuple[Binding, ...] = ()
            if rec.kind == "import":
                nm = rec.names[0]
                if nm.asname:
                    bindings = (Binding("*", nm.asname, type_only, nm.line),)
                elif "." in nm.name:
                    bindings = (Binding(nm.name, nm.name.split(".")[0], type_only, nm.line),)
                else:
                    bindings = (Binding("*", nm.name, type_only, nm.line),)
            for f in res.files:
                targets.append(_Target(f, rec.kind, None, bindings))
            return _Expansion(tuple(targets), not found, False, ambiguous or None)
        # from-imports (and star)
        if rec.level == 0:
            res = mm.resolve(rec.module, importer)
        else:
            res = mm.resolve_relative(importer, rec.level, rec.module)
            if res is None:
                return _Expansion((), True, True)
        found = module_part(res, rec.specifier)
        if rec.kind == "star":
            for f in res.files:
                targets.append(_Target(f, "export_star", None, ()))
            return _Expansion(tuple(targets), not found, False, ambiguous or None)
        names = tuple(n.name for n in rec.names)
        bindings = tuple(Binding(n.name, n.asname or n.name, type_only, n.line) for n in rec.names)
        for f in res.files:
            targets.append(_Target(f, "import", names, bindings))
        for n in rec.names:  # `from pkg import name` where name is a submodule imports that module
            dotted = f"{rec.module}.{n.name}" if rec.module else n.name
            sub = mm.resolve(dotted, importer) if rec.level == 0 else mm.resolve_relative(importer, rec.level, dotted)
            if sub is None:
                continue
            if sub.ambiguous:
                ambiguous[dotted] = sub.files
            for f in sub.files:
                targets.append(_Target(f, "import", None, (Binding("*", n.asname or n.name, type_only, n.line),)))
        return _Expansion(tuple(targets), not found, False, ambiguous or None)

    # ---- importers

    def _patterns(self, ctx: Context, path: str) -> list[str]:
        """The last segment of every dotted name of the file (the package name for an `__init__.py`)."""
        return list(ctx.modules.stems_of(path))  # type: ignore[attr-defined]

    def _gather(self, ctx: Context, s: Search) -> None:
        """Candidates: `git grep` for the last segment of every dotted name of the file (the package name
        for an `__init__.py`), every Python file under the folder of an `__init__.py` (`from . import x`
        names no package); a file with no name to search for makes every Python file a candidate."""
        mm: ModuleMap = ctx.modules  # type: ignore[attr-defined]
        stems = self._patterns(ctx, s.path)
        all_py = [p for p in ctx.checkout.files if is_py_path(p) and mm.is_module_path(p) and p != s.path]
        if not stems:
            self._pool(s, all_py)
            return
        if posixpath.basename(s.path) == "__init__.py":
            folder = posixpath.dirname(s.path)
            s.candidates.update(p for p in all_py if not folder or p.startswith(folder + "/"))
        for pattern in stems:
            self._grep_for(s, pattern, is_py_path)

    def _scan_candidate(self, ctx: Context, s: Search, cand: str, parsed: Parsed) -> None:
        mm: ModuleMap = ctx.modules  # type: ignore[attr-defined]
        stems = mm.stems_of(s.path)
        is_init = posixpath.basename(s.path) == "__init__.py"
        beyond: list[str] = []
        for rec in parsed.imports:
            exp = self._expand(ctx, cand, rec)
            hit = [t for t in exp.targets if t.path == s.path]
            if exp.beyond:
                beyond.append(f"{rec.specifier!r} at line {rec.line}")
            if exp.unresolved and self._names_module(ctx, cand, rec, stems, is_init):
                s.incomplete(f"{cand} imports {rec.specifier!r}, which resolves to nothing at this commit but names the module")
                self._record_unresolved(rec, cand, s.path, s.sha, stems)
            if not hit:
                continue
            for dotted, files in (exp.ambiguous or {}).items():
                if s.path in files:
                    record_quoted(self.target, "module_ambiguous", dotted,
                                  "more than one file has that name for the importer; each is kept as an importer of it", f"{cand}: {', '.join(files)}")
            ev = _evidence(self.repo, cand, s.sha, parsed, rec.line)
            if ev is None:
                continue
            for t in hit:
                s.edges.append(ImportEdge(
                    importer_path=cand, specifier=rec.specifier, names=t.names, evidence=replace(ev, claim=import_claim(s.path, t.names)), kind=t.kind,
                    type_only=rec.type_only, bindings=t.bindings, target=s.path,
                ))
        if beyond:  # Python refuses these: they reach nothing (a stated limit, not a gap in the list)
            record_quoted(self.target, "specifier_unresolved", cand,
                          "a relative import goes above the top level package, so it reaches nothing", ", ".join(beyond), blocks_selection=False)

    def _names_module(self, ctx: Context, importer: str, rec: ImportRecord, stems: Sequence[str], is_init: bool) -> bool:
        """Whether an import that resolved to nothing spells the module's name: the last segment of the
        module (any segment for a package), or a name imported from it (it may be the submodule). A standard
        library module is not a miss unless something in the repository could shadow it, and nor is a package that the repository's entry-point
        kind registered as installed (`reachmap.adapters.third_party`) unless the tree holds anything of that name."""
        if not stems:
            return False
        first = rec.module.split(".")[0]
        if rec.level == 0 and first in STDLIB and not self._could_shadow(ctx, importer, first):
            return False
        if rec.level == 0 and first in self.installed and not self._anywhere(ctx, first):
            return False
        segments = [s for s in rec.module.split(".") if s]
        spelled = set(segments if is_init else segments[-1:])
        if rec.kind == "from":
            spelled.update(n.name for n in rec.names)
        return bool(spelled & set(stems))

    def _anywhere(self, ctx: Context, name: str) -> bool:
        """Whether the tree holds a folder or a module called `name` anywhere (a vendored copy of an installed package may be what an import means)."""
        key = id(ctx)
        if key not in self._segments:
            segments: set[str] = set()
            for p in ctx.files:
                parts = p.split("/")
                segments.update(parts[:-1])
                if parts[-1].endswith(".py"):
                    segments.add(parts[-1][:-3])
            self._segments[key] = frozenset(segments)
        return name in self._segments[key]

    @staticmethod
    def _could_shadow(ctx: Context, importer: str, first: str) -> bool:
        """Whether something next to the importer is called like the standard library module `first`: a
        script run from its folder imports that sibling before the standard library's."""
        folder = posixpath.dirname(importer)
        prefix = f"{folder}/" if folder else ""
        return f"{prefix}{first}.py" in ctx.files or any(f.startswith(f"{prefix}{first}/") for f in ctx.files)

    def _record_unresolved(self, rec: ImportRecord, importer: str, path: str, sha: str, stems: Sequence[str]) -> None:
        """One record per prefix of an unresolved import (`vendor.pkg` of `vendor.pkg.totals`)."""
        spec = rec.specifier
        last = rec.module.rsplit(".", 1)[-1]
        subject = spec.rsplit(".", 1)[0] if "." in rec.module and last in stems else spec
        if subject in self._unresolved_prefixes:
            return
        self._unresolved_prefixes.add(subject)
        record_quoted(self.target, "specifier_unresolved", subject,
                      f"an import resolves to nothing at {sha[:12]} and spells the name of the module: an importer may be missed "
                      "(a package that is not in the repository, or a source root this adapter does not read?)", f"{spec!r} in {importer}, module {path}")

    # ---- narrowing

    def _narrowing(self, fc: Any, symbols: Sequence[Symbol], head: str, pre: str | None) -> Narrowing | None:
        """Name narrowing, or None when it does not apply (everything is then affected).

        It applies only with a pre-change version, for a file that is neither deleted nor renamed, when
        no change is module level, and when both versions parse and use no dynamic access (`eval`, `exec`,
        `globals()`, `locals()`, `vars()`, a module `__getattr__`). The names compared with an importer's
        are the names of the changed declarations, of what a changed declaration registers itself with (the
        names in its decorators: `@router.get(...)` registers on `router`), and of every top-level
        declaration that spells one of them, inside the file, repeated to a fixed point, in the before and
        the after text. Module-level code that spells a name of that set stops narrowing."""
        if pre is None or fc.status in ("deleted", "renamed") or not symbols or any(s.is_module for s in symbols):
            return None
        pa = self._parse(head, fc.path)
        pb = self._parse(pre, fc.path) if fc.status != "added" else None
        if pa is None or (fc.status != "added" and pb is None):
            return None
        parses = [p for p in (pa, pb) if p is not None]
        if any(p.problems or p.uses_dynamic_access() for p in parses):
            return None
        changed = {n for s in symbols for n in (s.export_names or (s.name.split(".")[0],))}
        # members of a class that changed (`Invoice.total`): another declaration of the file reaches one by `.total`
        members = {seg for s in symbols if not s.is_module for seg in s.name.split(".")[1:]}
        declared = {n for p in parses for d in p.declarations for n in d.names}

        def reaches(p: Parsed, d: Declaration, names: set[str]) -> bool:
            return bool(p.decl_references(d) & names) or bool(members and p.decl_attributes(d) & members)

        closure = set(changed)
        for p in parses:  # what a changed declaration registers itself with
            for d in p.declarations:
                if changed & set(d.names):
                    closure |= (p.decorator_names(d) | p.global_names(d)) & declared
        grown = True
        while grown:
            grown = False
            for p in parses:
                for d in p.declarations:
                    if not (set(d.names) & closure) and reaches(p, d, closure):
                        closure |= set(d.names) | ((p.decorator_names(d) | p.global_names(d)) & declared)  # a reached handler registers on its receiver and writes its globals too
                        grown = True
        if any(closure & p.module_references() or members & p.module_attributes() for p in parses):
            return None
        # names whose behaviour changed (not the receivers a handler is registered on): calling one of them
        # where a declaration runs at import (an assignment, a decorator, a default, a class body) runs for every importer
        behaviour = set(changed)
        grown = True
        while grown:
            grown = False
            for p in parses:
                for d in p.declarations:
                    if not set(d.names) <= behaviour and reaches(p, d, behaviour):
                        behaviour |= set(d.names)
                        grown = True
        if any(behaviour & p.import_time_calls(d) for p in parses for d in p.declarations):
            return None
        reach: list[Evidence] = []
        for sha, p in ((head, pa), (pre, pb)):
            if p is None:
                continue
            for d in p.declarations:
                if set(d.names) & closure and not set(d.names) & changed:
                    ev = _evidence(self.repo, fc.path, sha, p, d.start)
                    if ev is not None and not any((e.path, e.line, e.detail) == (ev.path, ev.line, ev.detail) for e in reach):
                        reach.append(ev)
        return Narrowing(closure, reach)

    def _reach(self, importer: str, edges: Sequence[ImportEdge], sha: str, names: frozenset[str] | None, sites: Sequence[CallSite]) -> Reach:
        """What the use of `names` touches inside `importer` (the walk's hop). The top-level declarations that use a
        reached import are reached, with what they register themselves on (decorator receivers) and every declaration
        that spells a reached one, to a fixed point; every name a reached declaration binds is a name another file
        can import it by. The whole module is reached (None) when a use sits at module level, an import is never
        used, a dynamic import is made, the file does not parse or reaches names dynamically, module-level code spells
        a reached name or a reached name is called where a declaration runs at import time (the same cases
        that stop narrowing of the first hop)."""
        parsed = self._parse(sha, importer)
        if parsed is None:
            return Reach(frozenset(), None, (), False, ("the file could not be read",), True)
        if parsed.problems:
            return Reach(frozenset(), None, (), False, (f"the file did not parse ({parsed.problems[0]})",), True)
        whole = parsed.uses_dynamic_access()
        locals_: set[str] = set()
        reached: set[str] = set()
        lines: list[int] = []
        for e in edges:
            if e.kind == "dynamic":
                whole = True  # `import_module("x")` at some place: the module is used as a whole
            for b in e.bindings:
                if names is None or self._is_module_binding(b) or b.imported in names:
                    locals_.add(b.local)
        for cs in sites:
            if cs.form == "import":
                whole = True
                continue
            lines.append(cs.line)
            if cs.caller_symbol == MODULE:
                whole = True
            else:
                reached.add(cs.caller_symbol)
                if cs.symbol not in ("*", MODULE):
                    locals_.add(cs.symbol)
        return self._close(parsed, reached, locals_, whole, lines)

    def _close(self, parsed: Parsed, reached: set[str], locals_: set[str], whole: bool, lines: list[int]) -> Reach:
        """The closure of the reached declarations inside the file (every declaration that uses a reached one or
        an import binding that carries the change, to a fixed point), what they register themselves on, and the names
        the module exports them under. The whole module when module-level code uses a name whose behaviour changed or
        one is called where a declaration runs at import time (a registration receiver is not such a name)."""
        declared = {n for d in parsed.declarations for n in d.names}
        closure = set(reached)
        grown = True
        while grown:
            grown = False
            for d in parsed.declarations:
                if not set(d.names) & closure and parsed.decl_references(d) & (closure | locals_):
                    closure.update(d.names)
                    grown = True
        # what a reached declaration registers itself with (`@router.get(...)` registers on `router`, a `global` write):
        # another file that imports that receiver is reached, though the declarations on it are not
        receivers: set[str] = set()
        for d in parsed.declarations:
            if set(d.names) & closure:
                receivers |= (parsed.decorator_names(d) | parsed.global_names(d)) & declared
        used = closure | locals_
        if parsed.module_references() & used or any(used & parsed.import_time_calls(d) for d in parsed.declarations):
            whole = True
        out = {n for d in parsed.declarations if set(d.names) & (closure | receivers) for n in d.names}
        names = None if whole or not out else frozenset(out)
        return Reach(frozenset(closure | locals_), names, tuple(sorted(set(lines))), True, (), whole)

    def _own_reach(self, path: str, sha: str, seeds: set[str]) -> Reach:
        parsed = self._parse(sha, path)
        if parsed is None:
            return Reach(frozenset(seeds), None, (), False, ("the file could not be read",), True)
        if parsed.problems:
            return Reach(frozenset(seeds), None, (), False, (f"the file did not parse ({parsed.problems[0]})",), True)
        return self._close(parsed, set(seeds), set(), parsed.uses_dynamic_access(), [])

    # ---- uses

    @staticmethod
    def _is_module_binding(b: Binding) -> bool:
        return b.imported == "*" or "." in b.imported

    @staticmethod
    def _module_uses(parsed: Parsed, b: Binding) -> list[tuple[Use, str | None]]:
        """The uses of a module binding as (use, member): `m.name(...)` has member `name`; a use of the
        module itself (passed on, `getattr(m, ...)`, `m` alone) has member None. A dotted `import a.b.c`
        is used through `a.b.c.name`; a use of `a` that goes elsewhere (`a.other`) is not a use of it."""
        prefix = tuple(b.imported.split(".")[1:]) if "." in b.imported else ()
        out: list[tuple[Use, str | None]] = []
        k = len(prefix)
        for u in parsed.uses(b.local):
            chain = u.chain
            if chain[:k] != prefix[: len(chain)]:
                continue  # diverges: another module of the same package
            member = chain[k] if len(chain) > k else None
            if member in ("__dict__", "__getattribute__", "__getattr__"):
                member = None  # the whole namespace of the module
            out.append((u, member))
        return out

    def _survives(self, importer: str, edges: Sequence[ImportEdge], sha: str, names: set[str]) -> bool:
        """Whether any import of the importer can reach a name of the narrowing set. Star and dynamic
        imports are whole module; a module import with no use at all is kept (importing runs the module)."""
        parsed = self._parse(sha, importer)
        for e in edges:
            if e.kind in ("export_star", "dynamic"):
                return True
            if e.names is not None:
                if set(e.names) & names:
                    return True
                continue
            if parsed is None or not e.bindings:
                return True
            for b in e.bindings:
                uses = self._module_uses(parsed, b)
                if not uses or any(member is None or member in names for _u, member in uses):
                    return True
        return False

    def _edge_lines(self, importer: str, edges: Sequence[ImportEdge], sha: str, names: set[str] | None) -> list[Evidence]:
        """The import lines of an importer: the line that names the module and, for a multi-line import,
        the line of each name that matters (the changed names only when narrowing applies)."""
        parsed = self._parse(sha, importer)
        out: list[Evidence] = []
        for e in edges:
            out.append(e.evidence)
            if parsed is None:
                continue
            for b in e.bindings:
                if b.line and b.line != e.evidence.line and (names is None or self._is_module_binding(b) or b.imported in names):
                    ev = _evidence(self.repo, importer, sha, parsed, b.line)
                    if ev is not None:
                        out.append(ev)
        return _dedupe(out)

    def _call_sites(self, importer: str, edges: Sequence[ImportEdge], sha: str, changed_names: set[str] | None) -> list[CallSite]:
        """The uses in `importer` of what it imports from the changed file: of the changed names only when
        narrowing applies, else of everything it imports. An importer with no use gets one site at its
        import line (`form` is `import`)."""
        parsed = self._parse(sha, importer)
        sites: list[CallSite] = []
        for edge in edges:
            if parsed is None:
                continue

            def add(callee: str, u: Use, b: Binding | None) -> None:
                ev = _evidence(self.repo, importer, sha, parsed, u.line)
                if ev is None:
                    return
                imp = edge.evidence
                if b is not None and b.line and b.line != edge.evidence.line:
                    imp = _evidence(self.repo, importer, sha, parsed, b.line) or edge.evidence
                sites.append(CallSite(
                    repo=self.repo, path=importer, line=u.line, symbol=callee, evidence=ev, import_evidence=imp,
                    args=parsed.args_of(u), caller_symbol=parsed.enclosing(u.line), form=u.form,
                ))

            if edge.kind == "export_star":
                pool = changed_names
                if pool is None:
                    target = self._parse(sha, edge.target)
                    pool = {n for d in target.declarations for n in d.names} if target is not None else set()
                for name in sorted(pool):
                    for u in parsed.uses(name):
                        add(name, u, None)
                continue
            for b in edge.bindings:
                if self._is_module_binding(b):
                    for u, member in self._module_uses(parsed, b):
                        if changed_names is not None and member is not None and member not in changed_names:
                            continue
                        add(member or "*", u, b)
                else:
                    if changed_names is not None and b.imported not in changed_names:
                        continue
                    for u in parsed.uses(b.local):
                        add(b.imported, u, b)
        if not sites:
            edge = edges[0]
            first = edge.bindings[0] if edge.bindings else None
            sites.append(CallSite(
                repo=self.repo, path=importer, line=edge.evidence.line,
                symbol=MODULE if first is None else "*" if self._is_module_binding(first) else first.imported,
                evidence=edge.evidence, import_evidence=edge.evidence, args=(), caller_symbol=MODULE, form="import",
            ))
        sites.sort(key=lambda c: (c.line, c.symbol))
        return sites

    def resolve_module(self, importer: str, sha: str, module: str, level: int = 0) -> tuple[str, ...]:
        """The files a dotted module name means for `importer` at `sha` (`level` leading dots for a relative
        import); empty when it resolves to nothing in the tree. More than one file is an ambiguity the caller keeps."""
        ctx = self._context(sha)
        if ctx is None:
            return ()
        mm: ModuleMap = ctx.modules  # type: ignore[attr-defined]
        res = mm.resolve(module, importer) if level == 0 else mm.resolve_relative(importer, level, module)
        return tuple(res.files) if res is not None and res.found else ()
