"""Judging sheets (M18): the rows of a report, CSV that is protected against formula injection and reads back as it was written, Markdown, the counts of
a filled sheet and what it refuses, and that nothing in the product fills or scores a sheet for a person."""

import csv
import io
import re
import tempfile
import unittest
from pathlib import Path

from reachmap import cli, identity, wording
from reachmap.evaluate import sheets
from reachmap.evaluate.sheets import COLUMNS, JUDGE, NOTES, RELEVANT, TESTED, Row, SheetError
from tests import support_git as sg
from tests import support_report as sr

TRIGGERS = ("=", "+", "-", "@", "\t", "\r")
SUGGESTIONS = [
    {"source": "playwright", "case_id": "e2e/billing.spec.ts:12", "title": "opens the invoice list", "matched_words": ["invoice"], "area_id": None,
     "evidence": [{"repo": "acme/web", "path": "e2e/billing.spec.ts", "line": 12, "ref": "a" * 40, "detail": "await page.goto('/billing/invoices');"}],
     "label": "suggested, not verified coverage"},
    {"source": "qase", "case_id": "QA-7", "title": "Invoice totals", "matched_words": ["invoice", "total"], "area_id": None, "evidence": [],
     "label": "suggested, not verified coverage"},
]


def report(hostile=False):
    data = sr.fixture("complete")
    for a in data["areas"]:
        a["id"] = identity.area_id(a["kind"], a["repo"], a["label"])
    data["suggestions"] = [dict(s) for s in SUGGESTIONS]
    data["suggestions"][0]["area_id"] = data["areas"][1]["id"]
    data["review"] = {"key": {"workspace": "acme", "repo": "acme/api", "change": "compare:main...topic", "slug": "acme--acme-api--compare-main-topic--0123abcd"},
                      "revision": 3, "previous": None, "decisions": None, "as_of": None}
    return sr.hostile_copy(data) if hostile else data


def parsed(text):
    return list(csv.reader(io.StringIO(text.removeprefix("\ufeff"), newline="")))


class Rows(unittest.TestCase):
    def test_one_row_per_area_in_the_reports_order_then_one_per_suggested_case(self):
        rows = sheets.build_rows(report())
        self.assertEqual(len(rows), 4 + 2)
        self.assertEqual([r.get("label") for r in rows[:4]], ["GET /api/billing/invoices/{invoice_id}", "/billing/invoices", "GET /api/cases", "/cases/archive"])
        self.assertEqual([r.get("kind") for r in rows], ["api_route", "screen", "api_route", "screen", "suggested case", "suggested case"])

    def test_a_row_has_the_key_the_revision_the_area_id_the_priority_and_empty_columns_for_a_person(self):
        row = sheets.build_rows(report())[1]
        self.assertEqual(row.get("report key"), "acme--acme-api--compare-main-topic--0123abcd")
        self.assertEqual((row.get("revision"), row.get("priority")), ("3", "must test"))
        self.assertRegex(row.get("area id"), r"^a-[0-9a-f]{16}$")
        self.assertEqual([row.get(c) for c in (RELEVANT, TESTED, NOTES, JUDGE)], ["", "", "", ""])
        self.assertEqual(len(row.cells), len(COLUMNS))

    def test_the_chain_is_the_first_and_the_last_hop_of_the_best_chain(self):
        data = report()
        area = data["areas"][1]
        chain = area["chains"][0]
        row = sheets.build_rows(data)[1]
        self.assertTrue(row.get(sheets.CHAIN).startswith(f"first: {chain[0]['path']}:{chain[0]['line']} "))
        self.assertIn(f" / last: {chain[-1]['path']}:{chain[-1]['line']} ", row.get(sheets.CHAIN))

    def test_a_candidate_found_by_name_has_its_route_line_and_every_priority_is_in_words(self):
        rows = sheets.build_rows(report())
        self.assertEqual([r.get("priority") for r in rows[:4]], ["must test", "must test", "sample", "candidate"])
        self.assertTrue(rows[3].get(sheets.CHAIN).startswith("first: "))

    def test_a_suggested_case_has_its_source_case_and_title_the_area_it_was_suggested_for_and_the_spec_line_or_the_matched_words(self):
        rows = sheets.build_rows(report())
        first, second = rows[4], rows[5]
        self.assertEqual(first.get("label"), "playwright e2e/billing.spec.ts:12: opens the invoice list")
        self.assertEqual((first.get("area id"), first.get("priority")), (rows[1].get("area id"), "must test"))
        self.assertIn("e2e/billing.spec.ts:12 await page.goto('/billing/invoices');", first.get(sheets.CHAIN))
        self.assertEqual((second.get("area id"), second.get("priority")), ("", ""))
        self.assertEqual(second.get(sheets.CHAIN), "matched words: invoice, total")

    def test_a_report_without_a_review_object_is_keyed_by_its_repository_and_change(self):
        data = report()
        data["review"] = None
        self.assertEqual(sheets.build_rows(data)[0].get("report key"), "acme/api compare:main...topic")
        self.assertEqual(sheets.build_rows(data)[0].get("revision"), "")

    def test_what_a_report_quotes_is_made_visible_and_one_line(self):
        rows = sheets.build_rows(report(hostile=True))
        for row in rows:
            for cell in row.cells:
                self.assertNotRegex(cell, "[\u202e\u2028\x1b\x00-\x1f\ud800]")
        self.assertTrue(any("\\u202e" in c for r in rows for c in r.cells))


