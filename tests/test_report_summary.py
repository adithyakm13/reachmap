"""The CLI summary (M10: moved to `report/summary.py`, driven by the report JSON, the same words as M9)."""

import json
import unittest

from reachmap import cli, wording
from reachmap.report import summary
from tests import support_cli
from tests import support_report as sr
from tests.test_cli import S11, S13


class FromTheReport(unittest.TestCase):
    def test_the_lines_are_what_the_cli_printed_for_the_same_run(self):
        for args in (S13, S11, ("--repo", "acme/web", "--compare", "main...scenario/s16-no-merge-base"),
                     ("--repo", "acme/web", "--compare", "main...scenario/s02-hub-cutoff"), ("--repo", "acme/api", "--compare", "main...scenario/s15-python-callers")):
            run = support_cli.cli_run(*args)
            with self.subTest(args=args):
                self.assertEqual("\n".join(summary.lines(run.report, str(run.path))) + "\n", run.out)

    def test_it_takes_the_json_report_and_nothing_else(self):
        run = support_cli.cli_run(*S13)
        before = json.dumps(run.report, sort_keys=True)
        summary.lines(run.report)
        self.assertEqual(json.dumps(run.report, sort_keys=True), before)
        with self.assertRaises(Exception):
            summary.lines(object())

    def test_a_report_with_no_path_has_no_report_line(self):
        lines = summary.lines(sr.fixture("complete"))
        self.assertFalse(any(ln.startswith("Report:") for ln in lines))

    def test_the_files_written_beside_the_report_are_named_after_it(self):
        lines = summary.lines(sr.fixture("complete"), "out/x.json", {"md": "out/x.md", "html": "out/x.html"})
        self.assertEqual(lines[-3:], ["Report: out/x.json", "Markdown: out/x.md", "HTML: out/x.html"])

    def test_a_failed_or_timed_out_report_is_said_without_a_count(self):
        failed = summary.lines(sr.fixture("failed"), "x.json")
        self.assertTrue(failed[0].startswith("reachmap: the change could not be analysed: "))
        self.assertEqual(failed[-1], "Report: x.json")
        timed = summary.lines(sr.fixture("timed_out"), None)
        self.assertTrue(timed[0].startswith("reachmap: the run hit its time limit: "))
        self.assertIn("No result is reported.", timed[0])
        for lines in (failed, timed):
            self.assertNotRegex("\n".join(lines), r"Areas to test|Findings:")

    def test_the_compat_function_of_the_cli_gives_the_same_lines_as_the_report_form(self):
        run = support_cli.cli_run(*S13)
        import reachmap.model as m
        analysis = m.Analysis.from_dict({k: run.report[k] for k in support_cli.ANALYSIS_KEYS})
        self.assertEqual(cli.summary_lines(analysis, str(run.path), False), summary.lines(run.report, str(run.path)))


class WhatIsPrinted(unittest.TestCase):
    def test_terminal_control_characters_in_content_are_shown_and_never_sent(self):
        data = sr.fixture("hostile")
        text = "\n".join(summary.lines(data, "x.json"))
        self.assertNotRegex(text, "[\u0000-\u0009\u000b-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069\u200b-\u200f\u2028\u2029\ud800-\udfff]")
        self.assertIn("\\x1b", text)
        self.assertIn("\\u202e", text)

    def test_a_banned_phrase_in_content_is_defused_as_before(self):
        data = sr.fixture("complete")
        data["areas"][0]["label"] = "GET /safe-mode"
        data["incompleteness"][0]["not_established"] = "Whether it is safe was not established"
        text = "\n".join(summary.lines(data, None))
        self.assertEqual(wording.check(text), [])
        self.assertIn("-mode", text)

    def test_the_summary_of_a_real_hostile_run_is_one_line_per_thing_and_inert(self):
        lines = summary.lines(sr.hostile_run(), "x.json")
        self.assertTrue(all("\n" not in ln for ln in lines))
        self.assertEqual(wording.check("\n".join(lines)), [])


class SameWayEverywhere(unittest.TestCase):
    ODD = "x\u001b[31m\u202ey\u200bz\u2028\ud800\x00\x7f"
    SHOWN = "x\\x1b[31m\\u202ey\\u200bz\\u2028\\ud800\\x00\\x7f"

    def test_the_summary_shows_odd_characters_as_markdown_and_the_page_do(self):
        import re
        from reachmap import report
        from reachmap.report import text
        data = sr.fixture("complete")
        data["areas"][0]["label"] = "GET /" + self.ODD
        data["incompleteness"][0]["not_established"] = "Not read " + self.ODD
        data["findings"][1]["kind"] = "kind" + self.ODD
        shown = text.visible(self.ODD)
        self.assertEqual(shown, self.SHOWN)
        summary_text = "\n".join(summary.lines(data, None))
        md = report.render(data, "md")
        page = report.render(data, "html")
        unescaped = re.sub(r"\\(.)", r"\1", md)  # Markdown's own backslashes in plain text (an escaped backslash, bracket) read back
        for out, where in ((summary_text, "summary"), (md, "markdown"), (page, "page")):
            self.assertIn("GET /" + shown, out, where)
        self.assertIn("Not read " + shown, summary_text)
        self.assertIn("Not read " + shown, unescaped)
        self.assertIn("Not read " + shown, page)
        self.assertIn("kind" + shown, summary_text)
        self.assertIn("kind" + shown, unescaped)
        for out in (summary_text, md, page):
            self.assertNotRegex(out, "[\x00-\x08\x0b-\x1f\x7f\u200b\u2028\u202e\ud800-\udfff]")


if __name__ == "__main__":
    unittest.main()
