import ast
import dataclasses
import glob
import inspect
import json
import os
import re
import unittest

from reachmap import incomplete, model, wording
from tests.fixtures import build
from reachmap.errors import ModelError, UnreadableError, WordingError
from reachmap.incomplete import record
from reachmap.model import (
    Analysis, Area, BranchState, Confidence, Decision, Evidence, Finding, MappedTest, Read,
    ReadResult, Risk, Suggestion,
)
from tests.support import (
    SHA, SHA2, analysis, area, branch, decision, ev, finding, mapped, screens, suggestion,
)

LONE_SURROGATE = b"\xff".decode("utf-8", "surrogateescape")
ROOT = os.path.dirname(os.path.dirname(__file__))
SCENARIOS = os.path.join(ROOT, "tests", "fixtures", "scenarios")


class ReadResultTest(unittest.TestCase):
    def test_the_three_states_are_string_constants(self):
        self.assertEqual((Read.FOUND, Read.ABSENT, Read.UNREADABLE), ("found", "absent", "unreadable"))

    def test_constructors_set_state_and_fields(self):
        self.assertEqual(ReadResult.found("x"), ReadResult("found", text="x"))
        self.assertEqual(ReadResult.absent().state, "absent")
        unreadable = ReadResult.unreadable("HTTP 503")
        self.assertEqual((unreadable.state, unreadable.detail), ("unreadable", "HTTP 503"))

    def test_found_empty_text_is_still_found(self):
        self.assertEqual(ReadResult.found("").text, "")
        self.assertTrue(ReadResult.found("").is_found)

    def test_found_result_with_no_text_raises(self):
        with self.assertRaises(ModelError):
            ReadResult("found")
        with self.assertRaises(ModelError):
            ReadResult.found(None)

    def test_absent_and_unreadable_results_with_text_raise(self):
        with self.assertRaises(ModelError):
            ReadResult("absent", text="x")
        with self.assertRaises(ModelError):
            ReadResult("unreadable", text="x", detail="d")

    def test_unreadable_result_needs_a_detail(self):
        with self.assertRaises(ModelError):
            ReadResult("unreadable")
        with self.assertRaises(ModelError):
            ReadResult.unreadable("  ")

    def test_unreadable_is_never_equal_to_absent(self):
        self.assertNotEqual(ReadResult.unreadable("HTTP 404 looked like a rate limit"), ReadResult.absent())
        self.assertNotEqual(ReadResult.unreadable("x").state, ReadResult.absent().state)

    def test_unknown_state_raises(self):
        with self.assertRaises(ModelError):
            ReadResult("missing")

    def test_a_lone_surrogate_in_found_text_or_detail_is_refused(self):
        with self.assertRaises(ModelError):
            ReadResult.found("a" + LONE_SURROGATE)
        with self.assertRaises(ModelError):
            ReadResult.unreadable("a" + LONE_SURROGATE)

    def test_the_state_properties_say_which_answer_it_is(self):
        found, absent, unreadable = ReadResult.found("x"), ReadResult.absent(), ReadResult.unreadable("503")
        self.assertEqual([r.is_found for r in (found, absent, unreadable)], [True, False, False])
        self.assertEqual([r.is_absent for r in (found, absent, unreadable)], [False, True, False])
        self.assertEqual([r.is_unreadable for r in (found, absent, unreadable)], [False, False, True])

    def test_testing_truthiness_raises_for_every_state(self):
        for result in (ReadResult.found("x"), ReadResult.found(""), ReadResult.absent(), ReadResult.unreadable("503")):
            with self.subTest(state=result.state):
                with self.assertRaises(TypeError) as cm:
                    bool(result)
                self.assertIn(".state", str(cm.exception))
                with self.assertRaises(TypeError):
                    if result:
                        pass
                with self.assertRaises(TypeError):
                    not result

    def test_text_raises_unless_the_read_found_something(self):
        with self.assertRaises(ModelError):
            ReadResult.absent().text
        with self.assertRaises(UnreadableError):
            ReadResult.unreadable("503").text
        self.assertEqual(ReadResult.found("x").text, "x")

    def test_the_wrong_check_patterns_all_fail_loudly_on_an_unreadable_read(self):
        unreadable = ReadResult.unreadable("rate limited")
        for wrong in (lambda r: r.text is None, lambda r: not r.text, lambda r: r.text == "", lambda r: bool(r)):
            with self.subTest():
                with self.assertRaises((ModelError, TypeError)):
                    wrong(unreadable)

    def test_text_or_none_returns_none_only_for_absent(self):
        self.assertEqual(ReadResult.found("x").text_or_none(), "x")
        self.assertIsNone(ReadResult.absent().text_or_none())
        with self.assertRaises(UnreadableError) as cm:
            ReadResult.unreadable("HTTP 503").text_or_none()
        self.assertEqual(cm.exception.detail, "HTTP 503")
        self.assertIsInstance(cm.exception, ModelError)

    def test_record_if_unreadable_adds_a_blocking_record_and_says_so(self):
        found = []
        self.assertTrue(ReadResult.unreadable("HTTP 503").record_if_unreadable(found, "src/a.ts"))
        self.assertEqual([(r.kind, r.subject, r.detail, r.blocks_selection) for r in found],
                         [("read_unreadable", "src/a.ts", "HTTP 503", True)])
        self.assertFalse(ReadResult.absent().record_if_unreadable(found, "src/b.ts"))
        self.assertFalse(ReadResult.found("x").record_if_unreadable(found, "src/c.ts"))
        self.assertEqual(len(found), 1)

    def test_match_statement_uses_the_state(self):
        def label(result):
            match result:
                case ReadResult("unreadable"):
                    return "blocked"
                case ReadResult("absent"):
                    return "absent"
                case _:
                    return "found"

        self.assertEqual([label(r) for r in (ReadResult.unreadable("x"), ReadResult.absent(), ReadResult.found("y"))],
                         ["blocked", "absent", "found"])

    def test_read_result_is_frozen(self):
        with self.assertRaises(dataclasses.FrozenInstanceError):
            ReadResult.found("x").state = "absent"


class EvidenceClaimTest(unittest.TestCase):
    def test_a_claim_is_optional_and_is_in_the_json_only_when_it_is_set(self):
        self.assertEqual(list(ev().to_dict()), ["repo", "path", "line", "ref", "detail"])
        claimed = ev(claim="import:src/a.ts:x")
        self.assertEqual(list(claimed.to_dict()), ["repo", "path", "line", "ref", "detail", "claim"])
        self.assertEqual(claimed.to_dict()["claim"], "import:src/a.ts:x")

    def test_from_dict_reads_both_forms_and_round_trips(self):
        for item in (ev(), ev(claim="def:Name")):
            self.assertEqual(Evidence.from_dict(item.to_dict()).claim, item.claim)
        with self.assertRaises(ModelError):
            Evidence.from_dict({**ev().to_dict(), "other": 1})
        with self.assertRaises(ModelError):
            Evidence.from_dict({k: v for k, v in ev().to_dict().items() if k != "detail"})

    def test_a_claim_is_a_one_line_text_or_none(self):
        for bad in ("", "  ", "a\nb", 5, b"x", "x\ud800"):
            with self.assertRaises(ModelError):
                ev(claim=bad)

    def test_two_pieces_of_evidence_for_the_same_line_are_equal_whatever_the_claim_so_a_chain_does_not_list_a_line_twice(self):
        self.assertEqual(ev(claim="def:A"), ev())
        self.assertEqual(hash(ev(claim="def:A")), hash(ev()))