class Formulas(unittest.TestCase):
    def test_a_cell_that_a_spreadsheet_would_run_gets_a_leading_quote(self):
        for cell in ("=1+1", "+1", "-1", "@SUM(A1)", "\tx", "\rx", "=cmd|' /C calc'!A0"):
            self.assertEqual(sheets.protect(cell), "'" + cell)
        for cell in ("", "x", "a=b", "1-2", " =x", "'x", "x'=", "\u2212x"):
            self.assertEqual(sheets.protect(cell), cell)

    def test_protect_and_unprotect_are_each_others_inverse(self):
        import itertools

        alphabet = ["'", "=", "+", "-", "@", "\t", "\r", "a", " ", ",", '"', "\n", "\u202e"]
        for n in range(0, 4):
            for combo in itertools.product(alphabet, repeat=n):
                cell = "".join(combo)
                self.assertEqual(sheets.unprotect(sheets.protect(cell)), cell, repr(cell))

    def test_a_protected_cell_never_starts_with_a_trigger(self):
        for cell in ("=1", "'=1", "''@x", "\t=", "-"):
            self.assertNotIn(sheets.protect(cell)[0], TRIGGERS)


class Csv(unittest.TestCase):
    def test_it_is_rfc_4180_with_crlf_a_header_and_quotes_where_needed(self):
        rows = [Row(("k", "1", "screen", "a-0", 'has, comma and "quotes"', "must test", "a\nb", "", "", "", ""))]
        text = sheets.render_csv(rows)
        self.assertTrue(text.startswith("\ufeff"))
        body = text.removeprefix("\ufeff")
        self.assertTrue(body.endswith("\r\n"))
        self.assertNotRegex(body.replace("\r\n", ""), "\r")
        lines = body.split("\r\n")
        self.assertEqual(lines[0], ",".join(COLUMNS).replace("relevant (yes / no / unsure)", "relevant (yes / no / unsure)"))
        self.assertIn('"has, comma and ""quotes"""', body)
        self.assertEqual(parsed(text)[1][4], 'has, comma and "quotes"')
        self.assertEqual(parsed(text)[1][6], "a\nb")

    def test_the_header_is_the_columns_of_the_brief(self):
        self.assertEqual(COLUMNS, ("report key", "revision", "kind", "area id", "label", "priority", "chain (first and last hop)",
                                   "relevant (yes / no / unsure)", "would have tested (yes / no)", "notes", "judge"))

    def test_no_cell_of_a_hostile_report_starts_with_a_character_a_spreadsheet_runs(self):
        data = report(hostile=True)
        for a in data["areas"]:
            a["label"] = "=HYPERLINK(\"http://x.invalid\")"
        data["suggestions"][0]["title"] = "-2+3"
        data["suggestions"][1]["title"] = "@SUM(1)"
        data["areas"][0]["route_line"]["detail"] = "+cmd"
        data["areas"][0]["chains"][0][0]["detail"] = "\t=1"
        text = sheets.render_csv(sheets.build_rows(data))
        for line in parsed(text)[1:]:
            for cell in line:
                self.assertFalse(cell and cell[0] in TRIGGERS, repr(cell))
        raw = text.removeprefix("\ufeff")
        self.assertIn("'=HYPERLINK", raw)

    def test_a_written_sheet_reads_back_row_for_row_with_every_awkward_cell(self):
        awkward = ["=1+1", "'=1+1", "''-x", "+", "@a", "\tx", "\rx", "'", "''", "a,b", 'say "hi"', "line1\nline2", "line1\r\nline2", "\u202eabc", "é\u2028", " lead", "trail ", ""]
        rows = [Row(tuple(awkward[(i + j) % len(awkward)] for j in range(len(COLUMNS)))) for i in range(len(awkward))]
        rows = [Row((f"k{i}", *r.cells[1:])) for i, r in enumerate(rows)]  # distinct rows
        self.assertEqual(sheets.read_csv(sheets.render_csv(rows)), rows)

    def test_a_report_to_a_sheet_to_rows_is_the_identity_hostile_report_included(self):
        for hostile in (False, True):
            rows = sheets.build_rows(report(hostile))
            self.assertEqual(sheets.read_csv(sheets.render_csv(rows)), rows)

    def test_a_sheet_a_spreadsheet_saved_without_a_byte_order_mark_or_with_lf_reads_too(self):
        rows = sheets.build_rows(report())
        text = sheets.render_csv(rows).removeprefix("\ufeff").replace("\r\n", "\n")
        self.assertEqual(sheets.read_csv(text), rows)


