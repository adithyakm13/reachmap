"""Carrying decisions forward (M11): for each latest decision of a key, against a new analysis, the target is still there with the same
fingerprint (current), there with another (stale, with what changed in its evidence when the earlier report is at hand) or gone
(orphaned: listed, never dropped). Targets with no decision are undecided. Nothing a decision says changes the analysis."""

import copy
import json
import unittest

from reachmap import identity
from reachmap.model import Area, Decision, Evidence, Finding
from reachmap.review import carry, store

SHA1, SHA2 = "1" * 40, "2" * 40
KEY = store.make_key("acme", "acme/web", "compare:main...topic")


def ev(path, line, detail, sha=SHA1, repo="acme/web"):
    return Evidence(repo, path, line, sha, detail)


def area(label, details, sha=SHA1, lines=(3, 4, 9)):
    chain = [ev(f"src/{label.strip(chr(47))}.tsx", n, d, sha) for n, d in zip(lines, details)]
    return Area(label, "screen", "acme/web", "src/routes.tsx", chain[-1], [chain], [], "must_test", False)


def finding(label, details, sha=SHA1, lines=(3, 4, 9)):
    chain = [ev(f"src/{label.strip(chr(47))}.tsx", n, d, sha) for n, d in zip(lines, details)]
    return Finding("direct", f"The change reaches the screen {label}", "", "medium", "high", "import-backed", chain, ("direct", "acme/web", "screen", label))


def report(areas, findings, revision=1, sha=SHA1):
    """The parts of a report that carry reads: the areas and findings as `to_dict` writes them, each with its id and fingerprint."""
    return {"areas": [a.to_dict() for a in areas], "findings": [f.to_dict() for f in findings], "ref": sha, "review": {"revision": revision}}


def deci(target, verdict=None, reason="", fingerprint=None, revision=1, author="Quinn", channel="cli"):
    verdict = verdict or ("test" if target.startswith("a-") else "accept")
    return store.Stored(Decision(target, verdict, reason, author, channel, fingerprint, revision), "2026-01-02T03:04:05Z")


def loaded(*items):
    return store.Loaded(tuple(items), None)


D = ["import A", "import page", 'route "/x"']
D2 = ["import A", 'import page from "../other"', 'route "/x"']


class Targets(unittest.TestCase):
    def test_every_area_and_finding_carries_its_id_and_fingerprint_in_its_own_json_in_report_order(self):
        a1, a2 = area("/one", D), area("/two", D)
        f1 = finding("/one", D)
        rep = report([a1, a2], [f1])
        self.assertEqual([(x["id"], x["fingerprint"]) for x in rep["areas"]], [(a1.id, identity.fingerprint(a1)), (a2.id, identity.fingerprint(a2))])
        self.assertEqual([(x["id"], x["fingerprint"]) for x in rep["findings"]], [(f1.id, identity.fingerprint(f1))])

    def test_the_fingerprint_is_the_models_so_a_moved_line_and_another_commit_do_not_change_it(self):
        self.assertEqual(identity.fingerprint(area("/x", D)), identity.fingerprint(area("/x", D, sha=SHA2, lines=(30, 40, 90))))

    def test_targets_are_read_from_a_report_with_their_labels_and_evidence(self):
        a, f = area("/one", D), finding("/one", D)
        got = carry.targets_of(report([a], [f]))
        self.assertEqual(list(got), [a.id, f.id])
        self.assertEqual((got[a.id].kind, got[a.id].label, got[a.id].fingerprint), ("area", "/one", identity.fingerprint(a)))
        self.assertEqual(got[f.id].kind, "finding")
        self.assertEqual(got[f.id].label, "The change reaches the screen /one")
        self.assertIn(("acme/web", "src/one.tsx", "import page"), got[f.id].triples)

    def test_a_report_without_ids_and_fingerprints_or_with_ones_that_are_not_are_refused(self):
        good = report([area("/one", D)], [finding("/one", D)])
        broken = []
        for name in ("id", "fingerprint"):
            for part in ("areas", "findings"):
                rep = copy.deepcopy(good)
                del rep[part][0][name]
                broken.append(rep)
        for part, name, value in (("areas", "id", "a-nope"), ("areas", "id", "f-" + "1" * 16), ("findings", "id", "a-" + "1" * 16), ("findings", "fingerprint", "abc"), ("areas", "fingerprint", 5)):
            rep = copy.deepcopy(good)
            rep[part][0][name] = value
            broken.append(rep)
        broken += [{**good, "areas": "x"}, {**good, "areas": [5], "findings": []}, {**good, "findings": None}]
        twice = copy.deepcopy(good)
        twice["areas"].append(twice["areas"][0])
        broken.append(twice)
        for rep in broken:
            with self.subTest(str(rep)[:60]):
                with self.assertRaises(carry.ReviewError):
                    carry.targets_of(rep)


