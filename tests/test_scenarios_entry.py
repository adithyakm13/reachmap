"""Screens and routes on the M1 scenarios, beyond what `ss.check` compares with expected.json: the route table of
each adapter against the independent parsers of tests/fixtures/claims.py (`api_routes`, `route_table_of_pages`), the
chains and areas of the scenarios the brief names (s01, s02, s03, s04, s06, s07, s08, s10, s15, s19, s20, s22, s23),
removed and added routes (s10, s22), the records (hub, unmounted router), and that the check itself fails when the
output is wrong.

The scenarios are the same runs test_scenarios_js.py and test_scenarios_py.py check against expected.json
(`ss.run_scenario` caches them per process).
"""

import dataclasses
import unittest

from reachmap import entrypoints
from reachmap.adapters.fastapi import FastApiAdapter
from reachmap.adapters.react_router import ReactRouterAdapter
from reachmap.entrypoints import EntryResult
from tests import scenario_support as ss
from tests import test_scenarios_js, test_scenarios_py
from tests.fixtures import claims

WEB = ["s01", "s02", "s03", "s04", "s05", "s14", "s16", "s19", "s20"]
API = ["s06", "s07", "s08", "s09", "s10", "s13", "s15", "s22", "s23"]


class Scenarios(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.web = {sc["id"].split("-")[0]: sc for sc in ss.scenarios("acme/web")}
        cls.api = {sc["id"].split("-")[0]: sc for sc in ss.scenarios("acme/api")}
        cls.runs = {k: ss.run_scenario(cls.web[k], test_scenarios_js.make_adapter) for k in WEB}
        cls.runs.update({k: ss.run_scenario(cls.api[k], test_scenarios_py.make_adapter) for k in API})
        cls.tree = claims.Tree(ss.env().repos)

    def reached(self, key):
        return self.runs[key].reach.reached

    def labels(self, key):
        return [r.area.label for r in self.reached(key)]

    # ---- the route tables against the independent parsers of the fixtures

    def test_the_api_route_table_is_the_one_the_independent_parser_derives_at_every_head(self):
        repos = ss.env().repos
        checked = 0
        for key in ["main"] + [sc["change"]["head"] for sc in self.api.values()]:
            run = next((r for k, r in self.runs.items() if k in self.api and self.api[k]["change"]["head"] == key), None)
            adapter = run.entry if run is not None else None
            if adapter is None:
                run = self.runs["s06"]
                adapter = run.entry
            sha = ss.sg.rev(repos["acme/api"], key)
            expected, unmounted = claims.api_routes(self.tree, repos, "acme/api", key)
            got = adapter.routes(None, sha)
            with self.subTest(head=key):
                self.assertEqual(
                    sorted((r.method, r.path, r.file, r.handler, tuple(e.detail for e in r.evidence)) for r in got),
                    sorted((r["method"], r["path"], r["file"], r["func"], tuple(r["chain"])) for r in expected),
                )
                self.assertEqual(adapter.unmounted(sha), unmounted)
                checked += len(got)
        self.assertGreater(checked, 40)

    def test_the_screen_table_is_the_one_the_independent_parser_derives_at_every_head(self):
        repos = ss.env().repos
        for key in ["main"] + [sc["change"]["head"] for sc in self.web.values()]:
            run = self.runs["s01"]
            sha = ss.sg.rev(repos["acme/web"], key)
            oracle = claims.route_table_of_pages(self.tree, repos, "acme/web", key)
            got: dict[str, set[str]] = {}
            for r in run.entry.routes(None, sha):
                if r.target:
                    got.setdefault(r.target, set()).add(r.path)
            with self.subTest(head=key):
                # the oracle reads the plain `<Route path element={<Page />} />` shape only: every page it knows is the adapter's
                for page, paths in oracle.items():
                    self.assertEqual(got.get(page), paths)
        screens = {r.path for r in self.runs["s01"].entry.routes(None, ss.sg.rev(repos["acme/web"], "main"))}
        self.assertEqual(screens, {"/billing/invoices", "/billing/invoices/:invoiceId", "/billing/settlements", "/cases", "/cases/:caseId",
                                   "/cases/:caseId/invoices/:invoiceId", "/reports", "/reports/schedule"})  # the lazy page too

    # ---- screens

    def test_s01_reaches_four_screens_one_of_them_also_mounted_with_a_chain_each(self):
        areas = [r.area for r in self.reached("s01")]
        self.assertEqual([(a.label, a.also_mounted, len(a.chains), a.kind, a.module) for a in areas], [
            ("/billing/invoices", (), 1, "screen", "billing"),
            ("/billing/invoices/:invoiceId", ("/cases/:caseId/invoices/:invoiceId",), 2, "screen", "billing"),
            ("/cases", (), 1, "screen", "cases"),
            ("/cases/:caseId", (), 1, "screen", "cases"),
        ])
        also = areas[1]
        self.assertEqual([(e.path, e.line) for e in also.chains[1]], [
            ("src/modules/billing/pages/InvoiceDetailPage.tsx", 4), ("src/modules/cases/routes.tsx", 3), ("src/modules/cases/routes.tsx", 11),
        ])
        self.assertEqual(also.route_line.path, "src/modules/billing/routes.tsx")
        self.assertTrue(all(r.complete for r in self.reached("s01")))
        self.assertEqual({a.priority for a in areas}, {"must_test"})

    def test_s02_walks_nothing_past_the_hub_and_says_so_once(self):
        run = self.runs["s02"]
        self.assertEqual(run.reach.reached, ())
        self.assertEqual([(r.kind, r.subject, r.blocks_selection) for r in run.records], [("hub_cutoff", "src/lib/format.ts", False)])
        self.assertFalse(run.reach.walk.complete)  # the screens that lie past the hub were not established
        self.assertIn("hub", run.reach.walk.reasons[0])

    def test_s03_a_guarded_call_does_not_reach_the_screen_of_the_excluded_page(self):
        self.assertEqual(self.labels("s03"), ["/billing/invoices", "/billing/settlements"])

    def test_s04_named_export_narrowing_is_carried_to_the_screens(self):
        self.assertEqual(self.labels("s04"), ["/cases"])

    def test_s05_the_screen_is_reached_inside_the_repository_and_the_http_edge_is_not_computed(self):
        self.assertEqual(self.labels("s05"), ["/billing/settlements"])

    def test_s19_a_generic_name_reaches_only_the_screen_of_the_file_that_imports_it(self):
        self.assertEqual(self.labels("s19"), ["/cases"])
        self.assertEqual([(r.kind, r.subject) for r in self.runs["s19"].records], [("not_distinctive", "mapStateToProps")])

    def test_s20_a_removed_import_line_reaches_the_screen_of_its_page_only(self):
        (r,) = self.reached("s20")
        self.assertEqual([(e.path, e.line) for e in r.area.chains[0]], [("src/modules/billing/routes.tsx", 4), ("src/modules/billing/routes.tsx", 9)])

    def test_s14_and_s16_reach_no_screen(self):
        self.assertEqual((self.labels("s14"), self.labels("s16")), ([], []))
        self.assertEqual([r.kind for r in self.runs["s16"].records], ["pre_change_unavailable", "routes_unmatched"])  # the orphan tree has no routes file: said, not an empty answer

    # ---- routes

    def test_s06_the_changed_handler_reaches_its_route_composed_over_three_mounts(self):
        (r,) = self.reached("s06")
        a = r.area
        self.assertEqual((a.label, a.kind, a.module, a.also_mounted), ("GET /api/billing/invoices/{invoice_id}", "api_route", "billing", ()))
        self.assertEqual([(e.path.rsplit("/", 1)[-1], e.line) for e in a.chains[0]], [("invoices.py", 40), ("invoices.py", 29), ("billing.py", 7), ("api.py", 6), ("main.py", 6)])
        self.assertEqual(r.routes[0].evidence, a.chains[0][1:])

    def test_s07_a_handler_on_an_unmounted_router_claims_no_route(self):
        run = self.runs["s07"]
        self.assertEqual(run.reach.reached, ())
        (rec,) = [r for r in run.records if r.kind == "router_unmounted"]  # (the walker adds the stated limit of the unfollowed event edge)
        self.assertEqual((rec.kind, rec.subject, rec.blocks_selection), ("router_unmounted", "services/billing/app/routers/legacy_exports.py:legacy_export_router", False))

    def test_s08_the_nested_function_reaches_the_handler_that_holds_it_not_its_sibling(self):
        self.assertEqual(self.labels("s08"), ["GET /api/billing/settlements/{settlement_id}"])

    def test_s10_a_removed_handler_is_a_removed_route_with_evidence_at_the_base(self):
        run = self.runs["s10"]
        c = run.change
        removed, added = run.entry.routes_changed(c.pre_change_sha, c.head_sha)
        self.assertEqual(([r.label for r in removed], added), (["GET /api/billing/audit-log"], []))
        (route,) = removed
        self.assertEqual([(e.path.rsplit("/", 1)[-1], e.line, e.detail, e.ref == c.pre_change_sha) for e in route.evidence], [
            ("billing.py", 14, '@billing_router.get("/audit-log")', True), ("api.py", 6, 'api_router.include_router(billing_router, prefix="/billing")', True),
            ("main.py", 6, 'app.include_router(api_router, prefix="/api")', True),
        ])
        self.assertEqual(ss.evidence_problems(ss.env().repos, [e for e in route.evidence]), [])
        # the removed route is not an area, and an unused import that went with it does not reach the routes under the mount file
        self.assertEqual(self.labels("s10"), [])

    def test_s22_an_added_handler_is_an_added_route_and_its_siblings_are_not_reached(self):
        run = self.runs["s22"]
        c = run.change
        removed, added = run.entry.routes_changed(c.pre_change_sha, c.head_sha)
        self.assertEqual(([r.label for r in removed], [r.label for r in added]), ([], ["GET /api/billing/invoices/{invoice_id}/pdf"]))
        self.assertEqual(self.labels("s22"), ["GET /api/billing/invoices/{invoice_id}/pdf"])

    def test_s15_a_shared_function_reaches_the_routes_of_its_callers_through_a_service_module(self):
        """format_cents is called by a handler (get_invoice), by a service module that a handler calls
        (settlement.summarise, from get_settlement_summary) and by a handler of the other service (get_case); jobs.py
        does not use it."""
        self.assertEqual(self.labels("s15"), ["GET /api/billing/invoices/{invoice_id}", "GET /api/billing/settlements/summary", "GET /api/cases/{case_id}"])
        by = {r.area.label: r for r in self.reached("s15")}
        chain = by["GET /api/billing/settlements/summary"].area.chains[0]
        self.assertEqual([(e.path.rsplit("/", 1)[-1], e.line) for e in chain], [
            ("settlement.py", 1), ("settlements.py", 1), ("settlements.py", 9), ("billing.py", 8), ("api.py", 6), ("main.py", 6),
        ])
        self.assertEqual(by["GET /api/cases/{case_id}"].area.module, "cases")

    def test_s13_s23_s09_reach_what_the_changed_files_reach(self):
        self.assertEqual(self.labels("s13"), ["GET /api/billing/invoices/{invoice_id}"])  # round_total is called by get_invoice
        self.assertEqual((self.labels("s23"), self.labels("s09")), ([], []))

    def test_every_chain_of_every_scenario_is_real_evidence_at_its_pinned_sha(self):
        total = 0
        for key, run in self.runs.items():
            evidence = [e for e in ss.iter_evidence(run)]
            total += len(evidence)
            with self.subTest(scenario=key):
                self.assertEqual(ss.evidence_problems(ss.env().repos, evidence), [])
        self.assertGreater(total, 300)

    def test_the_priority_follows_the_models_rule(self):
        self.assertEqual({r.area.priority for run in self.runs.values() if run.reach is not None for r in run.reach.reached}, {"must_test"})  # none reaches more than 8 screens

    def test_the_entry_adapters_are_the_ones_the_config_selects(self):
        self.assertIsInstance(self.runs["s01"].entry, ReactRouterAdapter)
        self.assertIsInstance(self.runs["s06"].entry, FastApiAdapter)
        self.assertIsInstance(self.runs["s06"].reach, EntryResult)

    # ---- the registry

    def test_the_config_selects_the_adapter_and_a_repository_without_entry_points_has_none(self):
        ws = ss.env().workspace
        run = self.runs["s01"]
        self.assertEqual(run.entry.globs, ("src/modules/*/routes.tsx",))  # routes_glob of the config
        self.assertIsNone(entrypoints.entry_adapter(ws.repos["acme/contracts"], run.adapter))  # no entry_points
        self.assertIsNone(entrypoints.entry_adapter(ws.repos["acme/worker"], self.runs["s06"].adapter))  # celery: no adapter yet
        self.assertEqual(entrypoints.KINDS[:2], ("react-router", "fastapi"))  # the first two; adapters registered after them add to the tuple
        with self.assertRaises(TypeError):
            entrypoints.entry_adapter(ws.repos["acme/web"], self.runs["s06"].adapter)  # a React Router adapter needs the JavaScript adapter
        with self.assertRaises(TypeError):
            entrypoints.entry_adapter(ws.repos["acme/api"], run.adapter)

    # ---- the check is real

    def test_the_harness_fails_when_the_screens_are_wrong(self):
        run = self.runs["s01"]
        self.assertEqual(ss.check(run), [])

        def with_reached(reached):
            return dataclasses.replace(run, reach=dataclasses.replace(run.reach, reached=tuple(reached)))

        base = list(run.reach.reached)
        self.assertTrue(ss.check(with_reached(base[1:])))  # a missing screen
        area = dataclasses.replace(base[1].area, also_mounted=())
        self.assertTrue(ss.check(with_reached([base[0], dataclasses.replace(base[1], area=area)] + base[2:])))  # also_mounted lost
        short = dataclasses.replace(base[0].area, chains=(base[0].area.chains[0][:-1],))
        self.assertTrue(ss.check(with_reached([dataclasses.replace(base[0], area=short)] + base[1:])))  # a chain without its route line
        swapped = dataclasses.replace(base[0].area, chains=(tuple(reversed(base[0].area.chains[0])),))
        self.assertTrue(ss.check(with_reached([dataclasses.replace(base[0], area=swapped)] + base[1:])))  # a chain in the wrong order
        other = dataclasses.replace(base[0].area, label="/reports")
        self.assertTrue(ss.check(with_reached([dataclasses.replace(base[0], area=other)] + base[1:])))  # a screen that must be excluded (and one missing)
        wrong_ev = dataclasses.replace(base[1].area, chains=(base[1].area.chains[0],))
        self.assertTrue(ss.check(with_reached([base[0], dataclasses.replace(base[1], area=wrong_ev)] + base[2:])))  # the also_mounted evidence lost

    def test_the_harness_fails_when_the_completeness_is_wrong(self):
        for key, runs in (("s01", self.runs), ("s02", self.runs), ("s07", self.runs)):
            run = runs[key]
            self.assertEqual(ss.check(run, not_established=ss.PY_NOT_ESTABLISHED if key == "s07" else None), [], key)
            flipped = dataclasses.replace(run, reach=dataclasses.replace(run.reach, complete=not run.reach.complete))
            self.assertTrue(ss.check(flipped, not_established=ss.PY_NOT_ESTABLISHED if key == "s07" else None), key)
        self.assertEqual((self.runs["s01"].reach.complete, self.runs["s02"].reach.complete), (True, False))  # s02 stops at a hub: the screens past it are not "none"

    def test_the_harness_fails_when_the_routes_are_wrong(self):
        run = self.runs["s06"]
        self.assertEqual(ss.check(run, not_established=ss.PY_NOT_ESTABLISHED), [])

        def with_reached(reached):
            return dataclasses.replace(run, reach=dataclasses.replace(run.reach, reached=tuple(reached)))

        (r,) = run.reach.reached
        self.assertTrue(ss.check(with_reached([]), not_established=ss.PY_NOT_ESTABLISHED))  # the route is missing
        route = dataclasses.replace(r.routes[0], evidence=r.routes[0].evidence[:-1])
        self.assertTrue(ss.check(with_reached([dataclasses.replace(r, routes=(route,))]), not_established=ss.PY_NOT_ESTABLISHED))  # a mount line missing
        route = dataclasses.replace(r.routes[0], path="/api/billing/invoices")
        self.assertTrue(ss.check(with_reached([dataclasses.replace(r, routes=(route,))]), not_established=ss.PY_NOT_ESTABLISHED))  # another route
        extra = dataclasses.replace(r, routes=(dataclasses.replace(r.routes[0], path="/api/x"),))
        extra_area = dataclasses.replace(r.area, label="GET /api/x")
        self.assertTrue(ss.check(with_reached([r, dataclasses.replace(extra, area=extra_area)]), not_established=ss.PY_NOT_ESTABLISHED))  # an extra route (complete_for)
        s07 = self.runs["s07"]
        wrong = dataclasses.replace(s07, reach=dataclasses.replace(s07.reach, reached=(r,)))
        self.assertTrue(ss.check(wrong, not_established=ss.PY_NOT_ESTABLISHED))  # a route that must be excluded


if __name__ == "__main__":
    unittest.main()
