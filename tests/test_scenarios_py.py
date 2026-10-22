"""The api and contracts scenarios (M1): the Python adapter's real output, and the routes the walk and the FastAPI adapter
reach from it, against expected.json for the types M6 and M7 implement. Types they do not implement, and entries that are the answer of
another repository's adapter, are listed per scenario below and the list is pinned: a scenario cannot start
skipping something silently. The importer sets are cross-checked with the independent Python import parser of
tests/fixtures/claims.py.
"""

import dataclasses
import unittest

from reachmap.adapters import ChangeAnalysis
from reachmap.adapters.python import PyAdapter
from tests import scenario_support as ss

API_SCENARIOS = ["s06", "s07", "s08", "s09", "s10", "s13", "s15", "s22", "s23"]
CONTRACTS_SCENARIOS = ["s11", "s12", "s17", "s18", "s21"]
ALL = API_SCENARIOS + CONTRACTS_SCENARIOS

# What is not checked, per scenario: nothing (the branch states of s13 are the branch matrix of M9). M7 checks the routes and the
# `router_unmounted` record, M8 the http edges, the screens of the web repository, the contract reads and consumers, the
# findings and `hop_cap` (the cross-repository walker that `ss.run_scenario` runs): entries about another repository
# (the web screens of the api scenarios, the unit test of the api in s11) are answered by it too.
SKIPPED = {key: [] for key in ("s06", "s07", "s08", "s09", "s10", "s15", "s22", "s23", "s11", "s12", "s17", "s18", "s21")}
SKIPPED["s13"] = []


def make_adapter(repo_cfg, git):
    return PyAdapter.from_config(repo_cfg, git, limits=ss.env().workspace.limits)