class EvidenceTest(unittest.TestCase):
    def test_locator_is_ref_path_line(self):
        self.assertEqual(ev(path="src/a.ts", line=7).locator, f"{SHA}:src/a.ts:7")

    def test_evidence_rejects_a_branch_name_or_short_sha_as_ref(self):
        for ref in ("main", "release/1.x", "abc1234", SHA[:39], SHA + "a", "", "A" * 40, "g" * 40, "a" * 63, "a" * 65, None):
            with self.subTest(ref=ref):
                with self.assertRaises(ModelError):
                    ev(ref=ref)

    def test_evidence_refuses_the_null_object_id_which_names_no_commit(self):
        for ref in ("0" * 40, "0" * 64):
            with self.subTest(ref=ref):
                with self.assertRaises(ModelError):
                    ev(ref=ref)

    def test_evidence_accepts_a_sha256_commit_id(self):
        self.assertEqual(ev(ref="c" * 64).ref, "c" * 64)

    def test_evidence_line_must_be_at_least_one(self):
        for line in (0, -1, "3", 1.0, True, None):
            with self.subTest(line=line):
                with self.assertRaises(ModelError):
                    ev(line=line)
        self.assertEqual(ev(line=1).line, 1)

    def test_evidence_path_is_repo_relative_posix(self):
        for path in ("", "/etc/passwd", "../x.ts", "a/../b.ts", "a\\b.ts", "a//b.ts", "./a.ts", "a/.", None):
            with self.subTest(path=path):
                with self.assertRaises(ModelError):
                    ev(path=path)
        self.assertEqual(ev(path="services/billing/app/main.py").path, "services/billing/app/main.py")

    def test_evidence_detail_is_one_line_and_not_blank(self):
        for detail in ("a\n", "a\nb", "a\r\n", "", "   ", None):
            with self.subTest(detail=detail):
                with self.assertRaises(ModelError):
                    ev(detail=detail)

    def test_evidence_detail_only_refuses_newline_a_carriage_return_elsewhere_is_kept(self):
        self.assertEqual(ev(detail="a\rb").detail, "a\rb")
        self.assertIn("CRLF", Evidence.__doc__)

    def test_a_lone_surrogate_is_refused_in_every_evidence_text_field(self):
        for field in ("repo", "path", "detail"):
            with self.subTest(field=field):
                with self.assertRaises(ModelError) as cm:
                    ev(**{field: "a" + LONE_SURROGATE})
                self.assertIn("replace", str(cm.exception))

    def test_a_lone_surrogate_is_refused_in_finding_identity_and_text(self):
        for kw in ({"identity": ["a" + LONE_SURROGATE]}, {"title": "a" + LONE_SURROGATE},
                   {"workflow": LONE_SURROGATE}, {"confidence_basis": LONE_SURROGATE}):
            with self.assertRaises(ModelError):
                finding(**kw)
        with self.assertRaises(ModelError):
            area(label="/a" + LONE_SURROGATE)
        with self.assertRaises(ModelError):
            area(also_mounted=[LONE_SURROGATE])

    def test_evidence_repo_is_required(self):
        with self.assertRaises(ModelError):
            ev(repo="")

    def test_evidence_is_frozen(self):
        with self.assertRaises(dataclasses.FrozenInstanceError):
            ev().line = 2


