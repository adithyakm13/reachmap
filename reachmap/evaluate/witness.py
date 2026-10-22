"""Reach beyond the diff (context section 9): does the report of a change cite files that a later follow-up fix touched?

    reachmap witness CASES.toml [--config reachmap.toml]

A case is an original change, the time it merged, and a later *witness* change (a follow-up fix) that touched files the original did not.
The original is analysed **as of its merge time** (`--as-of`), like any run, and the harness counts whether the report cited a witness file:

- **by findings**: a finding that is not an exclusion cites it;
- **by screens**: an area that is import-backed (a screen or route, not a candidate found by name) cites it in its chains or route line;
- **by any evidence**: a finding that is not an exclusion, or any area (a candidate found by name included), cites it.

Which files count: the witness change's files that are outside the original diff (the original's changed paths and the paths they were
renamed from). Paths are compared exactly, in one repository: a file is a hit when the report cites that very path of that very repository (never a
substring, never a suffix: `a/b/c.ts` is not `b/c.ts` and `test/x.ts` is not `backtest/x.ts`).

What the harness refuses, as a whole set and in words, before anything is counted (a refused set gives no count at all):

- **dependence on either axis**: two cases that share a subject (the same pull request, the same head commit, the same set of changed
  files) or a witness (the same pull request, head commit or set of touched files). One witness counted three times inflated an early
  result from 1 to 4;
- **a revert**: a witness whose diff holds the inverse of the original's for every file of the original (what the original added it removes, what it
  removed it adds back), whether or not it also touches other files, undoes the diff and cannot test reach;
- a witness with no file outside the original diff, a witness that is not later than the merge time, and a change that cannot be read.

Exclusions are not predictions: an exclusion finding never counts, and a witness file that only an exclusion cites is said apart. So are the
`release` findings, which depend on the tags of a producer and on the pins of its consumers, and `--as-of` does not pin those: a witness file
that only a release finding cites is said apart and is not a hit. The output also says what `--as-of` does not pin (the tags of a producer, the
commits a host reports for a pull request, the hosted test sources), because those can let a later fact into the report.

The output is counts per axis, never percentages, and says that small samples are weak and that no recall number exists without defects
linked to the changes that caused them. A run that failed or timed out is not a miss: it is listed as not analysed and left out of every
count, and the command exits 1 (the counts are printed). A run with blocking records is counted and said to be incomplete (its miss may be
an absence the analysis could not establish). A witness file that is counted for two cases is listed (the sets of two witnesses are
independent when they are not the same change or the same set of files, but a file can be in both: the reader decides what that means).
No model is called; the case file is made by a person.
"""

from __future__ import annotations

import datetime as _dt
import tomllib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from reachmap.evaluate import EXIT_FAILED, EXIT_OK, UsageError, emit, remote_of

__all__ = ["Case", "Cited", "Outcome", "Refused", "cited_files", "matches", "path_matches", "read_cases", "render", "run_cases"]

AS_OF_CAVEAT = (
    "As of the merge time pins the branches and heads read by name, and a commit named by hash is a pin already. It does not pin the tags of a producer, "
    "the commits a host reports for a pull request, or the hosted test sources (read as they are now): release findings are therefore not counted as hits."
)
SMALL_SAMPLE = (
    "These are counts over a handful of cases: small samples are weak, and a count of this size supports no rate. "
    "No recall number exists without defects linked to the changes that caused them (context section 9), so none is given here."
)
_CASE_KEYS = ("id", "repo", "merged_at", "original", "witness")
_CHANGE_KEYS = ("pr", "compare", "range", "repo")