class Markdown(unittest.TestCase):
    def test_it_has_the_notice_the_table_and_the_same_rows(self):
        rows = sheets.build_rows(report())
        text = sheets.render_markdown(rows, "x")
        self.assertIn("A model must not fill this sheet.", text)
        table = [line for line in text.splitlines() if line.startswith("| ")]
        self.assertEqual(len(table), 2 + len(rows))  # the header, the rule under it and a line per row
        self.assertEqual(table[0], "| " + " | ".join(COLUMNS) + " |")
        self.assertEqual(table[1], "| " + " | ".join("---" for _ in COLUMNS) + " |")
        self.assertEqual(wording.check(text), [])
        self.assertNotRegex(text, "[\u2014\u2013\u2192\u201c\u201d\u2018\u2019]|->")

    def test_report_content_is_in_a_code_span_and_cannot_break_the_table_or_the_page(self):
        row = Row(("k|ey", "1", "screen", "a-0", "a | b <b>x</b> `c` ![i](http://x.invalid) [l](http://y.invalid)", "must test", "first: a|b", "", "", "", ""))
        text = sheets.render_markdown([row])
        line = [l for l in text.splitlines() if l.startswith("| `")][0]
        self.assertEqual(len(re.findall(r"(?<!\\)\|", line)), len(COLUMNS) + 1)
        outside = re.sub(r"(`+).+?\1", "", line)  # what is left once the code spans are taken out
        self.assertNotRegex(outside, "[<>]|\\]\\(|!\\[")


def filled(answers, rows=None):
    """A sheet text with some rows answered: {row index: {column: value}}."""
    rows = rows or sheets.build_rows(report())
    out = []
    for i, row in enumerate(rows):
        cells = list(row.cells)
        for column, value in answers.get(i, {}).items():
            cells[COLUMNS.index(column)] = value
        out.append(Row(tuple(cells)))
    return sheets.render_csv(out)


