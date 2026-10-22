"""M15 review: a record has a scope. A record of a test source blocks the claim about suggestions and says nothing about which areas
the change reaches; it is not a record that blocks selection. The JSON says which (`scope`, `blocks_selection`, `blocks_suggestions`),
the counts keep the two apart, and an older report (no `scope`) still loads and renders."""

from __future__ import annotations

import json
import unittest

from reachmap import incomplete, report
from reachmap.errors import ModelError
from reachmap.incomplete import Incompleteness, kind_spec, record
from reachmap.model import Analysis
from reachmap.report import model, summary
from tests import support_report as sr
from tests.test_testsources_report import REC_CUT, REC_UNAVAILABLE, REC_UNCONFIGURED, base, section

SUGGESTION_KINDS = ("source_unavailable", "source_unconfigured", "suggestions_incomplete", "spec_url_partial", "spec_visit_unmatched")
BLOCKS_SUGGESTIONS = ("source_unavailable", "suggestions_incomplete")


class Registry(unittest.TestCase):
    def test_the_four_kinds_of_a_test_source_have_the_scope_suggestions_and_never_block_selection(self):
        for kind in SUGGESTION_KINDS:
            with self.subTest(kind):
                spec = kind_spec(kind)
                self.assertEqual(spec.scope, "suggestions")
                self.assertFalse(spec.blocks_selection)
                self.assertEqual(spec.blocks_suggestions, kind in BLOCKS_SUGGESTIONS)

    def test_every_other_kind_has_the_scope_selection(self):
        for kind in incomplete.kinds():
            if kind not in SUGGESTION_KINDS:
                self.assertEqual(kind_spec(kind).scope, "selection", kind)
                self.assertFalse(kind_spec(kind).blocks_suggestions, kind)

    def test_source_unavailable_is_always_blocking_for_its_scope(self):
        for kind in BLOCKS_SUGGESTIONS:
            self.assertTrue(kind_spec(kind).always_blocking)
            self.assertTrue(record([], kind, "qase:ACME", detail="x").blocks_suggestions)

    def test_a_record_of_a_test_source_cannot_be_made_to_block_selection(self):
        for kind in SUGGESTION_KINDS:
            with self.subTest(kind), self.assertRaises(ModelError):
                Incompleteness(kind, "qase:ACME", "", True)
            with self.subTest(kind=kind, how="record"), self.assertRaises(ModelError):
                record([], kind, "qase:ACME", blocks_selection=True)

    def test_a_record_that_does_not_block_suggestions_is_the_unconfigured_and_the_partial_address(self):
        self.assertFalse(record([], "source_unconfigured", "qase:ACME").blocks_suggestions)
        self.assertFalse(record([], "spec_url_partial", "acme/e2e a.spec.ts:3").blocks_suggestions)

    def test_a_kind_of_the_run_never_blocks_suggestions(self):
        rec = record([], "read_unreadable", "src/a.ts", detail="HTTP 503")
        self.assertTrue(rec.blocks_selection)
        self.assertFalse(rec.blocks_suggestions)


class Json(unittest.TestCase):
    def test_the_record_says_its_scope(self):
        d = record([], "source_unavailable", "qase:ACME", detail="HTTP 500").to_dict()
        self.assertEqual((d["scope"], d["blocks_selection"], d["blocks_suggestions"]), ("suggestions", False, True))
        d = record([], "read_unreadable", "src/a.ts", detail="HTTP 503").to_dict()
        self.assertEqual((d["scope"], d["blocks_selection"], d["blocks_suggestions"]), ("selection", True, False))
        self.assertEqual(list(d)[-3:], ["blocks_selection", "scope", "blocks_suggestions"])

    def test_round_trip(self):
        for kind in SUGGESTION_KINDS + ("read_unreadable", "hub_cutoff"):
            rec = record([], kind, "x", detail="d")
            self.assertEqual(Incompleteness.from_dict(json.loads(json.dumps(rec.to_dict()))), rec)

    def test_a_record_written_before_scope_existed_still_loads_and_is_read_in_its_scope(self):
        old = {"kind": "source_unavailable", "subject": "qase", "not_established": kind_spec("source_unavailable").template.format(subject="qase"),
               "detail": "HTTP 500", "blocks_selection": True}
        rec = Incompleteness.from_dict(old)
        self.assertEqual((rec.blocks_selection, rec.blocks_suggestions), (False, True))
        older = {"kind": "hub_cutoff", "subject": "a.ts", "not_established": kind_spec("hub_cutoff").template.format(subject="a.ts"), "detail": "", "blocks_selection": False}
        self.assertEqual(Incompleteness.from_dict(older).scope, "selection")

    def test_a_record_that_contradicts_its_kind_is_refused(self):
        d = record([], "source_unavailable", "qase", detail="x").to_dict()
        for change in ({"scope": "selection"}, {"blocks_suggestions": False}, {"blocks_selection": True}):
            with self.subTest(change), self.assertRaises(ModelError):
                Incompleteness.from_dict({**d, **change})