class Refused(ValueError):
    """The case set cannot be counted. `reasons` say why, each naming the cases."""

    def __init__(self, reasons: Sequence[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = tuple(reasons)


@dataclass(frozen=True)
class Change:
    repo: str
    kind: str  # pr | compare | range
    value: str

    def spec(self) -> dict[str, str]:
        return {self.kind: self.value}

    def label(self) -> str:
        return f"{self.repo} {self.kind} {self.value}"


@dataclass(frozen=True)
class Case:
    id: str
    repo: str
    merged_at: str
    original: Change
    witness: Change


# ----------------------------------------------------------------------------- the case file


def _change(table: Any, where: str, default_repo: str, allow_repo: bool = True) -> Change:
    if not isinstance(table, dict):
        raise UsageError(f"{where}: a table with one of pr, compare, range")
    if not allow_repo and "repo" in table:
        raise UsageError(f"{where}: the original change is in the case's repo; a repo of its own is not accepted (it would be read in one repository and analysed in another)")
    extra = sorted(set(table) - set(_CHANGE_KEYS))
    if extra:
        raise UsageError(f"{where}: unknown keys {extra}; use one of pr, compare, range, and repo for the witness in another repository")
    given = [k for k in ("pr", "compare", "range") if k in table]
    if len(given) != 1:
        raise UsageError(f"{where}: give exactly one of pr, compare, range")
    value = table[given[0]]
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not str(value).strip():
        raise UsageError(f"{where}.{given[0]}: a pull request number or a ref pair as text")
    repo = table.get("repo", default_repo)
    if not isinstance(repo, str) or not repo:
        raise UsageError(f"{where}.repo: a repository name as the config has it")
    return Change(repo, given[0], str(value).strip())


def _time(value: Any, where: str) -> str:
    if isinstance(value, _dt.datetime):
        moment = value if value.tzinfo is not None else value.replace(tzinfo=_dt.timezone.utc)
        return moment.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    from reachmap import pipeline

    if not isinstance(value, str):
        raise UsageError(f"{where}: a time such as 2024-05-01T12:00:00Z")
    try:
        return pipeline.parse_as_of(value)
    except ValueError as exc:
        raise UsageError(f"{where}: {exc}") from None


def read_cases(path: str | Path) -> tuple[list[Case], str | None]:
    """The cases of a TOML file, and its `config` key (a path, relative to the file) when it has one. A table per case:

        [[case]]
        id = "w01"
        repo = "acme/web"
        merged_at = "2024-03-01T12:00:00Z"
        original = { compare = "main...topic" }       # or pr = 12, or range = "a..b"; always in the case's repo
        witness = { compare = "main...fix", repo = "acme/web" }   # repo defaults to the case's
    """
    p = Path(path)
    try:
        data = tomllib.loads(p.read_bytes().decode("utf-8"))
    except OSError as exc:
        raise UsageError(f"{p.name}: cannot be read: {exc.strerror or type(exc).__name__}") from None
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise UsageError(f"{p.name}: is not valid TOML ({type(exc).__name__})") from None
    extra = sorted(set(data) - {"config", "case"})
    if extra:
        raise UsageError(f"{p.name}: unknown top level keys {extra}; a file has `config` and [[case]] tables")
    config = data.get("config")
    if config is not None and not isinstance(config, str):
        raise UsageError(f"{p.name}: config is a path")
    tables = data.get("case")
    if not isinstance(tables, list) or not tables:
        raise UsageError(f"{p.name}: no [[case]] table")
    cases: list[Case] = []
    seen: set[str] = set()
    for i, table in enumerate(tables, 1):
        where = f"{p.name}: case {i}"
        if not isinstance(table, dict):
            raise UsageError(f"{where}: not a table")
        missing = [k for k in _CASE_KEYS if k not in table]
        extra = sorted(set(table) - set(_CASE_KEYS))
        if missing or extra:
            raise UsageError(f"{where}: missing keys {missing}, unknown keys {extra}")
        cid = table["id"]
        if not isinstance(cid, str) or not cid.strip() or not cid.isprintable():
            raise UsageError(f"{where}: id is a short text")
        if cid in seen:
            raise UsageError(f"{where}: the id {cid!r} is used twice")
        seen.add(cid)
        repo = table["repo"]
        if not isinstance(repo, str) or not repo:
            raise UsageError(f"{where}: repo is the repository of the original change, as the config names it")
        cases.append(Case(cid, repo, _time(table["merged_at"], f"{where}.merged_at"),
                          _change(table["original"], f"{where}.original", repo, allow_repo=False), _change(table["witness"], f"{where}.witness", repo)))
    return cases, config


# ----------------------------------------------------------------------------- paths and what a report cites


def path_matches(a: str, b: str) -> bool:
    """Exact. Both paths are paths of one repository, written the way the repository has them, so there is no prefix to forgive: a suffix match
    would make `index.ts` every `index.ts` and `b/c.ts` a hit for `a/b/c.ts` (Shape B)."""
    return a == b


def matches(file: tuple[str, str], cited: tuple[str, str]) -> bool:
    """The same path of the same repository."""
    return file[0] == cited[0] and path_matches(file[1], cited[1])


@dataclass(frozen=True)
class Cited:
    """The files a report cites, by how: `findings` (evidence of findings that are not exclusions), `screens` (evidence of import-backed areas),
    `candidates` (evidence of areas found by name), `exclusions` (evidence of exclusion findings, which never count) and `releases` (evidence of `release`
    findings: they rest on the tags and pins that `--as-of` does not move, so they never count either)."""

    findings: frozenset[tuple[str, str]] = frozenset()
    screens: frozenset[tuple[str, str]] = frozenset()
    candidates: frozenset[tuple[str, str]] = frozenset()
    exclusions: frozenset[tuple[str, str]] = frozenset()
    releases: frozenset[tuple[str, str]] = frozenset()

    @property
    def anything(self) -> frozenset[tuple[str, str]]:
        return self.findings | self.screens | self.candidates


def cited_files(analysis: Any) -> Cited:
    """What an `Analysis` (a completed run) cites, as (repo, path)."""
    findings: set[tuple[str, str]] = set()
    exclusions: set[tuple[str, str]] = set()
    releases: set[tuple[str, str]] = set()
    for f in analysis.findings:
        (exclusions if f.is_exclusion else releases if f.kind == "release" else findings).update((e.repo, e.path) for e in f.evidence)
    screens: set[tuple[str, str]] = set()
    candidates: set[tuple[str, str]] = set()
    for a in analysis.areas:
        (candidates if a.by_name else screens).update((e.repo, e.path) for e in a.all_evidence())
    return Cited(frozenset(findings), frozenset(screens), frozenset(candidates), frozenset(exclusions), frozenset(releases))


def _hit(files: Sequence[tuple[str, str]], cited: frozenset[tuple[str, str]]) -> list[tuple[str, str]]:
    return [f for f in files if any(matches(f, c) for c in cited)]


# ----------------------------------------------------------------------------- the changes of a case


@dataclass(frozen=True)
class Resolved:
    """What a case needs of its two changes: the pinned head, the files with their diff, and the pull request."""

    head: str
    files: tuple[Any, ...]  # diffparse.FileChange
    pr: tuple[str, int] | None


def _paths(resolved: Resolved) -> frozenset[str]:
    return frozenset(f.path for f in resolved.files)


def outside(original: Sequence[Any], witness: Sequence[Any], same_repo: bool) -> list[str]:
    """The paths the witness touched that the original diff did not (the original's paths and the paths its renames came from)."""
    inside = {p for f in original for p in (f.path, f.old_path) if p} if same_repo else set()
    return sorted(f.path for f in witness if f.path not in inside)


def is_revert(original: Sequence[Any], witness: Sequence[Any]) -> bool:
    """The witness holds the inverse of the original's diff for every file of the original: in each, the lines the original added are lines the
    witness removes and the lines the original removed are lines the witness adds (as multisets, so a line added twice is removed twice). Other
    files in the witness do not change that: a revert with a fix beside it still undoes the diff. Files of the original with no text to compare
    (binary, not available, a change of mode only) are left out and at least one file must have been compared. Limit: an original of a line or
    two that are very common (a closing brace) can be contained by a witness that is no revert."""
    by_path = {f.path: f for f in witness}
    compared = 0
    for f in original:
        if f.binary or not f.content_available or not (f.added_lines or f.removed_lines):
            continue
        g = by_path.get(f.path)
        if g is None or g.binary or not g.content_available:
            return False
        compared += 1
        if Counter(x.text for x in f.added_lines) - Counter(x.text for x in g.removed_lines):
            return False
        if Counter(x.text for x in f.removed_lines) - Counter(x.text for x in g.added_lines):
            return False
    return compared > 0


def independence(cases: Sequence[Case], resolved: Mapping[str, tuple[Resolved, Resolved]]) -> list[str]:
    """Every pair of cases that share a subject or a witness, in words naming both cases and what they share."""
    problems: list[str] = []
    for i, a in enumerate(cases):
        for b in cases[i + 1:]:
            (oa, wa), (ob, wb) = resolved[a.id], resolved[b.id]
            subject = _shared(a.original, oa, b.original, ob)
            if subject:
                problems.append(f"cases {a.id} and {b.id} share a subject ({subject}): the same change would be counted twice")
            witness = _shared(a.witness, wa, b.witness, wb)
            if witness:
                problems.append(f"cases {a.id} and {b.id} share a witness ({witness}): the same follow-up would be counted twice")
    return problems


def _shared(ca: Change, ra: Resolved, cb: Change, rb: Resolved) -> str | None:
    if ra.pr is not None and ra.pr == rb.pr:
        return f"the same pull request, {ra.pr[0]} number {ra.pr[1]}"
    if ca.repo == cb.repo and ra.head == rb.head:
        return f"the same head commit {ra.head[:12]} in {ca.repo}"
    if ca.repo == cb.repo and _paths(ra) and _paths(ra) == _paths(rb):
        return f"the same {len(_paths(ra))} changed file{'s' if len(_paths(ra)) != 1 else ''} in {ca.repo}"
    return None


# ----------------------------------------------------------------------------- the run


@dataclass
class Outcome:
    case: Case
    status: str  # analysed | not_analysed
    run: str = ""  # the analysis status: completed, failed, timed_out
    incomplete: bool = False
    files: list[tuple[str, str]] = field(default_factory=list)  # the witness files outside the original diff
    by_findings: list[tuple[str, str]] = field(default_factory=list)
    by_screens: list[tuple[str, str]] = field(default_factory=list)
    by_any: list[tuple[str, str]] = field(default_factory=list)
    only_exclusions: list[tuple[str, str]] = field(default_factory=list)  # witness files that an exclusion finding cites and nothing else does
    only_releases: list[tuple[str, str]] = field(default_factory=list)  # witness files that only a release finding cites: not as of the merge time


Analyse = Callable[[Case], Any]  # an Analysis for the original change, as of its merge time


def score(case: Case, files: Sequence[tuple[str, str]], analysis: Any) -> Outcome:
    """The outcome of one case from the analysis of its original change."""
    if analysis.status != "completed":
        return Outcome(case, "not_analysed", analysis.status, files=list(files))
    cited = cited_files(analysis)
    by_any = _hit(files, cited.anything)
    return Outcome(
        case, "analysed", "completed", incomplete=bool(analysis.blocking()), files=list(files), by_findings=_hit(files, cited.findings),
        by_screens=_hit(files, cited.screens), by_any=by_any,
        only_exclusions=[f for f in _hit(files, cited.exclusions) if f not in by_any],
        only_releases=[f for f in _hit(files, cited.releases) if f not in by_any],
    )


def resolve_cases(cases: Sequence[Case], resolve: Callable[[Case, Change, str | None], Resolved], later: Callable[[Case, Resolved], bool | None]) -> tuple[dict[str, tuple[Resolved, Resolved]], list[str]]:
    """Both changes of every case, and the reasons any case cannot be used. `resolve(case, change, as_of)` reads a change (`as_of` is the case's
    merge time for the original and None for the witness); `later` says whether the witness is later than the merge time (None: could not be told)."""
    resolved: dict[str, tuple[Resolved, Resolved]] = {}
    problems: list[str] = []
    for case in cases:
        try:
            original = resolve(case, case.original, case.merged_at)
            witness = resolve(case, case.witness, None)
        except UsageError as exc:
            problems.append(f"case {case.id}: {exc}")
            continue
        same = case.original.repo == case.witness.repo
        if is_revert(original.files, witness.files) and same:
            problems.append(f"case {case.id}: the witness holds the inverse of the original's diff for every file of the original (a revert, with or without other files): a revert undoes the diff and cannot test reach")
            continue
        when = later(case, witness)
        if when is None:
            problems.append(f"case {case.id}: it could not be established that the witness is later than the merge time")
            continue
        if not when:
            problems.append(f"case {case.id}: the witness is not later than the merge time {case.merged_at}: it is not a follow-up")
            continue
        if not outside(original.files, witness.files, same):
            problems.append(f"case {case.id}: every file of the witness is inside the original diff, so there is no file outside it to look for")
            continue
        resolved[case.id] = (original, witness)
    return resolved, problems


def run_cases(cases: Sequence[Case], resolve: Callable[[Case, Change, str | None], Resolved], later: Callable[[Case, Resolved], bool | None], analyse: Analyse) -> list[Outcome]:
    """Count the cases. `Refused` (nothing analysed) when any case is or holds a revert, has no witness file outside the diff, is not later, cannot be read,
    or two share a subject or a witness."""
    resolved, problems = resolve_cases(cases, resolve, later)
    if not problems:
        problems = independence(cases, resolved)
    if problems:
        raise Refused(problems)
    out: list[Outcome] = []
    for case in cases:
        original, witness = resolved[case.id]
        files = [(case.witness.repo, p) for p in outside(original.files, witness.files, case.original.repo == case.witness.repo)]
        out.append(score(case, files, analyse(case)))
    return out


# ----------------------------------------------------------------------------- output


def _names(files: Sequence[tuple[str, str]], limit: int = 3) -> str:
    from reachmap.report import text as shown

    names = [shown.one_line(shown.show(f"{r}:{p}", 120)) for r, p in files[:limit]]
    return ", ".join(names) + (f" and {len(files) - limit} more" if len(files) > limit else "")


def shared_files(done: Sequence[Outcome]) -> dict[tuple[str, str], list[str]]:
    """The witness files that count as a hit (on any axis) in more than one case, with the ids of those cases."""
    by_file: dict[tuple[str, str], list[str]] = {}
    for o in done:
        for file in o.by_any:
            by_file.setdefault(file, []).append(o.case.id)
    return {file: ids for file, ids in by_file.items() if len(ids) > 1}


def exit_code(outcomes: Sequence[Outcome]) -> int:
    """0 when every case was analysed; 1 when any was not (a failed or timed out run is in no count, and a result with a case missing is not clean)."""
    return EXIT_OK if all(o.status == "analysed" for o in outcomes) else EXIT_FAILED


def render(outcomes: Sequence[Outcome]) -> list[str]:
    done = [o for o in outcomes if o.status == "analysed"]
    skipped = [o for o in outcomes if o.status != "analysed"]
    n = len(done)
    lines = [f"witness cases: {len(outcomes)} read, {n} analysed" + (f", {len(skipped)} not analysed" if skipped else "")]
    for o in outcomes:
        head = f"  {o.case.id} ({o.case.repo}, as of {o.case.merged_at}): "
        if o.status != "analysed":
            lines.append(head + f"not analysed (the run {o.run}); left out of every count")
            continue
        axes = [name for name, got in (("findings", o.by_findings), ("screens", o.by_screens), ("any evidence", o.by_any)) if got]
        said = ("hit by " + ", ".join(axes)) if axes else "no witness file cited"
        lines.append(head + f"{said}; {len(o.files)} witness file{'s' if len(o.files) != 1 else ''} outside the original diff"
                     + (f"; cited: {_names(o.by_any)}" if o.by_any else "") + ("; the run was incomplete (blocking records)" if o.incomplete else ""))
        if o.only_exclusions:
            lines.append(f"    cited only by an exclusion, which is not a prediction and does not count: {_names(o.only_exclusions)}")
        if o.only_releases:
            lines.append(f"    cited only by a release finding, which rests on tags and pins that as-of does not move and does not count: {_names(o.only_releases)}")
    lines.append(f"hits: {sum(bool(o.by_findings) for o in done)} of {n} by findings; {sum(bool(o.by_screens) for o in done)} of {n} by screens; "
                 f"{sum(bool(o.by_any) for o in done)} of {n} by any evidence")
    if any(o.incomplete for o in done):
        lines.append(f"{sum(o.incomplete for o in done)} of {n} analysed runs were incomplete: a miss there may be something the analysis could not establish")
    for file, ids in shared_files(done).items():
        lines.append(f"counted for more than one case: {_names([file])} (cases {', '.join(ids)}); the witnesses differ, the file does not")
    lines.append(AS_OF_CAVEAT)
    lines.append(SMALL_SAMPLE)
    return lines


# ----------------------------------------------------------------------------- the command


def command(ns: Any, env: Mapping[str, str], out: Any, git_env: Mapping[str, str] | None) -> int:
    """`reachmap witness`: exit 0 with the counts, 1 with the counts when a case was not analysed, 2 when the set is refused or cannot be read."""
    from reachmap import changeset, config, gitlayer, pipeline

    cases, named = read_cases(ns.cases)
    config_path = ns.config or (str(Path(ns.cases).parent / named) if named else "reachmap.toml")
    try:
        workspace = config.load(config_path, env=env)
    except config.ConfigError as exc:
        raise UsageError(f"config error: {exc}") from None
    for case in cases:
        for change in (case.original, case.witness):
            if change.repo not in workspace.repos:
                raise UsageError(f"case {case.id}: {change.repo!r} is not in the config; known: {', '.join(sorted(workspace.repos))}")
    cache = gitlayer.default_cache(env)
    git = gitlayer.Git(cache, env=git_env)

    def resolve(case: Case, change: Change, as_of: str | None) -> Resolved:
        try:
            cs = changeset.resolve(workspace, change.repo, git=git, as_of=as_of, **change.spec())
        except Exception as exc:  # a change that cannot be read is said, with the type of the failure and no more
            raise UsageError(f"{change.label()} cannot be read ({type(exc).__name__}: {str(exc)[:200]})") from None
        number = int(change.value.lstrip("#!")) if change.kind == "pr" and change.value.lstrip("#!").isdigit() else None
        return Resolved(cs.head_sha, cs.files, (change.repo, number) if number is not None else None)

    def later(case: Case, witness: Resolved) -> bool | None:
        try:
            remote = remote_of(workspace, case.witness.repo)
        except LookupError:
            return None
        found = git.pin_read(remote, witness.head, as_of=case.merged_at)
        if found.is_unreadable:
            return None
        return found.is_absent or found.text != witness.head

    def analyse(case: Case) -> Any:
        options = pipeline.Options(as_of=case.merged_at, cache=cache, git_env=git_env, env=env)
        return pipeline.build(workspace, case.repo, pipeline.ChangeSpec(**case.original.spec()), options)

    try:
        outcomes = run_cases(cases, resolve, later, analyse)
    except Refused as exc:
        raise UsageError("the case set is refused, so nothing was counted:\n  " + "\n  ".join(exc.reasons)) from None
    emit(out, "\n".join(render(outcomes)))
    return exit_code(outcomes)
