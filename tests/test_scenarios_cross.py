"""The cross-repository scenarios (M8): the walker's real output against expected.json for `http_edge`, `consumer`,
`contract_read`, `finding`, the screens and routes of other repositories, `hop_cap` and the complete `not_established`
lists, for s05, s06, s10, s11, s12, s17, s18, s21 and s22 (and the scenarios that must stay quiet), with every
claim cross-checked against the independent oracles of tests/fixtures/claims.py: the URL fit, the contract reads, the
consumers and their ancestry. The runs are the ones the language-level and entry-point modules check
(`ss.run_scenario` runs the walker after the in-repo pass and caches the result per process).
"""

import dataclasses
import re
import unittest

from reachmap.adapters.http_client import url_from_parts
from reachmap.edges.http import best_matches
from reachmap.model import Evidence
from tests import scenario_support as ss
from tests import support_git as sg
from tests import test_scenarios_js, test_scenarios_py
from tests.fixtures import build, claims

CROSS = ["s05", "s06", "s08", "s10", "s11", "s12", "s17", "s18", "s21", "s22"]
QUIET = ["s07", "s09", "s16"]  # nothing crosses: an unmounted router, a job, an orphan branch


def make_adapter(repo_cfg, git):
    if repo_cfg.language == "python":
        return test_scenarios_py.make_adapter(repo_cfg, git)
    return test_scenarios_js.make_adapter(repo_cfg, git)


