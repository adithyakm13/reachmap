"""The cross-repository walker: follow a change along the configured edges, hop by hop, with evidence at every hop.

`propagate(workspace, repo, change, git=...)` analyses the change in its own repository (the language adapter and the
repository's entry-point adapter, as for any change), then follows the edges out of it. Each edge kind has an adapter
(`reachmap.edges`); the walker owns what section 7.5 of the context says every edge must obey, in one place:

- the hop cap (`limits.hops`): an edge past it is not followed and `hop_cap` is recorded with the subject
  `edge:<kind>:<from>:<to>` in the edge's declared direction (non-blocking), but only when the pass had something to
  carry over that edge;
- the module cap and the hub rule are the in-repo walk's (`reachmap.walk`), applied inside every repository's pass; the
  walker never passes through a hub;
- distinctiveness before any name based match (`limits.distinct_max`, counted by definitions through the language
  adapter) and by-name matches as candidates (`Area.by_name`, priority `candidate`, shown apart, never must test);
- every hop pins the revision it read in the other repository: its primary branch for a change that is not released,
  each configured branch where the edge needs it (a consumer's pins), the head for the changed repository;
- a failed read on an edge is an incompleteness on that edge (the git layer's `read_unreadable`, and the hop and the
  propagation say they are incomplete), never "not adopted", "no caller" or "no consumer";
- a removed thing is followed too (a removed route still called by a client), and an added route no client calls yet
  is said;
- a repository and a set of seeds is processed once, so a cycle between repositories ends; the order is deterministic
  (by depth, then repository, path and line).

The consumer lines of a contract read and the client line of an http call are the "changed lines" of the other
repository's own pass: they are handed to its language adapter as a synthetic change (the same text before and after,
the lines added), so its changed symbols, callers, narrowing, unit tests and entry points are exactly those of a real
change. A chain that crosses a repository keeps the evidence of every hop (see `crossing_prefix`).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from reachmap import config, edges, entrypoints
from reachmap.adapters import ChangeAnalysis, Reached, UnitTest
from reachmap.adapters.base import dedupe
from reachmap.chains import crossing_prefix, python_segment
from reachmap.diffparse import ChangeSet, DiffLine, FileChange, Hunk
from reachmap.edges import Hop, Pin, Seed, edge_incomplete, edge_subject
from reachmap.gitlayer import Git, ReadFailed, RefNotFound
from reachmap.incomplete import Incompleteness, record, records_of
from reachmap.model import Area, Evidence, assign_priorities

__all__ = ["Pass", "Propagation", "Run", "UnsupportedLanguage", "crossing_prefix", "propagate", "python_segment", "seed_change", "walk_edges"]


@dataclass(frozen=True, slots=True)
class Pass:
    """One repository's own pass: the change (real, or the synthetic one made of seed lines), its language analysis and
    the entry points it reaches. `depth` is the number of edges followed to get here (0 for the changed repository);
    `prefix` is the evidence leading here from the original change (empty for the changed repository); `hop` is the
    hop that started the pass."""

    repo: str
    depth: int
    sha: str
    change: Any
    analysis: ChangeAnalysis
    reach: Any  # entrypoints.EntryResult | None
    hop: Hop | None = None
    prefix: tuple[Evidence, ...] = ()
    seed: Seed | None = None
    candidate: bool = False

    @property
    def reached(self) -> tuple[Reached, ...]:
        return self.reach.reached if self.reach is not None else ()

    @property
    def complete(self) -> bool:
        return self.reach.complete if self.reach is not None else True

    @property
    def key(self) -> tuple[Any, ...]:
        if self.seed is None:
            return (self.repo, self.sha, "change")
        return (self.repo, self.sha, self.seed.path, self.seed.lines)


@dataclass(frozen=True, slots=True)
class Propagation:
    """What a change reaches across repositories. Every list is deterministic; every item has evidence pinned to a
    commit. `reached` are the areas (screens, routes) of every repository with their chains composed across the
    repositories (a screen reached through an api has a chain that starts in the other repository); `complete` is
    False when something could not be established (`reasons`); a short list is never "the" list then. `unfollowed` are
    declared edges whose kind has no adapter yet (not followed, and not an absence). `unread_entry_points` names the
    repositories a pass ran in whose entry-point kind has no adapter yet (a Celery worker): what they run was not
    read, which is a stated limit and not an answer of "nothing"."""

    repo: str
    change: Any
    passes: tuple[Pass, ...]
    hops: tuple[Hop, ...]
    reached: tuple[Reached, ...]
    http_edges: tuple[Any, ...]
    backend_calls: tuple[Any, ...]
    consumers: tuple[Any, ...]
    contract_changes: tuple[Any, ...]
    contract_reads: tuple[Any, ...]
    contract_candidates: tuple[Any, ...]
    findings: tuple[Any, ...]
    unit_tests: tuple[UnitTest, ...]
    records: tuple[Incompleteness, ...]
    unfollowed: tuple[Any, ...]
    complete: bool
    reasons: tuple[str, ...]
    unread_entry_points: tuple[str, ...] = ()

    @property
    def areas(self) -> tuple[Area, ...]:
        return tuple(r.area for r in self.reached)

    def evidence(self) -> Iterable[Evidence]:
        """Every Evidence the propagation holds (for the citation audit: each one must resolve at its sha)."""
        for p in self.passes:
            yield from _pass_evidence(p)
        for r in self.reached:
            yield from r.area.all_evidence()
            for route in r.routes:
                yield from route.evidence
        for h in self.hops:
            yield from h.source_evidence
            yield from h.target_evidence
            yield from h.chain
        for group in (self.http_edges, self.backend_calls, self.consumers, self.contract_changes, self.contract_reads,
                      self.contract_candidates, self.findings):
            for item in group:
                yield from item.all_evidence()
        for u in self.unit_tests:
            yield from u.evidence


def _pass_evidence(p: Pass) -> Iterable[Evidence]:
    for impact in p.analysis.impacts:
        for s in impact.symbols:
            yield from s.evidence
        for edge in impact.importers:
            yield edge.evidence
        for caller in impact.callers:
            yield from caller.evidence
            for cs in caller.call_sites:
                yield cs.evidence
                if cs.import_evidence is not None:
                    yield cs.import_evidence
        for ex in impact.exclusions:
            yield from ex.evidence
        for ut in impact.unit_tests:
            yield from ut.evidence
    if p.reach is not None:
        for chain in p.reach.walk.chains:
            yield from chain.hops


# ------------------------------------------------------------------------------ the run


class UnsupportedLanguage(ValueError):
    """A repository's language has no adapter yet: what lies in it was not read, which the walker records on the edge."""


