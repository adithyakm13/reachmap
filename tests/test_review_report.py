"""Decisions in the renderers (M11): a Decisions section and a line per area and finding, in Markdown and in the page, made from the
report JSON alone. A reason is content (escaped, never markup, never checked as the tool's own wording), and a decision never moves an
area, changes a count, a priority, a risk or a confidence (rule 4.4)."""

import copy
import json
import re
import unittest
from unittest import mock

from reachmap import report, wording
from reachmap.model import verdict_text
from reachmap.report import blocks, layout, model
from tests import support_report as sr
from tests import support_review as rv
from tests.test_report_html import Parsed, TAGS
from tests.test_report_markdown import BAD_CONTROL, scan

HOSTILE_REASON = " ".join(h for h in sr.HOSTILE_ALL if h != "\ud800") + " it is safe, covered by tests"
HOSTILE_AUTHOR = "</b>Quinn\u202e <img src=x onerror=alert(1)>"


def md(data, view="full"):
    return report.render(data, "md", view=view)


def html(data, view="full"):
    return report.render(data, "html", view=view)


class Shown(unittest.TestCase):
    def setUp(self):
        self.data = rv.decided()
        self.md = md(self.data)

    def test_the_section_is_after_the_findings_and_names_current_stale_orphaned_and_undecided(self):
        text = self.md
        self.assertIn("\n## Decisions\n", text)
        self.assertLess(text.index("## Findings"), text.index("## Decisions"))
        for heading in ("### Current decisions", "### Decisions that need review", "### Decisions about something no longer in this report", "### Not decided"):
            self.assertIn(heading, text)

    def test_a_skip_reads_as_risk_accepted_not_tested_with_the_reason_quoted_and_attributed(self):
        self.assertIn('risk accepted, not tested (reason from Quinn: "retired in this release")', self.md)
        self.assertIn(verdict_text("skip"), self.md)

    def test_each_area_and_finding_says_what_became_of_its_decision(self):
        area_block = self.md.split("## Areas to test")[1].split("## Findings")[0]
        self.assertEqual(area_block.count("**Decision:**"), 4)  # every area says something once any decision exists
        self.assertIn("**Decision:** risk accepted, not tested", area_block)
        self.assertIn("decision needs review", area_block.split("**Decision:**")[2] + area_block.split("**Decision:**")[3] + area_block.split("**Decision:**")[4])
        self.assertEqual(area_block.count("No decision recorded."), 2)
        finding_block = self.md.split("## Findings")[1].split("## Decisions")[0]
        self.assertEqual(finding_block.count("**Decision:**"), 4)
        self.assertIn("**Decision:** finding accepted as stated", finding_block)
        self.assertEqual(finding_block.count("No decision recorded."), 3)

    def test_a_stale_decision_is_not_applied_and_says_what_changed(self):
        stale = self.md.split("### Decisions that need review")[1].split("###")[0]
        self.assertIn("not applied", stale)
        self.assertIn("revision 1", stale)
        self.assertIn('import { Badge } from "@components/Badge";', stale)
        self.assertIn('import { Badge } from "../Badge";', stale)
        self.assertIn("Removed", stale)
        self.assertIn("Added", stale)

    def test_a_stale_decision_is_worded_against_the_revision_of_the_report_it_is_shown_in(self):
        same = self.md.split("## Areas to test")[1].split("## Findings")[0]
        self.assertIn("the claim differs from the claim Quinn decided on at revision 1", same)
        d = copy.deepcopy(self.data)
        d["review"]["revision"] = 3
        text = md(d)
        self.assertIn("the claim on revision 3 differs from the claim Quinn decided on at revision 1", text)
        self.assertEqual(wording.check(text.replace("it is safe", "")), [])

    def test_a_stale_decision_without_the_earlier_report_says_that_it_cannot_say_what_changed(self):
        d = copy.deepcopy(self.data)
        d["review"]["decisions"]["stale"][0]["changes"] = None
        self.assertIn("what changed is not known", md(d))

    def test_an_orphaned_decision_is_listed_in_its_own_place_with_what_it_said(self):
        orphan = self.md.split("### Decisions about something no longer in this report")[1].split("###")[0]
        self.assertIn("a-" + "7" * 16, orphan)
        self.assertIn("/cases/old", orphan)
        self.assertIn("risk accepted, not tested", orphan)
        self.assertIn("retired in this release", orphan)
        self.assertIn("Quinn", orphan)

    def test_the_undecided_are_listed_by_label_and_title(self):
        undecided = self.md.split("### Not decided")[1]
        for a in self.data["areas"][2:]:
            self.assertIn(a["label"], undecided)
        self.assertIn("F2", undecided)

    def test_a_report_with_no_decision_has_no_section_and_no_line_so_it_reads_as_it_did_before(self):
        plain = rv.without_decisions(self.data)
        for variant in (plain, {k: v for k, v in plain.items() if k != "decisions"}):
            text = md(variant)
            self.assertNotIn("Decisions", text)
            self.assertNotIn("**Decision:**", text)
            self.assertNotIn("No decision recorded", text)

    def test_an_unreadable_store_is_a_blocking_record_in_the_section_and_no_target_claims_to_be_undecided(self):
        text = md(rv.unreadable())
        self.assertIn("## Decisions", text)
        self.assertIn("decision_store_unreadable", text)
        self.assertIn("which decisions were recorded for this change", text)
        self.assertNotIn("No decision recorded", text)
        self.assertNotIn("### Current decisions", text)

    def test_the_view_model_has_the_lists_and_the_states_and_the_ids_of_neither(self):
        v = model.build(self.data)
        self.assertEqual([x.state for x in v.decisions.current], ["current", "current"])
        self.assertEqual(len(v.decisions.stale), 1)
        self.assertEqual(len(v.decisions.orphaned), 1)
        self.assertEqual(len(v.decisions.undecided_areas), 2)
        self.assertEqual([n for n, _ in v.decisions.undecided_findings], [2, 3, 4])
        states = {str(a.label): a.decision.state for a in v.must_test}
        self.assertEqual(sorted(states.values()), ["current", "none", "none", "stale"])

    def test_the_words_for_a_verdict_are_the_models_not_a_copy(self):
        for verdict, target in (("test", "area"), ("sample", "area"), ("skip", "area"), ("accept", "finding"), ("reject", "finding")):
            d = copy.deepcopy(self.data)
            d["review"]["decisions"]["current"] = [rv.entry(d, target, 0, verdict, "r")]
            self.assertIn(verdict_text(verdict), md(d), verdict)


