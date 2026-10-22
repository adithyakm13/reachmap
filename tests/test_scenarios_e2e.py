"""Every fixture scenario end to end through `reachmap analyse`: the report the CLI writes, read back, against expected.json with
the same checks the other scenario modules use (`scenario_support.check`: no type is skipped), and the report as a whole: every
Evidence resolves at its pinned sha, the output is deterministic, and one scenario runs as a separate process (`python -m reachmap`)."""

import dataclasses
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from reachmap import __version__, payload, wording
from tests import scenario_support as ss
from tests import support_cli as sc_cli
from tests import support_git as sg
from tests.fixtures import build

ROOT = Path(__file__).resolve().parents[1]
# run twice: a web change (s01), an api change reaching the web (s06), a removed route (s10), a contract change across three repositories (s11),
# the branch matrix (s13), consumers per branch of a released change (s17), the hop cap (s21)
DETERMINISM = ("s01", "s06", "s10", "s11", "s13", "s17", "s21")
FINDING_ONLY_KINDS = {"direct", "cross-repo", "exclusion", "guard-excluded", "contract-tightened", "contract-relaxed", "release", "branch-missing"}


def strip_run(text: str) -> dict:
    data = json.loads(text)
    data["run"] = None  # the run-time metadata field is the one thing that may differ between two runs
    data["review"]["as_of"] = data["review"]["decisions"]["as_of"] = None  # (and the time the decision store was read)
    return data


