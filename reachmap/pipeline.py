"""The pipeline: one change in any configured repository to one `Analysis`. This module is the only orchestrator; every
step finds evidence or leaves a record, none is skipped quietly (context section 2).

1. Resolve the change set (`changeset.resolve`): a pull request, `base...head` or `a..b`, every ref pinned (`as_of` pins
   the refs a compare or range names). A refusal ends the run as `failed` with its blocking record and no findings.
2. The pre-change version comes with the change set; when it is unavailable its blocking record is already there, and
   the steps that need it say so through that record (no second one).
3. The branch matrix (`branches`): the change on every configured branch of the changed repository.
4. The language analysis of the changed repository (the adapter its `language` selects).
5. Its entry points (screens, routes) through the walk.
6. The edges: the change followed into the other repositories (`propagate`), hop by hop with pinned evidence.
7. Findings (`findings`), one function per kind, and the areas with their priority (one assignment over all of them).
8. Unit tests per repository, with the command the config gives (`tests.command`, `{paths}` in it).
9. The test sources (`testsources.run_all`): suggested cases for the areas, and the records of every source that is not
   configured, cannot be read or was read in part. They run after the findings and add suggestions and records only: a source
   that fails blocks the claim about suggestions (its records have the scope `suggestions`), never the areas, which are already made.
10. Incompleteness: every record of every step, one per (kind, subject), blocking first. Whether the areas are complete is
   in the propagation (`complete`, `reasons`): False when any record of steps 1 to 8 blocks selection or a stated limit was hit
   (the records of the test sources are made after it and are not in it).
11. `Analysis.finish`. A cooperative deadline (`limits.timeout_s`, or `Options.timeout_s`) is checked between the steps
    and returns `timed_out` with no findings: the hard ceiling is the runner's, which kills the process.

A run that does not complete keeps no partial results (the model's rule): a refused change set, a failure in a step (or in
making the report from the steps), or a timeout gives an `Analysis` whose result lists (findings, areas, branches,
propagations, backend calls, suggestions, mapped tests) are null in the report and whose blocking record says why. `build`
never raises for such a failure. A change set that was never resolved has no commit to cite: its `ref` is None, its
`change["resolved"]` is False and the subject of its record is what was asked for (`compare main...topic`).

The report content is plain JSON (`Analysis.to_dict`): the change set in `change`; `propagations` holds one object (see
`payload.propagation_to_dict`) with every pass, hop, edge, consumer, read and unit test; `backend_calls` the handler
modules a client change reaches; `branches`, `findings`, `areas`, `incompleteness` and `notes` are the model's own lists.
"""

from __future__ import annotations

import shlex
import time
from types import SimpleNamespace
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Sequence

from reachmap import branches, changeset, config, entrypoints, findings, payload, propagate, redact, testsources
from reachmap.changeset import ChangeSetRefused
from reachmap.gitlayer import Git, ReadFailed
from reachmap.hosts import get_host, parse_number
from reachmap.incomplete import Incompleteness
from reachmap.model import Analysis

__all__ = ["ChangeSpec", "Options", "build", "dedupe_records", "failed_analysis", "parse_as_of", "unique_areas", "unit_test_commands"]


# ------------------------------------------------------------------------------ inputs


@dataclass(frozen=True, slots=True)
class ChangeSpec:
    """The change to analyse: exactly one of a pull or merge request number (`12`, `#12`, `!12`), `base...head` (the
    change is what head has since the merge base) or `a..b` (everything from a to b)."""

    pr: int | str | None = None
    compare: str | None = None
    range: str | None = None

    def __post_init__(self) -> None:
        given = [n for n, v in (("pr", self.pr), ("compare", self.compare), ("range", self.range)) if v is not None]
        if len(given) != 1:
            raise ValueError(f"give exactly one of a pull request number, a base...head comparison and an a..b range (got {', '.join(given) or 'none'})")
        if self.pr is not None:
            parse_number(self.pr)
        elif self.compare is not None:
            self._two(self.compare, "...", "a comparison is written base...head (three dots)")
        else:
            assert self.range is not None
            if "..." in self.range:
                raise ValueError("a range is written a..b (two dots); three dots is a comparison")
            self._two(self.range, "..", "a range is written a..b (two dots)")

    @staticmethod
    def _two(text: str, dots: str, message: str) -> None:
        left, sep, right = text.partition(dots)
        if not sep or not left.strip() or not right.strip() or dots in right or (dots == ".." and right.startswith(".")):
            raise ValueError(f"{message}, not {text!r}")

    @property
    def kind(self) -> str:
        return "pr" if self.pr is not None else "compare" if self.compare is not None else "range"

    @property
    def value(self) -> Any:
        return self.pr if self.pr is not None else self.compare if self.compare is not None else self.range

    @property
    def label(self) -> str:
        return f"{self.kind} {self.value}"

    @property
    def head_ref(self) -> str | None:
        """The name the change's head is given by (the right side of a comparison or range); a pull request's comes from the host."""
        if self.compare is not None:
            return self.compare.partition("...")[2].strip()
        if self.range is not None:
            return self.range.partition("..")[2].strip()
        return None

    def as_dict(self) -> dict[str, str]:
        return {self.kind: str(self.value) if self.pr is None else str(parse_number(self.pr))}