class FindingTest(unittest.TestCase):
    def test_finding_without_evidence_raises(self):
        with self.assertRaises(ModelError):
            finding(evidence=[])

    def test_exclusion_without_evidence_raises(self):
        for kind in ("exclusion", "guard-excluded"):
            with self.assertRaises(ModelError):
                finding(kind=kind, evidence=[])

    def test_exclusion_is_a_property_of_the_kind(self):
        for kind in model.finding_kinds():
            self.assertEqual(finding(kind=kind).is_exclusion, kind in ("exclusion", "guard-excluded"), kind)

    def test_is_exclusion_cannot_be_passed_in(self):
        with self.assertRaises(TypeError):
            finding(is_exclusion=True)
        with self.assertRaises((AttributeError, TypeError)):
            finding().is_exclusion = True

    def test_is_exclusion_stays_in_to_dict_and_from_dict_rejects_a_mismatch(self):
        data = finding().to_dict()
        self.assertIs(data["is_exclusion"], False)
        data["is_exclusion"] = True
        with self.assertRaises(ModelError):
            Finding.from_dict(data)
        again = finding(kind="guard-excluded").to_dict()
        self.assertIs(again["is_exclusion"], True)
        again["is_exclusion"] = False
        with self.assertRaises(ModelError):
            Finding.from_dict(again)

    def test_id_and_fingerprint_are_in_the_json_of_a_finding_and_an_area_and_from_dict_checks_them_against_what_it_computes(self):
        from reachmap.identity import fingerprint

        for item, cls in ((finding(), Finding), (area(), Area)):
            data = item.to_dict()
            self.assertEqual((data["id"], data["fingerprint"]), (item.id, fingerprint(item)))
            self.assertEqual(list(data)[-2:], ["id", "fingerprint"])
            self.assertEqual(cls.from_dict(data), item)
            for key, wrong in (("id", item.id[:2] + "0" * 16), ("fingerprint", "0" * 64)):
                with self.assertRaises(ModelError, msg=key):
                    cls.from_dict({**data, key: wrong})
            for key in ("id", "fingerprint"):
                with self.assertRaises(ModelError, msg=key):
                    cls.from_dict({k: v for k, v in data.items() if k != key})

    def test_a_changed_evidence_line_with_the_old_fingerprint_is_refused_by_from_dict(self):
        data = finding().to_dict()
        data["evidence"][0]["detail"] = "import other from './z'"
        with self.assertRaises(ModelError):
            Finding.from_dict(data)
        data = finding().to_dict()
        data["evidence"][0]["line"] = 99  # the line number is not part of the claim
        self.assertEqual(Finding.from_dict(data).evidence[0].line, 99)

    def test_affected_and_excluded_claims_on_the_same_call_site_differ_in_id_and_fingerprint(self):
        from reachmap.identity import fingerprint

        site = ["acme/web", "src/pages/Pay.tsx", "formatAmount", "call:42"]
        affected = finding(kind="direct", identity=site)
        excluded = finding(kind="guard-excluded", identity=site)
        self.assertNotEqual(affected.id, excluded.id)
        self.assertNotEqual(fingerprint(affected), fingerprint(excluded))

    def test_adapters_can_register_an_exclusion_kind(self):
        self.addCleanup(model._FINDING_KINDS.pop, "stale-excluded", None)
        model.register_finding_kind("stale-excluded", is_exclusion=True)
        self.assertTrue(finding(kind="stale-excluded").is_exclusion)
        with self.assertRaises(ModelError):
            model.register_finding_kind("odd-kind", is_exclusion="yes")

    def test_a_finding_has_no_default_or_setter_for_evidence(self):
        field = {f.name: f for f in dataclasses.fields(Finding)}["evidence"]
        self.assertIs(field.default, dataclasses.MISSING)
        self.assertIs(field.default_factory, dataclasses.MISSING)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            finding().evidence = (ev(),)
        self.assertIsInstance(finding().evidence, tuple)

    def test_unknown_finding_kind_raises(self):
        with self.assertRaises(ModelError):
            finding(kind="made-up")

    def test_registry_has_the_kinds_of_the_brief_and_the_fixtures(self):
        for kind in ("direct", "cross-repo", "duplicate", "exclusion", "release", "contract-relaxed",
                     "contract-tightened", "removed_route_still_called", "added_route_uncalled",
                     "guard-excluded", "branch-missing"):
            self.assertIn(kind, model.finding_kinds())
        used = set()
        for path in [d / "expected.json" for d in build.m1_scenario_dirs()]:  # the M1 scenarios: the adapters of M16 keep their own
            with open(path, encoding="utf-8") as fh:
                used.update(m["kind"] for m in json.load(fh)["must_find"] if m["type"] == "finding")
        self.assertEqual(used, {"removed_route_still_called", "added_route_uncalled"})
        self.assertLessEqual(used, set(model.finding_kinds()))

    def test_register_finding_kind_rejects_duplicates_and_odd_names(self):
        with self.assertRaises(ModelError):
            model.register_finding_kind("direct")
        for name in ("", "Upper", "has space", "1x", None):
            with self.assertRaises(ModelError):
                model.register_finding_kind(name)

    def test_adapters_can_register_a_finding_kind(self):
        self.addCleanup(model._FINDING_KINDS.pop, "event-orphaned", None)
        model.register_finding_kind("event-orphaned")
        self.assertEqual(finding(kind="event-orphaned").kind, "event-orphaned")
        with self.assertRaises(ModelError):
            model.register_finding_kind("event-orphaned")

    def test_banned_wording_in_a_title_raises_for_each_phrase(self):
        for phrase in wording.BANNED:
            with self.subTest(phrase=phrase):
                with self.assertRaises(WordingError):
                    finding(title=f"Report says {phrase} here")

    def test_banned_wording_in_workflow_and_confidence_basis_raises(self):
        with self.assertRaises(WordingError):
            finding(workflow="this release is safe")
        with self.assertRaises(WordingError):
            finding(confidence_basis="already covers the route")

    def test_unsafe_in_a_title_is_allowed(self):
        self.assertEqual(finding(title="unsafe cast near safety check").title, "unsafe cast near safety check")

    def test_quoted_code_in_evidence_detail_is_exempt_from_the_wording_check(self):
        quoted = ev(detail='const mode = "safe"; // covered by tests, no impact')
        self.assertEqual(finding(evidence=[quoted]).evidence[0].detail, quoted.detail)

    def test_title_and_basis_must_be_filled(self):
        for name in ("title", "confidence_basis"):
            with self.assertRaises(ModelError):
                finding(**{name: " "})

    def test_identity_is_a_non_empty_tuple_of_non_empty_strings(self):
        for identity in ([], "abc", ["a", 1], ["a", ""], None):
            with self.subTest(identity=identity):
                with self.assertRaises(ModelError):
                    finding(identity=identity)
        self.assertEqual(finding(identity=["a", "b"]).identity, ("a", "b"))

    def test_evidence_must_be_evidence(self):
        with self.assertRaises(ModelError):
            finding(evidence=["not evidence"])
        with self.assertRaises(ModelError):
            finding(evidence=None)

    def test_decision_passed_as_evidence_raises(self):
        f = finding()
        d = decision(f, "reject", "wrong claim")
        with self.assertRaises(ModelError) as cm:
            finding(evidence=[d])
        self.assertIn("Decision", str(cm.exception))
        with self.assertRaises(ModelError):
            finding(evidence=[ev(), d])
        with self.assertRaises(ModelError):
            area(chains=[[d]])
        with self.assertRaises(ModelError):
            area(route_line=d)
        with self.assertRaises(ModelError):
            branch(evidence=[d])

    def test_id_is_the_identity_hash(self):
        from reachmap.identity import finding_id

        f = finding()
        self.assertEqual(f.id, finding_id(f.kind, f.identity))


class RiskAndConfidenceTest(unittest.TestCase):
    def test_risk_high_with_confidence_low_survives_a_json_round_trip_unchanged(self):
        f = finding(risk=Risk.HIGH, confidence=Confidence.LOW)
        again = Finding.from_dict(json.loads(json.dumps(f.to_dict())))
        self.assertEqual((again.risk, again.confidence), (Risk.HIGH, Confidence.LOW))
        self.assertEqual(again, f)
        data = f.to_dict()
        self.assertEqual((data["risk"], data["confidence"]), ("high", "low"))

    def test_risk_and_confidence_accept_their_string_values_and_nothing_else(self):
        self.assertIs(finding(risk="low").risk, Risk.LOW)
        for bad in ("critical", "", None, 3):
            with self.assertRaises(ModelError):
                finding(risk=bad)
            with self.assertRaises(ModelError):
                finding(confidence=bad)

    def test_risk_and_confidence_do_not_compare_or_mix(self):
        self.assertNotEqual(Risk.HIGH, Confidence.HIGH)
        self.assertNotEqual(Risk.HIGH, "high")
        with self.assertRaises(TypeError):
            Risk.HIGH < Confidence.LOW
        with self.assertRaises(TypeError):
            max(Risk.HIGH, Confidence.LOW)
        with self.assertRaises(TypeError):
            sorted([Risk.LOW, Confidence.HIGH])
        with self.assertRaises(ModelError):
            finding(risk=Confidence.LOW)
        with self.assertRaises(ModelError):
            finding(confidence=Risk.LOW)

    def test_rank_orders_one_scale_at_a_time(self):
        self.assertEqual([Risk.LOW.rank, Risk.MEDIUM.rank, Risk.HIGH.rank], [1, 2, 3])
        self.assertEqual([Confidence.LOW.rank, Confidence.MEDIUM.rank, Confidence.HIGH.rank], [1, 2, 3])
        self.assertEqual(sorted(Risk, key=lambda r: r.rank), [Risk.LOW, Risk.MEDIUM, Risk.HIGH])

    def test_risk_and_confidence_are_two_separate_enums(self):
        self.assertEqual([m.value for m in Risk], ["high", "medium", "low"])
        self.assertEqual([m.value for m in Confidence], ["high", "medium", "low"])
        self.assertIsNot(Risk, Confidence)
        self.assertNotIsInstance(Risk.HIGH, str)
        names = [f.name for f in dataclasses.fields(Finding)]
        self.assertIn("risk", names)
        self.assertIn("confidence", names)

    def test_no_attribute_or_function_combines_risk_and_confidence(self):
        combiner = re.compile(
            r"(avg|average|mean|combin|blend|merg|overall|score|weigh|sum)\w*(risk|confidence)"
            r"|(risk|confidence)\w*(avg|average|mean|combin|blend|merg|overall|score|weigh|sum)",
            re.IGNORECASE,
        )
        both = re.compile(r"def \w*risk\w*confidence|def \w*confidence\w*risk", re.IGNORECASE)
        for path in sorted(glob.glob(os.path.join(ROOT, "reachmap", "**", "*.py"), recursive=True)):
            with open(path, encoding="utf-8") as fh:
                for number, line in enumerate(fh, 1):
                    self.assertIsNone(combiner.search(line), f"{path}:{number}: {line.strip()}")
                    self.assertIsNone(both.search(line), f"{path}:{number}: {line.strip()}")
        for cls in (Finding, Risk, Confidence):
            for name in dir(cls):
                self.assertIsNone(combiner.search(name), f"{cls.__name__}.{name}")


