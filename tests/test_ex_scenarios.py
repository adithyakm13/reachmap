"""The M16 Express and NestJS scenarios (tests/fixtures/express) end to end through `reachmap analyse`: the report the CLI writes, read back,
against expected.json with the same checks the M1 scenarios use (`scenario_support.check`: no type is skipped), and the report as a whole:
every Evidence resolves at its pinned sha, the output is deterministic, wording, priorities, completeness. The adapters' route tables are
compared with the independent reading of the repositories (`tests/fixtures/ex_claims.py`) at main and at every scenario head.
"""

import json
import re
import unittest

from reachmap import entrypoints, payload, wording
from reachmap.adapters.js import JsAdapter
from reachmap.gitlayer import Git
from tests import scenario_support as ss
from tests import support_ex as se
from tests import support_git as sg
from tests.fixtures import ex_claims

IDS = [f"ex{n:02d}" for n in range(1, 26)]
EX_RECORDS = {"router_unmounted", "route_path_partial", "routes_unread", "mount_unresolved", "parse_degraded", "not_distinctive", "route_change_withheld"}


def strip_run(text: str) -> dict:
    data = json.loads(text)
    data["run"] = None  # the run-time metadata is the one thing that may differ between two runs
    data["review"]["as_of"] = data["review"]["decisions"]["as_of"] = None
    return data


