"""The CLI summary of a run, made from the report JSON (moved out of `cli.py`; the words are M9's).

Blocking records first, then the areas to test (the first few), findings by kind, the branches in one line, the records that do
not block, and, only when the run is not whole, that the lists may be incomplete (a whole run never says "complete": that reads as
coverage). It never says a release is safe or that nothing needs testing; with no area and no blocking record it says exactly "No
areas reached by this change were found; see the report for what was examined." A run that failed or timed out says so, with no count.

Every line passes the wording check: what is quoted from the report is scrubbed and a banned phrase in it is defused (`redact.for_record`),
and odd characters are written out as the Markdown and the page write them (`text.visible`), so that a source line cannot send an escape sequence
to a terminal.

Takes the report JSON and nothing else. Nothing here does I/O.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import reachmap
from reachmap import redact
from reachmap.report.read import blocks
from reachmap.report import text

__all__ = ["AREAS_SHOWN", "lines"]

AREAS_SHOWN = 5


def _line(value: str) -> str:
    """Quoted content as one line: odd characters written out the way the Markdown and the page write them (`text.visible`), then scrubbed,
    defused and cut."""
    return redact.for_record(text.visible(value[:4000]), 400)


def _label(change: Mapping[str, Any]) -> str:
    spec = change.get("spec") or {}
    for key in ("pr", "compare", "range"):
        if key in spec:
            return f"{key} {spec[key]}"
    return str(change.get("kind"))


def _extra(report_path: str | None, extra: Mapping[str, str] | None) -> list[str]:
    out = [f"Report: {report_path}"] if report_path else []
    names = {"md": "Markdown", "html": "HTML"}
    out += [f"{names[k]}: {v}" for k, v in (extra or {}).items() if k in names]
    return out


def _revision_lines(report: Mapping[str, Any]) -> list[str]:
    """From the second run of a change on: which revision this is and which one it revises (nothing for the first, as before M11)."""
    review = report.get("review") if isinstance(report.get("review"), dict) else {}
    previous = review.get("previous")
    number = review.get("revision")
    if isinstance(previous, dict) and isinstance(number, int) and not isinstance(number, bool) and isinstance(previous.get("number"), int):
        return [_line(f"Revision {number} of this change; it revises revision {previous['number']} ({previous.get('file')})")]
    return []


def _decision_lines(report: Mapping[str, Any]) -> list[str]:
    """What became of the recorded decisions: counted by state, or that they could not be read. Nothing when there are none."""
    review = report.get("review")
    d = review.get("decisions") if isinstance(review, dict) else None
    if not isinstance(d, dict):
        return []
    recs = [r for r in d.get("records") or [] if isinstance(r, dict)]
    if recs:
        return [_line(f"Decisions: not read: {r.get('not_established')} ({r.get('kind')}: {r.get('subject')})") for r in recs]
    counts = [len(d.get(k) or []) for k in ("current", "stale", "orphaned")]
    if not any(counts):
        return []
    return [f"Decisions: {counts[0]} current, {counts[1]} need review, {counts[2]} orphaned"]


def lines(report: Mapping[str, Any], report_path: str | None = None, extra: Mapping[str, str] | None = None) -> list[str]:
    """The summary lines of a run. `extra` maps a format (`md`, `html`) to the file written for it, listed after the report line."""
    records: Sequence[Mapping[str, Any]] = report["incompleteness"]
    status = report["status"]
    if status == "timed_out":
        rec = [r for r in records if r["kind"] == "timed_out"][0]
        return [_line(f"reachmap: the run hit its time limit: {rec['detail']}. No result is reported."), *_extra(report_path, extra)]
    if status == "failed":
        rec = [r for r in records if r["kind"] == "run_failed"][0]
        return [_line(f"reachmap: the change could not be analysed: {rec['detail']}"), *_extra(report_path, extra)]
    out: list[str] = []
    flags = [blocks(r) for r in records]
    area_blocking = [r for r, (sel, _) in zip(records, flags) if sel]  # a test source that failed says nothing about the areas
    blocking = [r for r, (sel, sug) in zip(records, flags) if sel or sug]
    for r in blocking:
        out.append(_line(f"Blocking: {r['not_established']} ({r['kind']}: {r['subject']})"))
    change = report["change"]
    head = f", head {report['ref'][:12]}" if change.get("resolved") else ""
    out.append(_line(f"reachmap {reachmap.__version__}: {change['repo']}, {_label(change)}{head}"))
    out += _revision_lines(report)
    areas = report["areas"]
    to_test = [x for x in areas if x["priority"] in ("must_test", "sample")]
    candidates = [x for x in areas if x["by_name"]]
    if to_test:
        must, sample = sum(x["priority"] == "must_test" for x in to_test), sum(x["priority"] == "sample" for x in to_test)
        split = ", ".join(p for p in (f"must test {must}" if must else "", f"sample {sample}" if sample else "") if p)
        out.append(f"Areas to test: {len(to_test)} ({split})")
        order = sorted(to_test, key=lambda x: (x["priority"] != "must_test", x["repo"], x["kind"], x["label"]))
        for x in order[:AREAS_SHOWN]:
            word = "must test" if x["priority"] == "must_test" else "sample"
            out.append(f"  {word.ljust(11)}" + _line(f"{x['kind']} {x['label']} ({x['repo']})"))  # the padding is ours; what follows quotes code
        if len(order) > AREAS_SHOWN:
            out.append(f"  and {len(order) - AREAS_SHOWN} more in the report")
    elif area_blocking:
        out.append("Areas to test: none were established; the blocking records above say what could not be read.")
    else:
        out.append("No areas reached by this change were found; see the report for what was examined.")
    if candidates:
        out.append(f"Candidates found by name, not import-backed and not counted above: {len(candidates)}")
    suggested = report.get("suggestions") or []
    if suggested:
        out.append(f"Suggested cases (not coverage and not counted above): {len(suggested)}")
    kinds: dict[str, int] = {}
    for f in report["findings"]:
        kinds[f["kind"]] = kinds.get(f["kind"], 0) + 1
    if kinds:
        out.append(_line("Findings: " + f"{sum(kinds.values())} (" + ", ".join(f"{k} {n}" for k, n in sorted(kinds.items())) + ")"))
    if report["branches"]:
        out.append(_line("Branches: " + ", ".join(f"{b['branch']} {b['state']}" for b in report["branches"])))
    out += _decision_lines(report)
    quiet = [r for r, (sel, sug) in zip(records, flags) if not (sel or sug)]
    if quiet:
        out.append(f"Also not established, without blocking: {len(quiet)} record(s) in the report")
    props = report.get("propagations") or []
    if props and props[0].get("complete") is False:
        out.append("The lists above may be incomplete: the report says what was not established.")
    out += _extra(report_path, extra)
    return out