class AreaTest(unittest.TestCase):
    def test_by_name_area_cannot_be_must_test_or_sample(self):
        for priority in ("must_test", "sample"):
            with self.assertRaises(ModelError):
                area(by_name=True, priority=priority, chains=[])
        candidate = area(by_name=True, priority="candidate", chains=[])
        self.assertTrue(candidate.by_name)

    def test_import_backed_area_needs_a_chain_with_evidence(self):
        with self.assertRaises(ModelError):
            area(chains=[])
        with self.assertRaises(ModelError):
            area(chains=[[]])
        with self.assertRaises(ModelError):
            area(chains=[[ev()], []])

    def test_area_label_is_exempt_from_the_wording_check(self):
        self.assertEqual(area(label="/api/safe/no-impact").label, "/api/safe/no-impact")

    def test_area_kind_and_priority_come_from_fixed_lists(self):
        for kind in ("screen", "api_route", "task", "job", "topic"):
            self.assertEqual(area(kind=kind).kind, kind)
        with self.assertRaises(ModelError):
            area(kind="page")
        with self.assertRaises(ModelError):
            area(priority="urgent")

    def test_area_id_comes_from_kind_repo_and_label(self):
        from reachmap.identity import area_id

        a = area()
        self.assertEqual(a.id, area_id("screen", "acme/web", "/cases"))
        self.assertEqual(a.id, area(priority="sample", module="other").id)
        self.assertNotEqual(a.id, area(label="/reports").id)

    def test_area_stores_chains_as_tuples(self):
        a = area()
        self.assertIsInstance(a.chains, tuple)
        self.assertIsInstance(a.chains[0], tuple)
        self.assertIsInstance(a.also_mounted, tuple)

    def test_also_mounted_must_be_strings(self):
        with self.assertRaises(ModelError):
            area(also_mounted="/x")
        self.assertEqual(area(also_mounted=["/cases/:caseId/invoices/:invoiceId"]).also_mounted,
                         ("/cases/:caseId/invoices/:invoiceId",))


class PrioritiesTest(unittest.TestCase):
    def test_threshold_is_a_named_constant(self):
        self.assertEqual(model.MUST_TEST_MAX_SCREENS, 8)

    def test_eight_screens_are_all_must_test(self):
        result = model.assign_priorities(screens(8, priority="sample"))
        self.assertEqual({a.priority for a in result}, {"must_test"})
        self.assertEqual(len(result), 8)

    def test_nine_screens_are_all_sample(self):
        result = model.assign_priorities(screens(9, priority="must_test"))
        self.assertEqual({a.priority for a in result}, {"sample"})

    def test_by_name_areas_stay_candidates_and_are_not_counted(self):
        by_name = [area(label=f"/n{i}", by_name=True, priority="candidate", chains=[]) for i in range(5)]
        result = model.assign_priorities(screens(8) + by_name)
        self.assertEqual([a.priority for a in result[:8]], ["must_test"] * 8)
        self.assertEqual([a.priority for a in result[8:]], ["candidate"] * 5)

    def test_only_screens_count_toward_the_threshold(self):
        routes = [area(label=f"/api/r{i}", kind="api_route") for i in range(5)]
        result = model.assign_priorities(screens(8) + routes)
        self.assertEqual({a.priority for a in result}, {"must_test"})

    def test_assign_priorities_returns_new_areas_and_keeps_the_input(self):
        original = screens(9, priority="must_test")
        model.assign_priorities(original)
        self.assertEqual({a.priority for a in original}, {"must_test"})

    def test_assign_priorities_keeps_order_and_ids(self):
        original = screens(3)
        self.assertEqual([a.id for a in model.assign_priorities(original)], [a.id for a in original])


class BranchStateTest(unittest.TestCase):
    def test_unreadable_branch_carries_a_detail(self):
        with self.assertRaises(ModelError):
            branch(state="unreadable", evidence=[], detail="")
        ok = branch(state="unreadable", evidence=[], detail="HTTP 503 reading src/a.ts")
        self.assertEqual(ok.state, "unreadable")

    def test_unreadable_is_never_treated_as_file_absent(self):
        unreadable = branch(state="unreadable", evidence=[], added_lines_found=0, detail="HTTP 503")
        absent = branch(state="file_absent", evidence=[], added_lines_found=0)
        self.assertNotEqual(unreadable, absent)
        self.assertNotEqual(unreadable.state, absent.state)

    def test_states_come_from_a_fixed_list(self):
        for state in ("present", "partial", "pre_change", "diverged", "file_absent", "unreadable"):
            kw = {}
            if state in ("file_absent", "unreadable"):
                kw.update(evidence=[], detail="d")
            if state in ("file_absent", "pre_change", "unreadable"):
                kw.update(added_lines_found=0)
            self.assertEqual(branch(state=state, **kw).state, state)
        with self.assertRaises(ModelError):
            branch(state="missing")

    def test_a_claim_about_content_needs_evidence(self):
        for state in ("present", "partial", "pre_change", "diverged"):
            with self.assertRaises(ModelError):
                branch(state=state, evidence=[], added_lines_found=0 if state == "pre_change" else 2)

    def test_present_needs_added_lines_found_and_absent_or_pre_change_needs_none(self):
        with self.assertRaises(ModelError):
            branch(state="present", added_lines_found=0)
        for state in ("file_absent", "pre_change"):
            with self.assertRaises(ModelError):
                branch(state=state, added_lines_found=1, evidence=[ev()] if state == "pre_change" else [])
            self.assertEqual(branch(state=state, added_lines_found=0,
                                    evidence=[ev()] if state == "pre_change" else []).added_lines_found, 0)

    def test_branch_detail_passes_the_wording_check(self):
        with self.assertRaises(WordingError):
            branch(detail="this branch is safe")

    def test_commit_is_a_full_sha_except_an_unreadable_branch_with_no_tip(self):
        with self.assertRaises(ModelError):
            branch(commit="main")
        with self.assertRaises(ModelError):
            branch(commit="")
        self.assertEqual(branch(commit="", state="unreadable", evidence=[], detail="no tip").commit, "")

    def test_added_lines_found_is_not_negative(self):
        with self.assertRaises(ModelError):
            branch(added_lines_found=-1)


