"""M16 OpenAPI: the `oa` scenarios end to end through `reachmap analyse`: the report the CLI writes, read back, against the hand-traced
expected.json with the checks of the M9 harness (`scenario_support.check`, pointed at the openapi workspace by
`tests/support_openapi_world.py`: nothing is skipped), and the report as a whole: every Evidence resolves at its pinned sha, the output is
deterministic, one scenario runs as a separate process, the wording rules hold, and the harness fails when the report is wrong.

What each scenario proves:

    oa01 handler body       an operationId finds its handler by name; the route has the server base path and three spec lines; its client
                            (the generated getInvoice) is found through the web copy of the spec; a DELETE of the same path is not an edge
    oa02 service function   a change in a service reaches the handler that uses it by name and no other, and the unit test is not a caller
    oa03 removed operation  a removed operation that the web app still calls is a finding (high risk, low confidence), with both ends cited
    oa04 added operation    a new operation is a route; an import line added to a handler module does not reach the other handlers; no client
                            names it, said once every client edge was read completely
    oa05 operation changed  a spec diff is a change: a changed signature is an area, an operation-changed finding cited at both versions
    oa06 schema ref changed a schema reached through $ref, even through a cycle, changes every operation that reaches it
    oa07 spec broken        a spec that cannot be read gives a blocking record and no route (never an empty answer that reads as complete)
    oa08 web generated call a change in the web app reaches the routes its generated client calls, and the modules the handlers run
    oa09 generic names      get, list and an over-defined name are dropped from matching by name and recorded; an import of one is still a resolved edge
    oa10 spec copy broken   the same failure on the client side: a blocking edge record, no call matched, nothing claimed about routes
"""

import dataclasses
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from reachmap import payload, report as report_pkg, wording
from tests import scenario_support as ss
from tests import support_cli as sc_cli
from tests import support_git as sg
from tests import support_openapi_world as w
from tests.fixtures import build_openapi as bo

ROOT = Path(__file__).resolve().parents[1]
DETERMINISM = ("oa01", "oa03", "oa05", "oa07", "oa08")
NOT_ESTABLISHED = ss.JS_NOT_ESTABLISHED | {"spec_unreadable", "edge_incomplete"}
TYPES = ss.JS_TYPES | {"backend_call"}


def key(sid):
    return sid.split("-")[0]


def strip_run(text):
    data = json.loads(text)
    data["run"] = None  # the run-time metadata field is the one thing that may differ between two runs
    data["review"]["as_of"] = data["review"]["decisions"]["as_of"] = None
    return data