class OnAnAnalysis(unittest.TestCase):
    def test_blocking_is_selection_only_and_blocking_suggestions_is_the_other(self):
        a = Analysis(change={"kind": "compare"}, ref="a" * 40)
        unreadable = record(a, "read_unreadable", "src/a.ts", detail="x")
        unavailable = record(a, "source_unavailable", "qase:ACME", detail="x")
        record(a, "source_unconfigured", "testrail:3")
        self.assertEqual(a.blocking(), [unreadable])
        self.assertEqual(a.blocking_suggestions(), [unavailable])


class Report(unittest.TestCase):
    def old(self, *records):
        """The same report as `base`, with its records as an older run wrote them: no scope, a blocking source record blocks_selection."""
        data = base(*records)
        for r in data["incompleteness"]:
            r.pop("scope", None)
            r.pop("blocks_suggestions", None)
            if r["kind"] in BLOCKS_SUGGESTIONS:
                r["blocks_selection"] = True
        return data

    def test_the_view_reads_a_record_by_its_scope(self):
        v = model.build(base(REC_UNAVAILABLE, REC_UNCONFIGURED))
        by = {str(r.kind): r for r in v.records}
        self.assertEqual((by["source_unavailable"].blocking, by["source_unavailable"].blocks_suggestions), (False, True))
        self.assertEqual((by["source_unconfigured"].blocking, by["source_unconfigured"].blocks_suggestions), (False, False))
        self.assertFalse(v.records_first)

    def test_an_older_report_is_read_the_same_way(self):
        new, old = model.build(base(REC_UNAVAILABLE, REC_CUT)), model.build(self.old(REC_UNAVAILABLE, REC_CUT))
        self.assertEqual([(r.blocking, r.blocks_suggestions) for r in new.records], [(r.blocking, r.blocks_suggestions) for r in old.records])
        self.assertEqual(report.render(base(REC_UNAVAILABLE, REC_CUT), "md"), report.render(self.old(REC_UNAVAILABLE, REC_CUT), "md"))

    def test_the_summary_prints_a_record_that_blocks_suggestions_and_does_not_call_it_a_missing_area_list(self):
        for data in (base(REC_UNAVAILABLE), self.old(REC_UNAVAILABLE)):
            data["areas"] = []
            lines = "\n".join(summary.lines(data))
            self.assertIn("Blocking: Suggestions from qase:ACME", lines)
            self.assertNotIn("none were established", lines)

    def test_the_counts_keep_the_two_apart(self):
        data = base(REC_UNAVAILABLE, REC_CUT, REC_UNCONFIGURED)
        data["counts"] = None
        view = model.build(data)
        counts = dict(view.header.counts)
        self.assertEqual((counts["blocking_records"], counts["blocking_suggestion_records"]), (0, 2))

    def test_the_mcp_record_carries_the_scope(self):
        from reachmap.mcp import tools

        rec = next(r for r in model.build(base(REC_UNAVAILABLE)).records if str(r.kind) == "source_unavailable")
        got = tools._record(rec)
        self.assertEqual((got["blocking"], got["blocks_suggestions"], str(got["scope"])), (False, True, "suggestions"))

    def test_the_report_names_the_two_sentences_by_scope_not_by_kind(self):
        text = report.render(base(REC_UNAVAILABLE), "md")
        self.assertIn("The areas come from the code and are not affected.", text)


if __name__ == "__main__":
    unittest.main()