class DecisionTest(unittest.TestCase):
    def test_skip_without_a_reason_raises(self):
        a = area()
        for reason in ("", "   "):
            with self.assertRaises(ModelError):
                decision(a, "skip", reason)

    def test_reject_without_a_reason_raises(self):
        with self.assertRaises(ModelError):
            decision(finding(), "reject", "")

    def test_test_sample_and_accept_need_no_reason(self):
        self.assertEqual(decision(area(), "test", "").verdict, "test")
        self.assertEqual(decision(area(), "sample", "").verdict, "sample")
        self.assertEqual(decision(finding(), "accept", "").verdict, "accept")

    def test_describe_of_a_skip_says_risk_accepted_not_tested(self):
        text = model.describe(decision(area(), "skip", "release is Friday", author="ada"))
        self.assertEqual(text, 'risk accepted, not tested (reason from ada: "release is Friday")')

    def test_describe_quotes_the_reason_so_it_never_reads_as_the_reports_claim(self):
        text = model.describe(decision(area(), "skip", "it is safe", author="ada"))
        self.assertTrue(text.startswith("risk accepted, not tested"))
        self.assertIn('reason from ada: "it is safe"', text)
        self.assertEqual(model.describe(decision(area(), "test", "", author="ada")), "marked to test")
        self.assertIn('"say \\"hi\\""', model.describe(decision(area(), "skip", 'say "hi"')))

    def test_describe_wording_outside_the_quoted_reason_is_clean(self):
        for verdict, target in (("test", area()), ("sample", area()), ("skip", area()),
                                ("accept", finding()), ("reject", finding())):
            self.assertEqual(wording.check(model.describe(decision(target, verdict, "because"))), [], verdict)

    def test_verdicts_must_fit_the_target_kind(self):
        with self.assertRaises(ModelError):
            decision(area(), "accept")
        with self.assertRaises(ModelError):
            decision(finding(), "skip")
        for bad in ("x-123", "a-123", "a-" + "g" * 16, "a-" + "0" * 15, "a-" + "0" * 17, "A-" + "0" * 16, "f- " + "0" * 15):
            with self.subTest(bad=bad):
                with self.assertRaises(ModelError):
                    decision(bad, "skip")
        self.assertEqual(decision("a-" + "0" * 16, "skip").target_id, "a-" + "0" * 16)

    def test_a_decision_that_breaks_a_rule_says_which_by_a_stable_code_not_by_its_message(self):
        a = area()
        cases = {
            "decision_target": dict(target_id="x-1234"), "decision_verdict": dict(verdict="accept"), "decision_reason": dict(verdict="skip", reason=" "),
            "decision_fingerprint": dict(fingerprint="short"), "decision_author": dict(author=" "), "decision_channel": dict(channel=""), "decision_revision": dict(revision=0),
        }
        for code, change in cases.items():
            with self.subTest(code), self.assertRaises(ModelError) as ctx:
                decision(a, **change)
            self.assertEqual(ctx.exception.code, code)
        self.assertIsNone(ModelError("other").code)

    def test_fingerprint_and_revision_are_checked(self):
        with self.assertRaises(ModelError):
            decision(area(), fingerprint="short")
        with self.assertRaises(ModelError):
            decision(area(), revision=0)
        with self.assertRaises(ModelError):
            decision(area(), author="")
        with self.assertRaises(ModelError):
            decision(area(), channel="")

    def test_a_decision_is_not_evidence_and_has_no_evidence_fields(self):
        names = {f.name for f in dataclasses.fields(Decision)}
        self.assertFalse(names & {"evidence", "ref", "line", "path"})
        self.assertNotIsInstance(decision(area()), Evidence)


class SuggestionTest(unittest.TestCase):
    def test_suggestion_is_always_labelled(self):
        self.assertEqual(model.SUGGESTION_LABEL, "suggested, not verified coverage")
        s = suggestion()
        self.assertEqual(s.label, model.SUGGESTION_LABEL)
        self.assertEqual(s.to_dict()["label"], model.SUGGESTION_LABEL)

    def test_a_suggestion_with_another_label_is_rejected_on_load(self):
        data = suggestion().to_dict()
        data["label"] = "covered"
        with self.assertRaises(ModelError):
            Suggestion.from_dict(data)

    def test_a_mapped_test_needs_the_mapping_that_made_it_so_a_suggestion_cannot_pass_for_one(self):
        a = area()
        with self.assertRaises(ModelError):
            mapped(a, mapping="")
        with self.assertRaises(TypeError):
            MappedTest(area_id=a.id, case_id="1", title="t", source="qase")
        fields = {f.name for f in dataclasses.fields(MappedTest)}
        self.assertIn("mapping", fields)
        self.assertNotIn("mapping", {f.name for f in dataclasses.fields(Suggestion)})
        self.assertNotEqual(suggestion(), mapped(a))

    def test_suggestions_and_mapped_tests_are_different_fields_of_different_types(self):
        self.assertTrue(hasattr(Analysis, "suggestions") and hasattr(Analysis, "mapped_tests"))
        self.assertIsNot(Suggestion, MappedTest)
        a = analysis(suggestions=[suggestion()], mapped_tests=[mapped(area())])
        self.assertIsInstance(a.suggestions[0], Suggestion)
        self.assertIsInstance(a.mapped_tests[0], MappedTest)
        with self.assertRaises(ModelError):
            analysis(mapped_tests=[suggestion()])
        with self.assertRaises(ModelError):
            analysis(suggestions=[mapped(area())])