class Scenarios(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        e = ss.env()
        cls.all = {sc["id"].split("-")[0]: sc for sc in build.scenarios(e.dest)}
        cls.runs = {key: ss.run_scenario(sc, make_adapter) for key, sc in cls.all.items()}
        cls.tree = claims.Tree(e.repos)
        cls.repos = e.repos

    def prop(self, key):
        return self.runs[key].propagation

    # ---- expected.json

    def test_the_walkers_output_matches_expected_json(self):
        for key in CROSS + QUIET:
            with self.subTest(scenario=self.all[key]["id"]):
                self.assertEqual(ss.check(self.runs[key]), [])

    def test_every_type_of_every_scenario_is_checked(self):
        skipped = {}
        for key, sc in self.all.items():
            tags = ss.skipped_types(sc["expected"], ss.PY_TYPES, ss.PY_NOT_ESTABLISHED)
            if tags:
                skipped[key] = tags
        self.assertEqual(skipped, {})

    def test_the_cross_types_are_all_exercised_by_these_scenarios(self):
        seen = {(sec, e["type"]) for key in CROSS for sec in ("must_find", "must_exclude") for e in self.all[key]["expected"][sec]}
        for type_ in ("http_edge", "consumer", "contract_read", "finding", "screen", "route"):
            self.assertIn(("must_find", type_), seen, type_)
        for type_ in ("http_edge", "contract_read", "screen"):
            self.assertIn(("must_exclude", type_), seen, type_)
        kinds = {n["kind"] for key in CROSS for n in self.all[key]["expected"]["not_established"]}
        self.assertEqual(kinds, {"not_distinctive", "hop_cap", "edge_unfollowed", "entry_points_unread"})

    # ---- s11: a contract field reaches the web screens through the api, with evidence at every hop

    def test_s11_the_field_change_reaches_the_screen_through_the_service_with_evidence_at_every_hop(self):
        prop = self.prop("s11")
        (screen,) = [r for r in prop.reached if r.area.kind == "screen"]
        chain = screen.area.chains[0]
        self.assertEqual([(e.repo, e.path.rsplit("/", 1)[-1], e.line) for e in chain], [
            ("acme/api", "settlement.py", 5),   # the import of the package: the consumer's entry for the contract edge
            ("acme/api", "settlement.py", 9),   # the read of the field
            ("acme/api", "settlement.py", 21),  # summarise uses reference_for
            ("acme/api", "settlements.py", 1),  # the router module imports the service
            ("acme/api", "settlements.py", 14),  # the handler uses summarise
            ("acme/web", "api.ts", 37),         # the client line: the http edge, crossed at the client
            ("acme/web", "SettlementsPage.tsx", 4),
            ("acme/web", "routes.tsx", 5),
            ("acme/web", "routes.tsx", 11),
        ])
        self.assertEqual({e.ref for e in chain if e.repo == "acme/web"}, {ss.sg.rev(self.repos["acme/web"], "main")})
        self.assertEqual({e.ref for e in chain if e.repo == "acme/api"}, {ss.sg.rev(self.repos["acme/api"], "main")})
        self.assertEqual(ss.evidence_problems(self.repos, chain), [])  # each one is its line at its pinned sha
        self.assertEqual(
            sorted((h.kind, h.from_repo, h.to_repo, h.direction, h.depth) for h in prop.hops if h.seeds or h.kind == "package"),
            [("contract", "acme/contracts", "acme/api", "forward", 1), ("contract", "acme/contracts", "acme/worker", "forward", 1),
             ("http", "acme/api", "acme/web", "reverse", 2), ("package", "acme/contracts", "acme/api", "forward", 1), ("package", "acme/contracts", "acme/worker", "forward", 1)],
        )

    def test_every_hop_pins_the_revision_it_read_in_the_other_repository(self):
        prop = self.prop("s11")
        main = {n: sg.rev(self.repos[n], "main") for n in ("acme/api", "acme/worker", "acme/web")}
        for h in prop.hops:
            self.assertTrue(h.pins, h)
            for pin in h.pins:
                self.assertEqual((pin.repo, pin.ref, pin.sha, pin.role), (h.to_repo, "main", main[h.to_repo], "primary"))
        # the changed repository is read at the head of the change
        self.assertEqual(prop.passes[0].sha, self.runs["s11"].change.head_sha)
        # a released change reads every configured branch of the consumer: s17
        branches = {(pin.repo, pin.ref, pin.role) for h in self.prop("s17").hops for pin in h.pins}
        self.assertEqual(branches, {("acme/api", "main", "branch"), ("acme/api", "release/1.2", "branch"), ("acme/api", "release/1.x", "branch"), ("acme/worker", "main", "branch")})

    def test_s11_the_consumers_pass_is_the_passes_of_a_real_change_unit_tests_and_priorities_included(self):
        prop = self.prop("s11")
        self.assertEqual([(p.repo, p.depth, p.seed.path if p.seed else None) for p in prop.passes], [
            ("acme/contracts", 0, None), ("acme/api", 1, "services/billing/app/services/settlement.py"),
            ("acme/worker", 1, "worker/tasks/settlement_sync.py"), ("acme/web", 2, "src/modules/billing/api.ts"),
        ])
        self.assertEqual([(t.repo, t.path) for t in prop.unit_tests], [("acme/api", "services/billing/tests/test_settlement.py")])
        self.assertEqual({r.area.priority for r in prop.reached}, {"must_test"})
        self.assertEqual(prop.unread_entry_points, ("acme/worker",))  # a Celery entry point has no adapter yet: said, not "nothing"
        self.assertEqual([(e.kind, e.from_repo, e.to_repo) for e in prop.unfollowed], [("event", "acme/api", "acme/worker")])
        # both are records (non-blocking) and the propagation is not complete for them, as for a hop cap
        self.assertEqual([(r.kind, r.subject, r.blocks_selection) for r in prop.records], [
            ("edge_unfollowed", "edge:event:acme/api:acme/worker", False), ("entry_points_unread", "acme/worker", False)])
        self.assertFalse(prop.complete)
        self.assertTrue(any("event" in why for why in prop.reasons) and any("entry points of acme/worker" in why for why in prop.reasons))

    def test_s06_the_screen_chain_starts_at_the_client_line_and_ends_at_the_route_with_the_other_mount_kept(self):
        prop = self.prop("s06")
        (screen,) = [r for r in prop.reached if r.area.kind == "screen"]
        self.assertEqual([(e.path.rsplit("/", 1)[-1], e.line) for e in screen.area.chains[0]], [("api.ts", 33), ("InvoiceDetailPage.tsx", 6), ("routes.tsx", 3), ("routes.tsx", 10)])
        self.assertEqual(screen.area.also_mounted, ("/cases/:caseId/invoices/:invoiceId",))
        (edge,) = prop.http_edges
        self.assertEqual((edge.direction, edge.route.label, edge.kind, edge.ambiguous_with, edge.method), ("to_client", "GET /api/billing/invoices/{invoice_id}", "exact", (), "GET"))

    def test_s05_a_web_change_reaches_the_route_it_calls_and_nothing_else(self):
        prop = self.prop("s05")
        (edge,) = prop.http_edges
        self.assertEqual((edge.direction, edge.route.path, edge.kind), ("to_server", "/api/billing/settlements/summary", "exact"))
        self.assertEqual([p.repo for p in prop.passes], ["acme/web"])  # the api did not change: the hop ends at the route
        (backend,) = prop.backend_calls
        self.assertEqual(backend.handler, "services/billing/app/routers/settlements.py")
        self.assertIn("services/billing/app/services/settlement.py", [m.path for m in backend.modules])
        self.assertEqual(backend.modules[0].depth, 1)
        self.assertTrue(all(m.depth <= 3 for m in backend.modules))

    def test_s10_the_removed_route_is_followed_to_the_screen_and_is_a_finding_with_the_values_of_the_readme(self):
        prop = self.prop("s10")
        (f,) = prop.findings
        self.assertEqual((f.kind, f.risk, f.confidence), ("removed_route_still_called", "high", "low"))
        (edge,) = prop.http_edges
        self.assertTrue(edge.removed)
        self.assertEqual({e.ref for e in edge.route.evidence}, {self.runs["s10"].change.pre_change_sha})
        self.assertEqual([r.area.label for r in prop.reached], ["/billing/invoices/:invoiceId"])

    def test_s22_an_added_route_nobody_calls_is_said_with_the_readme_values(self):
        (f,) = self.prop("s22").findings
        self.assertEqual((f.kind, f.risk, f.confidence), ("added_route_uncalled", "low", "medium"))
        self.assertEqual(self.prop("s22").http_edges, ())

    def test_s21_the_second_edge_is_not_followed_and_the_cap_is_said_once_in_the_declared_direction(self):
        prop = self.prop("s21")
        self.assertEqual(sorted((r.kind, r.subject, r.blocks_selection) for r in prop.records), [
            ("edge_unfollowed", "edge:event:acme/api:acme/worker", False), ("entry_points_unread", "acme/worker", False), ("hop_cap", "edge:http:acme/web:acme/api", False)])
        self.assertEqual([p.repo for p in prop.passes], ["acme/contracts", "acme/api", "acme/worker"])  # api and worker are the first hop, web is the second
        self.assertEqual([r.area.kind for r in prop.reached], ["api_route"])
        self.assertEqual(prop.http_edges, ())
        self.assertFalse(prop.complete)  # a stated limit: what lies past the edge is not an absence
        self.assertTrue(any("hop limit" in why for why in prop.reasons))
        self.assertEqual(len(prop.consumers), 2)  # the package edge is the first hop

    def test_the_reads_carry_the_pin_and_adoption_of_the_consumer(self):
        reads = {(r.repo, r.path): (r.pin, r.adoption, r.branch) for r in self.prop("s11").contract_reads}
        self.assertEqual(reads, {
            ("acme/api", "services/billing/app/services/settlement.py"): ("1.10.0", "on_adoption", "main"),
            ("acme/worker", "worker/tasks/settlement_sync.py"): ("1.9.0", "on_adoption", "main"),
        })

    def test_s12_a_generic_name_links_nothing_and_the_consumers_are_still_listed(self):
        prop = self.prop("s12")
        self.assertEqual((prop.contract_reads, prop.contract_candidates), ((), ()))
        (change,) = prop.contract_changes
        self.assertEqual((change.symbol, change.kinds, change.distinct, change.definitions), ("Settlement.interval", ("default_changed",), False, 5))
        self.assertEqual(len(prop.consumers), 2)

    def test_s18_the_word_in_a_comment_is_no_read_and_the_absence_is_established_without_a_record(self):
        prop = self.prop("s18")
        self.assertEqual((prop.contract_reads, prop.contract_candidates, prop.records), ((), (), ()))
        self.assertTrue(prop.complete)
        (change,) = prop.contract_changes
        self.assertEqual((change.distinct, change.definitions), (True, 1))

    def test_the_changed_fields_are_classified(self):
        got = {key: [(c.symbol, c.kinds) for c in self.prop(key).contract_changes] for key in ("s11", "s12", "s17", "s18", "s21")}
        self.assertEqual(got, {
            "s11": [("Invoice.settlement_reference", ("contract-tightened",))], "s12": [("Settlement.interval", ("default_changed",))],
            "s17": [("Invoice.due_date", ("added",))], "s18": [("Schedule.limit", ("default_changed",))],
            "s21": [("Invoice.settlement_reference", ("contract-tightened",))],
        })
        change = self.prop("s11").contract_changes[0]
        self.assertEqual((change.before.annotation, change.before.default, change.after.annotation, change.after.has_default), ("str | None", "None", "str", False))

    # ---- the independent oracles

    def test_every_client_call_fits_the_route_the_oracle_says_and_no_other(self):
        """claims.best_routes (exact fit beats a literal in a parameter, a client parameter fits only a route parameter) agrees
        with the matcher on every client URL of the web repository against every route of the api at main and at every
        scenario head, for GET, POST and DELETE; a method with no route on the path falls back to the other methods, said."""
        urls = claims.client_urls(claims.text_at(self.repos["acme/web"], "main", "src/api/endpoints.ts"))
        self.assertEqual(len(urls), 6)
        checked = fallbacks = 0
        for rev in ["main"] + [sc["change"]["head"] for sc in self.all.values() if sc["repo"] == "acme/api"]:
            routes, _ = claims.api_routes(self.tree, self.repos, "acme/api", rev)
            adapted = [_route(r) for r in routes]
            for name, url in urls.items():
                url_parts = _parts(url)
                for method in ("GET", "POST", "DELETE"):
                    got = best_matches(url_from_parts(url_parts), method, adapted)
                    mine = sorted((m.route.method, m.route.path) for m in got if not m.fallback)
                    theirs = sorted((method, r["path"]) for r in claims.best_routes(url, routes, method))
                    with self.subTest(rev=rev, url=name, method=method):
                        self.assertEqual(mine, theirs)
                        if not theirs:  # every other method that fits the path is kept, flagged
                            others = {(r["method"], r["path"]) for m in ("GET", "POST", "PUT", "PATCH", "DELETE") for r in claims.best_routes(url, routes, m)}
                            self.assertTrue(all(m.fallback for m in got))
                            self.assertEqual(bool(got), bool(others))
                            fallbacks += len(got)
                    checked += 1
        self.assertGreaterEqual(checked, 180)
        self.assertGreater(fallbacks, 0)

    def test_a_url_with_an_unknown_part_fits_exactly_the_routes_an_independent_matcher_says(self):
        """The partial URLs have no best route: every route they can fit is a candidate. A short recursive reference matcher, written
        apart from `match_url`, agrees on a set of partial URLs against every route of the api."""
        routes, _ = claims.api_routes(self.tree, self.repos, "acme/api", "main")
        adapted = [_route(r) for r in routes]

        def fits(c: list[str], r: list[str]) -> bool:
            if not c:
                return not r
            head = c[0]
            if head == "*":  # an unknown run of segments, also none
                return any(fits(c[1:], r[k:]) for k in range(len(r) + 1))
            if not r:
                return False
            if head == "?":  # an unknown part inside one segment
                return fits(c[1:], r[1:])
            if head == "{}":
                return r[0].startswith("{") and fits(c[1:], r[1:])
            return (r[0].startswith("{") or r[0] == head) and fits(c[1:], r[1:])

        cases = [
            (["*", "billing", "invoices"], [("unknown", "BASE"), ("lit", "/billing/invoices")]),
            (["api", "*", "settlements", "summary"], [("lit", "/api/"), ("unknown", "X"), ("lit", "/settlements/summary")]),
            (["api", "billing", "?", "{}"], [("lit", "/api/billing/"), ("unknown", "WHICH"), ("lit", "/"), ("param", "id")]),
            (["*"], [("unknown", "WHOLE")]),
            (["api", "?"], [("lit", "/api/v"), ("unknown", "N")]),
            (["*", "cases", "{}"], [("unknown", "BASE"), ("lit", "/cases/"), ("param", "id")]),
        ]
        checked = 0
        for ref, parts in cases:
            url = url_from_parts(parts)
            self.assertTrue(url.partial)
            mine = sorted((m.route.method, m.route.path) for m in best_matches(url, None, adapted))
            theirs = sorted((r["method"], r["path"]) for r in routes if fits(ref, r["path"].strip("/").split("/")))
            with self.subTest(parts=parts):
                self.assertEqual(mine, theirs)
                self.assertTrue(all(m.partial for m in best_matches(url, None, adapted)))
            checked += 1
        self.assertEqual(checked, 6)

    def test_the_client_calls_are_the_lines_the_oracle_reads(self):
        """Every `apiGet(<constant or builder call>)` line of the web repository is a client call, with the URL the oracle evaluates."""
        e = ss.env()
        git = ss.make_git()
        from reachmap import propagate
        run = propagate.Run(e.workspace, git)
        sha = sg.rev(self.repos["acme/web"], "main")
        calls = run.client("acme/web").calls(sha)
        self.assertTrue(calls.complete)
        urls = claims.client_urls(claims.text_at(self.repos["acme/web"], "main", "src/api/endpoints.ts"))
        mine = {(c.path, c.line, c.url.text) for c in calls.calls}
        theirs = set()
        for path in sorted(self.tree.files("acme/web", "main")):
            if not path.endswith((".ts", ".tsx")) or path == "src/api/endpoints.ts":
                continue
            for n, line in enumerate(claims.text_at(self.repos["acme/web"], "main", path).split("\n"), 1):
                url = claims.url_used_on_line(line, urls)
                if url is not None:
                    theirs.add((path, n, url))
        self.assertEqual(mine, theirs)
        self.assertEqual(len(theirs), 7)

    def test_the_contract_reads_are_the_ones_of_the_independent_scan(self):
        e = ss.env()
        checked = 0
        for key in ("s11", "s12", "s17", "s18", "s21"):
            sc = self.all[key]
            field = next(c for c in self.prop(key).contract_changes).name
            definitions = sum(
                len(re.findall(rf"(?m)^    {field}\s*:", claims.text_at(self.repos["acme/contracts"], sc["change"]["head"], f"contracts/models/{n}.py") or ""))
                for n in ("invoice", "settlement", "customer", "schedule", "common"))
            expected = set()
            if definitions <= e.workspace.limits.distinct_max:
                for repo in ("acme/api", "acme/worker"):
                    for path in sorted(self.tree.files(repo, "main")):
                        if not path.endswith(".py") or claims.is_test_path(path, ["tests/", "test_"]):
                            continue
                        text = claims.text_at(self.repos[repo], "main", path)
                        if any(not in_comment for in_comment in claims.occurrences_of_word(text, field)):
                            expected.add((repo, path))
            with self.subTest(scenario=key):
                self.assertEqual({(r.repo, r.path) for r in self.prop(key).contract_reads}, expected)
            checked += 1
        self.assertEqual(checked, 5)

    def test_the_consumers_are_what_the_manifests_and_the_ancestry_say(self):
        contracts = self.repos["acme/contracts"]
        total = 0
        for key in ("s11", "s12", "s17", "s18", "s21"):
            prop = self.prop(key)
            head = self.runs[key].change.head_sha
            released = bool(claims.git_out(contracts, "tag", "--contains", head).split())
            for c in prop.consumers:
                total += 1
                with self.subTest(scenario=key, consumer=c.repo, branch=c.branch):
                    manifest = claims.text_at(self.repos[c.repo], c.branch, "requirements.txt")
                    self.assertIn(f"acme-contracts=={c.pin}\n", manifest)
                    self.assertEqual(c.sha, sg.rev(self.repos[c.repo], c.branch))
                    if not released:
                        self.assertEqual((c.branch, c.adoption, c.contains_change), ("main", "on_adoption", False))
                    else:
                        contains = claims.run_git(contracts, "merge-base", "--is-ancestor", head, f"v{c.pin}").returncode == 0
                        self.assertEqual((c.adoption, c.contains_change), ("adopted" if contains else "on_adoption", contains))
        self.assertEqual(total, 2 + 2 + 4 + 2 + 2)

    def test_the_released_and_unreleased_policies_list_the_branches_the_readme_says(self):
        listed = {key: sorted((c.repo, c.branch) for c in self.prop(key).consumers) for key in ("s11", "s17")}
        self.assertEqual(listed["s11"], [("acme/api", "main"), ("acme/worker", "main")])  # once, from the primary branch
        self.assertEqual(listed["s17"], [("acme/api", "main"), ("acme/api", "release/1.2"), ("acme/api", "release/1.x"), ("acme/worker", "main")])
        self.assertEqual({c.adoption for c in self.prop("s17").consumers if c.branch != "main" or c.repo == "acme/worker"}, {"on_adoption"})

    # ---- the evidence

    def test_every_evidence_of_every_propagation_resolves_at_its_pinned_sha(self):
        total = cross = 0
        for key, run in self.runs.items():
            evidence = list(run.propagation.evidence())
            total += len(evidence)
            cross += sum(1 for ev in evidence if ev.repo != run.scenario["repo"])
            with self.subTest(scenario=key, evidence=len(evidence)):
                self.assertEqual(ss.evidence_problems(self.repos, evidence), [])
        self.assertGreater(total, 500)
        self.assertGreater(cross, 100)  # chains across repositories are in it

    # ---- the check is real

    def test_the_harness_fails_when_the_walkers_output_is_wrong(self):
        run = self.runs["s11"]
        prop = run.propagation
        self.assertEqual(ss.check(run), [])

        def with_prop(**changes):
            return dataclasses.replace(run, propagation=dataclasses.replace(prop, **changes))

        reads = prop.contract_reads
        self.assertTrue(ss.check(with_prop(contract_reads=reads[2:])))                      # a missing read (the worker's)
        extra = dataclasses.replace(reads[0], path="services/billing/app/routers/settlements.py")
        self.assertTrue(ss.check(with_prop(contract_reads=reads + (extra,))))               # the commented file read as a read (must_exclude)
        self.assertTrue(ss.check(with_prop(contract_reads=tuple(dataclasses.replace(r, evidence=r.evidence[-1:]) for r in reads))))  # reads without the import line
        self.assertTrue(ss.check(with_prop(consumers=prop.consumers[:1])))                  # a missing consumer
        wrong = dataclasses.replace(prop.consumers[0], adoption="adopted")
        self.assertTrue(ss.check(with_prop(consumers=(wrong,) + prop.consumers[1:])))       # a wrong adoption
        wrong_pin = dataclasses.replace(prop.consumers[0], pin="1.9.0")
        self.assertTrue(ss.check(with_prop(consumers=(wrong_pin,) + prop.consumers[1:])))
        self.assertTrue(ss.check(with_prop(http_edges=())))                                 # a missing http edge
        (edge,) = prop.http_edges
        lit = dataclasses.replace(edge, route=dataclasses.replace(edge.route, path="/api/billing/settlements/{settlement_id}"))
        self.assertTrue(ss.check(with_prop(http_edges=(lit,))))                             # the less specific route
        self.assertTrue(ss.check(with_prop(http_edges=(edge, dataclasses.replace(edge, client=prop.contract_reads[0].evidence[0])))))  # an extra edge (complete_for)
        screens = [r for r in prop.reached if r.area.kind == "screen"]
        rest = tuple(r for r in prop.reached if r.area.kind != "screen")
        self.assertTrue(ss.check(with_prop(reached=rest)))                                  # the screen is missing
        area = dataclasses.replace(screens[0].area, chains=(screens[0].area.chains[0][2:],))
        self.assertTrue(ss.check(with_prop(reached=rest + (dataclasses.replace(screens[0], area=area),))))  # the part of the chain in the api is lost
        self.assertTrue(ss.check(with_prop(unit_tests=())))                                 # the api's unit test is missing
        other = dataclasses.replace(prop.unit_tests[0], path="services/billing/tests/test_money.py")
        self.assertTrue(ss.check(with_prop(unit_tests=prop.unit_tests + (other,))))         # an extra unit test (complete_for)
        self.assertTrue(ss.check(with_prop(complete=not prop.complete)))                    # says the opposite of what the records say

    def test_the_harness_also_rejects_a_candidate_that_matches_an_excluded_entry(self):
        """"Must not appear" is stronger than "must not be must-test": a by-name area, a candidate edge or a candidate read fails too."""
        s06 = self.runs["s06"]
        prop = s06.propagation
        self.assertEqual(ss.check(s06), [])
        (screen,) = [r for r in prop.reached if r.area.kind == "screen"]
        other = dataclasses.replace(screen, area=dataclasses.replace(screen.area, label="/reports", also_mounted=(), by_name=True, priority="candidate"))
        self.assertTrue(ss.check(dataclasses.replace(s06, propagation=dataclasses.replace(prop, reached=prop.reached + (other,)))))
        (edge,) = prop.http_edges
        wrong = dataclasses.replace(edge, client=dataclasses.replace(edge.client, detail="    return apiGet<Invoice[]>(INVOICES_URL);"), route=edge.route, candidate=True)
        excluded_route = dataclasses.replace(edge.route, path="/api/billing/invoices/{invoice_id}")
        self.assertTrue(ss.check(dataclasses.replace(s06, propagation=dataclasses.replace(prop, http_edges=prop.http_edges + (dataclasses.replace(wrong, route=excluded_route),)))))
        s11 = self.runs["s11"]
        read = s11.propagation.contract_reads[0]
        comment_only = dataclasses.replace(read, path="services/billing/app/routers/settlements.py", candidate=True)
        self.assertEqual(ss.check(s11), [])
        self.assertTrue(ss.check(dataclasses.replace(s11, propagation=dataclasses.replace(s11.propagation, contract_candidates=(comment_only,)))))

    def test_the_harness_fails_for_findings_and_the_hop_cap(self):
        run = self.runs["s10"]
        prop = run.propagation
        self.assertEqual(ss.check(run), [])

        def with_prop(**changes):
            return dataclasses.replace(run, propagation=dataclasses.replace(prop, **changes))

        self.assertTrue(ss.check(with_prop(findings=())))
        self.assertTrue(ss.check(with_prop(findings=(dataclasses.replace(prop.findings[0], risk="medium"),))))
        self.assertTrue(ss.check(with_prop(findings=(dataclasses.replace(prop.findings[0], confidence="high"),))))
        self.assertTrue(ss.check(with_prop(findings=(dataclasses.replace(prop.findings[0], evidence=prop.findings[0].evidence[:1]),))))
        self.assertTrue(ss.check(with_prop(findings=prop.findings + (dataclasses.replace(prop.findings[0], kind="added_route_uncalled"),))))  # an extra finding (complete_for)
        removed = [dataclasses.replace(m, removed=False) for m in prop.http_edges]
        self.assertTrue(ss.check(with_prop(http_edges=tuple(removed))))
        capped = self.runs["s21"]
        self.assertEqual(ss.check(capped), [])
        self.assertTrue(ss.check(dataclasses.replace(capped, records=[r for r in capped.records if r.kind != "hop_cap"])))  # the cap not said
        orphan = self.runs["s16"]
        extra = dataclasses.replace(self.runs["s10"].propagation.findings[0])
        self.assertTrue(ss.check(dataclasses.replace(orphan, propagation=dataclasses.replace(orphan.propagation, findings=(extra,)))))  # a removed route claimed without a pre-change version


def _parts(url: str) -> list:
    parts: list = []
    for i, piece in enumerate(url.split("{}")):
        if i:
            parts.append(("param", "x"))
        parts.append(("lit", piece))
    return parts


def _route(r: dict):
    from reachmap.adapters import Route

    ev = Evidence(repo="acme/api", path=r["file"], line=1, ref="a" * 40, detail=r["chain"][0])
    return Route("acme/api", "api_route", r["method"], r["path"], r["file"], r["func"], r["file"], "", (ev,))


if __name__ == "__main__":
    unittest.main()