class Scoring(unittest.TestCase):
    ANSWERS = {
        0: {RELEVANT: "yes", TESTED: "yes", JUDGE: "Quinn"},
        1: {RELEVANT: "no", TESTED: "no", JUDGE: "Quinn", NOTES: "old screen"},
        2: {RELEVANT: "unsure", JUDGE: "Sam"},
        4: {RELEVANT: "yes", TESTED: "no", JUDGE: "Sam", NOTES: "= a note that starts with an equals sign"},
    }

    def lines(self, text):
        return "\n".join(sheets.score_text(text))

    def test_it_counts_the_answers_per_column_and_per_kind_and_priority(self):
        text = self.lines(filled(self.ANSWERS))
        self.assertIn("sheet: 6 rows, 4 with an answer", text)
        self.assertIn("judges: Quinn 2 rows, Sam 2 rows", text)
        self.assertIn("relevant (yes / no / unsure): yes 2, no 1, unsure 1, blank 2", text)
        self.assertIn("would have tested (yes / no): yes 1, no 2, blank 3", text)
        self.assertIn("rows with notes: 2", text)
        self.assertIn("screens and routes, must test: 2 rows; relevant: yes 1, no 1, unsure 0, blank 0", text)
        self.assertIn("suggested cases, must test: 1 rows; relevant: yes 1, no 0, unsure 0, blank 0", text)
        self.assertIn("suggested cases, none: 1 rows; relevant: yes 0, no 0, unsure 0, blank 1", text)

    def test_the_counts_are_counts_with_no_percentage_and_the_words_pass_the_wording_check(self):
        text = self.lines(filled(self.ANSWERS))
        self.assertNotIn("%", text)
        self.assertNotRegex(text, r"\d\.\d")
        self.assertEqual(wording.check(text), [])

    def test_a_sheet_nobody_filled_is_counted_as_blank_and_nothing_is_filled_in(self):
        text = self.lines(sheets.render_csv(sheets.build_rows(report())))
        self.assertIn("sheet: 6 rows, 0 with an answer", text)
        self.assertIn("judges: none yet", text)
        self.assertIn("unsure 0, blank 6", text)

    def test_answers_may_be_written_in_any_case_with_spaces(self):
        text = self.lines(filled({0: {RELEVANT: " Yes ", TESTED: "NO", JUDGE: "Quinn"}}))
        self.assertIn("yes 1, no 0, unsure 0, blank 5", text)

    def test_a_sheet_that_a_spreadsheet_saved_after_a_person_typed_a_quoted_formula_in_notes_is_counted(self):
        self.assertIn("rows with notes: 1", self.lines(filled({0: {NOTES: "'=SUM(A1)", RELEVANT: "yes", JUDGE: "Quinn"}})))

    def refused(self, text, *words):
        with self.assertRaises(SheetError) as ctx:
            sheets.score_text(text)
        for w in words:
            self.assertIn(w, str(ctx.exception))

    def test_a_sheet_without_the_judge_column_is_refused(self):
        rows = parsed(sheets.render_csv(sheets.build_rows(report())))
        without = "\r\n".join(",".join(c for c, h in zip(r, rows[0]) if h != JUDGE) for r in rows)
        self.refused(without, "no 'judge' column")

    def test_a_header_that_is_not_the_header_of_a_sheet_is_refused(self):
        rows = parsed(sheets.render_csv(sheets.build_rows(report())))
        rows[0][3] = "area"
        self.refused("\r\n".join(",".join(r) for r in rows), "not the header of a sheet", "missing ['area id']")
        rows = parsed(sheets.render_csv(sheets.build_rows(report())))
        extra = "\r\n".join(",".join([*r, "x"]) for r in rows)
        self.refused(extra, "unknown ['x']")
        swapped = parsed(sheets.render_csv(sheets.build_rows(report())))
        for r in swapped:
            r[0], r[1] = r[1], r[0]
        self.refused("\r\n".join(",".join(r) for r in swapped), "another order")

    def test_an_unknown_value_is_refused_for_each_answer_column_and_named_with_its_row(self):
        for column, bad in ((RELEVANT, "maybe"), (RELEVANT, "y"), (TESTED, "unsure"), (TESTED, "true")):
            with self.subTest(f"{column} {bad}"):
                self.refused(filled({2: {column: bad, JUDGE: "Quinn"}}), "row 3", repr(bad), column)

    def test_an_answer_with_no_judge_is_refused(self):
        self.refused(filled({1: {RELEVANT: "yes"}}), "row 2", "no name in 'judge'")
        self.refused(filled({1: {TESTED: "no", JUDGE: "  "}}), "row 2")

    def test_a_judge_alone_without_an_answer_is_not_an_answer(self):
        self.assertIn("judges: Quinn 1 rows", self.lines(filled({0: {JUDGE: "Quinn"}})))

    def test_a_repeated_row_a_short_row_and_a_file_without_rows_are_refused(self):
        rows = sheets.build_rows(report())
        self.refused(sheets.render_csv([rows[0], rows[0]]), "row 2 repeats row 1")
        text = sheets.render_csv(rows)
        self.refused(text.split("\r\n")[0] + "\r\nonly,two\r\n", "line 2 has 2 cells and a row of a sheet has 11")
        self.refused(text.split("\r\n")[0] + "\r\n", "no rows")
        self.refused("", "empty")
        self.refused('a,"b\r\n', "not CSV")

    def test_the_command_refuses_in_exit_2_naming_the_file_only(self):
        d = Path(tempfile.mkdtemp(prefix="reachmap-sheet-"))
        self.addCleanup(sg.remove_tree, d)
        bad = d / "filled.csv"
        bad.write_text(filled({1: {RELEVANT: "yes"}}), encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        self.assertEqual(cli.main(["sheet", "--score", str(bad)], stdout=out, stderr=err), 2)
        self.assertEqual(out.getvalue(), "")
        self.assertTrue(err.getvalue().startswith("reachmap: error: filled.csv: row 2 has an answer and no name"), err.getvalue())
        self.assertNotIn(str(d), err.getvalue())


class Command(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="reachmap-sheetcmd-"))
        self.addCleanup(sg.remove_tree, self.dir)
        import json

        self.report = self.dir / "r.json"
        self.report.write_text(json.dumps(report()), encoding="utf-8")

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(["sheet", *args], stdout=out, stderr=err)
        return code, out.getvalue(), err.getvalue()

    def test_a_csv_sheet_is_written_to_the_file_and_nothing_is_printed(self):
        target = self.dir / "sheet.csv"
        code, out, err = self.run_cli(str(self.report), "--out", str(target))
        self.assertEqual((code, out, err), (0, "", ""))
        raw = target.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        self.assertIn(b"\r\n", raw)
        self.assertEqual(len(sheets.read_csv(raw.decode("utf-8"))), 6)

    def test_a_file_name_ending_in_md_or_the_format_option_makes_markdown_and_without_out_it_is_printed(self):
        target = self.dir / "sheet.md"
        self.assertEqual(self.run_cli(str(self.report), "--out", str(target))[0], 0)
        self.assertIn("A model must not fill this sheet.", target.read_text(encoding="utf-8"))
        code, out, _ = self.run_cli(str(self.report), "--format", "md")
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("# Judging sheet"))
        code, out, _ = self.run_cli(str(self.report))
        self.assertTrue(out.startswith("\ufeffreport key,revision,kind,area id,label,priority"))

    def test_the_sheet_written_by_the_command_scores_as_empty_and_a_filled_copy_is_counted(self):
        target = self.dir / "sheet.csv"
        self.run_cli(str(self.report), "--out", str(target))
        code, out, _ = self.run_cli("--score", str(target))
        self.assertEqual(code, 0)
        self.assertIn("sheet: 6 rows, 0 with an answer", out)
        target.write_text(filled(Scoring.ANSWERS), encoding="utf-8")
        code, out, _ = self.run_cli("--score", str(target))
        self.assertIn("relevant (yes / no / unsure): yes 2, no 1, unsure 1, blank 2", out)

    def test_every_misuse_is_exit_2_with_a_message_and_nothing_written(self):
        for args, words in (
            ((), "give a REPORT.json"),
            ((str(self.report), "--score", str(self.report)), "takes no report"),
            ((str(self.dir / "missing.json"),), "not a report reachmap can read"),
            ((str(self.report), "--format", "xls"), "not a sheet format"),
            (("--score", str(self.dir / "none.csv")), "cannot be read"),
            ((str(self.report), "--out", str(self.dir / "nodir" / "s.csv")), "--out: cannot write"),
        ):
            with self.subTest(args):
                code, out, err = self.run_cli(*args)
                self.assertEqual((code, out), (2, ""))
                self.assertIn(words, err)
                self.assertNotIn("Traceback", err)

    def test_a_run_that_did_not_complete_and_a_report_with_nothing_to_judge_have_no_sheet(self):
        import json

        failed = self.dir / "failed.json"
        failed.write_text(json.dumps(sr.fixture("failed")), encoding="utf-8")
        self.assertIn("did not complete", self.run_cli(str(failed))[2])
        empty = report()
        empty["areas"], empty["suggestions"], empty["findings"] = [], [], []
        path = self.dir / "empty.json"
        path.write_text(json.dumps(empty), encoding="utf-8")
        self.assertIn("lists no screen, route or suggested case", self.run_cli(str(path))[2])


