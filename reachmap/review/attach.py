"""What `analyse`, `decide` and `report` put beside an analysis to make it a revision with decisions.

The report made by `analyse` has, after everything the analysis holds (which is a pure function of the code and is not touched, but for
each area and finding carrying its own `id` and `fingerprint`: see `model.Area.to_dict`), a `schema_minor` and one `review` object:

- `key`: the analysis key (workspace, repo, change identity) and the `slug` that names its files;
- `revision`: the number of this run among the runs on the key, and `previous` (that run's `number`, report `file` and `head_sha`, or null for
  the first). The top-level `revision` is the model's own and stays 1; the number a person means is `review.revision`;
- `decisions`: `current`, `stale`, `orphaned` (lists of decision objects), `undecided` (ids), `records` (a blocking record when the store
  could not be read) and `as_of`, the time the store was read;
- `as_of`: the same time. A consumer that wants the decisions of now recomputes them from the store (`reachmap report` does); what is in
  the file is what the store said when the report was made.

A run that did not complete has the key, `revision`, `previous`, `decisions` and `as_of` all null, and is not entered in the revision log.
`reachmap report` makes `decisions` again from the store beside the report, so a decision made after the analysis shows in the rendering
without the analysis file being rewritten.

`finish` and `record` are for the critical section of `cli` (under `lock.held`): numbering, carrying, writing and logging are one step.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from reachmap.model import Analysis
from reachmap.review import carry, revisions, store
from reachmap.review.exchange import Exchange, ExchangeError

__all__ = ["Imported", "SCHEMA_MINOR", "finish", "has_review", "import_effects", "key_of", "record", "refresh", "require_known_revision", "revision_targets", "seed"]

SCHEMA_MINOR = 2


def key_of(report: Mapping[str, Any]) -> dict[str, str]:
    """The analysis key a report was made for. `ReviewError` for a report made before M11 (it has none)."""
    review = report.get("review")
    given = review.get("key") if isinstance(review, Mapping) else None
    try:
        return store.make_key(given["workspace"], given["repo"], given["change"])  # type: ignore[index]
    except (TypeError, KeyError, store.StoreError):
        raise carry.ReviewError("this report has no review data (it was made before decisions existed): rerun reachmap analyse") from None


def has_review(report: Mapping[str, Any]) -> bool:
    review = report.get("review")
    return isinstance(review, Mapping) and isinstance(review.get("key"), Mapping)


def seed(report: dict[str, Any], key: Mapping[str, str]) -> None:
    """The review object of a report as the run leaves it before any store is read: the key, and nothing else."""
    report["schema_minor"] = SCHEMA_MINOR
    report["review"] = {"key": {**key, "slug": store.key_slug(key)}, "revision": None, "previous": None, "decisions": None, "as_of": None}


def _earlier(base: Path, log: list[revisions.Rev]) -> Callable[[int], Mapping[str, carry.Target] | None]:
    """The targets of the report file of an earlier revision, when it is still beside the others and is that revision (its own
    `review.revision` and head commit are the log's): what a stale decision's change is shown against."""
    from reachmap import report as reports

    cache: dict[int, Mapping[str, carry.Target] | None] = {}

    def load(number: int) -> Mapping[str, carry.Target] | None:
        if number not in cache:
            cache[number] = None
            rev = revisions.find(log, number)
            if rev is not None:
                try:
                    data = reports.load(base / rev.file)
                    review = data.get("review") or {}
                    if review.get("revision") == number and data.get("ref") == rev.head_sha and data.get("status") == "completed":
                        cache[number] = carry.targets_of(data)
                except (reports.ReportError, carry.ReviewError, OSError):
                    pass
        return cache[number]

    return load


def revision_targets(base: Path, log: list[revisions.Rev]) -> Callable[[int], Mapping[str, carry.Target] | None]:
    """For a reader of another kind of record (the model notes): the targets of the report file of a revision, when it is still beside the others
    and is that revision (see `_earlier`)."""
    return _earlier(base, log)


def _decisions(report: Mapping[str, Any], base: Path, key: Mapping[str, str], log: list[revisions.Rev], as_of: str) -> dict[str, Any]:
    out = carry.carry(report, store.read(base, key), key, earlier=_earlier(base, log))
    out["as_of"] = as_of
    return out


@dataclass(frozen=True, slots=True)
class Imported:
    """What an import did: how many decisions were added, how many of them replaced a decision in force, and a warning for each that is
    older (by its own time) than the one it replaces."""

    count: int = 0
    replaced: int = 0
    warnings: tuple[str, ...] = ()


def _time(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def import_effects(before: store.Loaded, items: list[store.Stored]) -> Imported:
    """Of the decisions to add: those that replace the decision in force for their target (against what the store holds now, and against
    the earlier decisions of the same file) and the warnings for the ones older than the one they replace. Nothing is refused."""
    in_force = {t: s.created_at for t, s in store.latest(before.items).items()}
    replaced, warnings = 0, []
    for item in items:
        target, created = item.decision.target_id, item.created_at
        if target in in_force:
            replaced += 1
            if _time(created) < _time(in_force[target]):
                warnings.append(
                    f"reachmap: warning: the imported decision for {target} was recorded at {created}, before the one it replaces ({in_force[target]}); it is now the one in force")
        in_force[target] = created
    return Imported(len(items), replaced, tuple(warnings))


def require_known_revision(incoming: Exchange, log: list[revisions.Rev]) -> None:
    """A decisions file says which revision, and which commit, it was exported from: the reports directory must have a record of that
    revision, else the fingerprints in the file cannot be told to belong to this change."""
    rev = revisions.find(log, incoming.revision)
    if rev is None or rev.head_sha != incoming.head_sha:
        raise ExchangeError(
            f"the file was exported from revision {incoming.revision} (commit {incoming.head_sha[:12]}) of this change, and the reports directory has "
            "no record of that revision; nothing was imported")


def finish(report: dict[str, Any], base: Path, key: Mapping[str, str], incoming: Exchange | None, now_iso: str) -> Imported:
    """For a run that completed, under the lock: add the decisions in `incoming` (a file read by `--decisions`) to the store, then give the
    report its revision (the next in the log, the previous one named) and its decisions. `StoreError` when the log or the store cannot be
    used, `ExchangeError` when the file's revision is not in the log. The revision is entered in the log by `record` once the report file is
    written."""
    log = revisions.read(base, key)
    done = Imported()
    if incoming is not None:
        require_known_revision(incoming, log)
        items = incoming.to_stored()
        before = store.read(base, key)
        store.append(base, key, items)
        done = import_effects(before, items)
    review = report["review"]
    review["revision"] = revisions.next_number(log)
    last = revisions.previous(log)
    review["previous"] = None if last is None else last.to_dict()
    review["decisions"] = _decisions(report, base, key, log, now_iso)
    review["as_of"] = now_iso
    return done


def record(report: Mapping[str, Any], base: Path, key: Mapping[str, str], file: str, now_iso: str) -> None:
    """Enter the run in the revision log, after its report is written."""
    revisions.record(base, key, revisions.Rev(report["review"]["revision"], file, report["ref"], now_iso))


def _present(path: Path) -> bool:
    """Whether the store file may be there. A folder that cannot be searched answers no question: it is taken as present, so that the
    reading says it cannot read it (an unreadable store is never an absent one)."""
    try:
        return path.exists()
    except OSError:
        return True


def refresh(report: dict[str, Any], base: Path, as_of: str | None = None) -> None:
    """Make `review.decisions` again from the store beside the report (`reachmap report`): nothing is changed when the report has no review
    data, did not complete, or has no store here (a report moved away from its folder shows the decisions it was made with)."""
    if not has_review(report) or report.get("status") != "completed" or not isinstance(report["review"].get("decisions"), Mapping):
        return
    try:
        key = key_of(report)
    except carry.ReviewError:
        return
    if not _present(store.decisions_path(base, key)):
        return
    try:
        log = revisions.read(base, key)
    except store.StoreError:
        log = []
    stamp = as_of or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        fresh = _decisions(report, base, key, log, stamp)
    except carry.ReviewError:
        return
    held = report["review"]["decisions"]
    if {k: v for k, v in fresh.items() if k != "as_of"} != {k: v for k, v in held.items() if k != "as_of"}:  # the store said something else since: say when
        report["review"]["decisions"] = fresh
        report["review"]["as_of"] = stamp