def parse_as_of(text: str) -> str:
    """An ISO 8601 time (a naive one is UTC), returned as given; ValueError when it is not one or is before 1970 (git cannot
    read a negative epoch as a time: it would pin some other commit)."""
    try:
        moment = datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        stamp = moment.timestamp()
    except (ValueError, OverflowError, OSError):
        raise ValueError(f"as-of must be an ISO 8601 time such as 2024-05-01T12:00:00Z, not {text!r}") from None
    if stamp < 0:
        raise ValueError(f"as-of {text!r} is before 1970, which git cannot read as a time")
    return text


@dataclass(frozen=True)
class Options:
    """How a run reads. `as_of` pins the refs read by name as of a time. `cache` is the git cache directory (default: the
    user's), `git_env` overrides of git's environment (tests cut git off from the user's config), `host` a host adapter
    for a pull request (default: the repository's configured host), `timeout_s` the cooperative deadline (default
    `limits.timeout_s`), `clock` the time source of that deadline. `retain`, when given, is filled with what the steps made
    (`change`, `language`, `entry`, `analysis`, `reach`, `prop`, `matrix`, `git`, `records`) for a caller that needs more
    than the report (the scenario harness checks the adapters' own results): the run is not repeated for it. `test_transport` is
    the transport the hosted test sources (Qase, TestRail) send their GET requests through (tests pass a fake; the default sends real
    requests) and `env` the variables their tokens are read from (default: the process's)."""

    as_of: str | None = None
    cache: Any = None
    git_env: Mapping[str, str] | None = None
    host: Any = None
    timeout_s: float | None = None
    clock: Callable[[], float] = field(default=time.monotonic, compare=False)
    retain: dict[str, Any] | None = field(default=None, compare=False)
    test_transport: Any = field(default=None, compare=False)
    env: Mapping[str, str] | None = field(default=None, compare=False)


class _TimedOut(testsources.StopRun):
    def __init__(self, after: str, seconds: float) -> None:
        super().__init__(after)
        self.after, self.seconds = after, seconds


# ------------------------------------------------------------------------------ small pure steps


def dedupe_records(records: Iterable[Incompleteness]) -> list[Incompleteness]:
    """One record per (kind, subject): the first wording stays, and a blocking record is never lost to a non-blocking one of
    the same kind and subject. Blocking first, then by kind and subject: the same order whatever order the steps ran in."""
    kept: dict[tuple[str, str], Incompleteness] = {}
    for rec in records:
        key = (rec.kind, rec.subject)
        old = kept.get(key)
        if old is None or (rec.blocks_selection and not old.blocks_selection):
            kept[key] = rec
    return sorted(kept.values(), key=lambda r: (not (r.blocks_selection or r.blocks_suggestions), r.kind, r.subject))


def unique_areas(reached: Sequence[Any]) -> tuple[list[Any], list[str]]:
    """The reached areas with one entry per area id: a candidate found by name that is also reached through imports is the
    area reached for sure, shown once (and said in a note)."""
    sure = {r.area.id for r in reached if not r.area.by_name}
    out: list[Any] = []
    notes: list[str] = []
    seen: set[tuple[str, bool]] = set()
    for r in reached:
        key = (r.area.id, r.area.by_name)
        if key in seen:
            continue
        seen.add(key)
        if r.area.by_name and r.area.id in sure:
            notes.append(findings.clean(f"{r.area.label} in {r.area.repo} was also reached by name: it is listed once, as reached through imports"))
            continue
        out.append(r)
    return out, notes