class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenarios = {key(sid): sc for sid, sc in w.scenarios().items()}
        cls.results = {k: w.cli_run(sc) for k, sc in cls.scenarios.items()}

    def check(self, k, report=None):
        return w.check(self.scenarios[k], report if report is not None else self.results[k].report, reach_incomplete=k == "oa10")

    def test_there_are_ten_scenarios_and_every_run_completes_with_exit_0_and_a_report(self):
        self.assertEqual(sorted(self.scenarios), [f"oa{n:02d}" for n in range(1, 11)])
        for k, r in self.results.items():
            with self.subTest(scenario=k):
                self.assertEqual((r.code, r.err), (0, ""))
                self.assertEqual((r.report["status"], r.report["schema"]), ("completed", 1))

    def test_no_type_and_no_record_kind_is_skipped_in_any_scenario(self):
        for k, sc in self.scenarios.items():
            with self.subTest(scenario=k):
                self.assertEqual(ss.skipped_types(sc["expected"], TYPES, NOT_ESTABLISHED), [])

    def test_the_report_of_every_scenario_matches_expected_json(self):
        for k in self.scenarios:
            with self.subTest(scenario=k):
                self.assertEqual(self.check(k), [])

    def test_every_evidence_in_every_report_resolves_at_its_pinned_sha(self):
        total = 0
        for k, r in self.results.items():
            found = list(payload.evidence_in(r.report))
            total += len(found)
            with self.subTest(scenario=k, evidence=len(found)):
                self.assertEqual(w.evidence_problems(r.report), [])
        self.assertGreater(total, 300)

    def test_a_route_is_cited_at_the_spec_and_the_spec_lines_are_the_ones_of_the_spec(self):
        report = self.results["oa01"].report
        routes = [r for r in payload.propagation_from_dict(report["propagations"][0]).reached if r.area.kind == "api_route"]
        (r,) = routes
        self.assertEqual([(e.path, e.detail.strip()) for e in r.routes[0].evidence],
                         [("api/openapi.json", '"get": {'), ("api/openapi.json", '"/invoices/{invoiceId}": {'), ("api/openapi.json", '"url": "https://ledger.example.invalid/v1"')])
        self.assertEqual(r.routes[0].path, "/v1/invoices/{invoiceId}")
        self.assertEqual(r.routes[0].handler, "get_invoice")

    def test_an_added_import_does_not_make_every_handler_of_the_module_changed(self):
        report = self.results["oa04"].report
        self.assertEqual([a["label"] for a in report["areas"]], ["GET /v1/invoices/{invoiceId}/pdf"])

    def test_an_operation_that_changes_has_a_finding_cited_at_both_versions_with_a_stable_identity(self):
        report = self.results["oa05"].report
        (f,) = [f for f in report["findings"] if f["kind"] == "operation-changed"]
        self.assertEqual((f["risk"], f["confidence"]), ("medium", "high"))
        self.assertEqual(f["identity"], ["operation-changed", "acme/ledger-api", "GET", "/v1/invoices/{invoiceId}"])
        before, after = f["evidence"]
        env = w.world()
        self.assertEqual((before["ref"], after["ref"]), (sg.rev(env.repos["acme/ledger-api"], "main"), sg.rev(env.repos["acme/ledger-api"], "scenario/oa05-operation-changed")))
        self.assertEqual(before["line"], after["line"])  # the method key did not move: the parameter was added after its summary, inside the operation
        self.assertIn("differs in: parameters", f["confidence_basis"])
        self.assertNotEqual(before["claim"], after["claim"])  # the claim holds the signature of the operation: a later edit of it is a new claim
        self.assertTrue(f["title"].startswith("The spec changes the contract of GET /v1/invoices/{invoiceId}"))

    def test_an_operation_changed_by_a_schema_two_hops_away_is_a_finding_for_each_operation_that_reaches_it(self):
        report = self.results["oa06"].report
        found = sorted(f["identity"][-2:] for f in report["findings"] if f["kind"] == "operation-changed")
        self.assertEqual(found, [["GET", "/v1/customers/{customerId}"], ["GET", "/v1/invoices"], ["GET", "/v1/invoices/{invoiceId}"], ["POST", "/v1/invoices"]])

    # ---- Shape A: a read that failed is an incompleteness, never an absence

    def test_a_spec_that_cannot_be_read_gives_a_blocking_record_no_route_and_no_claim_of_absence(self):
        r = self.results["oa07"]
        report = r.report
        self.assertTrue(r.out.startswith("Blocking: "), r.out[:200])
        self.assertEqual(report["areas"], [])
        self.assertEqual(report["findings"], [])
        self.assertEqual([(i["kind"], i["blocks_selection"]) for i in report["incompleteness"]], [("spec_unreadable", True)])
        prop = payload.propagation_from_dict(report["propagations"][0])
        self.assertFalse(prop.complete)
        self.assertEqual((prop.http_edges, prop.findings, prop.reached), ((), (), ()))
        self.assertIn("none were established", r.out)
        self.assertNotIn("No areas reached", r.out)

    def test_a_client_spec_that_cannot_be_read_is_a_blocking_edge_and_no_call_is_matched(self):
        r = self.results["oa10"]
        report = r.report
        kinds = sorted((i["kind"], i["subject"], i["blocks_selection"]) for i in report["incompleteness"])
        self.assertEqual(kinds, [("edge_incomplete", "edge:http:acme/ledger-web:acme/ledger-api", True), ("spec_unreadable", "acme/ledger-web:api/openapi.json", True)])
        prop = payload.propagation_from_dict(report["propagations"][0])
        self.assertFalse(prop.complete)
        self.assertEqual(prop.http_edges, ())
        self.assertEqual([a["label"] for a in report["areas"]], ["/invoices/:invoiceId"])  # what the web repository establishes by itself stays
        self.assertTrue(r.out.startswith("Blocking: "))

    def test_every_other_scenario_is_complete_and_the_two_that_fail_to_read_are_not(self):
        for k, r in self.results.items():
            prop = payload.propagation_from_dict(r.report["propagations"][0])
            with self.subTest(scenario=k):
                self.assertEqual(prop.complete, k not in ("oa07", "oa10"), list(prop.reasons)[:2])

    # ---- Shape B: a name that is everywhere does not link code

    def test_generic_and_over_defined_names_are_dropped_from_matching_by_name_recorded_and_still_resolved_when_imported(self):
        for k in ("oa01", "oa09"):
            report = self.results[k].report
            recs = sorted((i["kind"], i["subject"], i["blocks_selection"]) for i in report["incompleteness"])
            prefix = "acme/ledger-web:" if k == "oa01" else ""
            self.assertEqual(recs, [("not_distinctive", f"{prefix}downloadStatement", False), ("not_distinctive", f"{prefix}get", False), ("not_distinctive", f"{prefix}list", False)], k)
        prop = payload.propagation_from_dict(self.results["oa09"].report["propagations"][0])
        self.assertEqual(sorted((m.client.path, m.route.path) for m in prop.http_edges),  # the imports are resolved edges; cache.get(...) is no edge to GET /ping
                         [("src/modules/reports/pages/ReportsPage.tsx", "/v1/reports"), ("src/modules/statements/pages/StatementsPage.tsx", "/v1/statements/{statementId}/download")])
        self.assertEqual({a["label"] for a in self.results["oa09"].report["areas"]}, {"/reports", "/statements"})
        details = {i["subject"]: i["detail"] for i in self.results["oa09"].report["incompleteness"]}
        self.assertIn("generic", details["get"])
        self.assertIn("defined 4 times", details["downloadStatement"])

    def test_the_web_scenarios_name_the_api_edge_and_find_the_handler_modules(self):
        report = self.results["oa08"].report
        calls = {(b["route"]["method"], b["route"]["path"]): b for b in report["backend_calls"]}
        self.assertEqual(sorted(calls), [("DELETE", "/v1/invoices/{invoiceId}"), ("GET", "/v1/invoices/{invoiceId}")])
        for b in calls.values():
            self.assertEqual(b["handler"], "app/handlers/invoices.py")
            self.assertEqual([m["path"] for m in b["modules"]], ["app/services/ledger.py"])

    # ---- the report as a whole

    def test_every_human_readable_string_of_every_report_passes_the_wording_check(self):
        for k, r in self.results.items():
            data = r.report
            texts = [*(f["title"] for f in data["findings"]), *(f["confidence_basis"] for f in data["findings"]), *(i["detail"] for i in data["incompleteness"]),
                     *(i["not_established"] for i in data["incompleteness"]), *data["notes"], *r.out.splitlines()]
            for text in texts:
                self.assertEqual(wording.check(text), [], f"{k}: {text}")

    def test_the_summary_never_calls_a_whole_answer_complete_or_safe(self):
        for k, r in self.results.items():
            self.assertNotRegex(r.out.lower(), r"\bsafe\b|nothing to test|no need to test|\bcomplete\b", k)

    def test_no_report_says_anything_about_the_machine_it_ran_on(self):
        needles = [os.path.expanduser("~"), os.getcwd(), tempfile.gettempdir(), "/Users/", "/home/", "/var/folders", "file://", str(w.world().dest)]

        def strings(value):
            if isinstance(value, str):
                yield value
            elif isinstance(value, dict):
                for k, v in value.items():
                    yield k
                    yield from strings(v)
            elif isinstance(value, list):
                for v in value:
                    yield from strings(v)

        for k, r in self.results.items():
            for text in (*strings(r.report), *r.out.splitlines()):
                for needle in needles:
                    if needle and needle != "/":
                        self.assertNotIn(needle, text.replace(str(r.path), "") if r.path else text, f"{k}: {needle!r} in {text[:100]!r}")

    def test_every_finding_has_evidence_and_the_kinds_are_the_ones_the_scenarios_make(self):
        kinds = set()
        for k, r in self.results.items():
            for f in r.report["findings"]:
                kinds.add(f["kind"])
                with self.subTest(scenario=k, kind=f["kind"]):
                    self.assertTrue(f["evidence"])
                    self.assertIn(f["risk"], ("low", "medium", "high"))
                    self.assertIn(f["confidence"], ("low", "medium", "high"))
        self.assertEqual(kinds, {"direct", "cross-repo", "exclusion", "removed_route_still_called", "added_route_uncalled", "operation-changed"})

    def test_the_reports_render_to_markdown_and_html_and_name_the_new_kind(self):
        for k in ("oa05", "oa07"):
            data = report_pkg.check(self.results[k].report)
            md = report_pkg.render(data, "md")
            html = report_pkg.render(data, "html")
            self.assertTrue(md and html)
            if k == "oa05":
                self.assertIn("operation-changed", md)
                self.assertIn("An operation of a spec changes", md)

    def test_two_runs_give_the_same_bytes_except_the_run_object(self):
        for k in DETERMINISM:
            sc = self.scenarios[k]
            with tempfile.TemporaryDirectory(prefix="reachmap-oa-") as tmp:
                from reachmap import cli
                out, err = io.StringIO(), io.StringIO()
                e = w.world()
                code = cli.main(["analyse", "--config", str(bo.CONFIG_FILE), *sc_cli.scenario_args(sc), "--out", tmp], env=w.env_vars(), stdout=out, stderr=err, git_env=sg.isolated_env(e.home))
                self.assertEqual(code, 0)
                (path,) = Path(tmp).glob("*.json")
                with self.subTest(scenario=k):
                    self.assertEqual(json.dumps(strip_run(path.read_text(encoding="utf-8")), indent=2), json.dumps(strip_run(self.results[k].text), indent=2))

    def test_one_scenario_runs_as_a_separate_process_and_gives_the_same_report(self):
        sc = self.scenarios["oa05"]
        e = w.world()
        with tempfile.TemporaryDirectory(prefix="reachmap-oa-sub-") as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
            env.update(w.env_vars(), HOME=str(home), XDG_CONFIG_HOME=str(home / ".xdg"), PYTHONDONTWRITEBYTECODE="1")
            argv = [sys.executable, "-m", "reachmap", "analyse", "--config", str(bo.CONFIG_FILE), *sc_cli.scenario_args(sc), "--out", str(Path(tmp) / "out")]
            proc = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
            self.assertEqual((proc.returncode, proc.stderr), (0, ""))
            (path,) = (Path(tmp) / "out").glob("*.json")
            sub = path.read_text(encoding="utf-8")
            self.assertEqual(json.dumps(strip_run(sub), indent=2), json.dumps(strip_run(self.results["oa05"].text), indent=2))
            self.assertEqual(self.check("oa05", json.loads(sub)), [])
            self.assertTrue(e.dest.exists())

    # ---- the harness is not a rubber stamp

    def test_the_harness_fails_when_the_report_is_wrong(self):
        report = self.results["oa01"].report
        self.assertEqual(self.check("oa01", report), [])

        def broken(k, edit):
            data = json.loads(self.results[k].text)
            edit(data)
            return self.check(k, data)

        prop = lambda d: d["propagations"][0]  # noqa: E731
        own = lambda d: prop(d)["passes"][0]["reach"]  # noqa: E731  (the entry points of the changed repository's own pass)
        self.assertTrue(broken("oa01", lambda d: own(d).__setitem__("reached", [r for r in own(d)["reached"] if r["area"]["kind"] != "api_route"])))  # the route is missing
        self.assertTrue(broken("oa01", lambda d: prop(d).__setitem__("reached", [r for r in prop(d)["reached"] if r["area"]["kind"] != "screen"])))  # the screen is missing
        self.assertTrue(broken("oa01", lambda d: prop(d)["http_edges"].clear()))  # the edge is missing
        self.assertTrue(broken("oa01", lambda d: d["incompleteness"].clear()))  # the records are missing
        self.assertTrue(broken("oa01", lambda d: prop(d).__setitem__("complete", False)))  # incomplete beside records that say it is whole
        self.assertTrue(broken("oa01", lambda d: d["incompleteness"].pop()))  # one record fewer
        self.assertTrue(broken("oa07", lambda d: d["incompleteness"].clear()))  # the blocking record is missing
        self.assertTrue(broken("oa07", lambda d: prop(d).__setitem__("complete", True)))  # complete beside a blocking record
        self.assertTrue(broken("oa03", lambda d: [f for f in d["findings"] if f["kind"] == "removed_route_still_called"][0].__setitem__("risk", "low")))
        self.assertTrue(broken("oa05", lambda d: d["findings"].__setitem__(slice(None), [f for f in d["findings"] if f["kind"] != "operation-changed"])))  # the changed operation is not said
        self.assertTrue(broken("oa05", lambda d: [f for f in d["findings"] if f["kind"] == "operation-changed"][0].__setitem__("confidence", "low")))
        self.assertTrue(broken("oa08", lambda d: d["backend_calls"].clear()))  # the handler modules are missing
        self.assertTrue(broken("oa09", lambda d: d["incompleteness"].pop()))  # a dropped name is not recorded
        self.assertTrue(broken("oa04", lambda d: d["findings"].__setitem__(slice(None), [f for f in d["findings"] if f["kind"] != "added_route_uncalled"])))

    def test_the_harness_notices_an_extra_route_an_extra_edge_and_an_extra_screen(self):
        def with_extra(k, edit):
            data = json.loads(self.results[k].text)
            edit(data)
            return self.check(k, data)

        def another_route(d):
            reach = d["propagations"][0]["passes"][0]["reach"]
            extra = json.loads(json.dumps(next(r for r in reach["reached"] if r["area"]["kind"] == "api_route")))
            extra["area"]["label"] = "GET /v1/invoices"
            extra["routes"][0]["path"] = "/v1/invoices"
            reach["reached"].append(extra)

        def another_edge(d):
            edges = d["propagations"][0]["http_edges"]
            extra = json.loads(json.dumps(edges[0]))
            extra["route"]["path"] = "/v1/invoices"
            edges.append(extra)

        def another_screen(d):
            reached = d["propagations"][0]["reached"]
            extra = json.loads(json.dumps(next(r for r in reached if r["area"]["kind"] == "screen")))
            extra["area"]["label"] = "/invoices"
            reached.append(extra)

        self.assertTrue(with_extra("oa01", another_route))  # route is complete_for
        self.assertTrue(with_extra("oa01", another_edge))  # http_edge is complete_for
        self.assertTrue(with_extra("oa01", another_screen))  # screen is complete_for


if __name__ == "__main__":
    unittest.main()
