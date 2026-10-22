"""The M16 Spring Boot scenarios (tests/fixtures/spring) end to end through `reachmap analyse`: the report the CLI writes, read back, against
expected.json with the same checks the M1 scenarios use (`scenario_support.check`: no type is skipped), the route table of the adapter against the
independent reading of the fixtures, and the report as a whole: every Evidence resolves at its pinned sha, the output is deterministic, wording,
priorities, completeness.
"""

import json
import re
import unittest

from reachmap import payload, wording
from reachmap.adapters.jvm import JvmAdapter
from reachmap.adapters.spring import SpringAdapter
from tests import scenario_support as ss
from tests import support_git as sg
from tests import support_sb as sn
from tests.fixtures import sb_claims

IDS = [f"sb{n:02d}" for n in range(1, 21)]
SB_NOT_ESTABLISHED = ss.JS_NOT_ESTABLISHED | {"parse_degraded", "config_not_followed", "client_url_unmatched", "edge_incomplete", "route_path_partial", "route_change_withheld"}


def strip_run(text: str) -> dict:
    data = json.loads(text)
    data["run"] = None  # the run-time metadata is the one thing that may differ between two runs
    data["review"]["as_of"] = data["review"]["decisions"]["as_of"] = None
    return data


def adapter_routes(rev: str):
    e = sn.env()
    cfg = e.workspace.repos["acme/ledger"]
    with sn.sb_env():
        git = ss.make_git()
        language = JvmAdapter.from_config(cfg, git, limits=e.workspace.limits)
        entry = SpringAdapter.from_config(cfg, language)
        return entry.routes(None, sg.rev(e.repos["acme/ledger"], rev)), entry