class AnalysisTest(unittest.TestCase):
    def test_timed_out_analysis_has_no_finding_count(self):
        timed_out = analysis(status="timed_out")
        with self.assertRaises(ModelError):
            timed_out.finding_count
        self.assertNotIn("finding_count", timed_out.to_dict())

    def test_failed_analysis_has_no_finding_count(self):
        with self.assertRaises(ModelError):
            analysis(status="failed").finding_count

    def test_a_timed_out_or_failed_analysis_exposes_no_count_through_to_dict_either(self):
        for status in ("timed_out", "failed"):
            with self.subTest(status=status):
                data = analysis(status=status).to_dict()
                self.assertIsNone(data["findings"])
                self.assertIsNone(data["areas"])
                with self.assertRaises(TypeError):
                    len(data["findings"])

    def test_a_completed_analysis_lists_findings_and_areas_in_to_dict(self):
        data = analysis().to_dict()
        self.assertEqual(len(data["findings"]), 1)
        self.assertEqual(len(data["areas"]), 1)

    def test_completed_analysis_counts_its_findings(self):
        self.assertEqual(analysis(findings=[]).finding_count, 0)
        self.assertEqual(analysis(findings=[finding(), finding(identity=["other"])]).finding_count, 2)

    def test_status_comes_from_a_fixed_list(self):
        with self.assertRaises(ModelError):
            analysis(status="running")

    def test_revision_starts_at_one(self):
        with self.assertRaises(ModelError):
            analysis(revision=0)

    def test_ref_must_be_a_commit_sha_of_40_or_64_hex(self):
        for ref in ("main", "abc1234", "", "A" * 40):
            with self.assertRaises(ModelError):
                analysis(ref=ref)
        self.assertEqual(analysis(ref="d" * 64).ref, "d" * 64)

    def test_a_run_with_no_resolved_change_has_no_ref_and_cites_its_spec_in_its_record(self):
        for status, kind in (("timed_out", "timed_out"), ("failed", "run_failed")):
            with self.subTest(status=status):
                a = Analysis(change={"resolved": False}, ref=None)
                a.finish(status, detail="x", subject="compare main...nope")
                self.assertIsNone(a.ref)
                self.assertEqual([(r.kind, r.subject) for r in a.incompleteness], [(kind, "compare main...nope")])
                data = a.to_dict()
                self.assertIsNone(data["ref"])
                again = Analysis.from_dict(json.loads(json.dumps(data)))
                self.assertEqual((again.ref, again.status), (None, status))

    def test_a_completed_analysis_needs_its_ref_and_the_null_object_id_is_no_ref(self):
        builder = Analysis(change={}, ref=None)  # a run in progress that has not resolved its change
        with self.assertRaises(ModelError):
            builder.to_dict()  # a completed report names the commit it analysed
        with self.assertRaises(ModelError):
            builder.finding_count
        for ref in ("0" * 40, "0" * 64):
            with self.assertRaises(ModelError):
                Analysis(change={}, ref=ref)
            with self.assertRaises(ModelError):
                Analysis(change={}, ref=ref, status="failed", incompleteness=[record([], "run_failed", "x")])
        data = analysis().to_dict()
        with self.assertRaises(ModelError):
            Analysis.from_dict({**data, "ref": None})

    def test_finishing_a_run_with_no_ref_needs_the_subject_of_the_record(self):
        a = Analysis(change={}, ref=None)
        with self.assertRaises(ModelError):
            a.finish("failed")
        self.assertEqual(a.incompleteness, ())
        b = analysis()
        b.finish("failed", subject="ignored: the ref is the subject when there is one")
        self.assertEqual(b.incompleteness[0].subject, SHA)

    def test_a_run_that_did_not_complete_has_no_partial_results_of_any_kind(self):
        for status in ("timed_out", "failed"):
            with self.subTest(status=status):
                a = analysis()
                a.finish(status)
                self.assertEqual((a.branches, a.propagations, a.backend_calls, a.suggestions, a.mapped_tests), ((), (), (), (), ()))
                data = a.to_dict()
                for key in ("findings", "areas", "branches", "propagations", "backend_calls", "suggestions", "mapped_tests"):
                    self.assertIsNone(data[key], key)
                self.assertEqual(data["incompleteness"][0]["kind"], "timed_out" if status == "timed_out" else "run_failed")
                self.assertEqual(data["notes"], ["Run on a fixture"])  # notes are not results
                for add, item in ((a.add_branch, branch()), (a.add_suggestion, suggestion()), (a.add_propagation, {"x": 1}), (a.add_backend_call, {"x": 1})):
                    with self.assertRaises(ModelError):
                        add(item)
                again = Analysis.from_dict(json.loads(json.dumps(data)))
                self.assertEqual((again.status, again.branches, again.propagations), (status, (), ()))

    def test_from_dict_refuses_null_results_for_a_completed_analysis_and_real_ones_for_a_failed_one_in_every_field(self):
        done, failed = analysis().to_dict(), analysis(status="failed").to_dict()
        for key in ("branches", "propagations", "backend_calls", "suggestions", "mapped_tests"):
            with self.subTest(key=key):
                with self.assertRaises(ModelError):
                    Analysis.from_dict({**done, key: None})
        with self.assertRaises(ModelError):
            Analysis.from_dict({**failed, "branches": done["branches"]})
        with self.assertRaises(ModelError):
            Analysis.from_dict({**failed, "propagations": done["propagations"]})

    def test_notes_pass_the_banned_wording_check(self):
        with self.assertRaises(WordingError):
            analysis(notes=["nothing to test here"])

    def test_items_are_type_checked(self):
        with self.assertRaises(ModelError):
            analysis(findings=[{"kind": "direct"}])
        with self.assertRaises(ModelError):
            analysis(branches=[finding()])
        with self.assertRaises(ModelError):
            analysis(change=[])

    def test_the_collections_are_read_only_tuples(self):
        a = analysis()
        for name in ("branches", "findings", "areas", "propagations", "backend_calls", "suggestions",
                     "mapped_tests", "incompleteness", "notes"):
            with self.subTest(name=name):
                self.assertIsInstance(getattr(a, name), tuple)
                with self.assertRaises(AttributeError):
                    getattr(a, name).append("x")
                with self.assertRaises(AttributeError):
                    setattr(a, name, ())

    def test_status_and_revision_cannot_be_assigned(self):
        a = analysis(status="timed_out")
        for name, value in (("status", "completed"), ("revision", 2), ("ref", SHA2), ("change", {})):
            with self.assertRaises(AttributeError):
                setattr(a, name, value)
        self.assertEqual(a.status, "timed_out")

    def test_add_finding_validates_and_refuses_duplicate_ids(self):
        a = analysis(findings=[])
        f = finding()
        self.assertIs(a.add_finding(f), f)
        with self.assertRaises(ModelError):
            a.add_finding("x")
        with self.assertRaises(ModelError):
            a.add_finding(finding(title="StatusBadge reaches 9 screens"))
        self.assertEqual(a.findings, (f,))

    def test_add_area_validates_and_refuses_duplicate_ids(self):
        a = analysis(areas=[])
        one = area()
        a.add_area(one)
        with self.assertRaises(ModelError):
            a.add_area(area(priority="sample"))
        with self.assertRaises(ModelError):
            a.add_area(finding())
        self.assertEqual(a.areas, (one,))

    def test_construction_refuses_duplicate_ids_too(self):
        with self.assertRaises(ModelError):
            analysis(findings=[finding(), finding(title="other title")])
        with self.assertRaises(ModelError):
            analysis(areas=[area(), area()])

    def test_add_note_checks_wording_and_type(self):
        a = analysis(notes=[])
        a.add_note("Run on a fixture")
        with self.assertRaises(WordingError):
            a.add_note("this release is safe")
        with self.assertRaises(ModelError):
            a.add_note(3)
        self.assertEqual(a.notes, ("Run on a fixture",))

    def test_add_branch_suggestion_and_mapped_test_check_their_types(self):
        a = analysis(branches=[], suggestions=[], mapped_tests=[])
        a.add_branch(branch())
        a.add_suggestion(suggestion())
        a.add_mapped_test(mapped(area()))
        self.assertEqual((len(a.branches), len(a.suggestions), len(a.mapped_tests)), (1, 1, 1))
        with self.assertRaises(ModelError):
            a.add_branch(finding())
        with self.assertRaises(ModelError):
            a.add_suggestion(mapped(area()))
        with self.assertRaises(ModelError):
            a.add_mapped_test(suggestion())

    def test_add_incompleteness_dedupes_and_checks_its_type(self):
        a = analysis()
        rec = record([], "hop_cap", "edge:http:a:b")
        self.assertIs(a.add_incompleteness(rec), rec)
        self.assertIs(a.add_incompleteness(record([], "hop_cap", "edge:http:a:b")), rec)
        self.assertEqual(a.incompleteness, (rec,))
        with self.assertRaises(ModelError):
            a.add_incompleteness("hop_cap")

    def test_the_opaque_dicts_must_be_json_and_are_copied_in_and_out(self):
        change = {"kind": "compare", "files": ["a.ts"]}
        a = analysis(change=change)
        change["files"].append("b.ts")
        self.assertEqual(a.change["files"], ["a.ts"])
        a.change["files"].append("c.ts")
        self.assertEqual(a.change["files"], ["a.ts"])
        call = {"route": "/api/cases", "n": [1]}
        a.add_propagation(call)
        a.add_backend_call(call)
        call["n"].append(2)
        a.propagations[-1]["n"].append(3)
        self.assertEqual(a.propagations[-1]["n"], [1])
        self.assertEqual(a.backend_calls[-1]["n"], [1])

    def test_the_opaque_dicts_refuse_what_json_cannot_hold(self):
        a = analysis()
        for bad in ({"x": {1, 2}}, {"x": float("nan")}, {"x": object()}, {"x": "a" + LONE_SURROGATE}, [1], "text"):
            with self.subTest(bad=bad):
                with self.assertRaises(ModelError):
                    a.add_propagation(bad)
                with self.assertRaises(ModelError):
                    a.add_backend_call(bad)
                with self.assertRaises(ModelError):
                    analysis(change=bad)

    def test_blocking_returns_only_blocking_records(self):
        a = analysis()
        hub = record(a, "hub_cutoff", "src/lib/format.ts")
        unreadable = record(a, "read_unreadable", "src/a.ts", detail="HTTP 503")
        optional = record(a, "source_unconfigured", "qase")
        self.assertEqual(a.incompleteness, (hub, unreadable, optional))
        self.assertEqual(a.blocking(), [unreadable])

    def test_record_works_on_an_analysis_through_add_incompleteness(self):
        a = analysis()
        rec = record(a, "pre_change_unavailable", "scenario/s16")
        self.assertIs(record(a, "pre_change_unavailable", "scenario/s16"), rec)
        self.assertEqual(a.incompleteness, (rec,))
        self.assertEqual(a.blocking(), [rec])

    def test_record_if_unreadable_works_on_an_analysis(self):
        a = analysis()
        self.assertTrue(ReadResult.unreadable("HTTP 503").record_if_unreadable(a, "src/a.ts"))
        self.assertEqual([r.kind for r in a.blocking()], ["read_unreadable"])

    def test_the_lists_are_copied_so_a_caller_cannot_alias_them(self):
        shared = []
        a = analysis(incompleteness=shared)
        record(a, "hop_cap", "edge:http:a:b")
        self.assertEqual(shared, [])

    def test_finish_completed_keeps_everything(self):
        a = analysis()
        a.finish("completed")
        self.assertEqual((a.status, len(a.findings), len(a.areas), a.incompleteness), ("completed", 1, 1, ()))

    def test_finish_timed_out_drops_partial_results_and_records_the_timeout(self):
        a = analysis()
        a.finish("timed_out", detail="900 s")
        self.assertEqual(a.status, "timed_out")
        self.assertEqual((a.findings, a.areas), ((), ()))
        self.assertEqual([(r.kind, r.subject, r.detail, r.blocks_selection) for r in a.incompleteness],
                         [("timed_out", SHA, "900 s", True)])
        self.assertEqual(a.blocking(), list(a.incompleteness))
        with self.assertRaises(ModelError):
            a.finding_count

    def test_finish_failed_records_run_failed(self):
        a = analysis()
        a.finish("failed", detail="git exited 128")
        self.assertEqual([(r.kind, r.blocks_selection) for r in a.incompleteness], [("run_failed", True)])

    def test_finish_never_goes_back_to_completed_or_between_failures(self):
        for first in ("timed_out", "failed"):
            a = analysis()
            a.finish(first)
            for later in ("completed", "failed" if first == "timed_out" else "timed_out"):
                with self.subTest(first=first, later=later):
                    with self.assertRaises(ModelError):
                        a.finish(later)
            self.assertEqual(a.status, first)

    def test_finish_twice_with_the_same_failure_adds_one_record(self):
        a = analysis()
        a.finish("timed_out")
        a.finish("timed_out")
        self.assertEqual(len(a.incompleteness), 1)

    def test_finish_refuses_an_unknown_status(self):
        with self.assertRaises(ModelError):
            analysis().finish("running")

    def test_no_partial_results_after_a_failure(self):
        a = analysis()
        a.finish("timed_out")
        with self.assertRaises(ModelError):
            a.add_finding(finding())
        with self.assertRaises(ModelError):
            a.add_area(area())

    def test_a_non_completed_analysis_must_be_built_with_its_record_and_no_results(self):
        record_for = {"timed_out": "timed_out", "failed": "run_failed"}
        for status, kind in record_for.items():
            with self.subTest(status=status):
                with self.assertRaises(ModelError):
                    Analysis(change={}, ref=SHA, status=status)
                with self.assertRaises(ModelError):
                    Analysis(change={}, ref=SHA, status=status, incompleteness=[record([], "hop_cap", "x")])
                with self.assertRaises(ModelError):
                    Analysis(change={}, ref=SHA, status=status, findings=[finding()],
                             incompleteness=[record([], kind, SHA)])
                with self.assertRaises(ModelError):
                    Analysis(change={}, ref=SHA, status=status, areas=[area()],
                             incompleteness=[record([], kind, SHA)])
                self.assertEqual(Analysis(change={}, ref=SHA, status=status, incompleteness=[record([], kind, SHA)]).status, status)

    def test_validate_reruns_every_check_so_to_dict_cannot_emit_a_corrupted_analysis(self):
        corruptions = {
            "an item of the wrong type": lambda a: a._findings.append("x"),
            "a banned note": lambda a: a._notes.append("this release is safe"),
            "a duplicate finding": lambda a: a._findings.append(finding()),
            "a duplicate area": lambda a: a._areas.append(area()),
            "a non-json dict": lambda a: a._propagations.append({"x": {1}}),
            "timed out without its record": lambda a: setattr(a, "_status", "timed_out"),
            "a bad status": lambda a: setattr(a, "_status", "running"),
            "a bad ref": lambda a: setattr(a, "_ref", "main"),
        }
        for name, corrupt in corruptions.items():
            with self.subTest(name):
                a = analysis()
                a.validate()
                corrupt(a)
                with self.assertRaises(ModelError):
                    a.validate()
                with self.assertRaises(ModelError):
                    a.to_dict()

    def test_analyses_with_the_same_content_are_equal(self):
        self.assertEqual(analysis(), analysis())
        self.assertNotEqual(analysis(), analysis(revision=2))
        self.assertNotEqual(analysis(), analysis(notes=[]))
        with self.assertRaises(TypeError):
            hash(analysis())


