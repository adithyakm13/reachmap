"""Judging sheets (context section 9, "Human judging sheets"): one row per listed screen or route and per suggested case, for a person to fill
in before any rule is tuned, and the counts of a filled sheet.

    reachmap sheet REPORT.json [--out sheet.csv] [--format csv|md]
    reachmap sheet --score FILLED.csv

**Sheets are for people.** A person marks each row `relevant (yes / no / unsure)` and `would have tested (yes / no)`, may add notes, and writes
their name in `judge`. A model must not fill a sheet: this module only writes the empty columns and counts what people wrote, the MCP
server has no tool that writes or scores a sheet (a test lists its tools), and `docs/evaluation.md` says so. `--score` never fills or infers
an answer; it counts, and refuses a sheet it cannot read as one person's answers (a missing column, an unknown value, an answer with no
judge, a repeated row, a row that is not a row of a sheet).

A row: the report's key and revision, the kind (the kind of the area: screen, api_route, task, job, topic; or `suggested case`), the area id
(the area a suggested case was suggested for), the label, the priority (must test, sample, candidate), the first and last hop of the
area's best chain (a candidate found by name has its route line), then the four columns for a person.

**CSV** is RFC 4180 (CRLF, quotes where needed) in UTF-8 with a byte order mark, so that a spreadsheet opens it as UTF-8, and is protected
against formula injection: a cell that starts with `=`, `+`, `-`, `@`, a tab or a carriage return is written with a leading quote (`'=1+1`), and a cell that already
starts with quotes followed by one of those gets one more, so reading removes exactly one and the text comes back as it was
(`protect` and `unprotect`). Text from the report (a label, a source line) is made visible first: control, bidirectional and zero-width
characters are written out, whitespace is one space. **Markdown** has the same rows as a table, every cell of report content in a code span.
"""

from __future__ import annotations

import csv
import io
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from reachmap.evaluate import EXIT_OK, UsageError, emit
from reachmap.report import text as shown

__all__ = [
    "COLUMNS", "Row", "Score", "SheetError", "build_rows", "protect", "read_csv", "render_csv", "render_markdown", "score_text", "unprotect",
]

KEY, REVISION, KIND, AREA, LABEL, PRIORITY, CHAIN = "report key", "revision", "kind", "area id", "label", "priority", "chain (first and last hop)"
RELEVANT, TESTED, NOTES, JUDGE = "relevant (yes / no / unsure)", "would have tested (yes / no)", "notes", "judge"
COLUMNS = (KEY, REVISION, KIND, AREA, LABEL, PRIORITY, CHAIN, RELEVANT, TESTED, NOTES, JUDGE)
FIXED = COLUMNS[:7]
ANSWERS = {RELEVANT: ("yes", "no", "unsure"), TESTED: ("yes", "no")}
SUGGESTED = "suggested case"
PRIORITY_WORDS = {"must_test": "must test", "sample": "sample", "candidate": "candidate"}
_HOP = 120  # characters of a cited line in the chain summary
_TRIGGER = "=+-@\t\r"
_PROTECTED = re.compile(r"^'*[=+\-@\t\r]")  # a cell that starts with a trigger, or with quotes and then a trigger
_QUOTED = re.compile(r"^'+[=+\-@\t\r]")
BOM = "\ufeff"

NOTICE = ("For a person to fill in: the columns relevant, would have tested, notes and judge are a person's answers. "
          "A model must not fill this sheet.")


class SheetError(ValueError):
    """A sheet that cannot be written or read as the answers of people."""


@dataclass(frozen=True)
class Row:
    cells: tuple[str, ...]  # one string per column of COLUMNS

    def get(self, column: str) -> str:
        return self.cells[COLUMNS.index(column)]


# ----------------------------------------------------------------------------- formulas