class Malformed(unittest.TestCase):
    """What is wrong with a report is said as what it is: a missing field is named, a wrong shape is called a wrong shape."""

    def test_an_area_without_an_id_says_so_and_names_the_way_out(self):
        data = report()
        del data["areas"][1]["id"]
        with self.assertRaises(SheetError) as ctx:
            sheets.build_rows(data)
        self.assertIn("areas[1] has no 'id'", str(ctx.exception))
        self.assertIn("rerun reachmap analyse", str(ctx.exception))

    def test_any_other_missing_field_is_named_without_the_advice_about_ids(self):
        data = report()
        del data["areas"][2]["label"]
        with self.assertRaises(SheetError) as ctx:
            sheets.build_rows(data)
        self.assertIn("areas[2] has no 'label'", str(ctx.exception))
        self.assertNotIn("rerun", str(ctx.exception))
        data = report()
        del data["suggestions"][0]["title"]
        with self.assertRaises(SheetError) as ctx:
            sheets.build_rows(data)
        self.assertIn("suggestions[0] has no 'title'", str(ctx.exception))

    def test_a_part_of_the_wrong_shape_is_called_that(self):
        data = report()
        data["areas"][0]["chains"] = "not a list of chains"
        with self.assertRaises(SheetError) as ctx:
            sheets.build_rows(data)
        self.assertIn("areas[0] is not an area as a report holds one", str(ctx.exception))
        self.assertNotIn("no ids", str(ctx.exception))
        data = report()
        data["suggestions"][1] = "text"
        with self.assertRaises(SheetError) as ctx:
            sheets.build_rows(data)
        self.assertIn("suggestions[1] is not a suggestion", str(ctx.exception))