class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenarios = {sc["id"].split("-")[0]: sc for sc in se.scenarios()}
        cls.results = {key: se.scenario_run(sc) for key, sc in cls.scenarios.items()}

    def labels(self, key, kind="api_route"):
        return sorted(a["label"] for a in self.results[key].report["areas"] if a["kind"] == kind)

    def test_the_scenarios_are_the_twenty_five_and_each_has_an_explanation(self):
        self.assertEqual(sorted(self.scenarios), IDS)
        for key, sc in self.scenarios.items():
            self.assertGreater(len(sc["expected"]["explains"]), 250, key)
            self.assertTrue(sc["expected"]["must_find"], key)

    def test_every_run_completes_with_exit_0_and_a_report(self):
        for key, r in self.results.items():
            with self.subTest(scenario=key):
                self.assertEqual((r.code, r.err), (0, ""))
                self.assertEqual(r.report["status"], "completed")

    def test_the_report_of_every_scenario_matches_expected_json(self):
        for key, sc in self.scenarios.items():
            with self.subTest(scenario=sc["id"]):
                self.assertEqual(se.check(sc, self.results[key].report), [])

    def test_no_type_is_skipped_in_any_scenario(self):
        for key, sc in self.scenarios.items():
            with self.subTest(scenario=key):
                self.assertEqual(ss.skipped_types(sc["expected"], ss.JS_TYPES, ss.JS_NOT_ESTABLISHED | EX_RECORDS, repo=sc["repo"]), [])

    def test_the_harness_fails_when_the_report_is_wrong(self):
        sc = self.scenarios["ex01"]
        report = json.loads(self.results["ex01"].text)
        self.assertEqual(se.check(sc, report), [])

        def broken(edit):
            data = json.loads(json.dumps(report))
            edit(data)
            return se.check(sc, data)

        prop = lambda d: d["propagations"][0]  # noqa: E731
        own = lambda d: prop(d)["passes"][0]["reach"]  # noqa: E731
        self.assertTrue(broken(lambda d: prop(d)["http_edges"].clear()))  # a missing http edge
        self.assertTrue(broken(lambda d: own(d).__setitem__("reached", [r for r in own(d)["reached"] if r["area"]["label"] != "GET /api/v1/orders/{id}"])))  # a missing route
        self.assertTrue(broken(lambda d: prop(d).__setitem__("reached", [r for r in prop(d)["reached"] if r["area"]["label"] != "/orders/:orderId"])))  # a missing screen of the client
        self.assertTrue(broken(lambda d: prop(d).__setitem__("complete", False)))  # not complete, though no record says so

        def extra_route(d):
            r = json.loads(json.dumps(own(d)["reached"][0]))
            r["area"]["label"] = "GET /api/v1/orders"
            r["routes"][0]["path"] = "/api/v1/orders"
            own(d)["reached"].append(r)

        self.assertTrue(broken(extra_route))  # a route the trace does not have (complete_for route)
        sc5 = self.scenarios["ex05"]
        r5 = json.loads(self.results["ex05"].text)
        self.assertEqual(se.check(sc5, r5), [])
        r5["incompleteness"] = []
        self.assertTrue(se.check(sc5, r5))  # a record that is expected is missing

    def test_priority_is_must_test_for_api_routes_and_screens_up_to_eight_and_a_match_by_name_is_a_candidate(self):
        by_name = []
        for key, r in self.results.items():
            for a in r.report["areas"]:
                if a["by_name"]:
                    by_name.append((key, a["label"], a["priority"]))
                else:
                    self.assertEqual(a["priority"], "must_test", f"{key} {a['label']}")
        # the console calls /api/ledger/balances, which fits the balances route only through the unknown version segment: a candidate, never a must-test
        self.assertEqual(by_name, [("ex20", "/balances", "candidate")])

    def test_the_routes_reached_by_each_express_scenario(self):
        want = {
            "ex01": ["GET /api/v1/orders/{id}"], "ex02": ["GET /api/v1/customers/{customerId}/orders"], "ex03": ["GET /api/v1/admin/stats"], "ex04": ["POST /api/v1/orders/{id}/lines"],
            "ex05": [], "ex06": ["GET /legacy/invoices", "GET /legacy/invoices/{invoiceId}"], "ex07": ["GET /api/v1/reports/<unknown:dailyPath()>"],
            "ex09": ["GET /api/v1/orders/{id}", "POST /api/v1/orders"], "ex10": [], "ex11": ["GET /api/v1/orders/export"], "ex12": [], "ex13": ["GET /api/v1/customers/{customerId}"], "ex14": [], "ex24": [],
        }
        for key, labels in want.items():
            with self.subTest(scenario=key):
                self.assertEqual(self.labels(key), sorted(labels) if key != "ex08" else labels)
        self.assertEqual(len(self.labels("ex08")), 15)

    def test_the_routes_reached_by_each_nest_scenario(self):
        want = {
            "ex15": ["DELETE /api/ledger/entries/{id}", "POST /api/ledger/entries"], "ex16": ["GET /api/ledger/entries/{id}"], "ex17": [], "ex18": [],
            "ex19": ["DELETE /api/ledger/entries/{id}", "GET /api/ledger/entries", "GET /api/ledger/entries/{id}", "POST /api/ledger/entries"],
            "ex21": ["GET /api/<unknown:version>/balances/{account}"], "ex22": [], "ex23": [], "ex25": [],
        }
        for key, labels in want.items():
            with self.subTest(scenario=key):
                self.assertEqual(self.labels(key), sorted(labels))
        self.assertEqual(len(self.labels("ex20")), 6)

    def test_the_screens_reached_across_the_http_edge(self):
        want = {
            "ex01": ["/orders/:orderId"], "ex02": ["/customers/:customerId"], "ex03": ["/stats"], "ex06": ["/stats"], "ex09": ["/orders/:orderId"], "ex10": ["/customers/:customerId"],
            "ex13": ["/customers/:customerId"], "ex15": ["/ledger", "/ledger/:entryId"], "ex16": ["/ledger/:entryId"], "ex19": ["/ledger", "/ledger/:entryId"],
            "ex04": [], "ex05": [], "ex11": [], "ex12": [], "ex14": [], "ex17": [], "ex18": [], "ex21": [], "ex22": [], "ex23": [], "ex24": [], "ex25": [],
        }
        for key, labels in want.items():
            with self.subTest(scenario=key):
                self.assertEqual(self.labels(key, "screen"), sorted(labels))
        self.assertEqual(self.labels("ex08", "screen"), ["/customers/:customerId", "/orders", "/orders/:orderId", "/stats"])

    def test_an_app_middleware_reaches_every_route_and_says_which_it_is(self):
        report = self.results["ex08"].report
        mine = {a["label"] for a in report["areas"] if a["repo"] == "ex/orders"}
        e = se.env()
        truth = {f"{r['method']} {r['path']}" for r in ex_claims.express_routes(e.repos["ex/orders"], self.scenarios["ex08"]["change"]["head"])}
        self.assertEqual(mine, truth)
        self.assertEqual(len(mine), 15)

    def test_a_bootstrap_change_reaches_the_routes_that_are_served_and_not_the_ones_that_are_not(self):
        report = self.results["ex20"].report
        mine = {a["label"] for a in report["areas"] if a["repo"] == "ex/ledger"}
        e = se.env()
        truth = {f"{r['method']} {r['path']}" for r in ex_claims.nest_routes(e.repos["ex/ledger"], self.scenarios["ex20"]["change"]["head"])}
        self.assertEqual(mine, truth)
        self.assertFalse(any(i["kind"] == "router_unmounted" for i in report["incompleteness"]))

    def test_a_router_built_in_a_function_and_inherited_routes_make_the_answer_not_complete(self):
        for key, subject in (("ex12", "src/routes/factory.ts:5"), ("ex23", "src/base/status.controller.ts:5")):
            r = self.results[key]
            with self.subTest(scenario=key):
                self.assertEqual([(i["kind"], i["subject"], i["blocks_selection"]) for i in r.report["incompleteness"]], [("routes_unread", subject, True)])
                self.assertFalse(r.report["propagations"][0]["complete"])
                self.assertEqual(r.report["areas"], [])
                self.assertTrue(r.out.startswith("Blocking: "))

    def test_an_unreadable_router_or_module_is_not_a_removed_route_and_the_run_says_what_it_did_not_claim(self):
        for key, repo in (("ex24", "ex/orders"), ("ex25", "ex/ledger")):
            r = self.results[key]
            with self.subTest(scenario=key):
                kinds = sorted((i["kind"], i["blocks_selection"]) for i in r.report["incompleteness"])
                self.assertEqual(kinds, [("mount_unresolved", True), ("parse_degraded", True), ("route_change_withheld", True)])
                self.assertTrue(any(i["kind"] == "route_change_withheld" and i["subject"] == repo for i in r.report["incompleteness"]))
                self.assertFalse(any(f["kind"] in ("removed_route_still_called", "added_route_uncalled") for f in r.report["findings"]))
                self.assertFalse(r.report["propagations"][0]["complete"])
                self.assertEqual(r.report["areas"], [])

    def test_a_router_nothing_mounts_is_said_and_the_run_is_complete(self):
        for key in ("ex05", "ex17", "ex18"):
            r = self.results[key]
            with self.subTest(scenario=key):
                self.assertEqual([(i["kind"], i["blocks_selection"]) for i in r.report["incompleteness"]], [("router_unmounted", False)])
                self.assertTrue(r.report["propagations"][0]["complete"])
                self.assertEqual(r.report["areas"], [])

    def test_a_partial_path_is_marked_and_said_and_the_run_is_complete(self):
        for key in ("ex07", "ex21"):
            r = self.results[key]
            with self.subTest(scenario=key):
                self.assertTrue(any(i["kind"] == "route_path_partial" and not i["blocks_selection"] for i in r.report["incompleteness"]))
                self.assertTrue(r.report["propagations"][0]["complete"])
                self.assertTrue(all("<unknown:" in a["label"] for a in r.report["areas"] if a["kind"] == "api_route"))

    def test_the_routes_are_cited_with_a_claim_that_says_what_the_first_line_declares(self):
        report = self.results["ex01"].report
        (area,) = [a for a in report["areas"] if a["kind"] == "api_route"]
        claims = [e.get("claim") for e in area["chains"][0]]
        self.assertIn("route:GET /api/v1/orders/{id}:src/routes/orders.ts", claims)

    def test_a_removed_or_added_route_is_a_finding_and_the_other_kinds_are_the_pipelines(self):
        kinds = {key: {f["kind"] for f in r.report["findings"]} for key, r in self.results.items()}
        self.assertIn("removed_route_still_called", kinds["ex10"])
        self.assertIn("added_route_uncalled", kinds["ex11"])
        for key in set(kinds) - {"ex10", "ex11"}:
            self.assertFalse({"removed_route_still_called", "added_route_uncalled"} & kinds[key], key)

    def test_the_adapters_route_tables_are_the_ones_of_the_independent_reading_at_main_and_at_every_head(self):
        e = se.env()
        revs = {"ex/orders": ["main"], "ex/ledger": ["main"]}
        for sc in self.scenarios.values():
            if sc["repo"] in revs:
                revs[sc["repo"]].append(sc["change"]["head"])
        for name, refs in revs.items():
            cfg = e.workspace.repos[name]
            for ref in refs:
                records = []
                git = Git(e.cache, env=sg.isolated_env(e.home), record_to=records)
                lang = JsAdapter.from_config(cfg, git, limits=e.workspace.limits, record_to=records)
                entry = entrypoints.entry_adapter(cfg, lang, record_to=records)
                sha = sg.rev(e.repos[name], ref)
                got = sorted((r.method, r.path, r.file, tuple(x.detail.strip() for x in r.evidence)) for r in entry.routes(None, sha))
                oracle = ex_claims.express_routes if name == "ex/orders" else ex_claims.nest_routes
                unread = {"scenario/ex24-unreadable-router": "src/routes/customers.ts", "scenario/ex25-unreadable-module": "src/ledger/ledger.module.ts"}.get(ref)
                # a file that does not lex is not read, which the oracle (it reads text) does not know: the routes made through that file are missing, and only those
                want = sorted((r["method"], r["path"], r["file"], tuple(t for _p, t in r["evidence"])) for r in oracle(e.repos[name], ref)
                              if unread is None or unread not in {p for p, _t in r["evidence"]} | {r["file"]})
                with self.subTest(repo=name, ref=ref):
                    self.assertEqual(got, want)
                    if unread is not None:
                        continue
                    unmounted = (ex_claims.express_unmounted if name == "ex/orders" else ex_claims.nest_unmounted)(e.repos[name], ref)
                    self.assertEqual(sorted(entry.unmounted(sha)), sorted(unmounted))

    def test_the_route_table_of_the_two_servers_has_no_look_alike(self):
        e = se.env()
        cfg = e.workspace.repos["ex/orders"]
        git = Git(e.cache, env=sg.isolated_env(e.home), record_to=[])
        entry = entrypoints.entry_adapter(cfg, JsAdapter.from_config(cfg, git, limits=e.workspace.limits))
        paths = {r.path for r in entry.routes(None, sg.rev(e.repos["ex/orders"], "main"))}
        self.assertFalse({"/ping", "/orders/{id}"} & paths)

    def test_every_evidence_in_every_report_resolves_at_its_pinned_sha(self):
        total = 0
        e = se.env()
        for key, r in self.results.items():
            found = list(payload.evidence_in(r.report))
            total += len(found)
            with self.subTest(scenario=key, evidence=len(found)):
                self.assertEqual(ss.evidence_problems(e.repos, found), [])
        self.assertGreater(total, 500)  # the audit is looking at real evidence

    def test_two_runs_give_the_same_bytes_except_the_run_object(self):
        import io
        import tempfile
        from pathlib import Path

        from reachmap import cli
        from tests.fixtures import ex_build

        e = se.env()
        for key in ("ex01", "ex08", "ex15", "ex20"):
            sc = self.scenarios[key]
            with tempfile.TemporaryDirectory(prefix="reachmap-ex-e2e-") as tmp:
                code = cli.main(["analyse", "--config", str(ex_build.WORKSPACE_FILE), *se.scenario_args(sc), "--out", tmp], env=se.env_vars(), stdout=io.StringIO(), stderr=io.StringIO(),
                                git_env=sg.isolated_env(e.home))
                self.assertEqual(code, 0)
                (path,) = Path(tmp).glob("*.json")
                second = path.read_text(encoding="utf-8")
            with self.subTest(scenario=key):
                self.assertEqual(json.dumps(strip_run(second), indent=2), json.dumps(strip_run(self.results[key].text), indent=2))

    def test_one_scenario_runs_as_a_separate_process_and_the_kinds_are_known_to_a_fresh_interpreter(self):
        """`python -m reachmap analyse` in a process of its own loads the config (the kinds `express` and `nestjs` are registered by importing the CLI) and gives the same report."""
        import os
        import subprocess
        import sys
        import tempfile
        from pathlib import Path

        from tests.fixtures import ex_build

        root = Path(__file__).resolve().parents[1]
        for key in ("ex01", "ex15"):
            sc = self.scenarios[key]
            with tempfile.TemporaryDirectory(prefix="reachmap-ex-sub-") as tmp:
                home = Path(tmp) / "home"
                home.mkdir()
                env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
                env.update(se.env_vars(), HOME=str(home), XDG_CONFIG_HOME=str(home / ".xdg"), PYTHONDONTWRITEBYTECODE="1")
                argv = [sys.executable, "-m", "reachmap", "analyse", "--config", str(ex_build.WORKSPACE_FILE), *se.scenario_args(sc), "--out", str(Path(tmp) / "out")]
                proc = subprocess.run(argv, cwd=root, env=env, capture_output=True, text=True, timeout=300)
                self.assertEqual((proc.returncode, proc.stderr), (0, ""), key)
                (path,) = (Path(tmp) / "out").glob("*.json")
                sub = path.read_text(encoding="utf-8")
            self.assertEqual(json.dumps(strip_run(sub), indent=2), json.dumps(strip_run(self.results[key].text), indent=2))
            self.assertEqual(se.check(sc, json.loads(sub)), [])

    def test_every_human_readable_string_passes_the_wording_check(self):
        for key, r in self.results.items():
            data = r.report
            texts = [*(f["title"] for f in data["findings"]), *(f["confidence_basis"] for f in data["findings"]), *(i["detail"] for i in data["incompleteness"]),
                     *(i["not_established"] for i in data["incompleteness"]), *data["notes"], *r.out.splitlines()]
            for text in texts:
                self.assertEqual(wording.check(text), [], f"{key}: {text}")

    def test_no_report_says_anything_about_the_machine_it_ran_on(self):
        import os
        import tempfile

        e = se.env()
        needles = [os.path.expanduser("~"), os.getcwd(), tempfile.gettempdir(), "/Users/", "/var/folders", "file://", str(e.dest)]
        for key, r in self.results.items():
            for needle in needles:
                if needle and needle != "/":
                    self.assertNotIn(needle, r.text, f"{key}: {needle!r}")

    def test_the_scenario_files_hold_no_banned_character_and_no_ascii_arrow_in_prose(self):
        banned = chr(0x2013) + chr(0x2014) + chr(0x2192) + chr(0x2018) + chr(0x2019) + chr(0x201c) + chr(0x201d) + "".join(chr(c) for c in range(0x202a, 0x202f)) + "".join(chr(c) for c in range(0x2066, 0x206a))  # built from code points: no raw one in this file
        for key, sc in self.scenarios.items():
            for text in (sc["expected"]["explains"], sc["scenario"]["notes"], sc["scenario"]["commit_message"]):
                self.assertFalse(set(text) & set(banned), key)
                self.assertNotRegex(text, r"->|=>", key)

    def test_each_scenario_has_a_distinct_purpose(self):
        notes = [sc["scenario"]["notes"] for sc in self.scenarios.values()]
        self.assertEqual(len(notes), len(set(notes)))
        self.assertTrue(all(re.match(r"^[A-Za-z_]", n) for n in notes))


if __name__ == "__main__":
    unittest.main()
