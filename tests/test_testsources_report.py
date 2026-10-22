"""M15 rendering: the suggestions of a report under "Suggested, not verified coverage", per area, apart from mapped tests, with
what each source answered; and what a source that failed does and does not say about the rest of the report."""

from __future__ import annotations

import re
import unittest

from reachmap import report, wording
from reachmap.identity import area_id
from reachmap.report import layout, model, summary
from tests import support_report as sr

REC_UNAVAILABLE = {"kind": "source_unavailable", "subject": "qase:ACME", "blocks_selection": False, "scope": "suggestions", "blocks_suggestions": True,
                   "not_established": "Suggestions from qase:ACME were not looked up, because the source did not answer", "detail": "the host failed (HTTP 500)"}
REC_CUT = {"kind": "suggestions_incomplete", "subject": "qase:ACME", "blocks_selection": False, "scope": "suggestions", "blocks_suggestions": True,
           "not_established": "All suggestions from qase:ACME were not established, because the list of cases was cut short", "detail": "cases: read 15 of 30 (max_pages 1)"}
REC_UNCONFIGURED = {"kind": "source_unconfigured", "subject": "testrail:3", "blocks_selection": False, "scope": "suggestions", "blocks_suggestions": False,
                    "not_established": "Suggestions from testrail:3 were not looked up, because the source is not configured", "detail": "the variable TESTRAIL_API_KEY is unset or empty"}


def base(*records):
    """The committed `complete` report with ids on its areas, and no blocking record of its own, then `records`."""
    data = sr.fixture("complete")
    for a in data["areas"]:
        a["id"] = area_id(a["kind"], a["repo"], a["label"])
    data["incompleteness"] = [r for r in data["incompleteness"] if not r["blocks_selection"]] + [dict(r) for r in records]
    data["suggestions"] = []
    return data


def suggestion(area, case="ACME-1", title="Invoice list shows billing totals", source="qase:ACME", words=("billing", "invoice"), evidence=()):
    return {"source": source, "case_id": case, "title": title, "matched_words": list(words), "area_id": area["id"] if area else None,
            "evidence": list(evidence), "label": "suggested, not verified coverage"}


def md(data):
    return report.render(data, "md")


def section(text):
    return text.split("## Suggested, not verified coverage")[1].split("\n## ")[0]


class PerArea(unittest.TestCase):
    def setUp(self):
        self.data = base()
        self.a, self.b = self.data["areas"][0], self.data["areas"][1]
        spec = {"repo": "acme/web", "path": "e2e/a.spec.ts", "line": 7, "ref": "a" * 40, "detail": '  await page.goto("/cases");'}
        self.data["suggestions"] = [suggestion(self.b, "ACME-2", "Second area case"), suggestion(self.a, "ACME-1", "First area case"),
                                    suggestion(self.a, "e2e/a.spec.ts:7", "a spec title", "playwright:acme/web", (), [spec]), suggestion(None, "ACME-9", "Tied to no area")]

    def test_a_heading_per_area_with_its_cases_under_it_and_the_rest_apart(self):
        text = section(md(self.data))
        first, second = f"### `{self.a['label']}`", f"### `{self.b['label']}`"
        self.assertIn(first, text)
        self.assertIn(second, text)
        a_part = text.split(first)[1].split("\n### ")[0]
        b_part = text.split(second)[1].split("\n### ")[0]
        self.assertIn("First area case", a_part)
        self.assertIn("a spec title", a_part)
        self.assertNotIn("Second area case", a_part)
        self.assertIn("Second area case", b_part)
        other = text.split("### Not tied to an area of this report")[1]
        self.assertIn("Tied to no area", other)
        self.assertNotIn("First area case", other)

    def test_the_matched_words_and_the_spec_line_are_shown(self):
        text = section(md(self.data))
        self.assertIn("**Matched words:** `billing`, `invoice`", text)
        self.assertIn("**Spec line:**", text)
        self.assertIn('await page.goto("/cases");', text)
        self.assertIn("e2e/a.spec.ts:7", text)

    def test_the_section_says_what_a_suggestion_is_and_the_mapped_tests_are_elsewhere(self):
        text = md(self.data)
        self.assertIn("They are not coverage", section(text))
        self.assertNotIn("Mapped tests", section(text))

    def test_every_string_the_tool_says_passes_the_wording_check(self):
        for records in ((), (REC_UNAVAILABLE,), (REC_CUT,), (REC_UNCONFIGURED,), (REC_UNAVAILABLE, REC_CUT, REC_UNCONFIGURED)):
            data = base(*records)
            data["suggestions"] = list(self.data["suggestions"]) if not records else []
            layout.build(data)  # raises on a banned phrase

    def test_the_html_has_the_section_and_the_area_headings(self):
        html = report.render(self.data, "html")
        self.assertIn("Suggested, not verified coverage", html)
        self.assertIn("First area case", html)
        self.assertIn("Not tied to an area of this report", html)

    def test_a_hostile_title_and_evidence_render_as_text(self):
        data = base()
        a = data["areas"][0]
        spec = {"repo": "acme/web", "path": "e2e/a.spec.ts", "line": 7, "ref": "a" * 40, "detail": "</script><svg onload=alert(1)>"}
        data["suggestions"] = [suggestion(a, title="</script><script>alert(1)</script> <img src=x onerror=alert(1)>", evidence=[spec])]
        html = report.render(data, "html")
        self.assertNotIn("<svg onload", html)
        self.assertNotIn("<img src=x", html)
        self.assertEqual(html.count("<script"), html.count("</script>"))