class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenarios = {sc["id"].split("-")[0]: sc for sc in sn.scenarios()}
        cls.results = {key: sn.scenario_run(sc) for key, sc in cls.scenarios.items()}

    def test_the_scenarios_are_the_twenty_of_the_fixture_and_each_has_an_explanation(self):
        self.assertEqual(sorted(self.scenarios), IDS)
        for key, sc in self.scenarios.items():
            self.assertGreater(len(sc["expected"]["explains"]), 200, key)
            self.assertTrue(sc["expected"]["must_find"], key)

    def test_every_run_completes_with_exit_0_and_a_report(self):
        for key, r in self.results.items():
            with self.subTest(scenario=key):
                self.assertEqual((r.code, r.err), (0, ""))
                self.assertEqual(r.report["status"], "completed")

    def test_the_report_of_every_scenario_matches_expected_json(self):
        for key, sc in self.scenarios.items():
            with self.subTest(scenario=sc["id"]):
                self.assertEqual(sn.check(sc, self.results[key].report), [])

    def test_no_type_is_skipped_in_any_scenario(self):
        for key, sc in self.scenarios.items():
            with self.subTest(scenario=key):
                self.assertEqual(ss.skipped_types(sc["expected"], ss.JS_TYPES, SB_NOT_ESTABLISHED, repo=sc["repo"]), [])

    def test_the_harness_fails_when_the_report_is_wrong(self):
        sc = self.scenarios["sb01"]
        report = json.loads(self.results["sb01"].text)
        self.assertEqual(sn.check(sc, report), [])

        def broken(edit):
            data = json.loads(json.dumps(report))
            edit(data)
            return sn.check(sc, data)

        prop = lambda d: d["propagations"][0]  # noqa: E731
        own = lambda d: prop(d)["passes"][0]["reach"]  # noqa: E731
        self.assertTrue(broken(lambda d: prop(d)["http_edges"].clear()))  # a missing http edge
        self.assertTrue(broken(lambda d: own(d).__setitem__("reached", [])))  # a missing route
        self.assertTrue(broken(lambda d: prop(d).__setitem__("reached", [r for r in prop(d)["reached"] if r["area"]["repo"] != "acme/ledger-web"])))  # a missing screen
        self.assertTrue(broken(lambda d: prop(d).__setitem__("complete", False)))  # not complete, though no record says so
        sc12 = self.scenarios["sb12"]
        r12 = json.loads(self.results["sb12"].text)
        self.assertEqual(sn.check(sc12, r12), [])
        r12["incompleteness"] = []
        self.assertTrue(sn.check(sc12, r12))  # a record that is expected is missing
        sc10 = self.scenarios["sb10"]
        r10 = json.loads(self.results["sb10"].text)
        self.assertEqual(sn.check(sc10, r10), [])
        r10["propagations"][0]["passes"][0]["reach"]["complete"] = False
        self.assertTrue(sn.check(sc10, r10))  # the entry-point result says it is not complete and no record of its own says so

    def test_the_priority_of_an_api_route_is_must_test_and_of_a_screen_by_the_count_of_screens(self):
        for key, r in self.results.items():
            for a in r.report["areas"]:
                if a["kind"] == "api_route":
                    self.assertEqual(a["priority"], "must_test", key)
        self.assertEqual({a["priority"] for a in self.results["sb10"].report["areas"] if a["kind"] == "screen"}, {"must_test"})  # three screens

    def test_the_route_table_of_the_adapter_is_the_independent_reading_at_main_and_at_every_head_that_lexes(self):
        e = sn.env()
        repo = e.repos["acme/ledger"]
        for rev in ["main"] + [sc["change"]["head"] for sc in self.scenarios.values() if sc["repo"] == "acme/ledger"]:
            if rev in ("scenario/sb11-unlexable-controller",):
                continue  # a controller that does not lex has no route: the independent reading gives up there too
            truth = sb_claims.routes(repo, rev)
            got, _entry = adapter_routes(rev)
            with self.subTest(rev=rev):
                self.assertEqual({(r.method, r.path): r.file for r in got}, truth)
                self.assertTrue(all(not r.partial for r in got))

    def test_a_table_that_is_not_whole_is_said_and_lists_what_could_be_read(self):
        got, entry = adapter_routes("scenario/sb11-unlexable-controller")
        self.assertEqual(len(got), 6)  # the six routes of the other controllers; the seven of the controller that does not lex are not listed
        self.assertTrue(entry.problems)
        removed, added = entry.routes_changed(sg.rev(sn.env().repos["acme/ledger"], "main"), sg.rev(sn.env().repos["acme/ledger"], "scenario/sb11-unlexable-controller"))
        self.assertEqual((removed, added), ([], []))

    def test_an_unlexable_controller_makes_the_answer_not_complete_and_claims_no_route(self):
        report = self.results["sb11"].report
        self.assertFalse(report["propagations"][0]["complete"])
        self.assertEqual([a for a in report["areas"] if a["kind"] == "api_route"], [])
        self.assertEqual([(r["kind"], r["subject"], r["blocks_selection"]) for r in report["incompleteness"]],
                         [("parse_degraded", "src/main/java/com/acme/ledger/api/InvoiceController.java", True), ("route_change_withheld", "acme/ledger", True)])
        self.assertTrue(self.results["sb11"].out.startswith("Blocking: "))
        self.assertNotIn("removed_route_still_called", {f["kind"] for f in report["findings"]})

    def test_what_is_not_followed_is_a_blocking_record_and_no_route(self):
        for key, subject in (("sb13", "src/main/java/com/acme/ledger/config/WebConfig.java"), ("sb14", "src/main/resources/application.properties")):
            report = self.results[key].report
            self.assertEqual([(r["kind"], r["subject"], r["blocks_selection"]) for r in report["incompleteness"]], [("config_not_followed", subject, True)], key)
            self.assertEqual(report["areas"], [], key)
            self.assertFalse(report["propagations"][0]["complete"], key)

    def test_a_test_alone_reaches_no_area_and_the_summary_says_what_it_may_say(self):
        r = self.results["sb16"]
        self.assertEqual(r.report["areas"], [])
        self.assertIn("No areas reached by this change were found", r.out)
        self.assertTrue(r.report["propagations"][0]["complete"])

    def test_every_evidence_in_every_report_resolves_at_its_pinned_sha(self):
        total = 0
        e = sn.env()
        for key, r in self.results.items():
            found = list(payload.evidence_in(r.report))
            total += len(found)
            with self.subTest(scenario=key, evidence=len(found)):
                self.assertEqual(ss.evidence_problems(e.repos, found), [])
        self.assertGreater(total, 600)  # the audit is looking at real evidence

    def test_two_runs_give_the_same_bytes_except_the_run_object(self):
        import io
        import tempfile
        from pathlib import Path

        from reachmap import cli
        from tests.fixtures import sb_build

        e = sn.env()
        for key in ("sb01", "sb03", "sb09", "sb12"):
            sc = self.scenarios[key]
            with tempfile.TemporaryDirectory(prefix="reachmap-sb-e2e-") as tmp:
                code = cli.main(["analyse", "--config", str(sb_build.WORKSPACE_FILE), *sn.scenario_args(sc), "--out", tmp], env=sn.env_vars(), stdout=io.StringIO(), stderr=io.StringIO(),
                                git_env=sg.isolated_env(e.home))
                self.assertEqual(code, 0)
                (path,) = Path(tmp).glob("*.json")
                second = path.read_text(encoding="utf-8")
            with self.subTest(scenario=key):
                self.assertEqual(json.dumps(strip_run(second), indent=2), json.dumps(strip_run(self.results[key].text), indent=2))

    def test_one_scenario_runs_as_a_separate_process_and_the_kind_and_the_languages_are_known_to_a_fresh_interpreter(self):
        """`python -m reachmap analyse` in a process of its own loads the config (the kind `spring` and the languages `java` and `kotlin` are registered
        by importing the CLI) and gives the same report."""
        import os
        import subprocess
        import sys
        import tempfile
        from pathlib import Path

        from tests.fixtures import sb_build

        sc = self.scenarios["sb01"]
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="reachmap-sb-sub-") as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
            env.update(sn.env_vars(), HOME=str(home), XDG_CONFIG_HOME=str(home / ".xdg"), PYTHONDONTWRITEBYTECODE="1")
            argv = [sys.executable, "-m", "reachmap", "analyse", "--config", str(sb_build.WORKSPACE_FILE), *sn.scenario_args(sc), "--out", str(Path(tmp) / "out")]
            proc = subprocess.run(argv, cwd=root, env=env, capture_output=True, text=True, timeout=300)
            self.assertEqual((proc.returncode, proc.stderr), (0, ""))
            (path,) = (Path(tmp) / "out").glob("*.json")
            sub = path.read_text(encoding="utf-8")
        self.assertEqual(json.dumps(strip_run(sub), indent=2), json.dumps(strip_run(self.results["sb01"].text), indent=2))
        self.assertEqual(sn.check(sc, json.loads(sub)), [])

    def test_a_fresh_interpreter_that_only_reads_reports_knows_the_record_kinds_the_adapters_make(self):
        import subprocess
        import sys
        from pathlib import Path

        code = (
            "import reachmap.cli\n"
            "from reachmap.incomplete import kind_spec\n"
            "for k in ('config_not_followed', 'config_unread', 'route_style_unread', 'type_ambiguous', 'parse_degraded', 'not_distinctive'):\n"
            "    kind_spec(k)\n"
        )
        root = Path(__file__).resolve().parents[1]
        proc = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=120)
        self.assertEqual((proc.returncode, proc.stderr), (0, ""))

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

        e = sn.env()
        needles = [os.path.expanduser("~"), os.getcwd(), tempfile.gettempdir(), "/Users/", "/var/folders", "file://", str(e.dest)]
        for key, r in self.results.items():
            for needle in needles:
                if needle and needle != "/":
                    self.assertNotIn(needle, r.text, f"{key}: {needle!r}")

    def test_a_removed_or_added_route_is_a_finding_and_the_other_kinds_are_the_pipelines(self):
        kinds = {key: {f["kind"] for f in r.report["findings"]} for key, r in self.results.items()}
        for key in ("sb03", "sb09", "sb10"):
            self.assertIn("removed_route_still_called", kinds[key], key)
        for key in ("sb08", "sb15"):
            self.assertIn("added_route_uncalled", kinds[key], key)
        for key in set(kinds) - {"sb03", "sb09", "sb10", "sb08", "sb15"}:
            self.assertFalse({"removed_route_still_called", "added_route_uncalled"} & kinds[key], key)

    def test_the_scenario_files_hold_no_ascii_arrow_in_prose_and_each_scenario_has_its_own_purpose(self):
        notes = [sc["scenario"]["notes"] for sc in self.scenarios.values()]
        self.assertEqual(len(notes), len(set(notes)))
        self.assertTrue(all(re.match(r"^[A-Za-z_]", n) for n in notes))
        for sc in self.scenarios.values():
            self.assertNotRegex(sc["expected"]["explains"], r"->|=>")

    def test_a_generic_name_is_recorded_and_links_nothing_by_name(self):
        for key in ("sb02", "sb06"):
            r = self.results[key].report
            self.assertEqual([(i["kind"], i["subject"], i["blocks_selection"]) for i in r["incompleteness"]], [("not_distinctive", "get", False)], key)
        sb06 = self.results["sb06"].report
        self.assertEqual({a["label"] for a in sb06["areas"] if a["repo"] == "acme/ledger" and a["kind"] == "api_route"}, {"GET /ledger/v1/status"})  # not flags: it only calls the get of a map

    def test_the_text_of_a_comment_and_a_string_is_never_a_use(self):
        report = self.results["sb01"].report
        callers = {c["path"] for p in report["propagations"][0]["passes"][:1] for i in p["analysis"]["impacts"] for c in i["callers"]}
        self.assertNotIn("src/main/java/com/acme/ledger/support/Docs.java", callers)
        excluded = {x["subject_path"] for p in report["propagations"][0]["passes"][:1] for i in p["analysis"]["impacts"] for x in i["exclusions"]}
        self.assertNotIn("src/main/java/com/acme/ledger/support/Docs.java", excluded)


if __name__ == "__main__":
    unittest.main()