class Carrying(unittest.TestCase):
    def setUp(self):
        self.a1, self.a2, self.a3 = area("/one", D), area("/two", D), area("/three", D)
        self.f1 = finding("/one", D)
        self.old = report([self.a1, self.a2, self.a3], [self.f1])

    def fp(self, item):
        return identity.fingerprint(item)

    def test_the_same_fingerprint_is_current(self):
        d = carry.carry(self.old, loaded(deci(self.a1.id, fingerprint=self.fp(self.a1))))
        self.assertEqual([x["target_id"] for x in d["current"]], [self.a1.id])
        self.assertEqual((d["stale"], d["orphaned"]), ([], []))

    def test_another_fingerprint_is_stale_and_says_both_fingerprints(self):
        moved = area("/one", D2)
        new = report([moved, self.a2], [self.f1], revision=2)
        d = carry.carry(new, loaded(deci(self.a1.id, fingerprint=self.fp(self.a1))))
        (x,) = d["stale"]
        self.assertEqual((x["target_id"], x["fingerprint"], x["now_fingerprint"]), (self.a1.id, self.fp(self.a1), self.fp(moved)))
        self.assertEqual((d["current"], d["orphaned"]), ([], []))

    def test_a_target_that_is_gone_is_orphaned_and_listed_with_what_the_decision_said(self):
        new = report([self.a2], [], revision=2)
        d = carry.carry(new, loaded(deci(self.a1.id, "skip", "not used any more", fingerprint=self.fp(self.a1)), deci(self.f1.id, "reject", "wrong", fingerprint=self.fp(self.f1))))
        self.assertEqual([x["target_id"] for x in d["orphaned"]], [self.a1.id, self.f1.id])
        self.assertEqual((d["orphaned"][0]["verdict"], d["orphaned"][0]["reason"], d["orphaned"][0]["author"]), ("skip", "not used any more", "Quinn"))
        self.assertEqual((d["current"], d["stale"]), ([], []))

    def test_orphans_are_listed_in_the_order_of_the_decisions_in_force(self):
        new = report([], [], revision=2)
        d = carry.carry(new, loaded(deci(self.a1.id, fingerprint="0" * 64), deci(self.a2.id, fingerprint="0" * 64), deci(self.a1.id, "skip", "again", fingerprint="0" * 64)))
        self.assertEqual([x["target_id"] for x in d["orphaned"]], [self.a2.id, self.a1.id])

    def test_the_decision_in_force_is_the_last_written_one(self):
        d = carry.carry(self.old, loaded(
            deci(self.a1.id, "test", fingerprint="0" * 64), deci(self.a1.id, "skip", "later", fingerprint=self.fp(self.a1)), deci(self.a2.id, fingerprint=self.fp(self.a2)),
        ))
        by = {x["target_id"]: x for x in d["current"]}
        self.assertEqual(by[self.a1.id]["verdict"], "skip")
        self.assertEqual(d["stale"], [])  # the earlier, stale one was superseded

    def test_targets_with_no_decision_are_undecided_areas_and_findings_apart(self):
        d = carry.carry(self.old, loaded(deci(self.a1.id, fingerprint=self.fp(self.a1))))
        self.assertEqual(d["undecided"], {"areas": [self.a2.id, self.a3.id], "findings": [self.f1.id]})

    def test_a_stale_decision_is_not_undecided_it_needs_review(self):
        new = report([area("/one", D2)], [self.f1], revision=2)
        d = carry.carry(new, loaded(deci(self.a1.id, fingerprint=self.fp(self.a1))))
        self.assertEqual(len(d["stale"]), 1)
        self.assertNotIn(self.a1.id, d["undecided"]["areas"])

    def test_current_and_stale_follow_the_order_of_the_report_areas_then_findings(self):
        decisions = [deci(t.id, fingerprint=self.fp(t)) for t in (self.f1, self.a3, self.a1, self.a2)]
        d = carry.carry(self.old, loaded(*decisions))
        self.assertEqual([x["target_id"] for x in d["current"]], [self.a1.id, self.a2.id, self.a3.id, self.f1.id])

    def test_an_entry_has_the_decision_the_kind_the_label_and_the_time(self):
        d = carry.carry(self.old, loaded(deci(self.f1.id, "reject", "not our service", fingerprint=self.fp(self.f1), revision=1, channel="html-import")))
        (x,) = d["current"]
        self.assertEqual(
            x, {"target_id": self.f1.id, "kind": "finding", "label": "The change reaches the screen /one", "verdict": "reject", "reason": "not our service",
                "author": "Quinn", "channel": "html-import", "fingerprint": self.fp(self.f1), "revision": 1, "created_at": "2026-01-02T03:04:05Z"})

    def test_what_changed_is_shown_when_the_earlier_report_is_at_hand_and_it_is_the_one_the_person_saw(self):
        moved = area("/one", D2)
        new = report([moved], [], revision=2)
        old_targets = carry.targets_of(self.old)
        d = carry.carry(new, loaded(deci(self.a1.id, fingerprint=self.fp(self.a1))), earlier=lambda n: old_targets if n == 1 else None)
        (x,) = d["stale"]
        self.assertEqual(x["changes"]["removed"], [{"repo": "acme/web", "path": "src/one.tsx", "detail": "import page"}])
        self.assertEqual(x["changes"]["added"], [{"repo": "acme/web", "path": "src/one.tsx", "detail": 'import page from "../other"'}])

    def test_without_the_earlier_report_or_with_a_different_one_there_is_no_account_of_what_changed(self):
        moved = area("/one", D2)
        new = report([moved], [], revision=2)
        other = carry.targets_of(report([area("/one", ["x", "y", "z"])], []))  # not what the decision was made on
        for earlier in (None, lambda n: None, lambda n: other, lambda n: {}):
            with self.subTest(earlier):
                (x,) = carry.carry(new, loaded(deci(self.a1.id, fingerprint=self.fp(self.a1))), earlier=earlier)["stale"]
                self.assertIsNone(x["changes"])

    def test_an_orphan_gets_its_label_from_the_earlier_report_when_there_is_one(self):
        new = report([self.a2], [], revision=2)
        old_targets = carry.targets_of(self.old)
        (x,) = carry.carry(new, loaded(deci(self.a1.id, fingerprint=self.fp(self.a1))), earlier=lambda n: old_targets)["orphaned"]
        self.assertEqual(x["label"], "/one")
        (y,) = carry.carry(new, loaded(deci(self.a1.id, fingerprint=self.fp(self.a1))))["orphaned"]
        self.assertIsNone(y["label"])

    def test_a_decision_made_on_a_later_revision_than_this_report_is_still_judged_against_this_reports_claim(self):
        d = carry.carry(self.old, loaded(deci(self.a1.id, fingerprint=self.fp(self.a1), revision=5)))
        self.assertEqual(len(d["current"]), 1)

    def test_an_unreadable_store_gives_no_decision_at_all_and_the_blocking_record(self):
        problem = store.Problem(3, "the line is not JSON (a last line may be unfinished)")
        d = carry.carry(self.old, store.Loaded((), problem), key=KEY)
        self.assertEqual((d["current"], d["stale"], d["orphaned"]), ([], [], []))
        self.assertEqual(d["undecided"], {"areas": [], "findings": []})
        (rec,) = d["records"]
        self.assertEqual(rec["kind"], "decision_store_unreadable")
        self.assertTrue(rec["blocks_selection"])
        self.assertIn("line 3", rec["detail"])

    def test_the_result_is_plain_json(self):
        d = carry.carry(self.old, loaded(deci(self.a1.id, fingerprint=self.fp(self.a1))))
        self.assertEqual(json.loads(json.dumps(d)), d)
        self.assertEqual(set(d), {"current", "stale", "orphaned", "undecided", "records"})


class NeverChangesTheAnalysis(unittest.TestCase):
    def test_carrying_does_not_touch_the_report_it_is_given(self):
        a = area("/one", D)
        rep = report([a], [finding("/one", D)])
        before = copy.deepcopy(rep)
        carry.carry(rep, loaded(deci(a.id, "skip", "accepted", fingerprint=identity.fingerprint(a))))
        self.assertEqual(rep, before)

    def test_the_module_cannot_reach_priority_risk_or_confidence_of_anything(self):
        import inspect

        source = inspect.getsource(carry)
        for word in ("priority =", ".priority", "risk =", "confidence =", "must_test", "coverage_gaps", "finding_count"):
            self.assertNotIn(word, source, word)


if __name__ == "__main__":
    unittest.main()
