"""Every fixture scenario's real report (the JSON `reachmap analyse` wrote) through the renderers: it passes the checks, renders to
Markdown, HTML and the author view without a banned phrase in what the tool says, and the HTML embeds exactly that report."""

import json
import re
import unittest

from reachmap import report, wording
from reachmap.report import blocks, layout, summary
from tests import scenario_support as ss
from tests import support_cli
from tests.fixtures import build
from tests.test_report_html import Parsed


class EveryScenario(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        e = ss.env()
        cls.reports = {sc["id"].split("-")[0]: json.loads(json.dumps(support_cli.scenario_run(sc).report)) for sc in build.scenarios(e.dest)}

    def test_all_23_are_here(self):
        self.assertEqual(sorted(self.reports), [f"s{n:02d}" for n in range(1, 24)])

    def test_each_report_passes_the_check_and_renders_in_every_format_and_view(self):
        for key, data in self.reports.items():
            with self.subTest(key):
                report.check(data)
                for view in ("full", "author"):
                    md = report.render(data, "md", view=view)
                    page = report.render(data, "html", view=view)
                    self.assertTrue(md.startswith("# Reachmap report: "))
                    self.assertEqual(json.loads(Parsed(page).json_scripts[0]["text"]), data)

    def test_what_the_tool_says_in_every_report_passes_the_wording_check(self):
        for key, data in self.reports.items():
            with self.subTest(key):
                for view in ("full", "author"):
                    for s, content in blocks.strings(layout.build(data, view=view)):
                        if not content:
                            self.assertEqual(wording.check(s), [], s)

    def test_a_whole_run_never_says_it_is_complete_in_any_rendering(self):
        for key, data in self.reports.items():
            props = data["propagations"]
            if props[0]["complete"] is True:
                with self.subTest(key):
                    text = re.sub(r"(?s)```.*?```", "", report.render(data, "md"))
                    text = re.sub(r"`[^`\n]*`", "", text)
                    self.assertNotIn("complete", text.lower())

    def test_the_summary_of_every_report_is_what_the_cli_printed(self):
        for key, data in self.reports.items():
            run = [r for r in support_cli._RESULTS.values() if r.text is not None and json.loads(r.text) == data]
            if run:
                self.assertEqual("\n".join(summary.lines(data, str(run[0].path))) + "\n", run[0].out, key)

    def test_the_area_and_finding_counts_on_the_page_are_the_reports(self):
        for key, data in self.reports.items():
            md = report.render(data, "md")
            with self.subTest(key):
                self.assertEqual(len(re.findall(r"^- \*\*F\d+\*\*", md, re.M)), len(data["findings"]))
                shown = len(re.findall(r"^- `[^`]+`$", md.split("## Areas to test")[1].split("\n## ")[0], re.M))
                self.assertEqual(shown, len(data["areas"]))


if __name__ == "__main__":
    unittest.main()