def language_adapter(repo: config.Repo, git: Git, limits: config.Limits, remote: str | None = None) -> Any:
    """The language adapter a repository's config selects."""
    if repo.language in ("typescript", "javascript"):
        from reachmap.adapters.js import JsAdapter

        return JsAdapter.from_config(repo, git, limits=limits, remote=remote)
    if repo.language == "python":
        from reachmap.adapters.python import PyAdapter

        return PyAdapter.from_config(repo, git, limits=limits, remote=remote)
    if repo.language in ("java", "kotlin"):
        from reachmap.adapters.jvm import JvmAdapter

        return JvmAdapter.from_config(repo, git, limits=limits, remote=remote)
    raise UnsupportedLanguage(f"no language adapter for {repo.language!r} (repository {repo.name})")


class Run:
    """The walker's context for one propagation: the workspace, one git layer and one set of adapters per repository
    (each keeps what it read), the pins, and what the edge adapters found. Edge adapters read the other repositories
    through it and add to its lists; `problem` notes what could not be established."""

    def __init__(self, workspace: config.Workspace, git: Git, *, limits: config.Limits | None = None, as_of: str | None = None) -> None:
        self.workspace = workspace
        self.git = git
        self.limits = limits or workspace.limits
        self.as_of = as_of  # ISO 8601: the branches the edges read are pinned as of this time
        self.origin: Pass | None = None
        self.origin_repo: str | None = None  # the changed repository: its record subjects stay bare, the others carry their repo
        self._languages: dict[str, Any] = {}
        self._entries: dict[str, Any] = {}
        self._clients: dict[str, Any] = {}
        self._primary: dict[str, Pin | None] = {}
        self._branches: dict[str, dict[str, Pin] | None] = {}
        self._remotes: dict[str, str] = {}
        self.problems: list[str] = []
        self.http_edges: list[Any] = []
        self.backend_calls: list[Any] = []
        self.consumers: list[Any] = []
        self.contract_changes: list[Any] = []
        self.contract_reads: list[Any] = []
        self.contract_candidates: list[Any] = []
        self.findings: list[Any] = []
        self.hops: list[Hop] = []
        self._memo: dict[Any, Any] = {}

    # ---- recording

    @property
    def target(self) -> Any:
        return self.git.target

    def problem(self, why: str) -> None:
        if why not in self.problems:
            self.problems.append(why)

    def memo(self, key: Any, make: Any) -> Any:
        """A value computed once per run (adapters of one edge share what they read)."""
        if key not in self._memo:
            self._memo[key] = make()
        return self._memo[key]

    # ---- repositories

    def repo(self, name: str) -> config.Repo:
        return self.workspace.repos[name]

    def remote(self, name: str) -> str:
        if name not in self._remotes:
            cfg = self.repo(name)
            if cfg.remote:
                self._remotes[name] = cfg.remote
            else:
                from reachmap.hosts import get_host

                self._remotes[name] = get_host(cfg.host, record_to=self.git.target).remote(name)
        return self._remotes[name]

    def language(self, name: str) -> Any:
        if name not in self._languages:
            adapter = language_adapter(self.repo(name), self.git, self.limits, self.remote(name))
            if self.origin_repo is not None and name != self.origin_repo:
                adapter.subject_prefix = f"{name}:"  # `hub_cutoff` and `module_cap` of another repository name it
            self._languages[name] = adapter
        return self._languages[name]

    def adopt(self, name: str, language: Any, entry: Any = None) -> None:
        """Use the adapters a caller already built for a repository (the changed one, whose pass it ran)."""
        self._languages[name] = language
        if entry is not None:
            self._entries[name] = entry

    def entry(self, name: str) -> Any | None:
        if name not in self._entries:
            self._entries[name] = entrypoints.entry_adapter(self.repo(name), self.language(name))
        return self._entries[name]

    def client(self, name: str) -> Any | None:
        if name not in self._clients:
            from reachmap.adapters.http_client import client_adapter

            self._clients[name] = client_adapter(self.repo(name), self.language(name))
        return self._clients[name]

    # ---- pins

    def primary(self, name: str) -> Pin | None:
        """The repository's primary branch at its head now, pinned to a sha. None when it could not be read (the git
        layer recorded it) or the branch does not exist: the edge is then incomplete, never empty."""
        if name not in self._primary:
            cfg = self.repo(name)
            pin: Pin | None = None
            try:
                sha = self.git.pin(self.remote(name), cfg.primary_branch, self.as_of, kind="branch")
                pin = Pin(name, cfg.primary_branch, sha, "primary")
            except RefNotFound as exc:
                record(self.git.target, "read_unreadable", f"branch {cfg.primary_branch} of {name}", detail=str(exc))
                self.problem(f"the primary branch {cfg.primary_branch} of {name} does not exist: {exc}")
            except ReadFailed as exc:
                self.problem(f"the primary branch {cfg.primary_branch} of {name} could not be read: {exc}")
            self._primary[name] = pin
        return self._primary[name]

    def branch_pins(self, name: str) -> dict[str, Pin] | None:
        """Every configured branch of the repository that exists, each pinned: `branches` patterns against the heads
        of the remote. None when the heads could not be listed (recorded by the git layer)."""
        if name not in self._branches:
            cfg = self.repo(name)
            res = self.git.heads(self.remote(name), list(cfg.branches))
            if res.state == "unreadable":
                self.problem(f"the branches of {name} could not be listed: {res.detail}")
                self._branches[name] = None
            elif res.state == "absent":
                self._branches[name] = {}
            elif self.as_of is None:
                self._branches[name] = {b: Pin(name, b, sha, "branch") for b, sha in res.heads.items()}
            else:  # the heads are of now: each branch is read as of the time; one that had no commit then did not exist then
                pins: dict[str, Pin] = {}
                readable = True
                for b in res.heads:
                    pinned = self.git.pin_read(self.remote(name), b, self.as_of, kind="branch")
                    if pinned.is_found:
                        pins[b] = Pin(name, b, pinned.text, "branch")
                    elif pinned.is_unreadable:
                        readable = False
                        self.problem(f"the branch {b} of {name} could not be read as of {self.as_of}: {pinned.detail}")
                self._branches[name] = pins if readable else None
        return self._branches[name]