class States(unittest.TestCase):
    def view(self, data):
        return model.build(data).suggested

    def test_listed_carries_the_records_of_a_source_that_was_not_read_completely(self):
        data = base(REC_UNAVAILABLE, REC_UNCONFIGURED)
        data["suggestions"] = [suggestion(data["areas"][0])]
        v = self.view(data)
        self.assertEqual(v.state, "listed")
        self.assertEqual([str(r.kind) for r in v.records], ["source_unavailable", "source_unconfigured"])
        text = section(md(data))
        self.assertIn("A test source did not answer, so its suggestions are missing:", text)
        self.assertIn("A test source is not configured, so its suggestions were not looked up:", text)
        self.assertIn("(HTTP 500)", text)

    def test_listed_with_a_cut_says_the_list_is_incomplete(self):
        data = base(REC_CUT)
        data["suggestions"] = [suggestion(data["areas"][0])]
        text = section(md(data))
        self.assertIn("A test source was read in part, so this list is incomplete:", text)
        self.assertIn("read 15 of 30", text)
        self.assertNotIn("None suggested.", text)

    def test_no_suggestions_and_a_cut_is_not_none(self):
        data = base(REC_CUT)
        v = self.view(data)
        self.assertEqual(v.state, "incomplete")
        text = section(md(data))
        self.assertIn("incomplete", text)
        self.assertNotIn("None suggested.", text)

    def test_unavailable_wins_over_incomplete_and_unconfigured(self):
        v = self.view(base(REC_UNCONFIGURED, REC_CUT, REC_UNAVAILABLE))
        self.assertEqual(v.state, "unavailable")
        text = section(md(base(REC_UNCONFIGURED, REC_CUT, REC_UNAVAILABLE)))
        self.assertIn("did not answer", text)
        self.assertIn("read in part", text)
        self.assertIn("not configured", text)
        self.assertNotIn("None suggested.", text)

    def test_unconfigured_alone_and_none_suggested_and_not_read_are_as_before(self):
        self.assertEqual(self.view(base(REC_UNCONFIGURED)).state, "unconfigured")
        self.assertIn("No test source is configured.", section(md(base(REC_UNCONFIGURED))))
        data = base()
        data["inputs"]["test_source_read"] = True
        self.assertEqual(self.view(data).state, "read_none")
        self.assertIn("None suggested.", section(md(data)))
        self.assertEqual(self.view(base()).state, "not_read")

    def test_none_suggested_is_never_said_next_to_a_record_even_if_the_flag_says_read(self):
        data = base(REC_UNAVAILABLE)
        data["inputs"]["test_source_read"] = True
        self.assertNotIn("None suggested.", section(md(data)))


class WhatASourceDoesNotSay(unittest.TestCase):
    def test_a_source_record_does_not_put_the_records_before_the_areas(self):
        data = base(REC_UNAVAILABLE)
        order = [s.id for s in layout.build(data).sections()]
        self.assertLess(order.index("areas"), order.index("records"))

    def test_a_record_that_blocks_the_areas_still_does(self):
        data = base(REC_UNAVAILABLE, {"kind": "read_unreadable", "subject": "src/a.ts", "blocks_selection": True,
                                      "not_established": "Whether src/a.ts exists, and what it contains, was not established, because the read failed", "detail": "HTTP 503"})
        order = [s.id for s in layout.build(data).sections()]
        self.assertLess(order.index("records"), order.index("areas"))

    def test_the_sentence_about_a_blocking_source_record_names_what_it_does_not_touch(self):
        text = md(base(REC_UNAVAILABLE))
        self.assertIn("A blocking record of a test source means the list of suggested cases may be incomplete. The areas come from the code and are not affected.", text)
        self.assertNotIn("A blocking record means the lists in this report may be incomplete.", text)

    def test_with_a_record_that_blocks_the_areas_too_both_sentences_are_said(self):
        data = base(REC_UNAVAILABLE, {"kind": "read_unreadable", "subject": "src/a.ts", "blocks_selection": True, "not_established": "x was not established", "detail": ""})
        text = md(data)
        self.assertIn("A blocking record means the lists in this report may be incomplete.", text)
        self.assertIn("The areas come from the code and are not affected.", text)

    def test_the_summary_does_not_say_no_areas_were_established_for_a_source_that_failed(self):
        data = base(REC_UNAVAILABLE)
        data["areas"] = []
        data["status"] = "completed"
        lines = "\n".join(summary.lines(data))
        self.assertIn("Blocking: Suggestions from qase:ACME", lines)
        self.assertNotIn("none were established", lines)
        self.assertIn("No areas reached by this change were found", lines)

    def test_the_summary_counts_suggestions_as_not_coverage(self):
        data = base()
        data["suggestions"] = [suggestion(data["areas"][0]), suggestion(data["areas"][0], "ACME-2")]
        self.assertIn("Suggested cases (not coverage and not counted above): 2", summary.lines(data))


