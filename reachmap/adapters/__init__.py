"""Language adapter protocols and the types they share (context 7.3).

An adapter answers questions about code at a pinned commit and never says "none" for a
failure: every read is found, absent or unreadable, an unreadable one is an incompleteness
record (made by the git layer, or by the adapter when the limit is its own), and a result
that could not be established completely carries that fact (`complete=False`), so a short
list is never taken for the whole list.

Line numbers are 1-based. An `Evidence` is always pinned to a full commit sha.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Protocol, Sequence, runtime_checkable

from reachmap.errors import ModelError, WordingError
from reachmap.incomplete import kind_spec, record, register_kind
from reachmap.model import Area, Evidence

__all__ = [
    "Binding", "CallSite", "Caller", "ChangeAnalysis", "ChangedFile", "DefinitionCount", "EntryPointAdapter", "Exclusion",
    "FileImpact", "GuardVerdict", "ImportEdge", "ImporterResult", "LanguageAdapter", "MODULE", "Reach", "Reached", "Route", "Symbol",
    "UnitTest", "diff_routes", "guard_route_changes", "normalize_path",
]

# Kinds both language adapters make records of. The first adapter to be imported registers them; the
# wording does not name a language.
for _kind, _blocking, _template in (
    ("parse_degraded", False,
     "What {subject} declares and imports was not fully established, because the file did not parse cleanly"),
    ("specifier_unresolved", True,
     "Whether {subject} reaches the changed file was not established, because an import there could not be resolved"),
    ("file_not_analysed", False,
     "What imports {subject} was not established, because the adapter does not analyse that kind of file"),
    ("route_path_partial", False,
     "The full path of the routes behind {subject} was not established, because a part of it cannot be evaluated statically"),
    ("mount_unresolved", True,
     "Whether {subject} mounts a router was not established, because what it mounts could not be resolved"),
    ("route_change_withheld", True,
     "Which routes of {subject} the change removed or added was not established, because its route table could not be read completely at that version"),
):
    try:
        kind_spec(_kind)
    except ModelError:
        register_kind(_kind, _template, _blocking)

try:  # a blocking record that cannot be made non-blocking: the configuration pointed at routing files and the tree has none
    kind_spec("routes_unmatched")
except ModelError:
    register_kind("routes_unmatched", "Which screens or routes {subject} serves was not established, because nothing in the tree matches what the configuration looks for", True, always_blocking=True)

MODULE = "<module>"  # the name of a change outside every declaration: it affects every importer
SIDES = ("before", "after")
CHANGES = ("modified", "added", "removed")
EXCLUSION_REASONS = ("named_export_narrowing", "guard")
FORMS = ("call", "reference", "import")


@dataclass(frozen=True, slots=True)
class Symbol:
    """A top-level declaration (or `<module>`) touched by a change.

    `start` and `end` are the inclusive lines of the outermost declaration on `side` (the
    text version they refer to). `nested` names the functions inside it that contain the
    changed lines, outermost first. `change` is modified, added or removed. `export_names`
    are the names the module exports it under, looked up in the before and after export
    lists; empty means it is not exported. `evidence` cites the changed lines: added lines
    at the after commit, removed lines at the before commit; `added_lines` and
    `removed_lines` are their numbers in the after and the before text. `guards` cites the
    `if (<param> === "<literal>")`, `else` or `case` lines that enclose every changed line, when
    there are such lines (the condition a call has to meet to reach the change)."""

    repo: str
    path: str
    name: str
    kind: str
    start: int
    end: int
    exported: bool
    nested: tuple[str, ...]
    side: str
    change: str = "modified"
    export_names: tuple[str, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    added_lines: tuple[int, ...] = ()
    removed_lines: tuple[int, ...] = ()
    guards: tuple[Evidence, ...] = ()

    def __post_init__(self) -> None:
        if self.side not in SIDES:
            raise ValueError(f"Symbol.side must be one of {SIDES}, got {self.side!r}")
        if self.change not in CHANGES:
            raise ValueError(f"Symbol.change must be one of {CHANGES}, got {self.change!r}")
        if self.start < 1 or self.end < self.start:
            raise ValueError(f"Symbol span {self.start}..{self.end} is not a 1-based inclusive range")

    @property
    def is_module(self) -> bool:
        return self.name == MODULE


@dataclass(frozen=True, slots=True)
class Binding:
    """One name an import brings in: `imported` is a name, `default` or `*` (the module itself). For Python a
    dotted `import a.b.c` (no alias) is `imported="a.b.c"` bound through its first segment, `local="a"`."""

    imported: str
    local: str
    type_only: bool = False
    line: int = 0  # the line that names this binding when it differs from the statement's (a multi-line Python import); 0 = the edge's line


@dataclass(frozen=True, slots=True)
class ImportEdge:
    """One import of a file by another, as written at `evidence` (the line holding the
    specifier). `names` is None for a whole-module import (namespace, default, side effect,
    dynamic, require, `export *`), else the named imports, `type` ones included."""

    importer_path: str
    specifier: str
    names: tuple[str, ...] | None
    evidence: Evidence
    kind: str = "import"  # import | reexport | export_star | dynamic | require
    type_only: bool = False
    bindings: tuple[Binding, ...] = ()
    target: str = ""  # the file the specifier resolved to


@dataclass(frozen=True, slots=True)
class CallSite:
    """A use of a changed symbol in an importer. `symbol` is the changed symbol's export name
    as called, `caller_symbol` the top-level declaration the use sits in (`<module>` at the top
    level). `form` is `call` (`name(...)`, `ns.name(...)`, `new Name(...)`), `reference` (any
    other use: JSX, passed as a value) or `import` (the importer uses nothing it can be tied
    to: the import line is the evidence). `args` is the source text of each argument."""

    repo: str
    path: str
    line: int
    symbol: str
    evidence: Evidence
    import_evidence: Evidence | None
    args: tuple[str, ...]
    caller_symbol: str = MODULE
    form: str = "call"

    def __post_init__(self) -> None:
        if self.form not in FORMS:
            raise ValueError(f"CallSite.form must be one of {FORMS}, got {self.form!r}")


@dataclass(frozen=True, slots=True)
class Exclusion:
    """An importer (or a call) stated as not affected, with the evidence that shows it."""

    subject_path: str
    reason: str
    evidence: tuple[Evidence, ...]

    def __post_init__(self) -> None:
        if self.reason not in EXCLUSION_REASONS:
            raise ValueError(f"Exclusion.reason must be one of {EXCLUSION_REASONS}, got {self.reason!r}")
        if not self.evidence:
            raise ValueError("an exclusion needs evidence")


@dataclass(frozen=True, slots=True)
class Caller:
    """A direct importer of a changed file that survives narrowing: the first hop outside it.
    `reason` is None, `literal_enters_guard` (a literal the guard lets in) or `undecidable`
    (an argument that cannot be decided statically); both only when the change sits under a
    guard."""

    repo: str
    path: str
    symbol: str  # the top-level declaration of the first use, `<module>` when none
    reason: str | None
    call_sites: tuple[CallSite, ...]
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True, slots=True)
class UnitTest:
    """A test file that imports a changed file or a caller (one hop), with its import evidence."""

    repo: str
    path: str
    via: str  # the changed file or the caller it imports
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True, slots=True)
class ChangedFile:
    repo: str
    path: str
    role: str  # source | test
    status: str


@dataclass(frozen=True, slots=True)
class ImporterResult:
    """Every import of `target` at `sha`. `complete` is False when the search could not cover
    everything (a cut or failed search, an unreadable candidate file): the edges are then the
    ones found, never the whole list. `reasons` says why."""

    target: str
    sha: str
    edges: tuple[ImportEdge, ...]
    complete: bool
    reasons: tuple[str, ...] = ()
    candidates: int = 0

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(sorted({e.importer_path for e in self.edges}))


@dataclass(frozen=True, slots=True)
class DefinitionCount:
    """How many times `name` is defined in non-test code at `sha`. `count` is None when the
    search could not be made (state unreadable): the name is then not established and is never
    distinctive."""

    name: str
    sha: str
    state: str  # found | unreadable
    count: int | None
    sites: tuple[Evidence, ...] = ()
    complete: bool = True

    def distinctive(self, distinct_max: int) -> bool:
        return self.state == "found" and self.complete and self.count is not None and self.count <= distinct_max


@dataclass(frozen=True, slots=True)
class FileImpact:
    """What the adapter established about one changed file."""

    repo: str
    path: str
    role: str
    status: str
    symbols: tuple[Symbol, ...] = ()
    importers: tuple[ImportEdge, ...] = ()
    importer_count: int = 0  # distinct non-test importer files
    importers_complete: bool = True
    importer_reasons: tuple[str, ...] = ()
    callers: tuple[Caller, ...] = ()
    exclusions: tuple[Exclusion, ...] = ()
    unit_tests: tuple[UnitTest, ...] = ()
    hub: bool = False
    not_distinctive: tuple[str, ...] = ()  # changed names defined too often to link code by name (recorded)
    names_unreadable: tuple[str, ...] = ()  # changed names whose definitions could not be counted (never distinctive)
    narrowed: bool = False
    reached_names: tuple[str, ...] | None = None  # the names the change reaches in this file (what narrowing compared importers with); None: not narrowed


@dataclass(frozen=True, slots=True)
class Reach:
    """What the use of reached names touches inside one file (the in-repo walk asks for it at every hop).

    `locals` are the names in the file that carry the change: the import bindings that brought the reached
    names in, and the top-level declarations that use them, repeated to a fixed point. `names` are the names
    other files import those declarations under (what the next hop is narrowed with); None means the whole
    module (a use at module level, an import that is never used, a dynamic import, a `*` import, a file that
    cannot be read or reasoned about): the next hop is not narrowed. `whole` says that is the reason: an entry-point
    adapter then reaches every route in or through the file (undecidable means affected); `names` is also None when the
    declarations reached are exported by no name, which reaches only what `locals` names. `lines` are the lines of the uses.
    `complete` is False when the file could not be read or did not parse cleanly (`reasons` says why)."""

    locals: frozenset[str]
    names: frozenset[str] | None
    lines: tuple[int, ...] = ()
    complete: bool = True
    reasons: tuple[str, ...] = ()
    whole: bool = False  # the whole module is reached (`names` is None because of it, not merely because nothing reached is exported)


ROUTE_KINDS = ("screen", "api_route")


@dataclass(frozen=True, slots=True)
class Route:
    """One route an entry-point adapter read: a screen of a router file, or an HTTP route with its full path.

    `path` is the full path as the framework would serve it (`/billing/invoices/:invoiceId`,
    `/api/billing/invoices/{invoice_id}`); a segment that cannot be evaluated statically is written
    `<unknown:EXPR>` and `partial` is True. `handler` is what the route runs (the page binding of a screen, the
    handler function of an API route), `target` the file that defines it (the page file; the handler's file),
    `module` the product module (the folder a routes glob matches; the service folder). `evidence` is the route's
    own evidence in the order of the README: for a screen the import of the page and the route line, for an API
    route the decorator and then each `include_router` or `mount` line from the router outwards."""

    repo: str
    kind: str
    method: str  # GET, POST, ... for an API route; empty for a screen
    path: str
    file: str  # the file that holds the route line (the decorator, the `<Route>` element)
    handler: str
    target: str
    module: str
    evidence: tuple[Evidence, ...]
    partial: bool = False
    path_approximate: bool = False  # the path is a reading of a pattern that was not evaluated (a regex): an http match against it is by name only

    def __post_init__(self) -> None:
        if self.kind not in ROUTE_KINDS:
            raise ValueError(f"Route.kind must be one of {ROUTE_KINDS}, got {self.kind!r}")
        if not self.evidence:
            raise ValueError("a route needs evidence")

    @property
    def label(self) -> str:
        return f"{self.method} {self.path}" if self.method else self.path

    @property
    def line(self) -> Evidence:
        """The route line: the first line of an API route's evidence (its decorator), the last of a screen's."""
        return self.evidence[0] if self.kind == "api_route" else self.evidence[-1]


@dataclass(frozen=True, slots=True)
class GuardVerdict:
    """What a guard says about one call. `state` is excluded, affected or undecidable
    (undecidable counts as affected); `reason` is None, `literal_enters_guard` or `undecidable`,
    set only when the change sits under a guard; `evidence` is the guard line of an excluded call."""

    state: str
    reason: str | None = None
    evidence: tuple[Evidence, ...] = ()


@dataclass(frozen=True, slots=True)
class ChangeAnalysis:
    """The adapter's answer for a whole change: one FileImpact per changed file."""

    repo: str
    change: Any
    files: tuple[ChangedFile, ...]
    impacts: tuple[FileImpact, ...]

    def impact_for(self, path: str) -> FileImpact:
        for impact in self.impacts:
            if impact.path == path:
                return impact
        raise KeyError(path)


_UNKNOWN = re.compile(r"<unknown:.*?>")


def normalize_path(path: str) -> str:
    """A route path with no doubled slash and no trailing one (the root stays `/`): `/a/` joined with `b/` is `/a/b`,
    an empty part adds nothing. The text of an `<unknown:EXPR>` part is left as it was written."""
    out, last = "", 0
    for m in _UNKNOWN.finditer(path):
        out += re.sub("/{2,}", "/", path[last:m.start()]) + m.group()
        last = m.end()
    out += re.sub("/{2,}", "/", path[last:])
    return out.rstrip("/") or "/"


def diff_routes(old: Sequence[Route], new: Sequence[Route]) -> tuple[list[Route], list[Route]]:
    """(removed, added) between two route tables by method and full path, each route with the evidence of its own
    version: what `routes_changed` of every entry-point adapter answers."""
    old_keys, new_keys = {(r.method, r.path) for r in old}, {(r.method, r.path) for r in new}
    return [r for r in old if (r.method, r.path) not in new_keys], [r for r in new if (r.method, r.path) not in old_keys]


def guard_route_changes(
    target: Any, repo: str, removed: list[Route], added: list[Route], *, base_ok: bool, head_ok: bool, why: str = "",
    unsure_removed: Callable[[Route], bool] | None = None, unsure_added: Callable[[Route], bool] | None = None,
) -> tuple[list[Route], list[Route]]:
    """The one rule for what `diff_routes` says when a route table could not be read completely (context 5, Shape A: an absence the tool made).
    A route is removed only if the table at the head is whole (a file that did not parse has routes that were not read, not routes that were
    deleted), and added only if the table at the base is whole. An adapter that knows where its table has a hole says which routes the hole may hide
    (`unsure_removed(route)` for a removal, looked up in the head table, `unsure_added(route)` for an addition, in the base table): those are
    withheld, the others stand. What is not claimed is said, once per repository: a blocking `route_change_withheld` whose detail counts the removals
    and additions left out."""
    kept_removed = [r for r in removed if head_ok and not (unsure_removed and unsure_removed(r))]
    kept_added = [r for r in added if base_ok and not (unsure_added and unsure_added(r))]
    left_out_removed, left_out_added = len(removed) - len(kept_removed), len(added) - len(kept_added)
    if left_out_removed or left_out_added:
        fixed = f"{left_out_removed} removal(s) and {left_out_added} addition(s) were not claimed"
        try:
            record(target, "route_change_withheld", repo, detail=fixed + (f": {why}" if why else ""))
        except WordingError:  # what the reader said quotes code that trips the wording check: leave it out, never crash
            record(target, "route_change_withheld", repo, detail=fixed)
    return kept_removed, kept_added


@dataclass(frozen=True, slots=True)
class Reached:
    """An area an entry-point adapter reached, with what the area is made of: `routes` are the routes behind it (the
    primary route first, then the others a screen is also mounted at), `complete` is False when something along its
    chains could not be established completely (`reasons` says why; the records stay). The area's `priority` is a
    placeholder: the model's `assign_priorities` sets it over all areas of a run."""

    area: Area
    routes: tuple[Route, ...]
    complete: bool = True
    reasons: tuple[str, ...] = ()
    sources: tuple[Any, ...] = ()  # the walk chains that reached it, one per chain of the area (M8 shows the way inside the repository)


@runtime_checkable
class EntryPointAdapter(Protocol):
    """Context 7.3: what a person or a system invokes. An entry-point adapter is bound to one repository and its
    language adapter; the walk (`reachmap.walk`) asks it which files are entry points and hands it the chains that
    end in one. `routes` and `routes_changed` read the route table itself (for the http edge and the test sources)."""

    kind: str

    def is_entry_file(self, path: str, sha: str) -> bool: ...

    def entry_points_reached(self, chains: Any, sha: str | None = None) -> list[Reached]: ...  # areas with their routes and whether each is complete

    def structural_names(self, path: str, sha: str) -> Any: ...  # the exported names of an entry file that only wire it up

    problems: list[str]  # what the adapter could not establish so far (an unreadable routes file, a route table that is not whole)

    def routes(self, repo: str, sha: str) -> list[Route]: ...

    def routes_changed(self, before: str | None, after: str) -> tuple[list[Route], list[Route]]: ...  # removed, added


@runtime_checkable
class LanguageAdapter(Protocol):
    """Context 7.3, with the pinned commit, the git layer and a record target made explicit:
    an adapter is bound to a repository, its Git layer and a record target (a list or an
    Analysis), and each method takes the sha it reads at."""

    def is_test_path(self, path: str) -> bool: ...

    def changed_symbols(self, change: Any, file: Any, record_to: Any = None) -> list[Symbol]: ...

    def importers(self, path: str, sha: str, record_to: Any = None) -> ImporterResult: ...

    def callers(self, symbol: Symbol, sha: str, record_to: Any = None) -> list[CallSite]: ...

    def analyze_file(self, change: Any, file: Any, record_to: Any = None) -> FileImpact: ...

    def analyze(self, change: Any, record_to: Any = None) -> ChangeAnalysis: ...

    def guards(self, symbol: Symbol, call: CallSite, *, before_sha: str | None, after_sha: str | None) -> str: ...  # excluded | affected | undecidable

    def definitions(self, name: str, sha: str, record_to: Any = None) -> DefinitionCount: ...