# ------------------------------------------------------------------------------ seeds


def seed_change(run: Run, seed: Seed) -> ChangeSet:
    """The synthetic change of a seed: the same text before and after, the seed lines added. The language adapter
    places them in declarations by number, exactly as it does the lines of a real diff."""
    by_line = {e.line: e.detail for e in seed.evidence if e.path == seed.path}
    hunks = tuple(
        Hunk(old_start=n, old_count=0, new_start=n, new_count=1, removed=(), added=(DiffLine(n, by_line.get(n, "")),))
        for n in sorted(set(seed.lines))
    )
    fc = FileChange(path=seed.path, old_path=None, status="modified", hunks=hunks, binary=False, content_available=True)
    return ChangeSet(
        base_sha=seed.sha, head_sha=seed.sha, files=(fc,), kind="seed", repo=seed.repo, remote=run.remote(seed.repo),
        pre_change_sha=seed.sha, how={"head": "seed lines handed over an edge", "pre_change": "the same revision (the seed is not a diff)"},
    )


def _pass_from_seed(run: Run, hop: Hop, seed: Seed) -> Pass:
    change = seed_change(run, seed)
    lang = run.language(seed.repo)
    analysis = lang.analyze(change, distinct=False)  # a seed is code we were pointed at, not a name to link code by
    entry = run.entry(seed.repo)
    reach = entrypoints.reach(analysis, lang, entry, limits=run.limits) if entry is not None else None
    return Pass(seed.repo, hop.depth, seed.sha, change, analysis, reach, hop=hop, prefix=hop.chain, seed=seed, candidate=hop.candidate)