class NothingMoves(unittest.TestCase):
    """Rule 4.4 in the rendering: a decision is risk acceptance, never evidence."""

    def test_the_areas_stay_where_the_analysis_put_them_and_the_counts_are_the_reports(self):
        base = rv.without_decisions(rv.decided())
        decided = rv.decided()
        for view in ("full", "author"):
            a, b = md(base, view), md(decided, view)
            self.assertEqual(a.split("## Areas to test")[0].count("\n"), b.split("## Areas to test")[0].count("\n")) if view == "full" else None
        a, b = md(base), md(decided)
        counts = lambda t: re.search(r"\| What \| Number \|.*?\n\n", t, re.S).group(0)  # noqa: E731
        self.assertEqual(counts(a), counts(b))
        heads = lambda t: re.findall(r"^### (Must test|Sample|Candidates)$", t.split("## Areas to test")[1].split("## Findings")[0], re.M)  # noqa: E731
        self.assertEqual(heads(a), heads(b))
        self.assertEqual(heads(b), ["Must test"])
        labels = lambda t: re.findall(r"^- `(/[^`]*)`$", t.split("## Areas to test")[1].split("## Findings")[0], re.M)  # noqa: E731
        self.assertEqual(labels(a), labels(b))
        self.assertEqual(len(labels(b)), 4)  # the skipped one is still listed to test

    def test_every_finding_keeps_its_risk_and_confidence_and_evidence(self):
        a, b = md(rv.without_decisions(rv.decided())), md(rv.decided())
        for text in (a, b):
            self.assertEqual(text.count("**Risk:** medium"), 4)
            self.assertEqual(text.count("**Confidence:** high"), 4)
        strip = lambda t: re.sub(r"\n  - \*\*Decision:\*\*[^\n]*", "", t)  # noqa: E731
        self.assertEqual(strip(a.split("## Findings")[1].split("## Decisions")[0].split("## What the run")[0]), strip(b.split("## Findings")[1].split("## Decisions")[0].split("## What the run")[0]))

    def test_the_decision_is_never_listed_as_evidence_nor_a_finding_nor_a_record_of_what_was_not_established(self):
        text = md(rv.decided())
        self.assertNotIn("risk accepted", text.split("## Findings")[1].split("## Decisions")[0].split("**Evidence:**")[1].split("**Decision:**")[0])
        self.assertEqual(len(re.findall(r"^- \*\*F\d+\*\*", text.split("## Findings")[1].split("## Decisions")[0], re.M)), 4)

    def test_the_layout_never_reads_a_priority_to_change_it(self):
        v1, v2 = model.build(rv.without_decisions(rv.decided())), model.build(rv.decided())
        self.assertEqual([(str(a.label), a.priority) for a in v1.must_test], [(str(a.label), a.priority) for a in v2.must_test])
        self.assertEqual(v1.header.counts, v2.header.counts)
        self.assertEqual([(f.risk, f.confidence) for g in v1.groups for f in g.findings], [(f.risk, f.confidence) for g in v2.groups for f in g.findings])


