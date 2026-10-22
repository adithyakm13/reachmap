import dataclasses
import glob
import json
import os
import unittest

from reachmap import edges, incomplete  # `edges` registers the kinds the walker makes (edge_unfollowed, entry_points_unread, ...)
from reachmap.errors import ModelError, WordingError
from reachmap.incomplete import Incompleteness, kind_spec, record
from tests.fixtures import build

BLOCKING = (
    "read_unreadable", "search_truncated", "pre_change_unavailable", "diff_count_mismatch",
    "timed_out", "run_failed",
)
BLOCKING_SUGGESTIONS = ("source_unavailable", "suggestions_incomplete")  # records of a test source: they block the claim about suggestions, never selection (M15)
NON_BLOCKING = ("hub_cutoff", "router_unmounted", "not_distinctive", "hop_cap", "module_cap", "source_unconfigured")
LONE_SURROGATE = b"\xff".decode("utf-8", "surrogateescape")
SCENARIOS = os.path.join(os.path.dirname(__file__), "fixtures", "scenarios")


def make(kind="hub_cutoff", blocks=False, **kw):
    fields = dict(
        kind=kind,
        subject="src/lib/format.ts",
        detail="57 importers",
        blocks_selection=blocks,
    )
    fields.update(kw)
    return Incompleteness(**fields)


class RegistryTest(unittest.TestCase):
    def test_registry_has_every_kind_of_the_brief(self):
        for kind in BLOCKING + NON_BLOCKING:
            self.assertIn(kind, incomplete.kinds())

    def test_unknown_kind_raises(self):
        with self.assertRaises(ModelError):
            make(kind="made_up")
        with self.assertRaises(ModelError):
            record([], "made_up", "x")

    def test_blocking_kinds_default_to_blocking(self):
        for kind in BLOCKING:
            self.assertTrue(kind_spec(kind).blocks_selection, kind)

    def test_non_blocking_kinds_default_to_non_blocking(self):
        for kind in NON_BLOCKING:
            self.assertFalse(kind_spec(kind).blocks_selection, kind)

    def test_blocking_kinds_cannot_be_made_non_blocking(self):
        for kind in BLOCKING:
            with self.subTest(kind=kind):
                with self.assertRaises(ModelError):
                    make(kind=kind, blocks=False)
                with self.assertRaises(ModelError):
                    record([], kind, "x", blocks_selection=False)
                self.assertTrue(record([], kind, "x").blocks_selection)

    def test_source_unconfigured_is_non_blocking(self):
        rec = record([], "source_unconfigured", "qase")
        self.assertFalse(rec.blocks_selection)

    def test_a_configured_source_that_failed_to_answer_is_blocking_for_suggestions_unlike_an_unconfigured_one(self):
        self.assertTrue(record([], "source_unavailable", "qase", detail="HTTP 503").blocks_suggestions)
        self.assertFalse(record([], "source_unavailable", "qase", detail="HTTP 503").blocks_selection)  # it says nothing about the areas
        self.assertFalse(record([], "source_unconfigured", "qase").blocks_suggestions)

    def test_the_records_of_a_test_source_are_always_blocking_for_their_scope(self):
        for kind in BLOCKING_SUGGESTIONS:
            self.assertTrue(kind_spec(kind).always_blocking and kind_spec(kind).blocks_suggestions and not kind_spec(kind).blocks_selection)

    def test_timed_out_and_run_failed_always_block(self):
        for kind in ("timed_out", "run_failed"):
            with self.assertRaises(ModelError):
                record([], kind, "x", blocks_selection=False)

    def test_registry_matches_the_blocking_flag_of_every_fixture_expectation(self):
        self.assertIn("edge_unfollowed", incomplete.kinds())  # registered by importing `reachmap.edges`, not by chance of the test order
        seen = set()
        for path in [d / "expected.json" for d in build.m1_scenario_dirs()]:  # the M1 scenarios: the adapters of M16 keep their own
            with open(path, encoding="utf-8") as fh:
                expected = json.load(fh)
            for item in expected["not_established"]:
                seen.add(item["kind"])
                self.assertEqual(kind_spec(item["kind"]).blocks_selection, item["blocking"], path)
        self.assertEqual(seen, {"hub_cutoff", "router_unmounted", "not_distinctive", "pre_change_unavailable", "hop_cap", "edge_unfollowed", "entry_points_unread", "routes_unmatched"})

    def test_register_kind_rejects_duplicates(self):
        with self.assertRaises(ModelError):
            incomplete.register_kind("hub_cutoff", "{subject} again", blocks_selection=False)

    def test_register_kind_needs_real_bools(self):
        with self.assertRaises(ModelError):
            incomplete.register_kind("bool_a", "{subject} x", blocks_selection=1)
        with self.assertRaises(ModelError):
            incomplete.register_kind("bool_b", "{subject} x", blocks_selection=True, always_blocking=1)
        self.assertNotIn("bool_a", incomplete.kinds())
        self.assertNotIn("bool_b", incomplete.kinds())

    def test_register_kind_adds_a_kind_adapters_can_use(self):
        name = "test_only_kind"
        self.addCleanup(incomplete._KINDS.pop, name, None)
        incomplete.register_kind(name, "Nothing is known about {subject}", blocks_selection=True)
        self.assertEqual(record([], name, "x").not_established, "Nothing is known about x")

    def test_register_kind_rejects_a_template_without_subject_or_with_banned_wording(self):
        with self.assertRaises(ModelError):
            incomplete.register_kind("tmpl_a", "no placeholder", blocks_selection=False)
        with self.assertRaises(ModelError):
            incomplete.register_kind("tmpl_b", "{other} is wrong", blocks_selection=False)
        with self.assertRaises(WordingError):
            incomplete.register_kind("tmpl_c", "{subject} is safe", blocks_selection=False)
        for name in ("tmpl_a", "tmpl_b", "tmpl_c"):
            self.assertNotIn(name, incomplete.kinds())

    def test_every_registered_template_is_free_of_banned_wording(self):
        for kind in incomplete.kinds():
            record([], kind, "x")