# ------------------------------------------------------------------------------ the walk


def _origin_pass(run: Run, repo: str, change: Any, analysis: Any, reach: Any) -> Pass:
    lang = run.language(repo)
    if analysis is None:
        analysis = lang.analyze(change)
    entry = run.entry(repo)
    if reach is None and entry is not None:
        reach = entrypoints.reach(analysis, lang, entry, limits=run.limits)
    return Pass(repo, 0, change.head_sha, change, analysis, reach)


def propagate(
    workspace: config.Workspace, repo: str, change: Any, *, git: Git, language: Any = None, entry: Any = None, analysis: Any = None,
    reach: Any = None, limits: config.Limits | None = None, as_of: str | None = None,
) -> Propagation:
    """Follow `change` (resolved in `repo`) along the edges of the workspace, reading through `git` (whose records are
    the propagation's records). `language`, `entry`, `analysis` and `reach` are the changed repository's own pass when
    the caller already has it; otherwise it is made here. `as_of` (ISO 8601) pins the branches the edges read in the other
    repositories as of that time."""
    run = Run(workspace, git, limits=limits, as_of=as_of)
    run.origin_repo = repo
    if language is not None:
        run.adopt(repo, language, entry)
    return walk_edges(run, repo, change, analysis=analysis, reach=reach)