class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        e = ss.env()
        cls.scenarios = {sc["id"].split("-")[0]: sc for sc in build.scenarios(e.dest)}
        cls.results = {key: sc_cli.scenario_run(sc) for key, sc in cls.scenarios.items()}

    def test_all_23_scenarios_are_covered(self):
        self.assertEqual(sorted(self.scenarios), [f"s{n:02d}" for n in range(1, 24)])

    def test_every_run_completes_with_exit_0_and_a_report(self):
        for key, r in self.results.items():
            with self.subTest(scenario=key):
                self.assertEqual((r.code, r.err), (0, ""))
                self.assertIsNotNone(r.path)
                self.assertEqual(r.report["status"], "completed")
                self.assertEqual(r.report["schema"], 1)

    def test_no_type_is_skipped_in_any_scenario(self):
        for key, sc in self.scenarios.items():
            with self.subTest(scenario=key):
                self.assertEqual(ss.skipped_types(sc["expected"], ss.PY_TYPES, ss.PY_NOT_ESTABLISHED, repo=sc["repo"]), [])
                self.assertEqual(ss.skipped_types(sc["expected"], ss.JS_TYPES, ss.JS_NOT_ESTABLISHED, repo=sc["repo"]), [])

    def test_the_report_of_every_scenario_matches_expected_json(self):
        for key, sc in self.scenarios.items():
            with self.subTest(scenario=sc["id"]):
                run = sc_cli.to_run(sc, self.results[key].report)
                self.assertEqual(ss.check(run), [])

    def test_the_s13_branch_matrix_comes_through_the_cli(self):
        report = self.results["s13"].report
        rows = {b["branch"]: b for b in report["branches"]}
        self.assertEqual({b: r["state"] for b, r in rows.items()}, {"main": "pre_change", "release/1.x": "present", "release/1.2": "present"})
        repo = ss.env().repos["acme/api"]
        for name, row in rows.items():
            self.assertEqual(row["commit"], sg.rev(repo, name))
        self.assertEqual([f["kind"] for f in report["findings"] if f["kind"] == "branch-missing"], ["branch-missing"])
        self.assertIn("Branches: main pre_change", self.results["s13"].out)

    def test_lines_that_occur_elsewhere_in_a_file_are_not_the_change_on_a_branch(self):
        """s22 adds `    request: Request,` (every handler has it) and s14 adds `  });`: no branch has these changes."""
        for key in ("s14", "s22"):
            report = self.results[key].report
            with self.subTest(scenario=key):
                self.assertEqual({b["state"] for b in report["branches"]}, {"pre_change"})
                self.assertEqual([f for f in report["findings"] if f["kind"] == "branch-missing"], [])

    def test_no_report_says_anything_about_the_machine_it_ran_on(self):
        """No home directory, no temporary folder, no working directory, no `file://` remote in any string of any report or summary."""
        needles = [os.path.expanduser("~"), os.getcwd(), tempfile.gettempdir(), "/Users/", "/home/", "/var/folders", "/tmp", "file://", str(ss.env().dest)]

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

        seen = 0
        for key, r in self.results.items():
            for text in (*strings(r.report), *r.out.splitlines()):
                seen += 1
                for needle in needles:
                    if needle and needle != "/":
                        self.assertNotIn(needle, text.replace(str(r.path), "") if r.path else text, f"{key}: {needle!r} in {text[:120]!r}")
        self.assertGreater(seen, 10000)

    def test_a_local_remote_is_shown_as_the_repositorys_name(self):
        for key, r in self.results.items():
            self.assertEqual(r.report["change"]["remote"], self.scenarios[key]["repo"], key)

    def test_a_run_that_did_not_complete_has_null_results_in_its_json(self):
        failed = sc_cli.cli_run("--repo", "acme/api", "--compare", "main...does-not-exist")
        self.assertEqual(failed.code, 3)
        data = failed.report
        for key in ("findings", "areas", "branches", "propagations", "backend_calls", "suggestions", "mapped_tests", "counts", "ref"):
            self.assertIsNone(data[key], key)
        self.assertEqual([(r["kind"], r["subject"]) for r in data["incompleteness"]], [("run_failed", "compare main...does-not-exist")])
        self.assertNotIn("0" * 40, failed.text)
        self.assertEqual(data["change"]["resolved"], False)

    def test_the_areas_of_the_report_are_the_propagations_areas_once_each(self):
        """Every reached area is in `areas` once and nothing else is: a candidate dropped as a twin of an area reached by imports must not be."""
        from reachmap import pipeline
        for key, r in self.results.items():
            data = r.report
            prop = payload.propagation_from_dict(data["propagations"][0])
            wanted, _notes = pipeline.unique_areas(prop.reached)
            with self.subTest(scenario=key):
                self.assertEqual([a["label"] + "|" + a["repo"] + "|" + a["kind"] for a in data["areas"]], [x.area.label + "|" + x.area.repo + "|" + x.area.kind for x in wanted])
                self.assertEqual(data["areas"], [x.area.to_dict() for x in wanted])
        # and the harness fails when a by-name twin that was dropped is put back (or an area is lost)
        data = json.loads(self.results["s11"].text)
        prop = payload.propagation_from_dict(data["propagations"][0])
        twin = dataclasses.replace(prop.reached[0].area, by_name=True, priority="candidate")
        self.assertNotEqual(data["areas"] + [payload.encode(twin)], [payload.encode(x.area) for x in pipeline.unique_areas(prop.reached)[0]])
        self.assertNotEqual(data["areas"][1:], [payload.encode(x.area) for x in pipeline.unique_areas(prop.reached)[0]])

    def test_every_evidence_in_every_report_resolves_at_its_pinned_sha(self):
        total = 0
        for key, r in self.results.items():
            found = list(payload.evidence_in(r.report))
            total += len(found)
            with self.subTest(scenario=key, evidence=len(found)):
                self.assertEqual(ss.evidence_problems(ss.env().repos, found), [])
        self.assertGreater(total, 1000)  # the audit is looking at real evidence

    def test_an_area_finding_cites_the_definition_of_the_changed_symbol_first_and_then_the_chain(self):
        import re
        from reachmap import findings
        checked = 0
        for key, r in self.results.items():
            data = r.report
            symbols = [s for i in data["propagations"][0]["passes"][0]["analysis"]["impacts"] for s in i["symbols"] if s["name"] != "<module>"]
            for f in data["findings"]:
                if f["kind"] not in ("direct", "cross-repo"):
                    continue
                with self.subTest(scenario=key, finding=f["title"][:60]):
                    if symbols:
                        self.assertRegex(f["evidence"][0]["detail"], findings.DEFINITION_LINE)
                        checked += 1
                    changed = {(e["path"], e["line"], e["ref"]) for s in symbols for e in s["evidence"]}
                    first = f["evidence"][0]
                    self.assertFalse({(e["path"], e["line"], e["ref"]) for e in f["evidence"][1:]} & (changed - {(first["path"], first["line"], first["ref"])}))
        self.assertGreater(checked, 20)

    def test_every_finding_has_evidence_and_every_exclusion_names_its_proof(self):
        kinds = set()
        for key, r in self.results.items():
            for f in r.report["findings"]:
                kinds.add(f["kind"])
                with self.subTest(scenario=key, kind=f["kind"]):
                    self.assertTrue(f["evidence"])
                    self.assertIn(f["risk"], ("low", "medium", "high"))
                    self.assertIn(f["confidence"], ("low", "medium", "high"))
        # every kind a builder makes, except contract-relaxed: no fixture relaxes a field (test_findings builds one by hand)
        self.assertEqual(kinds, (FINDING_ONLY_KINDS | {"removed_route_still_called", "added_route_uncalled"}) - {"contract-relaxed"})

    def test_every_human_readable_string_of_every_report_passes_the_wording_check(self):
        for key, r in self.results.items():
            data = r.report
            texts = [*(f["title"] for f in data["findings"]), *(f["confidence_basis"] for f in data["findings"]), *(f["workflow"] for f in data["findings"]),
                     *(i["detail"] for i in data["incompleteness"]), *(i["not_established"] for i in data["incompleteness"]), *data["notes"],
                     *(b["detail"] for b in data["branches"]), *r.out.splitlines()]
            for text in texts:
                self.assertEqual(wording.check(text), [], f"{key}: {text}")

    def test_blocking_records_come_first_in_the_summary_and_a_whole_answer_is_never_called_complete(self):
        s16 = self.results["s16"]
        self.assertTrue(s16.out.startswith("Blocking: "))
        for key, r in self.results.items():
            self.assertNotRegex(r.out.lower(), r"\bsafe\b|nothing to test|no need to test|\bcomplete\b", key)

    def test_the_findings_of_a_scenario_are_the_expected_json_findings_and_those_the_pipeline_adds(self):
        s10 = self.results["s10"].report
        by_kind = {}
        for f in s10["findings"]:
            by_kind.setdefault(f["kind"], []).append(f)
        (removed,) = by_kind["removed_route_still_called"]
        self.assertEqual((removed["risk"], removed["confidence"]), ("high", "low"))
        self.assertEqual(len(by_kind["cross-repo"]), 1)  # the web screen the removed route's client reaches
        self.assertEqual(self.results["s16"].report["findings"], [])  # nothing is claimed without a pre-change version and no area was reached

    def test_two_runs_give_the_same_bytes_except_the_run_object(self):
        for key in DETERMINISM:
            sc = self.scenarios[key]
            out, err = self.fresh(sc)
            with self.subTest(scenario=key):
                self.assertEqual(json.dumps(strip_run(out), indent=2), json.dumps(strip_run(self.results[key].text), indent=2))

    def fresh(self, sc):
        """A second run of a scenario into a directory of its own (not the shared one)."""
        import io
        from reachmap import cli
        with tempfile.TemporaryDirectory(prefix="reachmap-e2e-") as tmp:
            out, err = io.StringIO(), io.StringIO()
            code = cli.main(["analyse", "--config", str(sc_cli.config_path(sc_cli.scenario_hops(sc))), *sc_cli.scenario_args(sc), "--out", tmp],
                            env=sc_cli.env_vars(), stdout=out, stderr=err, git_env=sg.isolated_env(ss.env().home))
            self.assertEqual(code, 0)
            (path,) = Path(tmp).glob("*.json")
            return path.read_text(encoding="utf-8"), out.getvalue()

    def test_one_scenario_runs_as_a_separate_process_and_gives_the_same_report(self):
        sc = self.scenarios["s11"]
        e = ss.env()
        with tempfile.TemporaryDirectory(prefix="reachmap-sub-") as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
            env.update(sc_cli.env_vars(), HOME=str(home), XDG_CONFIG_HOME=str(home / ".xdg"), PYTHONDONTWRITEBYTECODE="1")
            argv = [sys.executable, "-m", "reachmap", "analyse", "--config", str(sc_cli.config_path()), *sc_cli.scenario_args(sc), "--out", str(Path(tmp) / "out")]
            proc = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
            self.assertEqual((proc.returncode, proc.stderr), (0, ""))
            (path,) = (Path(tmp) / "out").glob("*.json")
            sub = path.read_text(encoding="utf-8")
            self.assertEqual(path.name, self.results["s11"].path.name)
            self.assertEqual(json.dumps(strip_run(sub), indent=2), json.dumps(strip_run(self.results["s11"].text), indent=2))
            self.assertEqual(proc.stdout.replace(str(Path(tmp) / "out"), "OUT").splitlines()[:-1], self.results["s11"].out.replace(str(self.results["s11"].path.parent), "OUT").splitlines()[:-1])
            self.assertEqual(ss.check(sc_cli.to_run(sc, json.loads(sub))), [])  # and it passes expected.json
            version = subprocess.run([sys.executable, "-m", "reachmap", "--version"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual((version.returncode, version.stdout.strip()), (0, f"reachmap {__version__}"))

    def test_the_harness_fails_when_the_report_is_wrong(self):
        """The mapping is not a rubber stamp: change the report and `ss.check` fails."""
        sc = self.scenarios["s11"]
        report = json.loads(self.results["s11"].text)
        self.assertEqual(ss.check(sc_cli.to_run(sc, report)), [])

        def broken(edit):
            data = json.loads(json.dumps(report))
            edit(data)
            return ss.check(sc_cli.to_run(sc, data))

        prop = lambda d: d["propagations"][0]  # noqa: E731
        self.assertTrue(broken(lambda d: prop(d)["consumers"].pop()))  # a missing consumer
        self.assertTrue(broken(lambda d: prop(d)["contract_reads"].pop()))  # a missing read
        self.assertTrue(broken(lambda d: prop(d)["http_edges"].clear()))  # a missing http edge
        self.assertTrue(broken(lambda d: prop(d).__setitem__("reached", [r for r in prop(d)["reached"] if r["area"]["kind"] != "screen"])))  # the screen is missing
        self.assertTrue(broken(lambda d: d["incompleteness"].clear()))  # the records are missing
        self.assertTrue(broken(lambda d: prop(d).__setitem__("complete", True)))  # complete beside records that say it is not
        # the finding rules of the README: another kind where `finding` is complete, a mirror without its area or exclusion, a lost mirror
        s10 = self.scenarios["s10"]
        r10 = json.loads(self.results["s10"].text)
        extra = dict(r10["findings"][0], kind="release", identity=["release", "x", "y", "z"], is_exclusion=False)
        r10["findings"].append(extra)
        self.assertTrue(any("complete_for" in f for f in ss.check(sc_cli.to_run(s10, r10))))
        r10 = json.loads(self.results["s10"].text)
        ghost = dict(r10["findings"][0], kind="cross-repo", identity=["cross-repo", "acme/web", "screen", "/nowhere"], is_exclusion=False)
        r10["findings"].append(ghost)
        self.assertTrue(any("one per reported area" in f for f in ss.check(sc_cli.to_run(s10, r10))))
        r10 = json.loads(self.results["s10"].text)
        r10["findings"] = [f for f in r10["findings"] if f["kind"] != "cross-repo"]
        self.assertTrue(any("one per reported area" in f for f in ss.check(sc_cli.to_run(s10, r10))))
        s05 = self.scenarios["s05"]
        r05 = json.loads(self.results["s05"].text)
        r05["findings"] = [f for f in r05["findings"] if f["kind"] != "exclusion"]
        self.assertTrue(any("one per stated exclusion" in f for f in ss.check(sc_cli.to_run(s05, r05))))
        s13 = self.scenarios["s13"]
        r13 = json.loads(self.results["s13"].text)
        r13["branches"][0]["state"] = "diverged"
        self.assertTrue(ss.check(sc_cli.to_run(s13, r13)))  # the wrong branch state
        s10 = self.scenarios["s10"]
        r10 = json.loads(self.results["s10"].text)
        for f in r10["findings"]:
            if f["kind"] == "removed_route_still_called":
                f["risk"] = "low"
        self.assertTrue(ss.check(sc_cli.to_run(s10, r10)))  # the wrong risk


if __name__ == "__main__":
    unittest.main()
