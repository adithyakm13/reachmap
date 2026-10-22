"""Reports (M10): the JSON that `reachmap analyse` writes is the contract, and everything else is rendered from it.

    report.load(path)            read a report file: JSON, the schema version, the parts the renderers rely on
    report.check(data)           the same checks on a report already in memory (returns it)
    report.render(data, "md")    Markdown; "html" for the single-file page; `view="author"` for the "For the author" view

A report with an unknown major version (`schema` is the major version, an integer) is refused with `UnsupportedSchema`, never
guessed at; one the renderers could not read safely is a `ReportError` that names what is wrong (never a value from the report).
Rendering takes the report JSON and nothing else: no repository, no configuration, no network, and no file but the page's own
assets; report content (it quotes source lines from every repository read) is never turned into markup (context rule 4.8).
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping

__all__ = ["FORMATS", "ReportError", "SCHEMA", "UnsupportedSchema", "VIEWS", "check", "load", "parse", "render"]

SCHEMA = 1  # the major version this code reads
FORMATS = ("md", "html")
VIEWS = ("full", "author")
_ALIASES = {"markdown": "md"}
STATUSES = ("completed", "failed", "timed_out")
RISKS = ("low", "medium", "high")
PRIORITIES = ("must_test", "sample", "candidate")
_FAILURE_RECORD = {"failed": "run_failed", "timed_out": "timed_out"}
_LISTS = ("findings", "areas", "branches", "propagations", "backend_calls", "suggestions")
_REQUIRED = ("schema", "status", "change", "ref", "incompleteness", "notes", *_LISTS)


class ReportError(ValueError):
    """The report cannot be rendered: it is not a report, or a part of it is not what the renderers rely on."""


class UnsupportedSchema(ReportError):
    """The report's major version is not one this code reads."""


# ------------------------------------------------------------------------------ checking


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _need(cond: bool, where: str, what: str) -> None:
    if not cond:
        raise ReportError(f"{where}: {what}")


def _evidence(v: Any, where: str) -> None:
    _need(isinstance(v, dict), where, "evidence must be an object")
    for key in ("repo", "path", "ref", "detail"):
        _need(isinstance(v.get(key), str), where, f"evidence needs a string {key}")
    _need(_is_int(v.get("line")), where, "evidence needs an integer line")
    _need("claim" not in v or isinstance(v["claim"], str), where, "an evidence claim must be a string")


def _evidence_list(v: Any, where: str, nonempty: bool = False) -> None:
    _need(isinstance(v, list), where, "must be a list")
    _need(bool(v) or not nonempty, where, "must hold evidence (every finding cites some)")
    for i, e in enumerate(v):
        _evidence(e, f"{where}[{i}]")


def _strings(v: Any, where: str) -> None:
    _need(isinstance(v, list) and all(isinstance(x, str) for x in v), where, "must be a list of strings")


def _records(data: Mapping[str, Any]) -> None:
    recs = data["incompleteness"]
    _need(isinstance(recs, list), "incompleteness", "must be a list")
    for i, r in enumerate(recs):
        where = f"incompleteness[{i}]"
        _need(isinstance(r, dict), where, "must be an object")
        for key in ("kind", "subject", "not_established", "detail"):
            _need(isinstance(r.get(key), str), where, f"needs a string {key}")
        _need(isinstance(r.get("blocks_selection"), bool), where, "needs a boolean blocks_selection")
    needed = _FAILURE_RECORD.get(data["status"])
    if needed is not None:
        _need(any(r["kind"] == needed for r in recs), "incompleteness", f"a run that {data['status'].replace('_', ' ')} carries a {needed} record")


