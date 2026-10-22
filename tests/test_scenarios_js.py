"""The web scenarios (M1): the JavaScript adapter's real output, and the screens the walk and the React Router
adapter reach from it, against expected.json for the types M5 and M7 implement. Types they do not implement are
listed per scenario below and the list is pinned: a scenario cannot start skipping a type silently."""

import dataclasses
import unittest

from reachmap.adapters import ChangeAnalysis
from reachmap.adapters.js import JsAdapter
from tests import scenario_support as ss

WEB_SCENARIOS = ["s01", "s02", "s03", "s04", "s05", "s14", "s16", "s19", "s20"]

# What is not checked, per scenario. M7 checks the screens (the walk and the React Router adapter) and M8 the http edge of
# s05 and the finding s16 must not have (the cross-repository walker, run by `ss.run_scenario`): nothing is left.
SKIPPED = {key: [] for key in ("s01", "s02", "s03", "s04", "s05", "s14", "s16", "s19", "s20")}


def make_adapter(repo_cfg, git):
    return JsAdapter.from_config(repo_cfg, git, limits=ss.env().workspace.limits)


class WebScenarios(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenarios = {sc["id"].split("-")[0]: sc for sc in ss.scenarios("acme/web")}
        cls.runs = {key: ss.run_scenario(cls.scenarios[key], make_adapter) for key in WEB_SCENARIOS}

    def test_every_web_scenario_is_covered(self):
        self.assertEqual(sorted(self.scenarios), sorted(WEB_SCENARIOS))

    def test_the_adapter_output_matches_expected_json(self):
        for key in WEB_SCENARIOS:
            with self.subTest(scenario=self.scenarios[key]["id"]):
                self.assertEqual(ss.check(self.runs[key]), [])

    def test_the_skipped_types_are_listed_and_pinned(self):
        for key in WEB_SCENARIOS:
            with self.subTest(scenario=key):
                self.assertEqual(ss.skipped_types(self.scenarios[key]["expected"]), SKIPPED[key])

    def test_implemented_and_skipped_types_cover_every_type_in_the_web_scenarios(self):
        seen = set()
        for sc in self.scenarios.values():
            for section in ("must_find", "must_exclude"):
                seen.update(e["type"] for e in sc["expected"][section])
        skipped = {tag.split(":")[1] for tags in SKIPPED.values() for tag in tags}
        self.assertEqual(seen - ss.JS_TYPES, skipped)  # every type that is seen and not implemented is on the skip list, and only those
        # every implemented type is exercised by a web scenario, except what the web repository does not have (a route) and
        # what only a contract change brings (the consumers and the reads of a changed field) or a fix on release branches (a branch state)
        self.assertEqual(ss.JS_TYPES - seen, {"route", "consumer", "contract_read", "branch_state"})

    def test_every_evidence_the_scenarios_produced_resolves_to_its_line(self):
        for key in WEB_SCENARIOS:
            run = self.runs[key]
            evidence = list(ss.iter_evidence(run))
            with self.subTest(scenario=key, evidence=len(evidence)):
                self.assertEqual(ss.evidence_problems(ss.env().repos, evidence), [])
        total = sum(len(list(ss.iter_evidence(r))) for r in self.runs.values())
        self.assertGreater(total, 100)  # the helper is looking at real evidence

    def test_the_importer_sets_agree_with_the_independent_parser_of_the_fixtures(self):
        """claims.direct_importers resolves every specifier with its own small parser; the adapter must
        find exactly the same files for every changed source file whose importers it searched."""
        checked = 0
        for key in WEB_SCENARIOS:
            run = self.runs[key]
            for impact in run.analysis.impacts:
                if impact.role != "source" or not impact.path.endswith((".ts", ".tsx")) or impact.status == "deleted" or not impact.importers_complete:
                    continue
                if not impact.symbols:
                    continue  # nothing changed in code: the importers were not searched
                with self.subTest(scenario=key, path=impact.path):
                    self.assertEqual({e.importer_path for e in impact.importers}, ss.oracle_importers(run, impact.path))
                    checked += 1
        self.assertGreaterEqual(checked, 8)

    def test_the_analysis_of_s02_found_all_57_importers_by_both_specifiers(self):
        run = self.runs["s02"]
        (impact,) = [i for i in run.analysis.impacts if i.path == "src/lib/format.ts"]
        specs = {e.specifier for e in impact.importers}
        self.assertEqual(impact.importer_count, 57)
        self.assertEqual(len({e.importer_path for e in impact.importers}), 57)
        self.assertEqual({"@/lib/format", "../lib/format"} - specs, set())
        self.assertTrue(impact.hub)
        self.assertEqual(impact.callers, ())

    def test_the_harness_fails_when_the_adapter_output_is_wrong(self):
        """The check is real: drop or add one item of each implemented type and it fails."""
        run = self.runs["s04"]
        (impact,) = [i for i in run.analysis.impacts if i.path == "src/lib/dates.ts"]

        def with_impact(**changes):
            changed = dataclasses.replace(impact, **changes)
            analysis = ChangeAnalysis(run.analysis.repo, run.analysis.change, run.analysis.files, (changed,))
            return dataclasses.replace(run, analysis=analysis)

        self.assertEqual(ss.check(run), [])
        self.assertTrue(ss.check(with_impact(callers=())))                       # a missing caller
        self.assertTrue(ss.check(with_impact(callers=impact.callers * 2, exclusions=())))  # a missing exclusion
        self.assertTrue(ss.check(with_impact(unit_tests=())))                    # a missing unit test
        self.assertTrue(ss.check(with_impact(symbols=())))                       # a missing changed symbol
        extra = dataclasses.replace(impact.callers[0], path="src/lib/other.ts")
        self.assertTrue(ss.check(with_impact(callers=impact.callers + (extra,))))  # an extra caller (complete_for)
        wrong_ev = dataclasses.replace(impact.callers[0], evidence=())
        self.assertTrue(ss.check(with_impact(callers=(wrong_ev,))))               # a caller without its evidence

    def test_s16_computes_no_removal_and_no_narrowing(self):
        run = self.runs["s16"]
        self.assertIsNone(run.change.pre_change_sha)
        self.assertEqual([r.kind for r in run.records], ["pre_change_unavailable", "routes_unmatched"])  # the change set record, and the orphan tree has no routes file: no second record of either
        for impact in run.analysis.impacts:
            self.assertTrue(all(s.change != "removed" for s in impact.symbols))
            self.assertEqual(impact.exclusions, ())
            self.assertFalse(impact.narrowed)


if __name__ == "__main__":
    unittest.main()
