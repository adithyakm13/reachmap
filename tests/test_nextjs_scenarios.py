"""The M16 Next.js scenarios (tests/fixtures/nextjs) end to end through `reachmap analyse`: the report the CLI writes, read back,
against expected.json with the same checks the M1 scenarios use (`scenario_support.check`: no type is skipped), and the report as
a whole: every Evidence resolves at its pinned sha, the output is deterministic, wording, priorities, completeness.
"""

import json
import re
import unittest

from reachmap import payload, wording
from tests import scenario_support as ss
from tests import support_nx as sn

IDS = [f"nx{n:02d}" for n in range(1, 19)]


def strip_run(text: str) -> dict:
    data = json.loads(text)
    data["run"] = None  # the run-time metadata is the one thing that may differ between two runs
    data["review"]["as_of"] = data["review"]["decisions"]["as_of"] = None
    return data


class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenarios = {sc["id"].split("-")[0]: sc for sc in sn.scenarios()}
        cls.results = {key: sn.scenario_run(sc) for key, sc in cls.scenarios.items()}

    def test_the_scenarios_are_the_eighteen_of_the_brief_and_each_has_an_explanation(self):
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
                self.assertEqual(ss.skipped_types(sc["expected"], ss.JS_TYPES, ss.JS_NOT_ESTABLISHED | {"route_intercepted", "route_path_partial", "parse_degraded"}, repo=sc["repo"]), [])

    def test_the_harness_fails_when_the_report_is_wrong(self):
        sc = self.scenarios["nx04"]
        report = json.loads(self.results["nx04"].text)
        self.assertEqual(sn.check(sc, report), [])

        def broken(edit):
            data = json.loads(json.dumps(report))
            edit(data)
            return sn.check(sc, data)

        prop = lambda d: d["propagations"][0]  # noqa: E731
        self.assertTrue(broken(lambda d: prop(d)["http_edges"].clear()))  # a missing http edge
        own = lambda d: prop(d)["passes"][0]["reach"]  # noqa: E731
        self.assertTrue(broken(lambda d: own(d).__setitem__("reached", [r for r in own(d)["reached"] if r["area"]["label"] != "GET /api/orders"])))  # a missing route
        self.assertTrue(broken(lambda d: own(d).__setitem__("reached", [r for r in own(d)["reached"] if r["area"]["label"] != "/cart"])))  # a missing screen
        self.assertTrue(broken(lambda d: prop(d).__setitem__("reached", [r for r in prop(d)["reached"] if r["area"]["label"] != "/orders"])))  # a missing screen of the client
        self.assertTrue(broken(lambda d: prop(d).__setitem__("complete", False)))  # not complete, though no record says so
        sc5 = self.scenarios["nx05"]
        r5 = json.loads(self.results["nx05"].text)
        self.assertEqual(sn.check(sc5, r5), [])
        r5["incompleteness"] = []
        self.assertTrue(sn.check(sc5, r5))  # a record that is expected is missing

    def test_priority_is_by_the_count_of_screens(self):
        def priorities(key):
            return {a["priority"] for a in self.results[key].report["areas"] if a["kind"] == "screen" or key in ("nx05",)}

        self.assertEqual(priorities("nx02"), {"must_test"})  # three screens
        self.assertEqual(priorities("nx03"), {"sample"})  # eleven
        self.assertEqual(priorities("nx17"), {"sample"})  # eighteen
        self.assertEqual(priorities("nx07"), {"must_test"})  # four

    def test_a_wrapper_says_how_many_pages_it_reaches_and_the_nesting_is_cited_in_the_chain(self):
        notes = {key: [n for n in r.report["notes"] if "wraps" in n or "middleware" in n or "was removed" in n] for key, r in self.results.items()}
        self.assertEqual(len(notes["nx02"]), 1)
        self.assertIn("src/app/(shop)/layout.tsx wraps the 3 screen(s) below its folder", notes["nx02"][0])
        self.assertIn("src/app/layout.tsx wraps the 11 screen(s) below its folder", notes["nx03"][0])
        self.assertIn("src/pages/_app.tsx wraps every page of the Pages Router (4 screen(s))", notes["nx07"][0])
        self.assertIn("was removed", notes["nx15"][0])
        self.assertIn("matcher is not evaluated", notes["nx17"][0])
        for key in ("nx01", "nx04", "nx05", "nx10", "nx12", "nx18"):
            self.assertEqual(notes[key], [], key)  # no wrapper, nothing to say
        findings = [f for f in self.results["nx02"].report["findings"] if f["kind"] == "direct"]
        self.assertEqual(len(findings), 3)
        for f in findings:
            claims = [e.get("claim") for e in f["evidence"]]
            self.assertTrue(any(c and c.startswith("wraps:src/app/(shop)/layout.tsx") for c in claims), claims)  # the nesting is cited, not implied

    def test_middleware_reaches_what_the_route_table_has(self):
        """nx17: every screen and route handler of the shop at the head (the route table of the adapter's own reading), and the client screens they lead to."""
        report = self.results["nx17"].report
        shop = {(a["kind"], a["label"]) for a in report["areas"] if a["repo"] == "nx/shop"}
        from tests.fixtures import nx_claims

        e = sn.env()
        truth = nx_claims.areas_of(e.repos["nx/shop"], self.scenarios["nx17"]["change"]["head"])
        self.assertEqual(shop, truth)
        self.assertEqual(len(shop), 23)
        self.assertEqual({a["label"] for a in report["areas"] if a["repo"] == "nx/mobile"}, {"/orders", "/orders/:orderId", "/status"})

    def test_the_removed_layout_cites_the_layout_at_the_base_and_the_pages_at_the_head(self):
        report = self.results["nx15"].report
        e = sn.env()
        base = ss.sg.rev(e.repos["nx/shop"], "main")
        head = ss.sg.rev(e.repos["nx/shop"], "scenario/nx15-removed-layout")
        for a in report["areas"]:
            self.assertEqual([x["ref"] for x in a["chains"][0]], [base, head], a["label"])

    def test_an_unlexable_handler_makes_the_answer_not_complete_and_claims_no_route(self):
        report = self.results["nx16"].report
        self.assertFalse(report["propagations"][0]["complete"])
        self.assertEqual(report["areas"], [])
        self.assertEqual([(r["kind"], r["subject"], r["blocks_selection"]) for r in report["incompleteness"]], [("parse_degraded", "src/app/api/broken/route.ts", True)])
        self.assertTrue(self.results["nx16"].out.startswith("Blocking: "))

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
        from tests import support_git as sg
        from tests.fixtures import nx_build

        e = sn.env()
        for key in ("nx02", "nx04", "nx13", "nx17"):
            sc = self.scenarios[key]
            with tempfile.TemporaryDirectory(prefix="reachmap-nx-e2e-") as tmp:
                code = cli.main(["analyse", "--config", str(nx_build.WORKSPACE_FILE), *sn.scenario_args(sc), "--out", tmp], env=sn.env_vars(), stdout=io.StringIO(), stderr=io.StringIO(),
                                git_env=sg.isolated_env(e.home))
                self.assertEqual(code, 0)
                (path,) = Path(tmp).glob("*.json")
                second = path.read_text(encoding="utf-8")
            with self.subTest(scenario=key):
                self.assertEqual(json.dumps(strip_run(second), indent=2), json.dumps(strip_run(self.results[key].text), indent=2))

    def test_one_scenario_runs_as_a_separate_process_and_the_kind_is_known_to_a_fresh_interpreter(self):
        """`python -m reachmap analyse` in a process of its own loads the config (the kind `nextjs` is registered by importing the CLI) and gives the same report."""
        import os
        import subprocess
        import sys
        import tempfile
        from pathlib import Path

        from tests.fixtures import nx_build

        sc = self.scenarios["nx02"]
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="reachmap-nx-sub-") as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
            env.update(sn.env_vars(), HOME=str(home), XDG_CONFIG_HOME=str(home / ".xdg"), PYTHONDONTWRITEBYTECODE="1")
            argv = [sys.executable, "-m", "reachmap", "analyse", "--config", str(nx_build.WORKSPACE_FILE), *sn.scenario_args(sc), "--out", str(Path(tmp) / "out")]
            proc = subprocess.run(argv, cwd=root, env=env, capture_output=True, text=True, timeout=300)
            self.assertEqual((proc.returncode, proc.stderr), (0, ""))
            (path,) = (Path(tmp) / "out").glob("*.json")
            sub = path.read_text(encoding="utf-8")
        self.assertEqual(json.dumps(strip_run(sub), indent=2), json.dumps(strip_run(self.results["nx02"].text), indent=2))
        self.assertEqual(sn.check(sc, json.loads(sub)), [])

    def test_each_report_passes_the_check_and_renders_in_every_format_and_view(self):
        from reachmap import report
        from reachmap.report import blocks, layout
        from tests.test_report_html import Parsed

        for key, r in self.results.items():
            data = json.loads(json.dumps(r.report))
            with self.subTest(key):
                report.check(data)
                for view in ("full", "author"):
                    md = report.render(data, "md", view=view)
                    page = report.render(data, "html", view=view)
                    self.assertTrue(md.startswith("# Reachmap report: "))
                    self.assertEqual(json.loads(Parsed(page).json_scripts[0]["text"]), data)
                    for text, content in blocks.strings(layout.build(data, view=view)):
                        if not content:
                            self.assertEqual(wording.check(text), [], text)
                for a in data["areas"]:  # an area's label and chain are on the page as the report has them
                    self.assertIn(a["label"], report.render(data, "md"))

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
            text = r.text
            for needle in needles:
                if needle and needle != "/":
                    self.assertNotIn(needle, text, f"{key}: {needle!r}")

    def test_a_removed_or_added_route_is_a_finding_and_the_other_kinds_are_the_pipelines(self):
        kinds = {key: {f["kind"] for f in r.report["findings"]} for key, r in self.results.items()}
        self.assertIn("removed_route_still_called", kinds["nx13"])
        self.assertIn("added_route_uncalled", kinds["nx14"])
        for key in set(kinds) - {"nx13", "nx14"}:
            self.assertFalse({"removed_route_still_called", "added_route_uncalled"} & kinds[key], key)

    def test_the_scenario_files_hold_no_banned_character_and_no_ascii_arrow_in_prose(self):
        banned = chr(0x2013) + chr(0x2014) + chr(0x2192) + chr(0x2018) + chr(0x2019) + chr(0x201c) + chr(0x201d) + "".join(chr(c) for c in range(0x202a, 0x202f)) + "".join(chr(c) for c in range(0x2066, 0x206a))  # en and em dash, arrow, curly quotes, bidi controls (built from code points: no raw one in this file)
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