def unit_test_commands(prop: Any, workspace: config.Workspace) -> list[dict[str, Any]]:
    """The unit tests the change reaches, per repository, with the command of the repository's `tests.command` (`{paths}` is
    replaced by the quoted paths; a command without it is kept as it is). A repository with no command still lists its tests."""
    by_repo: dict[str, set[str]] = {}
    for t in prop.unit_tests:
        by_repo.setdefault(t.repo, set()).add(t.path)
    out = []
    for repo in sorted(by_repo):
        paths = sorted(by_repo[repo])
        template = (workspace.repos[repo].tests or {}).get("command") if repo in workspace.repos else None
        command = None
        if isinstance(template, str) and template.strip():
            command = template.replace("{paths}", " ".join(shlex.quote(p) for p in paths)) if "{paths}" in template else template
        out.append({"repo": repo, "command": command, "paths": paths})
    return out


# ------------------------------------------------------------------------------ the run


def _git(options: Options, records: list, token_env: str | None) -> Git:
    return Git(options.cache, record_to=records, env=options.git_env, drop_env=[token_env] if token_env else ())


@dataclass
class _State:
    """What the steps have made so far, and the step in progress (a failure names it)."""

    change: Any = None
    matrix: Any = None
    analysis: Any = None
    prop: Any = None
    reached: Sequence[Any] = ()
    findings: Sequence[Any] = ()
    commands: Sequence[dict[str, Any]] = ()
    outcome: Any = None
    git: Any = None
    language: Any = None
    entry: Any = None
    reach: Any = None
    step: str = "starting"


def build(workspace: config.Workspace, repo: str, spec: ChangeSpec, options: Options | None = None) -> Analysis:
    """Analyse one change in `repo`. Raises ValueError for a usage error (an unknown repository, a bad time); everything
    else that goes wrong is an `Analysis` with status `failed` or `timed_out`, never an exception."""
    options = options or Options()
    if repo not in workspace.repos:
        raise ValueError(f"unknown repository {repo!r}; known: {', '.join(sorted(workspace.repos))}")
    as_of = parse_as_of(options.as_of) if options.as_of is not None else None
    cfg = workspace.repos[repo]
    clock = options.clock
    timeout = float(options.timeout_s if options.timeout_s is not None else workspace.limits.timeout_s)
    deadline = clock() + timeout
    records: list[Incompleteness] = []
    notes: list[str] = []
    st = _State()

    def begin(step: str) -> None:
        st.step = step

    def done(step: str) -> None:
        if clock() > deadline:
            raise _TimedOut(step, timeout)

    status, detail = "completed", ""
    try:
        host = options.host
        if host is None:
            opts: dict[str, Any] = {"record_to": records}
            if cfg.host == "git":
                opts["remotes"] = {cfg.name: cfg.remote} if cfg.remote else {}
            host = get_host(cfg.host, **opts)
        st.git = git = _git(options, records, getattr(host, "token_env", None))
        begin("resolving the change set")
        change = changeset.resolve(workspace, repo, git=git, host=host, as_of=as_of, **{spec.kind: spec.value})
        st.change = change
        done("resolving the change set")
        if as_of is not None and spec.kind == "pr":
            notes.append("as-of does not move the commits a host reports for a change request; it pins the branches read in the other steps")
        if workspace.context:  # configured, and not read by this version: said, so that no requirement context is not read as none exists
            notes.append(f"{len(workspace.context)} context source(s) are configured and were not read: this version of the analyser reads no requirement context")
        if as_of is not None and any(ts.kind in ("qase", "testrail") for ts in workspace.test_sources):
            notes.append("as-of does not move the hosted test sources: the test sources are read as they are now, the specs as of the time")
        run = propagate.Run(workspace, git, limits=workspace.limits, as_of=as_of)
        run.origin_repo = repo
        remote = run.remote(repo)
        head_ref = spec.head_ref or (change.change_request.head_ref if change.change_request is not None else None)
        begin("the branch matrix")
        st.matrix = branches.matrix(git, remote, cfg, change, head_ref=head_ref, as_of=as_of)
        done("the branch matrix")
        begin("the language analysis")
        st.language = lang = run.language(repo)
        st.analysis = analysis = lang.analyze(change)
        done("the language analysis")
        begin("the entry points")
        st.entry = entry = run.entry(repo)
        st.reach = reach = entrypoints.reach(analysis, lang, entry, limits=workspace.limits) if entry is not None else None
        if reach is not None:
            notes.extend(reach.notes)
        done("the entry points")
        begin("following the edges")
        st.prop = prop = propagate.walk_edges(run, repo, change, analysis=analysis, reach=reach)
        done("following the edges")
        begin("the findings")
        st.reached, area_notes = unique_areas(prop.reached)
        notes.extend(area_notes)
        if as_of is not None and any(h.kind == "package" for h in prop.hops):
            notes.append("as-of does not filter the tags of a producer: they are listed as they are now, so a tag made after the time can make a change look released")
        st.findings = findings.build(prop, st.matrix, findings.definition_lines(git, remote, change, prop))
        st.commands = unit_test_commands(prop, workspace)
        done("the findings")
        begin("the test sources")

        def deadline_check() -> None:  # between two requests: a run past its limit stops, it is not a source that failed to answer
            if clock() > deadline:
                raise _TimedOut("the test sources", timeout)

        st.outcome = outcome = testsources.run_all(
            workspace, SimpleNamespace(areas=[r.area for r in st.reached], findings=st.findings), env=options.env, transport=options.test_transport,
            git=git, run=run, origin=(repo, change.head_sha), as_of=as_of, check=deadline_check)
        records.extend(outcome.records)
        notes.extend(outcome.notes)
        done("the test sources")
    except _TimedOut as exc:
        status, detail = "timed_out", f"the time limit of {exc.seconds:g} s was reached after {exc.after}"
    except (ChangeSetRefused, ReadFailed, propagate.UnsupportedLanguage) as exc:  # the change cannot be analysed as it stands: the message is the reason
        status, detail = "failed", _tidy(str(exc))
    except Exception as exc:  # a bug or a surprise in a step: a failed run says so, with the type; never a traceback and never silence
        status, detail = "failed", _tidy(f"{type(exc).__name__} during {st.step}: {exc}")
    if options.retain is not None:
        options.retain.update(change=st.change, language=st.language, entry=st.entry, analysis=st.analysis, reach=st.reach, prop=st.prop,
                              matrix=st.matrix, git=st.git, records=records)
    return _analysis(workspace, repo, spec, as_of, records, notes, st, status, detail)