class Wording(unittest.TestCase):
    def test_what_the_tool_says_about_decisions_passes_the_wording_check_whatever_the_reason_says(self):
        data = rv.decided("it is safe: covered by tests, no need to test", "nothing to test")
        for view in ("full", "author"):
            doc = layout.build(data, view=view)
            for s, content in blocks.strings(doc):
                if not content:
                    self.assertEqual(wording.check(s), [], s)
        self.assertIn("it is safe", md(data))  # the reason is content: quoted, attributed, never the tool's claim

    def test_no_string_from_a_decision_reaches_the_wording_check_as_the_tools_own(self):
        reached = []
        real = wording.require_clean

        def spy(text, field):
            if "ZQX" in text:
                reached.append(text[:100])
            return real(text, field)

        for data in (rv.decided(), rv.unreadable()):
            marked = sr.mark(data)
            for view in ("full", "author"):
                with mock.patch.object(wording, "require_clean", spy):
                    layout.build(marked, view=view)
        self.assertEqual(reached, [])

    def test_every_new_heading_and_sentence_is_in_the_documents_strings_and_clean(self):
        doc = layout.build(rv.decided())
        said = [s for s, content in blocks.strings(doc) if not content]
        self.assertTrue(any("risk" in s for s in said))
        for s in said:
            self.assertEqual(wording.check(s), [], s)


class Hostile(unittest.TestCase):
    def setUp(self):
        self.data = rv.decided(HOSTILE_REASON, HOSTILE_AUTHOR)
        self.page = html(self.data)
        self.parsed = Parsed(self.page)

    def test_a_hostile_reason_and_author_renders_in_both_formats_and_both_views(self):
        for view in ("full", "author"):
            md(self.data, view).encode("utf-8")
            html(self.data, view).encode("utf-8")

    def test_the_page_still_has_one_json_script_and_one_inline_script_and_nothing_from_content_is_an_element(self):
        self.assertEqual(len(self.parsed.json_scripts), 1)
        self.assertEqual(len(self.parsed.code_scripts), 1)
        self.assertEqual(len(self.parsed.scripts), 2)
        for tag, attrs in self.parsed.tags:
            if tag != "meta":
                self.assertIn(tag, TAGS)
        self.assertNotIn("<img src=x", re.sub(r"(?s)<script.*?</script>", "", self.page))
        for script in self.parsed.scripts:
            self.assertNotRegex(script["text"], r"(?i)</script")
            self.assertNotIn("<!--", script["text"])

    def test_the_embedded_json_is_the_report_with_the_reason_as_it_was(self):
        self.assertEqual(json.loads(self.parsed.json_scripts[0]["text"]), self.data)

    def test_no_element_or_attribute_comes_from_a_decision(self):
        stripped = re.sub(r"(?s)<script type=\"application/json\".*?</script>", "", self.page)
        self.assertNotIn("<img", stripped)
        self.assertNotIn("<svg", stripped)
        for tag, attrs in self.parsed.tags:
            for key, value in attrs:
                self.assertNotIn("onerror", (value or ""))
                self.assertNotIn("alert(1)", (value or ""))

    def test_the_markdown_has_the_reason_inside_a_code_span_or_escaped_and_no_markup_outside_code(self):
        text = md(self.data)
        pieces = list(scan(text))
        self.assertEqual([v for k, v in pieces if k == "stray"], [])
        for kind, value in pieces:
            if kind == "text":
                value = re.sub(r"\*\*[^*]+\*\*", "", value)  # the tool's own strong labels
                self.assertNotRegex(value, r"[<>\[\]*_~|]")
                self.assertNotRegex(value, r"&(?!amp;|lt;|gt;)")
        self.assertIsNone(BAD_CONTROL.search(text))
        self.assertIn("alert(1)", text)  # the reason is there, in a code span or escaped

    def test_odd_characters_are_written_out_like_everywhere_else(self):
        text = md(self.data)
        self.assertIn("\\u202e", text)
        self.assertIn("\\x1b[31m", text)

    def test_a_decision_of_every_hostile_kind_in_every_field_does_not_break_a_renderer(self):
        data = sr.hostile_copy(rv.decided())
        for view in ("full", "author"):
            md(data, view).encode("utf-8")
            html(data, view).encode("utf-8")
        data["review"]["decisions"]["current"][0]["verdict"] = "safe"
        self.assertIn("safe", md(data))  # an unknown verdict is content: shown as it is, in code, never as the tool's own word

    def test_the_real_hostile_run_given_hostile_decisions_stays_inert_in_both_formats(self):
        data = sr.hostile_run()
        self.assertTrue(data["areas"][0]["id"])
        data["review"]["decisions"] = {
            "current": [rv.entry(data, "area", 0, "skip", HOSTILE_REASON, HOSTILE_AUTHOR), rv.entry(data, "finding", 0, "reject", HOSTILE_REASON, HOSTILE_AUTHOR)],
            "stale": [], "orphaned": [rv.entry(data, "area", 0, "skip", HOSTILE_REASON, HOSTILE_AUTHOR, target_id="a-" + "5" * 16, label=HOSTILE_REASON)],
            "undecided": {"areas": [], "findings": []}, "records": [],
        }
        text, page = md(data), html(data)
        pieces = list(scan(text))
        self.assertEqual([v for k, v in pieces if k == "stray"], [])
        for kind, value in pieces:
            if kind == "text":
                value = re.sub(r"\*\*[^*]+\*\*", "", value)
                self.assertNotRegex(value, r"[<>\[\]*_~|]")
                self.assertNotRegex(value, r"&(?!amp;|lt;|gt;)")
        self.assertIsNone(BAD_CONTROL.search(text))
        parsed = Parsed(page)
        self.assertEqual((len(parsed.json_scripts), len(parsed.code_scripts), len(parsed.scripts)), (1, 1, 2))
        self.assertEqual(json.loads(parsed.json_scripts[0]["text"]), data)
        for tag, _ in parsed.tags:
            if tag != "meta":
                self.assertIn(tag, TAGS)
        for script in parsed.scripts:
            self.assertNotRegex(script["text"], r"(?i)</script")
            self.assertNotIn("<!--", script["text"])
        self.assertNotIn("<img src=x", re.sub(r"(?s)<script.*?</script>", "", page))

    def test_the_csp_still_has_no_connect_src_and_nothing_is_added_to_it(self):
        csp = re.search(r'Content-Security-Policy" content="([^"]*)"', self.page).group(1)
        self.assertNotIn("connect-src", csp)
        self.assertIn("default-src 'none'", csp)
        self.assertNotIn("unsafe-eval", csp)
        self.assertNotIn("script-src 'unsafe-inline'", csp)


