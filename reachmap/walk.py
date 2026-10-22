"""The in-repo walk: from what a language adapter found changed, up the importer graph to the files a person or a
system invokes (entry points), saying how it got there.

Input is a `ChangeAnalysis` (changed symbols per file, the importers that survived narrowing and guards, the hub
rule already applied to the changed file), the language adapter that made it and the repository's entry-point
adapter, which says which files are entry points (a routes file, a module that declares route handlers). Output is
chains: for each way the change reaches an entry-point file, the ordered Evidence of every hop from the first import
of the changed file to the import in the entry-point file (or the changed line itself when the entry point is in the
changed file). The entry-point adapter turns chains into areas and adds its own route evidence.

Rules (docs/briefs/M7-entry-points.md):

- Each step asks the language adapter for the importers of the current file, narrowed against the names that reached
  it: the walk carries the set of reached exported names per file and narrows the next hop the way the first hop was
  narrowed, with the closure inside the file (`Reach`). A use the adapter cannot tie to a declaration (module level,
  an import that is never used, a dynamic import) reaches the whole module: the next hop is not narrowed.
- The walk stops at entry-point files, except through the exported names of an entry file that do not just wire it up
  (a helper declared in a handler or routes file and imported by another module): the entry-point adapter's
  `structural_names` (routers and apps, route holders) are not followed, since who imports them mounts them.
- A file with more importers than `hub_importers` is not passed through: `hub_cutoff`, once per hub (the same rule
  the language adapters apply to the changed file, `exceeds_hub` and `record_hub_cutoff`).
- When more files are reached at one hop than `module_cap`, the first `module_cap` files of that hop (in path order)
  are expanded, the others are only looked at for being entry files (their chains are kept: that costs nothing), and
  the walk goes no further from that hop: `module_cap` is recorded with the subject `hop:<n>` and the result says it
  is incomplete. A partial set is never presented as the whole one.
- A file in a chain whose importer list is incomplete, or that could not be read or did not parse cleanly, makes the
  chains through it incomplete (`Chain.complete`); the records stay.
- Each file is expanded once per reached-name set, so a cycle ends; a file already on the path is not entered again.
- Depth is unbounded inside a repository except by these caps. (The hop cap of the config is about edges between
  repositories and is not applied here.)

Test files are never walked: they are unit tests, not callers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from reachmap.incomplete import record
from reachmap.model import Evidence

if TYPE_CHECKING:  # the walk is generic over the adapters: it only calls the methods named below
    from reachmap.adapters import CallSite, ChangeAnalysis, FileImpact, ImportEdge, Reach, Symbol

__all__ = ["Chain", "DEFAULT_MODULE_CAP", "EntryFiles", "Visit", "WalkResult", "exceeds_hub", "record_hub_cutoff", "walk"]

DEFAULT_MODULE_CAP = 200


def exceeds_hub(importers: int, limit: int | None) -> bool:
    """The one hub rule: a file with more importers than `hub_importers` is not passed through."""
    return limit is not None and importers > limit


def record_hub_cutoff(target: Any, path: str, importers: int, limit: int | None) -> Any:
    """The one record of it (non-blocking), one per hub path: an equal record is not added twice."""
    return record(target, "hub_cutoff", path, detail=f"{importers} files import it; the limit is {limit}, so the walk does not pass through it")


class EntryFiles(Protocol):
    def is_entry_file(self, path: str, sha: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class Chain:
    """One way the change reaches an entry-point file.

    `files` run from the changed file to the entry file (a change inside the entry file has just that one);
    `hops` is the Evidence of each import on the way, from the import of the changed file in the first importer to
    `edge`, the import in the entry file (for a change inside the entry file, `hops` is the first changed line and
    `edge` is None). `reach` says what inside the entry file the change reaches; `names` are the exported names of
    the previous file that arrive (None: the whole module); `changed` are the changed symbols of a change inside the
    entry file. `sha` is the commit the evidence was read at (the pre-change commit for a deleted or renamed file);
    `pre_sha` is the pre-change commit of the change, when there is one. `complete` is False when an importer
    list or a file along the way could not be established completely; `reasons` says why."""

    repo: str
    sha: str
    pre_sha: str | None
    files: tuple[str, ...]
    hops: tuple[Evidence, ...]
    entry: str
    edge: Any  # ImportEdge | None
    reach: Any  # Reach
    names: frozenset[str] | None
    changed: tuple[Any, ...]  # tuple[Symbol, ...]
    complete: bool = True
    reasons: tuple[str, ...] = ()

    @property
    def direct(self) -> bool:
        return self.edge is None

    @property
    def before_path(self) -> str:
        """The path of the entry file in the pre-change text: the old path of a renamed file (the removed lines
        cite it), else the entry file itself."""
        return next((e.path for s in self.changed for e in s.evidence if self.pre_sha is not None and e.ref == self.pre_sha), self.entry)

    @property
    def source(self) -> str:
        """The changed file this chain starts from."""
        return self.files[0]


@dataclass(frozen=True, slots=True)
class Visit:
    """A file the walk reached beyond the changed files, with what the change touches inside it (`reach.locals`: the
    declarations that carry the change; `reach.whole`: the whole module). The edge adapters read it to find what else
    the change reaches in the repository (the client calls of a frontend change)."""

    file: str
    sha: str
    files: tuple[str, ...]
    hops: tuple[Evidence, ...]
    reach: Any  # Reach


@dataclass(frozen=True, slots=True)
class WalkResult:
    """The chains, and whether they are the whole answer: `complete` is False when a cap, a hub or an incomplete
    file limited the walk (`reasons` lists them; the records are made as they happen). `expanded` counts the files
    whose importers were searched (a file is expanded once per reached-name set); `depths` is the number of
    distinct files reached at each hop, starting at hop 1."""

    chains: tuple[Chain, ...]
    complete: bool
    reasons: tuple[str, ...] = ()
    expanded: int = 0
    depths: tuple[int, ...] = ()
    visited: tuple[Visit, ...] = ()


@dataclass(slots=True)
class _Node:
    file: str
    sha: str
    files: tuple[str, ...]
    hops: tuple[Evidence, ...]
    edges: list  # ImportEdge, the surviving imports of `file` that take what reached it
    names: frozenset[str] | None  # what reached this file from the one below
    sites: list  # CallSite
    complete: bool
    reasons: tuple[str, ...]
    pre_sha: str | None


def _first_changed_line(symbols: tuple[Any, ...]) -> Evidence | None:
    """The first changed line of a change inside an entry file (added lines first, in file order)."""
    for s in symbols:
        if s.evidence:
            return s.evidence[0]
    return None


def walk(
    analysis: "ChangeAnalysis",
    language: Any,
    entry: EntryFiles,
    *,
    limits: Any = None,
    hub_importers: int | None = None,
    module_cap: int | None = None,
    record_to: Any = None,
) -> WalkResult:
    """Walk from every changed source file of `analysis` to the entry-point files it reaches (see the module
    docstring). `language` is the adapter that made the analysis (its `importers`, `survivors`, `sites_for`,
    `reach` and `is_test_path` are what the walk calls), `entry` the repository's entry-point adapter.
    `hub_importers` and `module_cap` come from `limits` (the workspace limits) unless given; records go to
    `record_to`, by default where the language adapter records."""
    change = analysis.change
    head, pre = change.head_sha, change.pre_change_sha
    hub = hub_importers if hub_importers is not None else (limits.hub_importers if limits is not None else language.hub_importers)
    cap = module_cap if module_cap is not None else (getattr(limits, "module_cap", None) if limits is not None else None)
    cap = DEFAULT_MODULE_CAP if cap is None else cap
    target = record_to if record_to is not None else language.target
    prefix = getattr(language, "subject_prefix", "")  # `<repo>:` when the walk runs in a repository that is not the changed one
    chains: list[Chain] = []
    reasons: list[str] = []
    visits: list[Visit] = []
    state = {"complete": True, "expanded": 0}

    def incomplete(why: str) -> None:
        state["complete"] = False
        if why not in reasons:
            reasons.append(why)

    frontier: list[_Node] = []
    seen: set[tuple[Any, ...]] = set()
    structural_of = getattr(entry, "structural_names", None)

    def structural(path: str, sha: str) -> frozenset[str]:
        """The names of an entry file that only wire it up (routers and apps, route holders): who imports one of
        those mounts it, which the entry-point adapter already knows; they do not carry a change past the file."""
        return frozenset(structural_of(path, sha)) if structural_of is not None else frozenset()

    def push(node: _Node) -> None:
        # a file is expanded once per reached-name set *and* per set of imports that bring the names: two parents that
        # bring the same names through different imports reach different declarations of the file
        key = (node.sha, node.file, node.names, tuple((e.evidence.line, e.specifier, e.kind) for e in node.edges))
        if key in seen:
            return
        seen.add(key)
        frontier.append(node)

    def importers_of(file: str, sha: str, files: tuple[str, ...], hops: tuple[Evidence, ...], names: frozenset[str] | None,
                     complete: bool, rsn: tuple[str, ...], pre_sha: str | None) -> None:
        """Search the importers of `file`, apply the hub rule, narrow them by `names` and queue the survivors."""
        res = language.importers(file, sha)
        state["expanded"] += 1
        if not res.complete:
            complete = False
            rsn = rsn + tuple(f"importers of {file}: {why}" for why in res.reasons)
            incomplete(f"the importers of {file} were not established completely: " + "; ".join(res.reasons))
        by_importer: dict[str, list] = {}
        for e in res.edges:
            if not language.is_test_path(e.importer_path):
                by_importer.setdefault(e.importer_path, []).append(e)
        if exceeds_hub(len(by_importer), hub):
            record_hub_cutoff(target, prefix + file, len(by_importer), hub)
            incomplete(f"{file} is a hub ({len(by_importer)} importers): the walk does not pass through it")
            return
        for importer in sorted(by_importer):
            if importer in files:
                continue  # a cycle: the file is already on this path
            edges = language.survivors(importer, by_importer[importer], sha, names)
            if edges:
                push(_Node(
                    file=importer, sha=sha, files=files + (importer,), hops=hops + (edges[0].evidence,), edges=edges, names=names,
                    sites=language.sites_for(importer, edges, sha, names), complete=complete, reasons=rsn, pre_sha=pre_sha,
                ))

    def go_on(file: str, sha: str, files: tuple[str, ...], hops: tuple[Evidence, ...], reach: "Reach", complete: bool,
              rsn: tuple[str, ...], pre_sha: str | None) -> None:
        """Past an entry-point file: only through the exported names that do not just wire it up. A change that
        reaches the whole module of an entry file (a statement that runs when it loads) is not followed past it: the
        routes it affects are the ones in or through the file, and who imports the file mounts it."""
        if reach.names is None or structural_of is None:
            return  # an entry-point adapter that does not say which names wire a file up is not walked past
        rest = reach.names - structural(file, sha)
        if rest:
            importers_of(file, sha, files, hops, frozenset(rest), complete, rsn, pre_sha)

    # ---- the starts: every changed source file
    for impact in analysis.impacts:
        if impact.hub:
            incomplete(f"{impact.path} is a hub ({impact.importer_count} importers): the walk does not pass through it")
            continue  # recorded by the language adapter
        if impact.role == "test" or not impact.symbols:
            continue  # a test is a unit test, a change with no code reaches no one
        sha = pre if impact.status in ("deleted", "renamed") else head
        if sha is None:
            continue  # no pre-change version: the language adapter already said the importers of the old path are unknown
        if impact.status != "deleted" and entry.is_entry_file(impact.path, head):  # the entry file as it is now
            first = _first_changed_line(impact.symbols)
            seeds = {s.name.split(".")[0] for s in impact.symbols if not s.is_module and s.change != "removed"}
            own = language.own_reach(impact.path, head, seeds)
            own_reasons = tuple(f"{impact.path}: {why}" for why in own.reasons)
            chains.append(Chain(
                repo=analysis.repo, sha=head, pre_sha=pre, files=(impact.path,), hops=(first,) if first is not None else (), entry=impact.path,
                edge=None, reach=own, names=None, changed=tuple(impact.symbols), complete=own.complete, reasons=own_reasons,
            ))
            if not own.complete:
                incomplete(f"a change inside {impact.path} is not complete: " + "; ".join(own_reasons))
            go_on(impact.path, head, (impact.path,), (), own, own.complete, own_reasons, pre)
            continue
        by_importer: dict[str, list] = {}
        for e in impact.importers:
            by_importer.setdefault(e.importer_path, []).append(e)
        names = frozenset(impact.reached_names) if impact.reached_names is not None else None
        start_complete = impact.importers_complete
        start_reasons = tuple(f"{impact.path}: {why}" for why in impact.importer_reasons) if not start_complete else ()
        search_path = next((e.target for e in impact.importers if e.target), impact.path)  # the old path of a renamed file
        for caller in impact.callers:
            edges = language.survivors(caller.path, by_importer.get(caller.path, ()), sha, names)
            if not edges:
                continue
            push(_Node(
                file=caller.path, sha=sha, files=(search_path, caller.path), hops=(edges[0].evidence,), edges=edges, names=names,
                sites=list(caller.call_sites), complete=start_complete, reasons=start_reasons, pre_sha=pre,
            ))
        if not start_complete:
            incomplete(f"the importers of {impact.path} were not established completely: " + "; ".join(impact.importer_reasons))

    # ---- breadth first, one hop at a time
    depths: list[int] = []
    depth = 1
    while frontier:
        level = sorted(frontier, key=lambda n: (n.file, n.files, tuple(sorted(n.names)) if n.names is not None else ()))
        frontier = []
        files = sorted({n.file for n in level})
        depths.append(len(files))
        keep = set(files)
        if len(files) > cap:
            # the first `cap` files of the hop are expanded; the rest are only looked at for being entry files (that is free)
            record(target, "module_cap", f"{prefix}hop:{depth}",
                   detail=f"{len(files)} files are reached at hop {depth}; the limit is {cap}, so the walk expands the first {cap} and goes no further from this hop")
            incomplete(f"module cap: {len(files)} files reached at hop {depth}, the limit is {cap}")
            keep = set(files[:cap])
        prefetch = getattr(language, "prefetch_importers", None)
        if prefetch is not None:  # the importers of the whole hop in one search, not one search per file
            by_sha: dict[str, set[str]] = {}
            for node in level:
                if node.file in keep and not entry.is_entry_file(node.file, node.sha):
                    by_sha.setdefault(node.sha, set()).add(node.file)
            for sha, paths in by_sha.items():
                prefetch(sorted(paths), sha)
        for node in level:
            is_entry = entry.is_entry_file(node.file, node.sha)
            if node.file not in keep and not is_entry:
                continue
            reach = language.reach(node.file, node.edges, node.sha, node.names, node.sites)
            complete = node.complete and reach.complete
            rsn = node.reasons + (() if reach.complete else tuple(f"{node.file}: {why}" for why in reach.reasons))
            visits.append(Visit(node.file, node.sha, node.files, node.hops, reach))
            if is_entry:
                chains.append(Chain(
                    repo=analysis.repo, sha=node.sha, pre_sha=node.pre_sha, files=node.files, hops=node.hops, entry=node.file,
                    edge=node.edges[0], reach=reach, names=node.names, changed=(), complete=complete, reasons=rsn,
                ))
                if not complete:
                    incomplete(f"a chain through {node.file} is not complete: " + "; ".join(rsn))
                if node.file in keep:
                    go_on(node.file, node.sha, node.files, node.hops, reach, complete, rsn, node.pre_sha)
                continue
            if not reach.complete:
                incomplete(f"{node.file} could not be read or reasoned about completely: " + "; ".join(reach.reasons))
            importers_of(node.file, node.sha, node.files, node.hops, reach.names, complete, rsn, node.pre_sha)
        if len(keep) < len(files):
            frontier = []  # the capped hop is the last one expanded
        depth += 1
    chains.sort(key=lambda c: (c.entry, c.files, tuple(e.line for e in c.hops)))
    return WalkResult(tuple(chains), state["complete"], tuple(reasons), state["expanded"], tuple(depths), tuple(visits))