def _completed(data: Mapping[str, Any]) -> None:
    for key in _LISTS:
        _need(isinstance(data[key], list), key, "a completed run has this list (null is for a run that did not complete)")
    _need(isinstance(data.get("mapped_tests", []), list), "mapped_tests", "must be a list")
    for i, f in enumerate(data["findings"]):
        where = f"findings[{i}]"
        _need(isinstance(f, dict), where, "must be an object")
        _need(isinstance(f.get("kind"), str) and f["kind"] != "", where, "needs a string kind")
        for key in ("title", "confidence_basis"):
            _need(isinstance(f.get(key), str), where, f"needs a string {key}")
        _need(f.get("risk") in RISKS, where, "risk must be low, medium or high")
        _need(f.get("confidence") in RISKS, where, "confidence must be low, medium or high")
        _need(isinstance(f.get("is_exclusion"), bool), where, "needs a boolean is_exclusion")
        _strings(f.get("identity"), f"{where}.identity")
        _evidence_list(f.get("evidence"), f"{where}.evidence", nonempty=True)
    for i, a in enumerate(data["areas"]):
        where = f"areas[{i}]"
        _need(isinstance(a, dict), where, "must be an object")
        for key in ("label", "kind", "repo", "module"):
            _need(isinstance(a.get(key), str), where, f"needs a string {key}")
        _need(a.get("priority") in PRIORITIES, where, "priority must be must_test, sample or candidate")
        _need(isinstance(a.get("by_name"), bool), where, "needs a boolean by_name")
        _evidence(a.get("route_line"), f"{where}.route_line")
        _need(isinstance(a.get("chains"), list), where, "chains must be a list")
        for j, chain in enumerate(a["chains"]):
            _evidence_list(chain, f"{where}.chains[{j}]")
        _strings(a.get("also_mounted"), f"{where}.also_mounted")
    for i, b in enumerate(data["branches"]):
        where = f"branches[{i}]"
        _need(isinstance(b, dict), where, "must be an object")
        for key in ("branch", "commit", "state", "detail"):
            _need(isinstance(b.get(key), str), where, f"needs a string {key}")
        _need(_is_int(b.get("added_lines_found")), where, "needs an integer added_lines_found")
        _evidence_list(b.get("evidence"), f"{where}.evidence")
    _need(all(isinstance(p, dict) for p in data["propagations"]), "propagations", "must be a list of objects")
    _need(all(isinstance(c, dict) for c in data["backend_calls"]), "backend_calls", "must be a list of objects")
    _need(all(isinstance(s, dict) for s in data["suggestions"]), "suggestions", "must be a list of objects")
    _need(all(isinstance(s, dict) for s in data.get("mapped_tests", [])), "mapped_tests", "must be a list of objects")


def _review(data: Mapping[str, Any]) -> None:
    """The review object (M11) is optional: a report from before it has none. When it is there it has the shapes the renderers and
    `reachmap decide` read; what is inside a decision is content and is read tolerantly. Each area and finding may carry an `id` and a
    `fingerprint` (strings)."""
    if "schema_minor" in data:
        _need(_is_int(data["schema_minor"]) and data["schema_minor"] >= 0, "schema_minor", "must be a whole number")
    review = data.get("review")
    _need(review is None or isinstance(review, dict), "review", "must be an object or null")
    if isinstance(review, dict):
        for key in ("key", "previous", "decisions"):
            _need(review.get(key) is None or isinstance(review[key], dict), f"review.{key}", "must be an object or null")
        _need(review.get("revision") is None or (_is_int(review["revision"]) and review["revision"] >= 1), "review.revision", "must be a whole number from 1 or null")
        _need(review.get("as_of") is None or isinstance(review["as_of"], str), "review.as_of", "must be a time or null")
        dec = review.get("decisions")
        if isinstance(dec, dict):
            for name in ("current", "stale", "orphaned", "records"):
                _need(isinstance(dec.get(name, []), list), f"review.decisions.{name}", "must be a list")
            _need(isinstance(dec.get("undecided", {}), dict), "review.decisions.undecided", "must be an object")
    for name in ("areas", "findings"):
        for i, item in enumerate(data.get(name) or []):
            for key in ("id", "fingerprint"):
                _need(not isinstance(item, dict) or key not in item or isinstance(item[key], str), f"{name}[{i}].{key}", "must be a string")