EDGE_ORDER = {"package": 0, "contract": 1, "http": 2}  # the package edge first: the contract edge reads the branches it says are adopted


def _reached_code(p: Pass) -> bool:
    """Whether the pass reached code of its own: a changed or seeded declaration in a file that is not a test."""
    return any(i.role != "test" and i.symbols for i in p.analysis.impacts)


def walk_edges(run: Run, repo: str, change: Any, *, analysis: Any = None, reach: Any = None) -> Propagation:
    run.origin_repo = repo
    origin = _origin_pass(run, repo, change, analysis, reach)
    run.origin = origin
    run.findings.extend(origin.reach.findings if origin.reach is not None else ())  # the findings the entry-point adapter made of the change itself (a spec diff)
    queue: list[Pass] = [origin]
    by_key: dict[Any, Pass] = {origin.key: origin}
    passes: list[Pass] = []
    unfollowed: list[config.Edge] = []
    capped: set[str] = set()
    limit = run.limits.hops
    while queue:
        queue.sort(key=lambda p: (p.depth, p.repo, p.seed.path if p.seed else "", p.seed.lines if p.seed else ()))
        p = queue.pop(0)
        passes.append(p)
        if run.repo(p.repo).entry_points and run.entry(p.repo) is None and _reached_code(p):
            record(run.git.target, "entry_points_unread", p.repo,
                   detail=f"the change reached code in {p.repo}, whose entry points are of the kind {run.repo(p.repo).entry_points.get('kind')!r}: what it runs was not read")
            run.problem(f"the entry points of {p.repo} were not read: no adapter for their kind")
        for edge in sorted(
            (e for e in run.workspace.edges if p.repo in (e.from_repo, e.to_repo)),
            key=lambda e: (EDGE_ORDER.get(e.kind, 9), e.kind, e.from_repo, e.to_repo),
        ):
            adapter = edges.adapter_for(edge.kind)
            if adapter is None:
                if _reached_code(p):  # an edge of a kind nobody follows yet is said when the change is at one of its ends
                    if edge not in unfollowed:
                        unfollowed.append(edge)
                    record(run.git.target, "edge_unfollowed", edge_subject(edge),
                           detail=f"the change reached code at one end of an edge of the kind {edge.kind!r} that no adapter follows yet")
                    run.problem(f"{edge_subject(edge)} was not followed: no adapter for the kind {edge.kind!r}")
                continue
            if not adapter.applies(run, p, edge):
                continue
            if p.depth >= limit:
                subject = edge_subject(edge)
                if subject not in capped:
                    capped.add(subject)
                    record(run.git.target, "hop_cap", subject,
                           detail=f"the change reached {p.repo} after {p.depth} edge(s) and limits.hops is {limit}: this edge was not followed")
                    run.problem(f"{subject} was not followed: the hop limit is {limit}")
                continue
            try:
                hops = list(adapter.follow(run, p, edge))
            except UnsupportedLanguage as exc:  # not read: a gap on this edge, never an empty answer
                edge_incomplete(run.git.target, edge, str(exc))
                run.problem(str(exc))
                continue
            for hop in sorted(hops, key=lambda h: h.candidate):  # backed hops first: a candidate never takes the place of one
                run.hops.append(hop)
                for seed in hop.seeds:
                    key = (seed.repo, seed.sha, seed.path, seed.lines)
                    old = by_key.get(key)
                    if old is not None and not (old.candidate and not hop.candidate):
                        continue  # a repository and a set of seeds is processed once: a cycle ends
                    child = _pass_from_seed(run, hop, seed)
                    by_key[key] = child
                    if old is None:
                        queue.append(child)
                    else:  # the seed was first reached by name: what is reached for sure takes its place
                        for where in (queue, passes):
                            for i, q in enumerate(where):
                                if q is old:
                                    where[i] = child
    reached = _assemble(run, passes)
    consumers = {(c.repo, c.branch): c for c in run.consumers}
    reads = tuple(
        dataclasses.replace(r, pin=consumers[(r.repo, r.branch)].pin, adoption=consumers[(r.repo, r.branch)].adoption) if (r.repo, r.branch) in consumers else r
        for r in run.contract_reads
    )
    problems = list(run.problems)
    for p in passes:
        if not p.complete:
            why = list(p.reach.reasons)[:3] if p.reach is not None else []
            where = f" from {p.seed.path}:{p.seed.lines[0]}" if p.seed else ""
            problems.append(f"the pass in {p.repo}{where} is not complete: " + "; ".join(why))
    for h in run.hops:
        if not h.complete:
            problems += list(h.reasons[:2]) or [f"a hop of the {h.kind} edge {h.declared[0]} to {h.declared[1]} is not complete"]
    records = records_of(run.git.target)
    for r in records:  # the invariant: a record that blocks selection means something was not established, so the answer is not complete
        if r.blocks_selection:
            problems.append(f"{r.kind} ({r.subject}) blocks: {r.not_established}")
    reasons = tuple(dict.fromkeys(problems))
    return Propagation(
        repo=repo, change=change, passes=tuple(passes), hops=tuple(run.hops), reached=reached,
        http_edges=tuple(run.http_edges), backend_calls=tuple(run.backend_calls), consumers=tuple(run.consumers),
        contract_changes=tuple(run.contract_changes), contract_reads=reads,
        contract_candidates=tuple(run.contract_candidates), findings=tuple(run.findings), unit_tests=_unit_tests(passes),
        records=records, unfollowed=tuple(unfollowed), complete=not reasons, reasons=reasons,
        unread_entry_points=tuple(sorted({r.subject for r in records if r.kind == "entry_points_unread"})),
    )