class CoverageGapsTest(unittest.TestCase):
    def test_coverage_gaps_counts_areas_to_test_without_a_mapped_test(self):
        one, two = area(label="/a"), area(label="/b", priority="sample")
        by_name = area(label="/c", by_name=True, priority="candidate", chains=[])
        a = analysis(areas=[one, two, by_name], mapped_tests=[mapped(one)])
        self.assertEqual(model.coverage_gaps(a), [two])

    def test_coverage_gaps_ignores_suggestions_even_when_they_match_the_area_by_word(self):
        one = area(label="/cases")
        without = analysis(areas=[one], suggestions=[])
        many = analysis(
            areas=[one],
            suggestions=[suggestion(case_id=str(i), title="Open the cases list", matched_words=["cases", "/cases"])
                         for i in range(5)],
        )
        self.assertEqual(model.coverage_gaps(without), [one])
        self.assertEqual(model.coverage_gaps(many), [one])

    def test_a_suggestion_copied_into_mapped_test_fields_is_not_a_mapped_test(self):
        one = area(label="/cases")
        s = suggestion(title="Open the cases list", matched_words=["cases"])
        a = analysis(areas=[one], suggestions=[s])
        with self.assertRaises(ModelError):
            a.add_mapped_test(s)
        self.assertEqual(model.coverage_gaps(a), [one])
        self.assertIsNot(Suggestion, MappedTest)

    def test_coverage_gaps_never_reads_the_suggestions_field(self):
        tree = ast.parse(inspect.getsource(model.coverage_gaps))
        attributes = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        self.assertNotIn("suggestions", attributes)
        self.assertIn("mapped_tests", attributes)

    def test_coverage_gaps_raises_for_a_run_that_did_not_complete(self):
        for status in ("timed_out", "failed"):
            with self.assertRaises(ModelError):
                model.coverage_gaps(analysis(status=status))

    def test_only_coverage_gaps_decides_which_areas_have_a_mapped_test(self):
        """Any other function that reads mapped tests or is named for gaps or coverage is a second counter."""
        plumbing = {"mapped_tests", "add_mapped_test", "_content", "_check", "validate", "__init__", "from_dict", "finish"}  # finish only clears it
        readers, named = [], []
        for path in sorted(glob.glob(os.path.join(ROOT, "reachmap", "*.py"))):
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            for fn in ast.walk(tree):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if re.search(r"gap|uncovered|coverage", fn.name, re.IGNORECASE):
                    named.append(fn.name)
                reads = any(isinstance(n, ast.Attribute) and n.attr in ("mapped_tests", "_mapped_tests")
                            for n in ast.walk(fn))
                if reads and fn.name not in plumbing:
                    readers.append(fn.name)
        self.assertEqual(named, ["coverage_gaps"])
        self.assertEqual(readers, ["coverage_gaps"])