def _model_notes(data: Mapping[str, Any]) -> None:
    """The model notes (M14) are optional: `reachmap report` and the runner add them from the store beside the report. When there, they are an
    object with a list of notes (each an object whose parts are content and are read leniently) and a list of records with the strings of an
    incompleteness record."""
    given = data.get("model_notes")
    _need(given is None or isinstance(given, dict), "model_notes", "must be an object or null")
    if not isinstance(given, dict):
        return
    items, recs = given.get("notes", []), given.get("records", [])
    _need(isinstance(items, list) and all(isinstance(x, dict) for x in items), "model_notes.notes", "must be a list of objects")
    _need(isinstance(recs, list), "model_notes.records", "must be a list")
    for i, r in enumerate(recs):
        where = f"model_notes.records[{i}]"
        _need(isinstance(r, dict), where, "must be an object")
        for key in ("kind", "subject", "not_established", "detail"):
            _need(isinstance(r.get(key), str), where, f"needs a string {key}")
    _need(given.get("as_of") is None or isinstance(given["as_of"], str), "model_notes.as_of", "must be a time or null")


def check(data: Any) -> Any:
    """`data` if it is a report these renderers can read, else `ReportError` (`UnsupportedSchema` for another major version).
    The report is not changed."""
    _need(isinstance(data, dict), "report", "a report is a JSON object")
    _finite(data)
    schema = data.get("schema")
    _need("schema" in data, "schema", "the report has no schema version")
    _need(_is_int(schema) and schema >= 1, "schema", "must be a positive integer (the major version of the report format)")
    if schema != SCHEMA:
        raise UnsupportedSchema(f"schema {schema}: this version of reachmap reads schema {SCHEMA} reports only; use the reachmap that wrote it")
    for key in _REQUIRED:
        _need(key in data, key, "the report has no such key")
    _need(data["status"] in STATUSES, "status", "must be completed, failed or timed_out")
    _need(isinstance(data["change"], dict), "change", "must be an object")
    _need(isinstance(data["notes"], list) and all(isinstance(n, str) for n in data["notes"]), "notes", "must be a list of strings")
    _records(data)
    _review(data)
    _model_notes(data)
    if data["status"] == "completed":
        _completed(data)
    return data


def _finite(data: Any) -> None:
    """No float anywhere in a report is NaN or infinite (JSON has no such number; `1e999` is one in Python)."""
    stack = [data]
    while stack:
        v = stack.pop()
        if isinstance(v, float) and not math.isfinite(v):
            raise ReportError("a number in the report is not finite")
        elif isinstance(v, dict):
            stack.extend(v.values())
        elif isinstance(v, list):
            stack.extend(v)


def parse(text: str) -> Any:
    """The report in JSON text, checked."""

    def refuse(name: str) -> Any:
        raise ReportError(f"JSON: {name} is not allowed in a report")

    def number(literal: str) -> float:
        value = float(literal)
        if not math.isfinite(value):
            raise ReportError("JSON: a number is too large to be a number")
        return value

    try:
        data = json.loads(text, parse_constant=refuse, parse_float=number)
    except RecursionError:
        raise ReportError("JSON: nested too deeply") from None
    except ValueError as exc:
        if isinstance(exc, ReportError):
            raise
        raise ReportError(f"not JSON: {exc}") from None
    return check(data)


def load(path: str | Path) -> Any:
    """Read a report file (UTF-8 JSON) and check it. A file that cannot be read is a `ReportError`, never an `OSError`."""
    p = Path(path)
    try:
        raw = p.read_bytes()
    except OSError as exc:
        raise ReportError(f"cannot read {p.name}: {exc.strerror or type(exc).__name__}") from None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ReportError(f"{p.name} is not UTF-8 text") from None
    return parse(text)


# ------------------------------------------------------------------------------ rendering


def render(data: Any, fmt: str, view: str = "full") -> str:
    """The report as Markdown (`md`, or `markdown`) or one self-contained HTML page (`html`); `view` is `full` or `author`."""
    name = _ALIASES.get(fmt, fmt)
    if name not in FORMATS:
        raise ValueError(f"format: {fmt!r} is not one of {', '.join(FORMATS)}")
    if view not in VIEWS:
        raise ValueError(f"view: {view!r} is not one of {', '.join(VIEWS)}")
    from reachmap.report import html, layout, markdown

    check(data)
    doc = layout.build(data, view)
    if name == "md":
        return markdown.render(doc)
    try:
        return html.render(doc, data)
    except (TypeError, ValueError, RecursionError) as exc:  # a value JSON cannot carry (NaN, a set) in a report that did not come from a file
        raise ReportError(f"the report holds a value that JSON cannot carry ({type(exc).__name__})") from None
