"""M16, Django: every `dj` scenario end to end through `reachmap analyse`: the report the CLI writes, read back, against the hand-traced expected.json
with the checks of the M1 scenarios (`scenario_support.check`, nothing skipped), the list of types each scenario checks, every Evidence of every report
resolved at its pinned sha, determinism, one run as a separate process, and the acceptance bar of context 10 for this adapter: a trap that makes a
read fail is an incompleteness and never an empty answer marked complete, and a generic name links nothing.
"""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from reachmap import cli, payload, wording
from tests import scenario_support as ss
from tests import support_cli as sc_cli
from tests import support_dj as sd
from tests import support_git as sg

ROOT = Path(__file__).resolve().parents[1]
ALL_TYPES = frozenset({"changed_symbol", "changed_file", "caller", "unit_test", "route", "screen", "http_edge", "finding"})

# What each scenario claims, by type: pinned, so that a scenario that stops checking something fails here and is not silently weaker.
TYPES = {
    "dj01-view-body": {"must_find": {"changed_symbol", "caller", "unit_test", "route", "http_edge", "screen"}, "must_exclude": {"caller", "route", "screen"}},
    "dj02-method-decorator": {"must_find": {"changed_symbol", "caller", "unit_test", "route", "http_edge", "screen"}, "must_exclude": {"caller", "route", "screen"}},
    "dj03-class-view-method": {"must_find": {"changed_symbol", "caller", "unit_test", "route", "http_edge", "screen"}, "must_exclude": {"caller", "route", "screen"}},
    "dj04-service-helper": {"must_find": {"changed_symbol", "caller", "unit_test", "route", "http_edge", "screen"}, "must_exclude": {"caller", "route", "screen"}},
    "dj05-drf-action": {"must_find": {"changed_symbol", "route"}, "must_exclude": {"route"}},
    "dj06-serializer": {"must_find": {"changed_symbol", "caller", "route"}, "must_exclude": {"route"}},
    "dj07-unmounted-urls": {"must_find": {"changed_symbol", "caller"}, "must_exclude": {"route"}},
    "dj08-generic-name": {"must_find": {"changed_symbol", "caller", "route", "http_edge", "screen"}, "must_exclude": {"route", "screen"}},
    "dj09-include-moved": {"must_find": {"changed_symbol", "route", "http_edge", "finding", "screen"}, "must_exclude": {"route"}},
    "dj10-route-added": {"must_find": {"changed_symbol", "caller", "unit_test", "route", "finding"}, "must_exclude": {"route", "screen"}},
    "dj11-urlconf-syntax-error": {"must_find": {"changed_file"}, "must_exclude": {"route", "screen"}},
    "dj12-root-urlconf-dynamic": {"must_find": {"changed_symbol"}, "must_exclude": {"route", "screen"}},
    "dj13-regex-route": {"must_find": {"changed_symbol", "caller", "unit_test", "route"}, "must_exclude": {"caller", "route", "screen"}},
    "dj14-settings-flag": {"must_find": {"changed_symbol"}, "must_exclude": {"route"}},
}
RECORDS = {
    "dj07-unmounted-urls": ["urlconf_unmounted"], "dj08-generic-name": ["not_distinctive"], "dj09-include-moved": ["client_url_unmatched", "edge_incomplete"],
    "dj11-urlconf-syntax-error": ["mount_unresolved", "parse_degraded", "route_change_withheld"], "dj12-root-urlconf-dynamic": ["route_change_withheld", "urlconf_root_unknown"], "dj13-regex-route": ["route_path_partial"],
    "dj14-settings-flag": ["settings_not_followed"],
}


def strip_run(text: str) -> dict:
    data = json.loads(text)
    data["run"] = None  # the run-time metadata field is the one thing that may differ between two runs
    data["review"]["as_of"] = data["review"]["decisions"]["as_of"] = None
    return data