def _tidy(text: str) -> str:
    """Text of an exception or a record for a report: no credential, no machine path, one line."""
    return redact.for_record(redact.hide_machine_paths(redact.scrub(text)))


def _local_remotes(workspace: config.Workspace) -> dict[str, str]:
    """The remotes that are a directory or a `file://` url on this machine, by every way a record may spell them, to the
    repository's name: a report names the repository and not the machine's paths."""
    out: dict[str, str] = {}
    for name, cfg in workspace.repos.items():
        remote = cfg.remote
        if remote and (remote.startswith(("file://", "/", "./", "../", "~")) or (len(remote) > 1 and remote[1] == ":")):
            bare = remote[len("file://"):] if remote.startswith("file://") else remote
            for form in (remote, bare):
                out[form] = name
    return out


def _publish(text: str, local: Mapping[str, str]) -> str:
    for remote in sorted(local, key=len, reverse=True):
        text = text.replace(remote, local[remote])
    return redact.hide_machine_paths(text)


def _show_record(rec: Incompleteness, local: Mapping[str, str]) -> Incompleteness:
    subject, detail = _publish(rec.subject, local), _publish(rec.detail, local)
    if (subject, detail) == (rec.subject, rec.detail):
        return rec
    return Incompleteness(rec.kind, subject, detail, rec.blocks_selection)


def _roles(analysis: Any) -> dict[str, str]:
    return {f.path: f.role for f in analysis.files} if analysis is not None else {}


def _minimal_change(repo: str, spec: ChangeSpec, as_of: str | None, change: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"kind": spec.kind, "repo": repo, "spec": spec.as_dict(), "as_of": as_of, "resolved": change is not None}
    if change is not None:
        out["head_sha"] = change.head_sha
    return out