class McpSection(unittest.TestCase):
    def test_the_items_carry_the_area_and_the_spec_line_as_fenced_content(self):
        from reachmap.mcp import tools

        data = base()
        spec = {"repo": "acme/web", "path": "e2e/a.spec.ts", "line": 7, "ref": "a" * 40, "detail": "x"}
        data["suggestions"] = [suggestion(data["areas"][0], evidence=[spec]), suggestion(None, "ACME-9")]
        v = model.build(data)
        items = tools._suggestion_items(v, data)
        self.assertEqual(items[0]["area_id"], data["areas"][0]["id"])
        self.assertIsNone(items[1]["area_id"])
        self.assertEqual(items[0]["evidence"][0]["line"], 7)
        self.assertIn("e2e/a.spec.ts", str(items[0]["evidence"][0]["path"]))


if __name__ == "__main__":
    unittest.main()


REC_PARTIAL = [{"kind": "spec_url_partial", "subject": f"acme/web e2e/a.spec.ts:{n}", "blocks_selection": False, "scope": "suggestions", "blocks_suggestions": False,
                "not_established": f"Which screen e2e/a.spec.ts:{n} visits was not established, because its address is not a literal",
                "detail": "the address is a variable: await page.goto(url);"} for n in (4, 9)]
REC_UNMATCHED = {"kind": "spec_visit_unmatched", "subject": "acme/web playwright specs", "blocks_selection": False, "scope": "suggestions", "blocks_suggestions": False,
                 "not_established": "Which screen the visits of acme/web playwright specs reach was not established, because their addresses match no screen route",
                 "detail": "3 visits match no screen route: e2e/a.spec.ts:5"}


class WhatWasNotRead(unittest.TestCase):
    def read(self, *records, states=None):
        data = base(*records)
        data["inputs"]["test_source_read"] = True
        if states:
            data["inputs"]["test_sources"] = [{"source": n, "kind": "x", "state": s, "read": s == "read"} for n, s in states]
            data["inputs"]["test_source_read"] = all(s == "read" for _, s in states)
        return data

    def test_none_suggested_next_to_addresses_that_were_not_literal_says_what_could_not_be_read(self):
        text = section(md(self.read(*REC_PARTIAL)))
        self.assertIn("None suggested from what could be read; 2 visits have an address that is not a literal", text)
        self.assertNotIn("None suggested.", text)
        self.assertIn("e2e/a.spec.ts:4", text)
        self.assertIn("e2e/a.spec.ts:9", text)
        self.assertIn("the address is a variable", text)

    def test_visits_that_match_no_screen_route_are_said_too(self):
        text = section(md(self.read(REC_UNMATCHED)))
        self.assertIn("None suggested from what could be read", text)
        self.assertIn("match no screen route", text)
        self.assertIn("3 visits match no screen route: e2e/a.spec.ts:5", text)

    def test_the_state_is_still_read_none_and_the_flag_still_true(self):
        v = model.build(self.read(*REC_PARTIAL)).suggested
        self.assertEqual(v.state, "read_none")
        self.assertEqual(len(v.partial), 2)

    def test_with_nothing_unread_it_is_still_plain_none_suggested(self):
        self.assertIn("None suggested.", section(md(self.read())))

    def test_listed_suggestions_are_followed_by_what_was_not_read(self):
        data = self.read(*REC_PARTIAL)
        data["suggestions"] = [suggestion(data["areas"][0])]
        text = section(md(data))
        self.assertIn("2 visits have an address that is not a literal", text)
        self.assertNotIn("None suggested", text)

    def test_some_sources_not_configured_and_the_others_read_says_which(self):
        data = self.read(REC_UNCONFIGURED, states=[("qase:ACME", "read"), ("testrail:3", "unconfigured")])
        text = section(md(data))
        self.assertIn("Some test sources are not configured", text)
        self.assertIn("testrail:3", text)
        self.assertNotIn("No test source is configured.", text)
        self.assertNotIn("None suggested.", text)

    def test_every_source_not_configured_is_still_no_test_source_is_configured(self):
        data = self.read(REC_UNCONFIGURED, states=[("testrail:3", "unconfigured")])
        self.assertIn("No test source is configured.", section(md(data)))

    def test_a_listed_state_with_one_source_not_configured_names_it(self):
        data = self.read(REC_UNCONFIGURED, states=[("qase:ACME", "read"), ("testrail:3", "unconfigured")])
        data["suggestions"] = [suggestion(data["areas"][0])]
        text = section(md(data))
        self.assertIn("A test source is not configured, so its suggestions were not looked up:", text)
        self.assertIn("testrail:3", text)