class Checked(unittest.TestCase):
    def test_a_report_from_before_the_review_object_is_still_a_report(self):
        data = rv.s01()
        del data["schema_minor"], data["review"]
        for item in data["areas"] + data["findings"]:
            del item["id"], item["fingerprint"]
        report.check(data)
        self.assertIn("## Areas to test", md(data))
        self.assertNotIn("Decision", md(data))

    def test_review_keys_of_the_wrong_shape_are_a_report_error_naming_the_key(self):
        cases = (
            (("review",), []), (("review",), "x"), (("review", "key"), []), (("review", "previous"), "x"), (("review", "decisions"), []), (("review", "decisions"), 5),
            (("review", "revision"), "1"), (("review", "revision"), 0), (("review", "revision"), True), (("review", "as_of"), 5), (("schema_minor",), "1"),
            (("schema_minor",), True), (("schema_minor",), -1), (("areas", 0, "id"), 5), (("findings", 0, "fingerprint"), None),
        )
        for path, bad in cases:
            data = rv.s01()
            parent = data
            for step in path[:-1]:
                parent = parent[step]
            parent[path[-1]] = bad
            with self.subTest(path=path, bad=bad), self.assertRaises(report.ReportError) as ctx:
                report.check(data)
            self.assertIn(".".join(str(p) for p in path if not isinstance(p, int)).split(".")[-1], str(ctx.exception))

    def test_a_later_minor_version_is_read_the_major_version_is_what_is_refused(self):
        data = rv.s01()
        data["schema_minor"] = 7
        report.check(data)
        data["schema"] = 2
        with self.assertRaises(report.UnsupportedSchema):
            report.check(data)

    def test_an_evidence_claim_that_is_not_text_is_refused(self):
        data = rv.s01()
        data["areas"][0]["route_line"]["claim"] = 5
        with self.assertRaises(report.ReportError):
            report.check(data)


class RetypedFields(unittest.TestCase):
    """The deep payload is read tolerantly: a decisions object of the wrong shape is a render, or a ReportError, never another exception."""

    def test_every_key_of_the_decisions_object_removed_or_retyped(self):
        base = rv.decided()
        paths = []

        def walk(obj, path):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    paths.append(path + [k])
                    walk(v, path + [k])
            elif isinstance(obj, list):
                for i, v in enumerate(obj):
                    paths.append(path + [i])
                    walk(v, path + [i])

        walk(base["review"], ["review"])
        for path in paths:
            for how in ("delete", None, 5, "x", [], {}, True, 1e999):
                data = copy.deepcopy(base)
                parent = data
                for step in path[:-1]:
                    parent = parent[step]
                if how == "delete":
                    if isinstance(parent, dict):
                        del parent[path[-1]]
                    else:
                        parent.pop(path[-1])
                else:
                    parent[path[-1]] = how
                try:
                    md(data)
                    html(data)
                except report.ReportError:
                    pass


if __name__ == "__main__":
    unittest.main()
