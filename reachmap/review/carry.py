"""Carrying decisions to a new analysis: which decisions are still about the claim they were made on.

For each target's latest decision (the last one in the store), against the targets of the report at hand:

- the target id is there and its fingerprint is the one the decision was made on: `current`;
- the id is there and the fingerprint is another: `stale` (the claim changed: when the earlier report is at hand and is the one the
  person saw, what changed in the evidence set is listed);
- the id is not there: `orphaned` (the area or finding no longer exists): listed in its own place, never dropped.

Targets that have no decision at all are `undecided`. A stale or orphaned decision is never applied: the report shows "decision needs
review". A decision is risk acceptance and never evidence (context rule 4.4): this module only reads a report and a store and returns
the lists; it cannot change a priority, a risk, a confidence, a count or a gap, and the report it is given is not touched.

The fingerprint of a target is `identity.fingerprint` of the area or finding: the claim (see `Evidence.claim`), without the commit or the line
numbers. It is in the report with the id, on each area and finding, so the HTML page exports the report's own value.

Nothing here does I/O.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from reachmap.review import store
from reachmap.review.formats import AREA_ID, FINDING_ID, FINGERPRINT

_ID = {"areas": AREA_ID, "findings": FINDING_ID}

__all__ = ["ReviewError", "Target", "carry", "targets_of"]

MAX_CHANGES = 20  # lines listed per side of a change


class ReviewError(ValueError):
    """A report that has no usable review data (it was made before M11, or its targets do not match its areas and findings)."""


@dataclass(frozen=True, slots=True)
class Target:
    """An area or a finding as a decision sees it: its id, fingerprint, a label to show and the evidence triples the fingerprint hashes."""

    id: str
    kind: str  # "area" or "finding"
    fingerprint: str
    label: str
    triples: tuple[tuple[str, str, str], ...]


def _triple(e: Any) -> tuple[str, str, str]:
    if not isinstance(e, Mapping):
        raise ReviewError("an evidence entry of the report is not an object")
    repo, path, detail = e.get("repo"), e.get("path"), e.get("detail")
    if not (isinstance(repo, str) and isinstance(path, str) and isinstance(detail, str)):
        raise ReviewError("an evidence entry of the report has no repository, path and line")
    return (repo, path, detail.strip())


def targets_of(report: Mapping[str, Any]) -> dict[str, Target]:
    """Every target of a report by id, in the order of its lists (areas, then findings): each area and finding carries its own `id` and
    `fingerprint` (checked when the report is read back as an `Analysis`). `ReviewError` when a report has none (made before M11)."""
    out: dict[str, Target] = {}
    for name, kind in (("areas", "area"), ("findings", "finding")):
        items = report.get(name)
        if not isinstance(items, list):
            raise ReviewError(f"the report has no {name} to decide about")
        for item in items:
            if not isinstance(item, Mapping):
                raise ReviewError(f"an entry of the report's {name} is not an object")
            tid, fp = item.get("id"), item.get("fingerprint")
            if not (isinstance(tid, str) and _ID[name].match(tid) and isinstance(fp, str) and FINGERPRINT.match(fp)):
                raise ReviewError(f"the report's {name} have no id and fingerprint (it was made before decisions existed): rerun reachmap analyse")
            if kind == "area":
                evidence = [item.get("route_line"), *(e for chain in item.get("chains") or [] for e in chain)]
                label = item.get("label")
            else:
                evidence = list(item.get("evidence") or [])
                label = item.get("title")
            if not isinstance(label, str):
                raise ReviewError(f"an entry of the report's {name} has no label")
            if tid in out:
                raise ReviewError("the report repeats an id")
            out[tid] = Target(tid, kind, fp, label, tuple(sorted(_triple(e) for e in evidence)))
    return out


def _entry(s: store.Stored, kind: str, label: str | None) -> dict[str, Any]:
    d = s.decision
    return {
        "target_id": d.target_id, "kind": kind, "label": label, "verdict": d.verdict, "reason": d.reason, "author": d.author, "channel": d.channel,
        "fingerprint": d.fingerprint, "revision": d.revision, "created_at": s.created_at,
    }


def _changes(old: Target, new: Target) -> dict[str, list[dict[str, str]]]:
    before, after = Counter(old.triples), Counter(new.triples)
    as_dict = lambda t: {"repo": t[0], "path": t[1], "detail": t[2]}  # noqa: E731
    return {
        "added": [as_dict(t) for t in sorted((after - before).elements())[:MAX_CHANGES]],
        "removed": [as_dict(t) for t in sorted((before - after).elements())[:MAX_CHANGES]],
    }


def carry(
    report: Mapping[str, Any], loaded: store.Loaded, key: Mapping[str, str] | None = None,
    earlier: Callable[[int], Mapping[str, Target] | None] | None = None,
) -> dict[str, Any]:
    """The `decisions` object of a report: `current`, `stale`, `orphaned` (lists of entries), `undecided` (the ids of areas and findings
    with no decision) and `records` (the blocking `decision_store_unreadable` record when the store could not be read: then there is no
    decision in any list, whatever lines were intact). `earlier(revision)` gives the targets of the report of that revision, when it is
    at hand (it is used only when its fingerprint for the target is the one the decision was made on)."""
    if loaded.problem is not None:
        if key is None:
            raise ReviewError("an unreadable store needs the analysis key to say which store it is")
        return {"current": [], "stale": [], "orphaned": [], "undecided": {"areas": [], "findings": []}, "records": [store.unreadable_record(key, loaded.problem)]}
    targets = targets_of(report)
    latest = store.latest(loaded.items)
    current: list[dict[str, Any]] = []
    stale: list[dict[str, Any]] = []
    for tid, target in targets.items():  # the report's order: areas, then findings
        item = latest.get(tid)
        if item is None:
            continue
        entry = _entry(item, target.kind, target.label)
        if item.decision.fingerprint == target.fingerprint:
            current.append(entry)
            continue
        then = None
        if earlier is not None:
            known = earlier(item.decision.revision)
            seen = known.get(tid) if known else None
            then = seen if seen is not None and seen.fingerprint == item.decision.fingerprint else None
        stale.append({**entry, "now_fingerprint": target.fingerprint, "changes": None if then is None else _changes(then, target)})
    orphaned: list[dict[str, Any]] = []
    for tid, item in latest.items():  # the order the decisions were written
        if tid in targets:
            continue
        kind = "area" if tid.startswith("a-") else "finding"
        label = None
        if earlier is not None:
            known = earlier(item.decision.revision)
            seen = known.get(tid) if known else None
            label = seen.label if seen is not None and seen.fingerprint == item.decision.fingerprint else None
        orphaned.append(_entry(item, kind, label))
    undecided = {
        "areas": [t.id for t in targets.values() if t.kind == "area" and t.id not in latest],
        "findings": [t.id for t in targets.values() if t.kind == "finding" and t.id not in latest],
    }
    return {"current": current, "stale": stale, "orphaned": orphaned, "undecided": undecided, "records": []}