class RoundTripTest(unittest.TestCase):
    def objects(self):
        a = area()
        f = finding()
        return {
            "ReadResult": ReadResult.unreadable("HTTP 503"),
            "Evidence": ev(),
            "Finding": f,
            "Area": a,
            "BranchState": branch(),
            "Decision": decision(a),
            "Suggestion": suggestion(),
            "MappedTest": mapped(a),
            "Incompleteness": record([], "hub_cutoff", "src/lib/format.ts", detail="57 importers"),
            "Analysis": analysis(
                incompleteness=[record([], "read_unreadable", "src/a.ts", detail="HTTP 503")],
                mapped_tests=[mapped(a)],
            ),
            "Analysis (timed out)": analysis(status="timed_out"),
        }

    def test_json_round_trip_for_every_type(self):
        for name, obj in self.objects().items():
            with self.subTest(name=name):
                text = json.dumps(obj.to_dict())
                again = type(obj).from_dict(json.loads(text))
                self.assertEqual(again, obj)
                self.assertEqual(json.dumps(again.to_dict()), text)
                self.assertEqual(model.dumps(again), model.dumps(obj))

    def test_to_dict_key_order_is_the_field_order(self):
        for name, obj in self.objects().items():
            with self.subTest(name=name):
                if isinstance(obj, Analysis):
                    fields = ["change", "ref", "branches", "findings", "areas", "propagations", "backend_calls",
                              "suggestions", "mapped_tests", "incompleteness", "notes", "revision", "status"]
                else:
                    fields = [f.name for f in dataclasses.fields(obj)]
                keys = list(obj.to_dict())
                if name in ("Suggestion",):
                    fields.append("label")
                if name in ("Finding", "Area"):
                    fields += (["is_exclusion"] if name == "Finding" else []) + ["id", "fingerprint"]
                if name == "Evidence":
                    fields.remove("claim")  # in the JSON only when it is set (EvidenceClaimTest)
                if name == "ReadResult":
                    fields[1] = "text"
                if name == "Incompleteness":
                    fields.insert(2, "not_established")
                    fields += ["scope", "blocks_suggestions"]
                self.assertEqual(keys, fields)

    def test_dumps_is_canonical_and_equal_for_equal_values(self):
        a, b = finding(), finding()
        self.assertEqual(model.dumps(a), model.dumps(b))
        data = json.loads(model.dumps(a))
        self.assertEqual(list(data), sorted(data))

    def test_from_dict_rejects_unknown_and_missing_keys_for_every_type(self):
        for name, obj in self.objects().items():
            with self.subTest(name=name):
                data = obj.to_dict()
                with self.assertRaises(ModelError):
                    type(obj).from_dict({**data, "extra": 1})
                with self.assertRaises(ModelError):
                    type(obj).from_dict({k: v for k, v in list(data.items())[1:]})

    def test_from_dict_revalidates(self):
        data = finding().to_dict()
        data["evidence"] = []
        with self.assertRaises(ModelError):
            Finding.from_dict(data)
        data = area().to_dict()
        data["by_name"] = True
        with self.assertRaises(ModelError):
            Area.from_dict(data)

    def test_a_timed_out_analysis_round_trips_without_gaining_a_count(self):
        a = analysis(status="timed_out")
        again = Analysis.from_dict(json.loads(json.dumps(a.to_dict())))
        self.assertEqual(again.status, "timed_out")
        self.assertEqual([r.kind for r in again.blocking()], ["timed_out"])
        with self.assertRaises(ModelError):
            again.finding_count

    def test_from_dict_refuses_null_results_for_a_completed_analysis_or_real_ones_for_a_failed_one(self):
        data = analysis().to_dict()
        with self.assertRaises(ModelError):
            Analysis.from_dict({**data, "findings": None})
        with self.assertRaises(ModelError):
            Analysis.from_dict({**data, "areas": None})
        failed = analysis(status="failed").to_dict()
        with self.assertRaises(ModelError):
            Analysis.from_dict({**failed, "findings": data["findings"]})


class ModuleHygieneTest(unittest.TestCase):
    def test_banned_characters_are_absent_from_the_package_and_these_tests(self):
        banned = "".join(chr(code) for code in (0x2013, 0x2014, 0x2192, 0x2018, 0x2019, 0x201C, 0x201D))
        files = glob.glob(os.path.join(ROOT, "reachmap", "*.py")) + [
            os.path.join(ROOT, "tests", n)
            for n in ("test_model.py", "test_identity.py", "test_wording.py", "test_incomplete.py", "support.py")
        ]
        for path in files:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            for ch in banned:
                self.assertNotIn(ch, text, f"{path} has U+{ord(ch):04X}")


if __name__ == "__main__":
    unittest.main()