class IncompletenessTest(unittest.TestCase):
    def test_banned_wording_in_detail_raises(self):
        with self.assertRaises(WordingError):
            make(detail="the rest is safe")

    def test_detail_is_checked_as_written_even_when_it_quotes_the_subject(self):
        with self.assertRaises(WordingError):
            make(subject="src/safe/guard.ts", detail="see src/safe/guard.ts")

    def test_a_quoted_subject_does_not_trip_the_derived_conclusion(self):
        rec = record([], "hub_cutoff", "src/safe/guard.ts", detail="57 importers")
        self.assertIn("src/safe/guard.ts", rec.note())

    def test_not_established_is_derived_from_the_kind_template_and_the_subject(self):
        rec = make()
        self.assertEqual(rec.not_established, kind_spec("hub_cutoff").template.format(subject="src/lib/format.ts"))
        self.assertNotIn("not_established", [f.name for f in dataclasses.fields(Incompleteness)])
        with self.assertRaises(TypeError):
            Incompleteness(kind="hub_cutoff", subject="x", not_established="own words", detail="", blocks_selection=False)

    def test_a_lone_surrogate_in_subject_or_detail_is_refused(self):
        with self.assertRaises(ModelError):
            make(subject="a" + LONE_SURROGATE)
        with self.assertRaises(ModelError):
            make(detail="a" + LONE_SURROGATE)

    def test_subject_must_be_filled(self):
        with self.assertRaises(ModelError):
            make(subject="")
        with self.assertRaises(ModelError):
            make(subject=3)

    def test_blocks_selection_must_be_a_bool(self):
        with self.assertRaises(ModelError):
            make(blocks=1)

    def test_a_non_blocking_kind_may_be_raised_to_blocking(self):
        self.assertTrue(make(kind="hub_cutoff", blocks=True).blocks_selection)

    def test_json_round_trip_and_stable_key_order(self):
        rec = make()
        data = rec.to_dict()
        self.assertEqual(list(data), ["kind", "subject", "not_established", "detail", "blocks_selection", "scope", "blocks_suggestions"])
        self.assertEqual(Incompleteness.from_dict(json.loads(json.dumps(data))), rec)

    def test_from_dict_rejects_missing_and_unknown_keys(self):
        data = make().to_dict()
        with self.assertRaises(ModelError):
            Incompleteness.from_dict({k: v for k, v in data.items() if k != "detail"})
        with self.assertRaises(ModelError):
            Incompleteness.from_dict({**data, "note": "x"})

    def test_from_dict_rejects_a_not_established_that_is_not_the_derived_one(self):
        data = make().to_dict()
        data["not_established"] = "Everything was established"
        with self.assertRaises(ModelError):
            Incompleteness.from_dict(data)

    def test_from_dict_revalidates_the_blocking_rule(self):
        data = make(kind="read_unreadable", blocks=True).to_dict()
        data["blocks_selection"] = False
        with self.assertRaises(ModelError):
            Incompleteness.from_dict(data)


class RecordTest(unittest.TestCase):
    def test_record_creates_the_record_and_its_note_together(self):
        found = []
        rec = record(found, "hub_cutoff", "src/lib/format.ts", detail="57 importers")
        self.assertEqual(found, [rec])
        self.assertIn("src/lib/format.ts", rec.not_established)
        self.assertEqual(rec.note(), f"{rec.not_established}: 57 importers")

    def test_note_is_derived_not_stored(self):
        self.assertNotIn("note", incomplete.Incompleteness.__dataclass_fields__)
        self.assertNotIn("note", make().to_dict())

    def test_note_without_detail_is_the_conclusion_alone(self):
        rec = record([], "hop_cap", "edge:http:acme/web:acme/api")
        self.assertEqual(rec.note(), rec.not_established)

    def test_record_takes_blocking_from_the_registry(self):
        self.assertTrue(record([], "pre_change_unavailable", "scenario/s16").blocks_selection)
        self.assertFalse(record([], "hub_cutoff", "src/lib/format.ts").blocks_selection)

    def test_record_may_raise_a_non_blocking_kind_to_blocking(self):
        self.assertTrue(record([], "hub_cutoff", "x", blocks_selection=True).blocks_selection)

    def test_record_does_not_add_the_same_record_twice(self):
        found = []
        first = record(found, "hop_cap", "edge:http:a:b")
        second = record(found, "hop_cap", "edge:http:a:b")
        self.assertEqual(found, [first])
        self.assertIs(first, second)

    def test_record_hands_the_record_to_add_incompleteness_of_an_object(self):
        class Holder:
            def __init__(self):
                self.records = []

            def add_incompleteness(self, rec):
                self.records.append(rec)
                return rec

        holder = Holder()
        rec = record(holder, "hop_cap", "edge:http:a:b")
        self.assertEqual(holder.records, [rec])

    def test_record_with_a_lone_surrogate_in_the_subject_is_refused(self):
        with self.assertRaises(ModelError):
            record([], "hop_cap", "a" + LONE_SURROGATE)

    def test_record_rejects_a_target_without_a_list(self):
        with self.assertRaises(ModelError):
            record(object(), "hop_cap", "x")


if __name__ == "__main__":
    unittest.main()