class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenarios = {sc["id"]: sc for sc in sd.scenarios()}
        cls.results = {sid: sd.scenario_run(sc) for sid, sc in cls.scenarios.items()}

    def report(self, sid):
        return self.results[sid].report

    def labels(self, sid, repo=None):
        return [a["label"] for a in self.report(sid)["areas"] if repo is None or a["repo"] == repo]

    def blocking(self, sid):
        return sorted((r["kind"], r["subject"]) for r in self.report(sid)["incompleteness"] if r["blocks_selection"])

    # ---- the scenarios

    def test_all_14_scenarios_are_covered(self):
        self.assertEqual(sorted(self.scenarios), sorted(TYPES))

    def test_every_run_completes_with_exit_0_and_a_report(self):
        for sid, r in self.results.items():
            with self.subTest(scenario=sid):
                self.assertEqual((r.code, r.err), (0, ""))
                self.assertEqual(r.report["status"], "completed")
                self.assertEqual(r.report["schema"], 1)

    def test_the_report_of_every_scenario_matches_expected_json(self):
        for sid, sc in self.scenarios.items():
            with self.subTest(scenario=sid):
                self.assertEqual(sd.check(sc, self.report(sid)), [])

    def test_each_scenario_names_the_types_it_checks_and_none_is_skipped(self):
        for sid, sc in self.scenarios.items():
            exp = sc["expected"]
            got = {sec: {e["type"] for e in exp[sec]} for sec in ("must_find", "must_exclude")}
            with self.subTest(scenario=sid):
                self.assertEqual(got, TYPES[sid])
                self.assertTrue(set().union(*got.values()) <= ALL_TYPES)
                self.assertEqual(ss.skipped_types(exp, ss.PY_TYPES, ss.PY_NOT_ESTABLISHED | {"client_url_unmatched", "edge_incomplete", "parse_degraded", "mount_unresolved",
                                                                                        "urlconf_root_unknown", "route_path_partial", "settings_not_followed", "urlconf_unmounted", "route_change_withheld"}), [])
                self.assertEqual(sorted(e["kind"] for e in exp["not_established"]), RECORDS.get(sid, []))

    def test_every_type_the_adapter_makes_is_claimed_by_some_scenario(self):
        used = set()
        for t in TYPES.values():
            for types in t.values():
                used |= types
        self.assertEqual(used, ALL_TYPES)

    def test_every_evidence_in_every_report_resolves_at_its_pinned_sha(self):
        total = 0
        for sid, r in self.results.items():
            found = list(payload.evidence_in(r.report))
            total += len(found)
            with self.subTest(scenario=sid, evidence=len(found)):
                self.assertEqual(ss.evidence_problems(sd.env().repos, found), [])
        self.assertGreater(total, 150)  # the audit is looking at real evidence

    def test_the_findings_and_areas_follow_the_reported_routes(self):
        for sid in self.scenarios:
            data = self.report(sid)
            routes = [a for a in data["areas"] if a["kind"] == "api_route"]
            direct = [f for f in data["findings"] if f["kind"] == "direct"]
            with self.subTest(scenario=sid):
                self.assertEqual(len(direct), len([a for a in routes]))
                for f in data["findings"]:
                    self.assertTrue(f["evidence"])

    def test_every_human_readable_string_of_every_report_passes_the_wording_check(self):
        for sid, r in self.results.items():
            data = r.report
            texts = [*(f["title"] for f in data["findings"]), *(f["confidence_basis"] for f in data["findings"]), *(i["detail"] for i in data["incompleteness"]),
                     *(i["not_established"] for i in data["incompleteness"]), *data["notes"], *r.out.splitlines()]
            for text in texts:
                self.assertEqual(wording.check(text), [], f"{sid}: {text}")

    def test_a_blocking_record_comes_first_in_the_summary_and_a_whole_answer_is_never_called_complete(self):
        for sid in ("dj11-urlconf-syntax-error", "dj12-root-urlconf-dynamic", "dj14-settings-flag"):
            self.assertTrue(self.results[sid].out.startswith("Blocking: "), sid)
        for sid, r in self.results.items():
            self.assertNotRegex(r.out.lower(), r"\bsafe\b|nothing to test|no need to test", sid)

    # ---- the routes, as the scenarios trace them

    def test_the_routes_reached_are_the_ones_traced_and_no_more(self):
        self.assertEqual(self.labels("dj01-view-body", "acme/portal"), ["ANY /billing/invoices"])
        self.assertEqual(self.labels("dj02-method-decorator", "acme/portal"), ["GET /billing/invoices/{invoice_id}", "PUT /billing/invoices/{invoice_id}"])
        self.assertEqual(self.labels("dj03-class-view-method", "acme/portal"), ["POST /billing/invoices/{invoice_id}/void"])
        self.assertEqual(self.labels("dj04-service-helper", "acme/portal"), ["ANY /billing/invoices", "GET /api/v1/invoices/overdue"])
        self.assertEqual(self.labels("dj05-drf-action"), ["POST /api/v1/invoices/{pk}/void"])
        self.assertEqual(len(self.labels("dj06-serializer")), 6)
        self.assertEqual(self.labels("dj07-unmounted-urls"), [])
        self.assertEqual(self.labels("dj08-generic-name", "acme/portal"), ["ANY /cases"])
        self.assertEqual(self.labels("dj13-regex-route"), ["ANY /legacy/{year}"])

    def test_a_route_that_is_removed_still_called_is_a_finding_and_an_added_one_nobody_calls_is_said(self):
        by_kind = lambda sid, kind: [f for f in self.report(sid)["findings"] if f["kind"] == kind]  # noqa: E731
        (removed,) = by_kind("dj09-include-moved", "removed_route_still_called")
        self.assertEqual((removed["risk"], removed["confidence"]), ("high", "low"))
        self.assertEqual(by_kind("dj09-include-moved", "added_route_uncalled"), [])  # a client call fits nothing: that cannot be said
        (added,) = by_kind("dj10-route-added", "added_route_uncalled")
        self.assertEqual((added["risk"], added["confidence"]), ("low", "medium"))
        self.assertEqual(by_kind("dj10-route-added", "removed_route_still_called"), [])

    def test_the_regex_route_is_partial_and_no_client_url_makes_it_a_must_test_screen(self):
        data = self.report("dj13-regex-route")
        self.assertEqual([a["label"] for a in data["areas"] if a["by_name"]], [])
        route = data["propagations"][0]["reached"][0]["routes"][0]
        self.assertTrue(route["partial"])

    # ---- the acceptance bar: reads that fail, and generic names

    def test_a_trap_that_makes_a_read_fail_is_an_incompleteness_and_never_an_empty_answer_marked_complete(self):
        for sid, kinds in (
            ("dj11-urlconf-syntax-error", [("mount_unresolved", "config/urls.py:9"), ("parse_degraded", "billing/urls.py"), ("route_change_withheld", "acme/portal")]),
            ("dj12-root-urlconf-dynamic", [("route_change_withheld", "acme/portal"), ("urlconf_root_unknown", "acme/portal")]),
            ("dj14-settings-flag", [("settings_not_followed", "config/settings/base.py")]),
        ):
            data = self.report(sid)
            with self.subTest(scenario=sid):
                self.assertEqual(data["areas"], [])
                self.assertEqual(self.blocking(sid), kinds)
                self.assertFalse(data["propagations"][0]["complete"])
                self.assertEqual(data["counts"]["areas"], 0)
                self.assertNotIn("No areas reached by this change were found", self.results[sid].out)  # that sentence is for a run with nothing blocking

    def test_a_file_that_cannot_be_read_does_not_turn_its_routes_into_removed_ones(self):
        for sid in ("dj11-urlconf-syntax-error", "dj12-root-urlconf-dynamic"):
            kinds = [f["kind"] for f in self.report(sid)["findings"]]
            with self.subTest(scenario=sid):
                self.assertNotIn("removed_route_still_called", kinds)
                self.assertEqual(self.report(sid)["propagations"][0]["http_edges"], [])

    def test_a_generic_name_is_recorded_and_links_only_by_import(self):
        self.assertEqual([(r["kind"], r["subject"], r["blocks_selection"]) for r in self.report("dj08-generic-name")["incompleteness"]], [("not_distinctive", "index", False)])
        self.assertEqual(self.labels("dj08-generic-name", "acme/portal"), ["ANY /cases"])  # the index of billing, with the same name, is not reached
        self.assertTrue(self.report("dj08-generic-name")["propagations"][0]["complete"])

    def test_an_unmounted_urlconf_is_said_and_no_route_is_claimed_for_it(self):
        data = self.report("dj07-unmounted-urls")
        self.assertEqual([(r["kind"], r["subject"], r["blocks_selection"]) for r in data["incompleteness"]], [("urlconf_unmounted", "billing/legacy_urls.py:urlpatterns", False)])
        self.assertEqual(data["areas"], [])
        self.assertTrue(data["propagations"][0]["complete"])

    # ---- the whole report

    def test_two_runs_give_the_same_bytes_except_the_run_object(self):
        for sid in ("dj04-service-helper", "dj09-include-moved", "dj11-urlconf-syntax-error"):
            sc = self.scenarios[sid]
            out, err = self.fresh(sc)
            with self.subTest(scenario=sid):
                self.assertEqual(json.dumps(strip_run(out), indent=2), json.dumps(strip_run(self.results[sid].text), indent=2))

    def fresh(self, sc):
        e = sd.env()
        with tempfile.TemporaryDirectory(prefix="reachmap-dj-again-") as tmp:
            out, err = io.StringIO(), io.StringIO()
            code = cli.main(["analyse", "--config", str(sd.config_path()), *sd.scenario_args(sc), "--out", tmp], env=sd.env_vars(), stdout=out, stderr=err, git_env=sg.isolated_env(e.home))
            self.assertEqual(code, 0)
            (path,) = Path(tmp).glob("*.json")
            return path.read_text(encoding="utf-8"), out.getvalue()

    def test_one_scenario_runs_as_a_separate_process_and_gives_the_same_report(self):
        sc = self.scenarios["dj04-service-helper"]
        with tempfile.TemporaryDirectory(prefix="reachmap-dj-sub-") as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
            env.update(sd.env_vars(), HOME=str(home), XDG_CONFIG_HOME=str(home / ".xdg"), PYTHONDONTWRITEBYTECODE="1")
            argv = [sys.executable, "-m", "reachmap", "analyse", "--config", str(sd.config_path()), *sd.scenario_args(sc), "--out", str(Path(tmp) / "out")]
            proc = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
            self.assertEqual((proc.returncode, proc.stderr), (0, ""))
            (path,) = (Path(tmp) / "out").glob("*.json")
            sub = path.read_text(encoding="utf-8")
            self.assertEqual(json.dumps(strip_run(sub), indent=2), json.dumps(strip_run(self.results["dj04-service-helper"].text), indent=2))
            self.assertEqual(sd.check(sc, json.loads(sub)), [])

    def test_the_harness_fails_when_the_report_is_wrong(self):
        """The mapping is not a rubber stamp: change the report and the check fails."""
        sc = self.scenarios["dj04-service-helper"]
        report = json.loads(self.results["dj04-service-helper"].text)
        self.assertEqual(sd.check(sc, report), [])

        def broken(edit, scenario=sc, base=report):
            data = json.loads(json.dumps(base))
            edit(data)
            return sd.check(scenario, data)

        prop = lambda d: d["propagations"][0]  # noqa: E731
        reach = lambda d: prop(d)["passes"][0]["reach"]  # noqa: E731
        self.assertTrue(broken(lambda d: reach(d).__setitem__("reached", [r for r in reach(d)["reached"] if r["area"]["label"] != "GET /api/v1/invoices/overdue"])))  # a route is missing
        def invent(d):
            ghost = json.loads(json.dumps(reach(d)["reached"][0]))
            ghost["routes"][0]["path"] = "/api/v1/ghost"
            reach(d)["reached"].append(ghost)

        self.assertTrue(broken(invent))  # a route that is made up
        self.assertTrue(broken(lambda d: prop(d).__setitem__("reached", [r for r in prop(d)["reached"] if r["area"]["kind"] != "screen"])))  # the screen is missing
        self.assertTrue(broken(lambda d: prop(d)["http_edges"].clear()))  # the http edges are missing
        self.assertTrue(broken(lambda d: prop(d).__setitem__("complete", False)))  # incomplete beside no record that says so
        # a record that a scenario expects is missing, or one it does not expect is there
        sc07 = self.scenarios["dj07-unmounted-urls"]
        r07 = json.loads(self.results["dj07-unmounted-urls"].text)
        self.assertEqual(sd.check(sc07, r07), [])
        self.assertTrue(broken(lambda d: d["incompleteness"].clear(), sc07, r07))
        sc11 = self.scenarios["dj11-urlconf-syntax-error"]
        r11 = json.loads(self.results["dj11-urlconf-syntax-error"].text)
        self.assertTrue(broken(lambda d: d["incompleteness"].pop(), sc11, r11))  # a blocking record lost
        self.assertTrue(broken(lambda d: prop(d).__setitem__("complete", True), sc11, r11))  # complete beside blocking records
        sc09 = self.scenarios["dj09-include-moved"]
        r09 = json.loads(self.results["dj09-include-moved"].text)
        self.assertTrue(broken(lambda d: [f.__setitem__("risk", "low") for f in d["findings"] if f["kind"] == "removed_route_still_called"], sc09, r09))  # the wrong risk


if __name__ == "__main__":
    unittest.main()