def _change_dict(workspace: config.Workspace, repo: str, spec: ChangeSpec, as_of: str | None, change: Any, analysis: Any) -> dict[str, Any]:
    if change is None:
        return _minimal_change(repo, spec, as_of, None)
    roles = _roles(analysis)
    cr = change.change_request
    local = _local_remotes(workspace)
    shown = local.get(workspace.repos[repo].remote or "", None) if local else None
    show = redact.display
    return {
        "kind": change.kind, "repo": repo, "remote": shown or _publish(change.remote, local), "spec": spec.as_dict(), "as_of": as_of, "resolved": True,
        "base_sha": change.base_sha, "head_sha": change.head_sha, "pre_change_sha": change.pre_change_sha, "how": dict(change.how),
        "change_request": None if cr is None else {
            "number": cr.number, "title": cr.title, "state": cr.state, "base_ref": cr.base_ref, "head_ref": cr.head_ref, "url": cr.url,
            "file_count": cr.file_count, "commit_count": cr.commit_count,
        },
        "file_count": change.file_count, "rename_detection_skipped": change.rename_detection_skipped,
        "files": [
            {"path": show(f.path), "old_path": show(f.old_path) if f.old_path else None, "status": f.status, "role": roles.get(f.path),
             "added": len(f.added_lines), "removed": len(f.removed_lines), "binary": f.binary, "content_available": f.content_available,
             "similarity": f.similarity}
            for f in sorted(change.files, key=lambda f: f.path)
        ],
    }


def _analysis(workspace: config.Workspace, repo: str, spec: ChangeSpec, as_of: str | None, records: list, notes: list[str],
              st: _State, status: str, detail: str) -> Analysis:
    """The Analysis of the run, validated: if making it fails (a value the model refuses, a step's result that cannot be
    written), the run is a failed one with a minimal change and the failure as its record, not an exception."""
    try:
        a = _assemble(workspace, repo, spec, as_of, records, notes, st, status, detail)
        a.to_dict()  # what the CLI writes: it must validate here, not after the caller has stopped looking
        return a
    except Exception as exc:
        failure = _tidy(f"{type(exc).__name__} during assembling the report: {exc}")
    return failed_analysis(workspace, repo, spec, as_of, failure, records, st.change)


def failed_analysis(workspace: config.Workspace, repo: str, spec: ChangeSpec, as_of: str | None, detail: str,
                    records: Sequence[Incompleteness] = (), change: Any = None, status: str = "failed") -> Analysis:
    """A run that did not complete (`failed`, or `timed_out` for the runner that killed it) with a minimal change, the records of
    the steps that could be kept, and `detail` as its blocking record (named by what was asked for when the change was never
    resolved). For a caller whose run ended outside `build`."""
    local = _local_remotes(workspace)
    ref = change.head_sha if change is not None else None
    for kept in (list(records), []):  # the records of the steps if they can be written; the failure alone if they cannot
        try:
            a = Analysis(change=_minimal_change(repo, spec, as_of, change), ref=ref)
            for rec in dedupe_records(_show_record(r, local) for r in kept):
                a.add_incompleteness(rec)
            a.finish(status, _publish(_tidy(detail), local), subject=spec.label)
            a.to_dict()
            return a
        except Exception:
            continue
    raise AssertionError("an analysis with only its failure record cannot be built")  # unreachable unless the model itself is broken


def _assemble(workspace: config.Workspace, repo: str, spec: ChangeSpec, as_of: str | None, records: list, notes: list[str],
              st: _State, status: str, detail: str) -> Analysis:
    change = st.change
    local = _local_remotes(workspace)
    a = Analysis(change=_change_dict(workspace, repo, spec, as_of, change, st.analysis), ref=change.head_sha if change is not None else None)
    if status == "completed":
        for row in st.matrix.rows:
            a.add_branch(row)
        notes = [*notes, *st.matrix.notes]
        for r in st.reached:
            a.add_area(r.area)
        for f in st.findings:
            a.add_finding(f)
        a.add_propagation(payload.propagation_to_dict(st.prop, st.commands))
        if st.outcome is not None:
            for s in st.outcome.suggestions:
                a.add_suggestion(s)
        for call in st.prop.backend_calls:
            a.add_backend_call(payload.encode(call))
    for note in dict.fromkeys(findings.clean(_publish(n, local), 600) for n in notes):
        a.add_note(note)
    for rec in dedupe_records(_show_record(r, local) for r in records):
        a.add_incompleteness(rec)
    a.finish(status, _publish(detail, local), subject=spec.label)
    return a