def protect(cell: str) -> str:
    """The cell as it is written to a CSV file: a leading quote when a spreadsheet would read it as a formula (it starts with `=`, `+`, `-`,
    `@`, a tab or a carriage return), and one more quote when it already starts with quotes before such a character, so that `unprotect`
    gives back exactly the text."""
    return "'" + cell if _PROTECTED.match(cell) else cell


def unprotect(cell: str) -> str:
    """What `protect` was given: a cell that starts with quotes followed by a trigger character loses one quote."""
    return cell[1:] if _QUOTED.match(cell) else cell


# ----------------------------------------------------------------------------- the rows of a report


def _one(text: Any) -> str:
    return shown.one_line(shown.show(str(text), shown.CONTENT_LIMIT))


def _hop(e: Mapping[str, Any]) -> str:
    return _one(f"{e['path']}:{e['line']} {shown.clip(str(e['detail']).strip(), _HOP)}")


def _chain(area: Mapping[str, Any]) -> str:
    chain = (area.get("chains") or [[]])[0] or [area["route_line"]]
    first, last = chain[0], chain[-1]
    return f"first: {_hop(first)}" if first is last else f"first: {_hop(first)} / last: {_hop(last)}"


def build_rows(report: Mapping[str, Any]) -> list[Row]:
    """The rows of a report: its areas in the report's order, then its suggested cases. A report that did not complete has none."""
    review = report.get("review") if isinstance(report.get("review"), dict) else {}
    key = (review.get("key") or {}).get("slug") if isinstance(review.get("key"), dict) else None
    if not isinstance(key, str):
        inputs = report.get("inputs") if isinstance(report.get("inputs"), dict) else {}
        change = inputs.get("change")
        spec = ":".join(str(x) for x in next(iter(change.items()))) if isinstance(change, dict) and change else ""
        key = f"{inputs.get('repo', '')} {spec}".strip() or "unkeyed report"
    revision = review.get("revision")
    rev = str(revision) if isinstance(revision, int) and not isinstance(revision, bool) else ""
    rows: list[Row] = []
    priority_of = {}
    for i, area in enumerate(report.get("areas") or []):
        where = f"areas[{i}]"
        try:
            priority_of[area["id"]] = PRIORITY_WORDS.get(area["priority"], _one(area["priority"]))
            rows.append(Row((_one(key), rev, _one(area["kind"]), _one(area["id"]), _one(area["label"]), priority_of[area["id"]], _chain(area), "", "", "", "")))
        except KeyError as exc:
            name = exc.args[0]
            more = " (an area without an id was made before decisions existed: rerun reachmap analyse)" if name == "id" else ""
            raise SheetError(f"{where} has no {name!r}, which a row of a sheet is made from{more}") from None
        except (TypeError, AttributeError, IndexError):
            raise SheetError(f"{where} is not an area as a report holds one: its fields, chains or route line are not the shapes a sheet reads") from None
    for i, s in enumerate(report.get("suggestions") or []):
        where = f"suggestions[{i}]"
        try:
            area_id = _one(s.get("area_id") or "")
            spec = (s.get("evidence") or [None])[0]
            how = _hop(spec) if spec else "matched words: " + _one(", ".join(s.get("matched_words") or []))
            rows.append(Row((_one(key), rev, SUGGESTED, area_id, _one(f"{s['source']} {s['case_id']}: {s['title']}"), priority_of.get(s.get("area_id") or "", ""), how, "", "", "", "")))
        except KeyError as exc:
            raise SheetError(f"{where} has no {exc.args[0]!r}, which a row of a sheet is made from") from None
        except (TypeError, AttributeError, IndexError):
            raise SheetError(f"{where} is not a suggestion as a report holds one: its fields or evidence are not the shapes a sheet reads") from None
    return rows


# ----------------------------------------------------------------------------- writing