class PythonScenarios(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenarios = {}
        for repo in ("acme/api", "acme/contracts", "acme/worker"):
            cls.scenarios.update({sc["id"].split("-")[0]: sc for sc in ss.scenarios(repo)})
        cls.runs = {key: ss.run_scenario(cls.scenarios[key], make_adapter) for key in ALL}

    def test_every_python_scenario_is_covered(self):
        python = {k for k, sc in self.scenarios.items() if sc["repo"] in ("acme/api", "acme/contracts", "acme/worker")}
        self.assertEqual(sorted(python), sorted(ALL))

    def test_the_adapter_output_matches_expected_json(self):
        for key in ALL:
            with self.subTest(scenario=self.scenarios[key]["id"]):
                self.assertEqual(ss.check(self.runs[key], not_established=ss.PY_NOT_ESTABLISHED), [])

    def test_the_skipped_types_are_listed_and_pinned(self):
        for key in ALL:
            sc = self.scenarios[key]
            with self.subTest(scenario=key):
                self.assertEqual(ss.skipped_types(sc["expected"], ss.PY_TYPES, ss.PY_NOT_ESTABLISHED, repo=sc["repo"]), SKIPPED[key])

    def test_implemented_and_skipped_types_cover_every_type_in_the_python_scenarios(self):
        seen = set()
        for key in ALL:
            for section in ("must_find", "must_exclude"):
                seen.update(e["type"] for e in self.scenarios[key]["expected"][section])
        skipped = {tag.split(":")[1].split("@")[0] for tags in SKIPPED.values() for tag in tags if not tag.startswith("not_established")}
        self.assertEqual(seen - ss.PY_TYPES, skipped - ss.PY_TYPES)  # every type that is seen and not implemented is on the skip list
        self.assertLessEqual(skipped, seen)
        self.assertEqual(ss.PY_TYPES - seen, set())  # every implemented type is exercised by an api or contracts scenario

    def test_every_evidence_the_scenarios_produced_resolves_to_its_line(self):
        total = 0
        for key in ALL:
            run = self.runs[key]
            evidence = list(ss.iter_evidence(run))
            total += len(evidence)
            with self.subTest(scenario=key, evidence=len(evidence)):
                self.assertEqual(ss.evidence_problems(ss.env().repos, evidence), [])
        self.assertGreater(total, 40)  # the helper is looking at real evidence

    def test_the_importer_sets_agree_with_the_independent_parser_of_the_fixtures(self):
        """claims.direct_importers resolves every import with its own small parser (a module named in a
        `from` or `import` line, from the importer's service folder, libs/*, or the repo root); the adapter must
        find exactly the same files for every changed source file whose importers it searched."""
        checked = 0
        for key in API_SCENARIOS:
            run = self.runs[key]
            for impact in run.analysis.impacts:
                if impact.role != "source" or not impact.path.endswith(".py") or impact.status == "deleted" or not impact.importers_complete:
                    continue
                if not impact.symbols:
                    continue  # nothing changed in code: the importers were not searched
                with self.subTest(scenario=key, path=impact.path):
                    self.assertEqual({e.importer_path for e in impact.importers}, ss.oracle_importers(run, impact.path))
                    checked += 1
        self.assertGreaterEqual(checked, 8)

    def test_s15_finds_the_three_callers_by_three_import_forms_and_excludes_the_fourth(self):
        run = self.runs["s15"]
        (impact,) = [i for i in run.analysis.impacts if i.path == "libs/common/acme_common/money.py"]
        forms = {c.path: sorted({e.specifier for e in impact.importers if e.importer_path == c.path}) for c in impact.callers}
        self.assertEqual(sorted(forms), ["services/billing/app/routers/invoices.py", "services/billing/app/services/settlement.py", "services/cases/app/routers/cases.py"])
        self.assertEqual([x.subject_path for x in impact.exclusions], ["services/billing/app/jobs.py"])
        self.assertEqual(sorted(t.path for t in impact.unit_tests), ["services/billing/tests/test_money.py", "services/billing/tests/test_settlement.py"])
        self.assertTrue(impact.importers_complete and impact.narrowed)
        self.assertEqual([r.kind for r in run.records], ["edge_unfollowed"])  # the stated limit of the walker: the event edge of the api

    def test_s08_reports_the_nested_function_under_its_outer_one_and_not_the_sibling(self):
        run = self.runs["s08"]
        syms = [(i.path, s.name, s.nested) for i in run.analysis.impacts for s in i.symbols]
        self.assertEqual(syms, [("services/billing/app/routers/settlements.py", "get_settlement", ("_load",))])

    def test_s09_attributes_the_decorator_argument_to_the_decorated_function(self):
        run = self.runs["s09"]
        (s,) = [s for i in run.analysis.impacts for s in i.symbols]
        self.assertEqual((s.name, s.start, s.end, [e.detail for e in s.evidence]), ("run_nightly_settlement", 22, 30, ["    retries=5,", "    retries=3,"]))

    def test_s10_is_a_removal_with_the_import_line_as_a_module_level_change(self):
        run = self.runs["s10"]
        syms = sorted((s.name, s.change, s.side) for i in run.analysis.impacts for s in i.symbols)
        self.assertEqual(syms, [("<module>", "modified", "after"), ("audit_log", "removed", "before")])

    def test_s11_to_s21_name_a_model_field_as_the_class_and_its_field(self):
        got = {key: sorted((s.name, s.export_names, s.change) for i in self.runs[key].analysis.impacts for s in i.symbols) for key in CONTRACTS_SCENARIOS}
        self.assertEqual(got, {
            "s11": [("Invoice.settlement_reference", ("Invoice",), "modified")],
            "s12": [("Settlement.interval", ("Settlement",), "modified")],
            "s17": [("Invoice.due_date", ("Invoice",), "modified")],
            "s18": [("Schedule.limit", ("Schedule",), "modified")],
            "s21": [("Invoice.settlement_reference", ("Invoice",), "modified")],
        })

    def test_s12_counts_five_definitions_of_interval_and_records_the_stated_limit(self):
        run = self.runs["s12"]
        self.assertEqual([(r.kind, r.subject, r.blocks_selection) for r in run.records], [("not_distinctive", "interval", False)])
        self.assertEqual(run.adapter.definitions("interval", run.change.head_sha).count, 5)
        # settlement_reference is defined in two models: distinctive (distinct_max = 3), s11
        s11 = self.runs["s11"]
        self.assertEqual(s11.adapter.definitions("settlement_reference", s11.change.head_sha).count, 2)
        self.assertEqual([r.kind for r in s11.records], ["edge_unfollowed", "entry_points_unread"])  # the stated limits of the walker, and nothing else
        s18 = self.runs["s18"]
        self.assertEqual(s18.adapter.definitions("limit", s18.change.head_sha).count, 1)

    def test_s23_markers_match_by_segment(self):
        run = self.runs["s23"]
        roles = {(f.path, f.role) for f in run.analysis.files}
        self.assertEqual(roles, {
            ("services/billing/app/contests/replay.py", "source"), ("services/billing/app/latest_rates.py", "source"), ("services/billing/tests/test_money.py", "test"),
        })
        self.assertTrue(all(i.callers == () for i in run.analysis.impacts))

    def test_s22_places_the_added_handler_only(self):
        run = self.runs["s22"]
        (s,) = [s for i in run.analysis.impacts for s in i.symbols]
        self.assertEqual((s.name, s.change), ("get_invoice_pdf", "added"))

    def test_s13_keeps_the_module_level_import_change_and_the_function(self):
        run = self.runs["s13"]
        (impact,) = run.analysis.impacts
        self.assertEqual(sorted(s.name for s in impact.symbols), ["<module>", "round_total"])
        self.assertFalse(impact.narrowed)

    def test_the_harness_fails_when_the_adapter_output_is_wrong(self):
        """The check is real: drop or add one item of each implemented type and it fails."""
        run = self.runs["s15"]
        (impact,) = [i for i in run.analysis.impacts if i.path == "libs/common/acme_common/money.py"]

        def with_impact(**changes):
            changed = dataclasses.replace(impact, **changes)
            analysis = ChangeAnalysis(run.analysis.repo, run.analysis.change, run.analysis.files, (changed,))
            return dataclasses.replace(run, analysis=analysis)

        def check(r):
            return ss.check(r, not_established=ss.PY_NOT_ESTABLISHED)

        self.assertEqual(check(run), [])
        self.assertTrue(check(with_impact(callers=impact.callers[1:])))                  # a missing caller
        self.assertTrue(check(with_impact(unit_tests=())))                               # a missing unit test
        self.assertTrue(check(with_impact(symbols=())))                                  # a missing changed symbol
        self.assertTrue(check(with_impact(exclusions=())))                               # a missing exclusion (jobs.py with its import line)
        extra = dataclasses.replace(impact.callers[0], path="services/billing/app/jobs.py")
        self.assertTrue(check(with_impact(callers=impact.callers + (extra,))))           # an extra caller (complete_for)
        wrong_ev = dataclasses.replace(impact.callers[0], evidence=())
        self.assertTrue(check(with_impact(callers=(wrong_ev,) + impact.callers[1:])))    # a caller without its evidence
        tests = [t for t in impact.unit_tests if t.path == "services/billing/tests/test_money.py"]
        other_via = [dataclasses.replace(t, via="services/billing/app/jobs.py") for t in tests]
        rest = tuple(t for t in impact.unit_tests if t not in tests)
        self.assertTrue(check(with_impact(unit_tests=tuple(other_via) + rest)))          # a unit test of another file than its import line names
        as_caller = dataclasses.replace(impact.callers[0], path="services/billing/tests/test_money.py")
        self.assertTrue(check(with_impact(callers=impact.callers + (as_caller,))))       # a test listed as a caller

    def test_s13_the_branch_matrix_says_which_branches_have_the_fix(self):
        run = self.runs["s13"]
        self.assertEqual(ss.check(run, not_established=ss.PY_NOT_ESTABLISHED), [])
        rows = {r.branch: r for r in run.branches.rows}
        self.assertEqual({b: r.state for b, r in rows.items()}, {"main": "pre_change", "release/1.x": "present", "release/1.2": "present"})
        self.assertEqual(rows["release/1.2"].added_lines_found, 2)  # both added lines are at the tip of the older line
        self.assertEqual(run.branches.rows[0].branch, "main")  # the primary branch first

    def test_the_harness_fails_when_the_branch_matrix_is_wrong(self):
        run = self.runs["s13"]
        matrix = run.branches
        rows = matrix.rows

        def with_rows(new):
            return dataclasses.replace(run, branches=dataclasses.replace(matrix, rows=tuple(new)))

        def check(r):
            return ss.check(r, not_established=ss.PY_NOT_ESTABLISHED)

        self.assertTrue(check(with_rows(rows[1:])))  # main missing
        self.assertTrue(check(with_rows([r for r in rows if r.branch != "release/1.2"])))
        pre = [r for r in rows if r.branch == "main"][0]
        wrong = dataclasses.replace(pre, state="diverged")
        self.assertTrue(check(with_rows([wrong] + [r for r in rows if r.branch != "main"])))  # the wrong state
        extra = dataclasses.replace(pre, branch="release/9")
        self.assertTrue(check(with_rows(list(rows) + [extra])))  # a branch the scenario does not list (complete_for)
        moved = dataclasses.replace(pre, commit="1" * 40)
        self.assertTrue(check(with_rows([moved] + [r for r in rows if r.branch != "main"])))  # read at another commit than the tip
        self.assertTrue(check(dataclasses.replace(run, branches=None)))  # no matrix at all

    def test_a_foreign_repository_entry_is_checked_by_the_propagation(self):
        """The unit test of the api in the contracts scenario s11 is the answer of the api's pass, reached across the edge: it is
        checked, and the check fails when the propagation does not have it."""
        run = self.runs["s11"]
        self.assertEqual(ss.skipped_types(run.scenario["expected"], ss.PY_TYPES, ss.PY_NOT_ESTABLISHED, repo=run.scenario["repo"]), [])
        self.assertEqual(ss.check(run), [])
        prop = run.propagation
        self.assertEqual([(t.repo, t.path) for t in prop.unit_tests], [("acme/api", "services/billing/tests/test_settlement.py")])
        lost = dataclasses.replace(run, propagation=dataclasses.replace(prop, unit_tests=()))
        self.assertTrue(any("unit_test" in f for f in ss.check(lost)))


if __name__ == "__main__":
    unittest.main()