def _unit_tests(passes: Sequence[Pass]) -> tuple[UnitTest, ...]:
    seen: dict[tuple[str, str, str], UnitTest] = {}
    for p in passes:
        if p.candidate:
            continue
        for impact in p.analysis.impacts:
            for t in impact.unit_tests:
                seen.setdefault((t.repo, t.path, t.via), t)
    return tuple(seen[k] for k in sorted(seen))


def _assemble(run: Run, passes: Sequence[Pass]) -> tuple[Reached, ...]:
    """The areas of every pass with their chains composed across repositories, merged by identity (one screen
    reached twice has both chains), and the priorities set once over all of them."""
    merged: dict[str, Reached] = {}
    order: list[str] = []
    for p in passes:
        for r in p.reached:
            area = r.area
            chains = tuple(tuple(dedupe([*p.prefix, *c])) if p.prefix else c for c in area.chains)
            if p.candidate:
                area = dataclasses.replace(area, chains=chains, by_name=True, priority="candidate", also_mounted=area.also_mounted)
            else:
                area = dataclasses.replace(area, chains=chains)
            key = area.id + ("/candidate" if area.by_name else "")
            if key not in merged:
                merged[key] = dataclasses.replace(r, area=area, complete=r.complete and p.complete)
                order.append(key)
                continue
            old = merged[key]
            old_area = old.area
            more = tuple(c for c in area.chains if c not in old_area.chains)
            also = tuple(sorted(set(old_area.also_mounted) | set(area.also_mounted)))
            routes = old.routes + tuple(x for x in r.routes if x not in old.routes)
            merged[key] = dataclasses.replace(
                old, area=dataclasses.replace(old_area, chains=old_area.chains + more, also_mounted=also), routes=routes,
                complete=old.complete and r.complete, reasons=tuple(dict.fromkeys(old.reasons + r.reasons)),
            )
    by_name = [k for k in order if merged[k].area.by_name]
    backed = [k for k in order if not merged[k].area.by_name]
    prioritised = assign_priorities([merged[k].area for k in backed])
    out = [dataclasses.replace(merged[k], area=a) for k, a in zip(backed, prioritised)]
    out += [merged[k] for k in by_name]
    out.sort(key=lambda r: (r.area.by_name, r.area.repo, r.area.kind, r.area.label))
    return tuple(out)