class Terminal(unittest.TestCase):
    """What the commands print quotes names and lines from anywhere: standard output is UTF-8 bytes whatever the terminal says."""

    def test_a_judge_name_with_an_accent_prints_as_utf_8_even_when_the_terminal_is_ascii(self):
        import os
        import subprocess
        import sys

        d = Path(tempfile.mkdtemp(prefix="reachmap-term-"))
        self.addCleanup(sg.remove_tree, d)
        path = d / "filled.csv"
        path.write_text(filled({0: {RELEVANT: "yes", JUDGE: "Zo\u00eb"}}), encoding="utf-8")
        root = Path(sheets.__file__).resolve().parents[2]
        env = {**os.environ, "PYTHONIOENCODING": "ascii", "PYTHONPATH": str(root)}
        done = subprocess.run([sys.executable, "-m", "reachmap", "sheet", "--score", str(path)], capture_output=True, env=env, cwd=d)
        self.assertEqual((done.returncode, done.stderr), (0, b""))
        self.assertIn("Zo\u00eb 1 rows".encode("utf-8"), done.stdout)

    def test_emit_writes_bytes_replaces_what_cannot_be_encoded_and_gives_a_plain_stream_the_text(self):
        from reachmap.evaluate import emit

        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="ascii")
        emit(stream, "a" + chr(0xD800) + "b caf" + chr(0xE9))
        self.assertEqual(raw.getvalue(), ("a?b caf" + chr(0xE9) + chr(10)).encode("utf-8"))
        plain = io.StringIO()
        emit(plain, "text")
        self.assertEqual(plain.getvalue(), "text\n")


class ForPeople(unittest.TestCase):
    def test_the_mcp_server_has_no_tool_that_writes_audits_judges_or_scores(self):
        from reachmap.mcp import tools

        for name in tools.TOOL_NAMES:
            self.assertNotRegex(name, r"sheet|score|judge|fill|audit|witness|evaluat")

    def test_the_evaluation_code_calls_no_model_and_opens_no_connection(self):
        root = Path(sheets.__file__).parent
        for path in sorted(root.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            self.assertNotRegex(source, r"(?m)^\s*(import|from)\s+(urllib|http|socket|ssl|requests|anthropic|openai|ftplib|smtplib|xmlrpc)\b", path.name)

    def test_the_docs_say_that_sheets_are_for_people_and_a_model_must_not_fill_them(self):
        text = (Path(sheets.__file__).resolve().parents[2] / "docs" / "evaluation.md").read_text(encoding="utf-8")
        self.assertIn("A model must not fill", text)
        self.assertIn("no tool", text)


if __name__ == "__main__":
    unittest.main()