def render_csv(rows: Sequence[Row]) -> str:
    """RFC 4180 text (CRLF) with a byte order mark; every cell passes `protect`."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(COLUMNS)
    for row in rows:
        writer.writerow([protect(c) for c in row.cells])
    return BOM + buf.getvalue()


def render_markdown(rows: Sequence[Row], title: str = "") -> str:
    from reachmap.report import markdown as md

    def cell(column: str, value: str) -> str:
        if value == "":
            return " "
        return md._code(value, table=True) if column in (KEY, LABEL, CHAIN, AREA) else md._escape(value).replace("|", "\\|")

    head = f"# Judging sheet{': ' + md._escape(title) if title else ''}"
    lines = [head, "", NOTICE, "", "relevant is yes, no or unsure; would have tested is yes or no; write your name in judge.", "",
             "| " + " | ".join(COLUMNS) + " |", "|" + "|".join(" --- " for _ in COLUMNS) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(cell(c, v) for c, v in zip(COLUMNS, row.cells)) + " |")
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------------- reading


def read_csv(text: str) -> list[Row]:
    """A sheet as rows, each cell unprotected. `SheetError` for text that is not a sheet: the header is not exactly the columns of a sheet,
    a row has another number of cells, or there is no row."""
    body = text[1:] if text.startswith(BOM) else text
    try:
        parsed = list(csv.reader(io.StringIO(body, newline=""), strict=True))
    except csv.Error as exc:
        raise SheetError(f"the file is not CSV ({exc})") from None
    if not parsed:
        raise SheetError("the file is empty: a sheet starts with a header row")
    header = tuple(parsed[0])
    missing = [c for c in COLUMNS if c not in header]
    if JUDGE in missing:
        raise SheetError(f"the sheet has no {JUDGE!r} column: an answer without the name of the person who gave it is not counted")
    extra = [c for c in header if c not in COLUMNS]
    if missing or extra or header != COLUMNS:
        raise SheetError(f"the header is not the header of a sheet (missing {missing}, unknown {extra}, or the columns are in another order)")
    rows = []
    for number, cells in enumerate(parsed[1:], start=2):
        if not cells:  # a blank line
            continue
        if len(cells) != len(COLUMNS):
            raise SheetError(f"line {number} has {len(cells)} cells and a row of a sheet has {len(COLUMNS)}")
        rows.append(Row(tuple(unprotect(c) for c in cells)))
    if not rows:
        raise SheetError("the sheet has no rows")
    return rows


@dataclass
class Score:
    rows: int
    answered: int  # rows with at least one answer
    judges: Counter
    relevant: Counter
    tested: Counter
    noted: int
    by_group: dict[tuple[str, str], Counter]  # (kind group, priority) to the relevant counts


def score_rows(rows: Sequence[Row]) -> Score:
    judges: Counter = Counter()
    relevant, tested = Counter(), Counter()
    groups: dict[tuple[str, str], Counter] = {}
    seen: dict[tuple[str, ...], int] = {}
    answered = noted = 0
    for number, row in enumerate(rows, start=1):
        identity = tuple(row.get(c) for c in (KEY, REVISION, KIND, AREA, LABEL))
        if identity in seen:
            raise SheetError(f"row {number} repeats row {seen[identity]} (the same report, revision, kind, area and label)")
        seen[identity] = number
        values = {}
        for column, allowed in ANSWERS.items():
            value = row.get(column).strip().lower()
            if value and value not in allowed:
                raise SheetError(f"row {number}: {shown.show(row.get(column), 40)!r} is not an answer for {column!r} (use {', '.join(allowed)} or leave it empty)")
            values[column] = value
        judge = row.get(JUDGE).strip()
        has_answer = any(values.values())
        if has_answer and not judge:
            raise SheetError(f"row {number} has an answer and no name in {JUDGE!r}: every answer is a person's and is signed")
        if judge:
            judges[_one(judge)] += 1
        answered += has_answer
        noted += bool(row.get(NOTES).strip())
        relevant[values[RELEVANT] or "blank"] += 1
        tested[values[TESTED] or "blank"] += 1
        group = ("suggested cases" if row.get(KIND) == SUGGESTED else "screens and routes", row.get(PRIORITY) or "none")
        groups.setdefault(group, Counter())[values[RELEVANT] or "blank"] += 1
    return Score(len(rows), answered, judges, relevant, tested, noted, groups)


def _counts(c: Counter, names: Sequence[str]) -> str:
    return ", ".join(f"{n} {c.get(n, 0)}" for n in (*names, "blank"))


def score_text(text: str) -> list[str]:
    """The counts of a filled sheet, as lines. Counts only: no rate, and nothing is filled or inferred."""
    s = score_rows(read_csv(text))
    lines = [f"sheet: {s.rows} rows, {s.answered} with an answer"]
    lines.append("judges: " + (", ".join(f"{name} {n} rows" for name, n in sorted(s.judges.items())) if s.judges else "none yet"))
    lines.append(f"{RELEVANT}: {_counts(s.relevant, ANSWERS[RELEVANT])}")
    lines.append(f"{TESTED}: {_counts(s.tested, ANSWERS[TESTED])}")
    lines.append(f"rows with notes: {s.noted}")
    for (group, priority), counts in sorted(s.by_group.items()):
        lines.append(f"  {group}, {priority}: {sum(counts.values())} rows; relevant: {_counts(counts, ANSWERS[RELEVANT])}")
    lines.append("These are the answers of the people who filled the sheet, counted as they are written. Nothing here fills, infers or rounds an answer.")
    return lines


# ----------------------------------------------------------------------------- the command


def command(ns: Any, env: Mapping[str, str], out: Any) -> int:
    """`reachmap sheet`: write a sheet for a report, or count a filled one. Exit 0; a file or option that cannot be used is exit 2."""
    from reachmap import report as reports

    if ns.score is not None:
        if ns.report is not None or ns.out is not None or ns.format is not None:
            raise UsageError("--score reads a filled sheet and takes no report, --out or --format")
        try:
            raw = Path(ns.score).read_bytes()
            text = raw.decode("utf-8")
        except OSError as exc:
            raise UsageError(f"{Path(ns.score).name}: cannot be read: {exc.strerror or type(exc).__name__}") from None
        except UnicodeDecodeError:
            raise UsageError(f"{Path(ns.score).name}: is not UTF-8 text") from None
        try:
            lines = score_text(text)
        except SheetError as exc:
            raise UsageError(f"{Path(ns.score).name}: {exc}") from None
        emit(out, "\n".join(lines))
        return EXIT_OK
    if ns.report is None:
        raise UsageError("give a REPORT.json to write a sheet for, or --score FILLED.csv to count a filled one")
    fmt = ns.format or ("md" if ns.out and str(ns.out).lower().endswith((".md", ".markdown")) else "csv")
    if fmt not in ("csv", "md"):
        raise UsageError(f"--format: {shown.show(fmt, 20)} is not a sheet format; use csv or md")
    try:
        data = reports.load(ns.report)
    except reports.UnsupportedSchema as exc:
        raise UsageError(f"{ns.report}: {exc}") from None
    except reports.ReportError as exc:
        raise UsageError(f"{ns.report}: not a report reachmap can read: {exc}") from None
    if data["status"] != "completed":
        raise UsageError(f"{ns.report}: the run did not complete, so it lists no screen, route or suggested case to judge")
    try:
        rows = build_rows(data)
    except SheetError as exc:
        raise UsageError(f"{ns.report}: {exc}") from None
    if not rows:
        raise UsageError(f"{ns.report}: the report lists no screen, route or suggested case to judge")
    rendered = render_csv(rows) if fmt == "csv" else render_markdown(rows, rows[0].get(KEY))
    if ns.out is None:
        emit(out, rendered, end="")
        return EXIT_OK
    try:
        Path(ns.out).write_bytes(rendered.encode("utf-8"))
    except OSError as exc:
        raise UsageError(f"--out: cannot write {Path(ns.out).name}: {exc.strerror or type(exc).__name__}") from None
    return EXIT_OK
