"""`reachmap analyse` (M9): the exit codes, the report file, the summary on stdout and what it never says. In process, against
the M1 fixture repositories through the cache the scenario modules fill; the one run as a separate process is in
test_scenarios_e2e."""

import hashlib
import io
import json
import os
import re
import unittest

from unittest import mock

import reachmap
from reachmap import cli, pipeline, wording
from tests import scenario_support as ss
from tests import support_cli
from tests import support_git as sg

FIXTURE_TOML = ss.FIXTURES / "reachmap.toml"
CLAIM = "No areas reached by this change were found; see the report for what was examined."


def seen(*args):
    """(exit code, stdout, stderr) of a run whose output the test only reads: made once per process and shared with the end to
    end scenarios (a deterministic run is not repeated; the orphan scenario s16 alone costs about five seconds)."""
    r = support_cli.cli_run(*args)
    return r.code, r.out, r.err


class CliCase(sg.TempDirCase):
    def setUp(self):
        super().setUp()
        e = ss.env()
        self.config = self.tmp / "reachmap.toml"
        self.config.write_text(FIXTURE_TOML.read_text())
        self.env = {"REACHMAP_FIXTURES": "file://" + str(e.dest), "REACHMAP_CACHE": str(e.cache)}
        self.out = self.tmp / "out"

    def go(self, *args, env=None, config=True, **kw):
        stdout, stderr = io.StringIO(), io.StringIO()
        argv = ["analyse", *(["--config", str(self.config)] if config else []), *args]
        if "--out" not in args:
            argv += ["--out", str(self.out)]
        code = cli.main(argv, env=env if env is not None else self.env, stdout=stdout, stderr=stderr, git_env=sg.isolated_env(ss.env().home), **kw)
        return code, stdout.getvalue(), stderr.getvalue()

    def reports(self):
        return sorted(self.out.glob("*.json")) if self.out.exists() else []

    def report(self):
        (path,) = self.reports()
        return json.loads(path.read_text(encoding="utf-8"))


S13 = ("--repo", "acme/api", "--compare", "main...scenario/s13-branch-matrix")
S11 = ("--repo", "acme/contracts", "--compare", "main...scenario/s11-contract-field")


class Success(CliCase):
    def test_a_completed_run_exits_0_and_writes_the_report_in_an_out_directory_it_makes(self):
        out = self.tmp / "deep" / "er"
        code, stdout, err = self.go(*S13, "--out", str(out), now=lambda: 1_700_000_000.0)
        self.assertEqual((code, err), (0, ""))
        (path,) = list(out.glob("*.json"))
        head = sg.rev(ss.env().repos["acme/api"], "scenario/s13-branch-matrix")
        self.assertRegex(path.name, rf"^acme-api-compare-main-scenario-s13-branch-matrix-[0-9a-f]{{8}}-{head[:12]}-r1\.json$")
        self.assertIn(str(path), stdout)
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["run"]["started_at"], "2023-11-14T22:13:20Z")  # the time is the caller's: the run object is the one thing that moves
        raw = path.read_bytes()
        self.assertTrue(raw.endswith(b"\n"))
        self.assertEqual(sorted(p.name for p in out.iterdir() if not p.name.startswith(".")), [path.name])  # written whole: no temporary file is left (the dot folder is the review layer's log)

    def test_the_report_is_versioned_and_has_the_analysis_and_its_metadata(self):
        r = support_cli.cli_run(*S13)
        data = r.report
        self.assertEqual(list(data)[:5], ["schema", "tool", "run", "inputs", "counts"])
        self.assertEqual(data["schema"], 1)
        self.assertEqual(data["tool"], {"name": "reachmap", "version": reachmap.__version__})
        self.assertEqual(sorted(data["run"]), ["elapsed_s", "started_at"])
        self.assertRegex(data["run"]["started_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        inputs = data["inputs"]
        self.assertEqual((inputs["repo"], inputs["change"], inputs["as_of"], inputs["formats"]), ("acme/api", {"compare": "main...scenario/s13-branch-matrix"}, None, ["json"]))
        self.assertEqual(inputs["config"]["workspace"], "acme")
        config = support_cli.config_path()
        self.assertEqual(inputs["config"]["path_sha256"], hashlib.sha256(str(config.resolve()).encode()).hexdigest())
        self.assertNotIn(str(config), json.dumps(data))  # the hash of the path, never the path and never the content
        for key in ("change", "ref", "branches", "findings", "areas", "propagations", "backend_calls", "suggestions", "mapped_tests", "incompleteness", "notes", "revision", "status"):
            self.assertIn(key, data)
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["counts"]["findings"], len(data["findings"]))
        self.assertEqual(data["counts"]["areas"], len(data["areas"]))

    def test_the_counts_are_what_the_lists_hold(self):
        data = support_cli.cli_run(*S11).report
        c = data["counts"]
        self.assertEqual((c["areas"], c["must_test"], c["sample"], c["candidates"]), (2, 2, 0, 0))
        self.assertEqual(c["exclusions"], sum(f["is_exclusion"] for f in data["findings"]))
        self.assertEqual(c["blocking_records"], sum(r["blocks_selection"] for r in data["incompleteness"]))
        self.assertEqual(c["records"], len(data["incompleteness"]))

    def test_the_report_is_utf8_json_and_a_range_works_as_well(self):
        r = support_cli.cli_run("--repo", "acme/contracts", "--range", "v1.9.0..v1.10.0")
        self.assertEqual(r.code, 0)
        self.assertRegex(r.path.name, r"^acme-contracts-range-v1\.9\.0-v1\.10\.0-[0-9a-f]{8}-[0-9a-f]{12}-r1\.json$")
        json.loads(r.path.read_bytes().decode("utf-8"))

    def test_the_default_out_directory_is_reports_under_the_working_directory(self):
        here = os.getcwd()
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, here)
        code = cli.main(["analyse", "--config", str(self.config), *S13], env=self.env, stdout=io.StringIO(), stderr=io.StringIO(), git_env=sg.isolated_env(ss.env().home))
        self.assertEqual(code, 0)
        self.assertEqual(len(list((self.tmp / "reports").glob("*.json"))), 1)

    def test_running_again_gives_the_same_bytes_except_the_run_object(self):
        self.go("--config", str(support_cli.config_path()), *S11, config=False)  # the same config file: its path is hashed into the report
        again = self.reports()[0].read_text(encoding="utf-8")
        first = support_cli.cli_run(*S11).text
        strip = lambda text: re.sub(r'"run": \{[^}]*\}', '"run": {}', text)  # noqa: E731
        again, first = json.loads(strip(again)), json.loads(strip(first))
        self.assertEqual((again["review"]["revision"], first["review"]["revision"]), (1, 1))  # each is the first run of a reports folder of its own
        for d in (again, first):
            d.pop("review")  # (when the store was read is the one other thing that differs)
        self.assertEqual(again, first)


class Summary(CliCase):
    def test_the_summary_names_the_areas_the_findings_and_the_report(self):
        code, out, _ = seen("--repo", "acme/contracts", "--compare", "main...scenario/s11-contract-field")
        self.assertEqual(code, 0)
        self.assertIn("Areas to test: 2 (must test 2)", out)
        self.assertIn("must test  api_route GET /api/billing/settlements/summary (acme/api)", out)
        self.assertIn("must test  screen /billing/settlements (acme/web)", out)
        self.assertRegex(out, r"Findings: 8 \(contract-tightened 2, cross-repo 2, exclusion 2, release 2\)")
        self.assertTrue(out.rstrip().splitlines()[-1].startswith("Report: "))

    def test_only_the_first_few_areas_are_listed_and_the_rest_counted(self):
        code, out, _ = seen("--repo", "acme/api", "--compare", "main...scenario/s15-python-callers")
        lines = [ln for ln in out.splitlines() if ln.startswith("  must test")]
        self.assertEqual(len(lines), cli.AREAS_SHOWN)
        self.assertRegex(out, r"and \d+ more in the report")

    def test_blocking_records_come_first_and_the_run_still_exits_0(self):
        code, out, err = seen("--repo", "acme/web", "--compare", "main...scenario/s16-no-merge-base")
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("Blocking: "), out[:200])
        self.assertIn("pre_change_unavailable", out.splitlines()[0])
        self.assertIn("Removed routes, removed calls and narrowed exports", out.splitlines()[0])
        self.assertNotIn(CLAIM, out)  # blocking records: not even this sentence
        self.assertIn("none were established", out)

    def test_zero_areas_and_no_blocking_record_says_exactly_that_sentence(self):
        code, out, _ = seen("--repo", "acme/web", "--compare", "main...scenario/s02-hub-cutoff")
        self.assertEqual(code, 0)
        self.assertIn(CLAIM, out)
        self.assertEqual(wording.check(CLAIM), [])  # the sentence passes the check, and stays
        self.assertNotIn("Blocking", out)

    def test_an_incomplete_answer_says_the_lists_may_be_short_and_a_whole_one_says_nothing_about_it(self):
        _, out, _ = seen("--repo", "acme/web", "--compare", "main...scenario/s02-hub-cutoff")
        self.assertIn("may be incomplete", out)
        _, out, _ = seen("--repo", "acme/web", "--compare", "main...scenario/s01-shared-component")
        self.assertNotIn("incomplete", out)
        self.assertNotIn("complete", out.lower())  # a whole answer never says it is "complete": that reads as coverage

    def test_the_summary_never_says_safe_or_that_nothing_needs_testing(self):
        for repo, spec in (("acme/web", "main...scenario/s01-shared-component"), ("acme/web", "main...scenario/s02-hub-cutoff"), ("acme/web", "main...scenario/s16-no-merge-base"),
                           ("acme/api", "main...scenario/s13-branch-matrix"), ("acme/contracts", "main...scenario/s11-contract-field")):
            _, out, _ = seen("--repo", repo, "--compare", spec)
            self.assertEqual(wording.check(out), [], out)
            self.assertNotRegex(out.lower(), r"nothing to test|no need to test|\bsafe\b")

    def test_a_route_or_path_with_a_banned_word_is_defused_in_the_summary(self):
        lines = cli.summary_lines(self.fake_analysis("/safe-mode", "src/safe/x.ts"), None, True)
        text = "\n".join(lines)
        self.assertEqual(wording.check(text), [])
        self.assertIn("-mode", text)

    def fake_analysis(self, label, path):
        from reachmap.model import Analysis, Area, Evidence
        sha = "a" * 40
        e = Evidence("t/web", path, 1, sha, "import x")
        a = Analysis(change={"kind": "compare", "repo": "t/web", "resolved": True, "head_sha": sha, "spec": {"compare": "a...b"}}, ref=sha)
        a.add_area(Area(label, "screen", "t/web", "m", e, ((e,),), (), "must_test", False))
        return a

    def test_the_branch_matrix_is_one_line(self):
        _, out, _ = seen("--repo", "acme/api", "--compare", "main...scenario/s13-branch-matrix")
        self.assertIn("Branches: main pre_change, release/1.2 present, release/1.x present", out)

    def test_quiet_prints_nothing_on_success(self):
        code, out, err = self.go("--repo", "acme/api", "--compare", "main...scenario/s13-branch-matrix", "--quiet")
        self.assertEqual((code, out, err), (0, "", ""))
        self.assertEqual(len(self.reports()), 1)


class Names(CliCase):
    def test_two_changes_that_slug_alike_do_not_write_the_same_file(self):
        """`a...b` and `a-b`, `acme/api` and `acme-api`: the slug drops what tells them apart, the hash of the whole change does not."""
        for spec in ("main...nope-1", "main...nope/1", "main...nope+1", "main...nope:1"):
            self.go("--repo", "acme/api", "--compare", spec)
        names = [p.name for p in self.reports()]
        self.assertEqual(len(names), 4, names)
        self.go("--repo", "acme/api", "--compare", "main...nope-1", "--as-of", "2100-01-01T00:00:00Z")
        self.assertEqual(len(self.reports()), 5)  # the same change as of another time is another report

    def test_the_name_has_nothing_that_can_leave_the_out_directory(self):
        self.go("--repo", "acme/api", "--compare", "../../x...y/../z")
        (path,) = self.reports()
        self.assertEqual(path.parent, self.out)
        self.assertNotIn("/", path.name)


class Quiet(CliCase):
    def test_quiet_still_says_what_blocks_on_stderr(self):
        code, out, err = self.go("--repo", "acme/web", "--compare", "main...scenario/s16-no-merge-base", "--quiet")
        self.assertEqual((code, out), (0, ""))
        self.assertTrue(err.startswith("Blocking: "), err)
        self.assertIn("pre_change_unavailable", err)
        self.assertEqual(wording.check(err), [])
        self.assertEqual(len(self.reports()), 1)

    def test_quiet_with_nothing_blocking_prints_nothing_at_all(self):
        self.assertEqual(self.go(*S13, "--quiet")[1:], ("", ""))


class Out(CliCase):
    def test_an_out_directory_under_a_file_is_a_usage_error_not_a_traceback(self):
        blocker = self.tmp / "afile"
        blocker.write_text("x")
        code, out, err = self.go(*S13, "--out", str(blocker / "reports"))
        self.assertEqual((code, out), (2, ""))
        self.assertTrue(err.startswith("reachmap: error: "), err)
        self.assertIn("--out", err)
        self.assertNotIn("Traceback", err)

    def test_an_out_that_is_a_file_is_a_usage_error_too(self):
        blocker = self.tmp / "afile"
        blocker.write_text("x")
        code, _, err = self.go(*S13, "--out", str(blocker))
        self.assertEqual(code, 2)
        self.assertIn("--out", err)

    def test_no_summary_names_a_report_that_was_not_written(self):
        blocker = self.tmp / "afile"
        blocker.write_text("x")
        code, out, _ = self.go(*S13, "--out", str(blocker))
        self.assertEqual(out, "")  # no summary naming a report that was not written


class AlwaysAReport(CliCase):
    def test_a_failure_the_pipeline_did_not_foresee_still_writes_a_report_and_exits_3(self):
        with mock.patch.object(pipeline, "build", side_effect=RuntimeError("boom at /Users/someone/secret")):
            code, out, err = self.go(*S13)
        self.assertEqual(code, 3)
        data = self.report()
        self.assertEqual((data["status"], data["findings"], data["counts"], data["ref"]), ("failed", None, None, None))
        (rec,) = data["incompleteness"]
        self.assertEqual((rec["kind"], rec["subject"]), ("run_failed", "compare main...scenario/s13-branch-matrix"))
        self.assertIn("RuntimeError", rec["detail"])
        self.assertNotIn("/Users/", rec["detail"] + err)

    def test_a_report_that_cannot_be_made_from_a_completed_run_is_a_failed_report(self):
        real, calls = cli._report, []

        def flaky(*a, **kw):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("boom")
            return real(*a, **kw)

        with mock.patch.object(cli, "_report", flaky):
            code, _, err = self.go(*S13)
        self.assertEqual(code, 3)
        self.assertEqual(self.report()["status"], "failed")
        self.assertIn("RuntimeError", err)

    def test_a_file_name_that_is_not_utf8_does_not_stop_the_command(self):
        tmp = self.tmp / "raw"
        tmp.mkdir()
        repo, shas = sg.make_raw_repo(tmp, "raw", [[("100644", b"ok.py", b"def f():\n    return 1\n")],
                                                  [("100644", b"ok.py", b"def f():\n    return 2\n"), ("100644", b"caf\xe9.py", b"x = 1\n")]])
        cfg = tmp / "reachmap.toml"
        cfg.write_text(f'workspace = "t"\n[repos."t/app"]\nhost = "git"\nremote = "{sg.remote_url(repo)}"\nlanguage = "python"\nroles = ["backend"]\nbranches = ["main"]\n')
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(["analyse", "--config", str(cfg), "--repo", "t/app", "--range", f"{shas[0]}..{shas[1]}", "--out", str(self.out)],
                        env={"REACHMAP_CACHE": str(tmp / "cache")}, stdout=out, stderr=err, git_env=sg.isolated_env(tmp / "home"))
        self.assertEqual((code, err.getvalue()), (0, ""))
        data = self.report()
        self.assertIn("caf\\xe9.py", [f["path"] for f in data["change"]["files"]])
        self.assertEqual(data["inputs"]["repo"], "t/app")


class Usage(CliCase):
    def assertUsage(self, *args, mention=None, **kw):
        code, out, err = self.go(*args, **kw)
        self.assertEqual(code, 2, (out, err))
        self.assertEqual(out, "")
        self.assertTrue(err.startswith("reachmap: "), err)
        if mention:
            self.assertIn(mention, err)
        self.assertEqual(self.reports(), [])
        return err

    def test_no_repository(self):
        self.assertUsage("--compare", "a...b", mention="--repo")

    def test_no_change_given(self):
        self.assertUsage("--repo", "acme/api")

    def test_two_changes_given(self):
        self.assertUsage("--repo", "acme/api", "--pr", "3", "--compare", "a...b", mention="--compare")

    def test_a_comparison_with_two_dots_and_a_range_with_three(self):
        self.assertUsage("--repo", "acme/api", "--compare", "a..b", mention="three dots")
        self.assertUsage("--repo", "acme/api", "--range", "a...b", mention="two dots")

    def test_a_bad_pull_request_number(self):
        self.assertUsage("--repo", "acme/api", "--pr", "twelve", mention="twelve")

    def test_an_unknown_repository_is_named_with_the_known_ones(self):
        err = self.assertUsage("--repo", "acme/nope", "--compare", "a...b", mention="acme/nope")
        self.assertIn("acme/api", err)

    def test_a_bad_time(self):
        self.assertUsage("--repo", "acme/api", "--compare", "a...b", "--as-of", "yesterday", mention="--as-of")

    def test_a_time_before_1970_is_refused_with_a_message(self):
        err = self.assertUsage("--repo", "acme/api", "--compare", "a...b", "--as-of", "1960-01-01T00:00:00Z", mention="--as-of")
        self.assertIn("1970", err)

    def test_what_is_not_a_report_format_is_refused_with_a_clear_message(self):
        # M10: md and html are formats now (test_report_cli); what is not one is still refused
        err = self.assertUsage("--repo", "acme/api", "--compare", "a...b", "--format", "json,pdf", mention="pdf")
        self.assertIn("json, md, html", err)
        self.assertUsage("--repo", "acme/api", "--compare", "a...b", "--format", "docx", mention="docx")

    def test_json_alone_is_accepted(self):
        code, _, _ = self.go("--repo", "acme/api", "--compare", "main...scenario/s13-branch-matrix", "--format", "json")
        self.assertEqual(code, 0)

    def test_a_config_file_that_is_not_there(self):
        err = self.assertUsage("--repo", "acme/api", "--compare", "a...b", "--config", str(self.tmp / "missing.toml"), config=False, mention="missing.toml")

    def test_a_config_error_names_the_key_and_never_the_value(self):
        self.config.write_text(FIXTURE_TOML.read_text().replace('hops = 3', 'hops = "three"'))
        err = self.assertUsage("--repo", "acme/api", "--compare", "a...b", mention="limits.hops")
        self.assertNotIn("three", err)

    def test_an_unset_variable_in_the_config_is_a_config_error_naming_the_key(self):
        err = self.assertUsage("--repo", "acme/api", "--compare", "a...b", env={"REACHMAP_CACHE": self.env["REACHMAP_CACHE"]}, mention="remote")

    def test_the_help_exits_0_and_a_missing_command_is_a_usage_error(self):
        out = io.StringIO()
        self.assertEqual(cli.main(["analyse", "--help"], stdout=out, stderr=io.StringIO()), 0)
        self.assertIn("--compare", out.getvalue())
        err = io.StringIO()
        self.assertEqual(cli.main([], stderr=err), 2)
        self.assertEqual(cli.main(["frobnicate"], stderr=io.StringIO()), 2)


class Failed(CliCase):
    def test_a_refused_change_set_exits_3_and_still_writes_the_json_without_a_count(self):
        code, out, err = self.go("--repo", "acme/web", "--compare", "main...no-such-branch")
        self.assertEqual(code, 3)
        self.assertIn("no-such-branch", err)
        data = self.report()
        self.assertEqual(data["status"], "failed")
        self.assertIsNone(data["findings"])
        self.assertIsNone(data["areas"])
        self.assertIsNone(data["counts"])
        self.assertEqual([r["kind"] for r in data["incompleteness"]], ["run_failed"])
        self.assertNotIn("Findings:", out)
        self.assertNotRegex(out + err, r"\d+ finding")
        self.assertTrue(re.search(r"-unresolved-failed-[0-9a-f]{8}\.json$", self.reports()[0].name))

    def test_a_pull_request_on_a_repository_with_no_host_is_refused(self):
        code, out, err = self.go("--repo", "acme/web", "--pr", "7")
        self.assertEqual(code, 3)
        self.assertEqual(self.report()["status"], "failed")
        self.assertTrue(err)

    def test_a_time_before_the_first_commit_exits_3(self):
        code, _, err = self.go("--repo", "acme/api", "--compare", "main...scenario/s13-branch-matrix", "--as-of", "2000-01-01T00:00:00Z")
        self.assertEqual(code, 3)
        self.assertEqual(self.report()["inputs"]["as_of"], "2000-01-01T00:00:00Z")

    def test_as_of_after_everything_is_the_same_report_with_the_input_recorded(self):
        self.go("--repo", "acme/api", "--compare", "main...scenario/s13-branch-matrix")
        plain = self.report()
        for p in self.reports():
            p.unlink()
        code, _, _ = self.go("--repo", "acme/api", "--compare", "main...scenario/s13-branch-matrix", "--as-of", "2100-01-01T00:00:00Z")
        self.assertEqual(code, 0)
        late = self.report()
        self.assertEqual(late["inputs"]["as_of"], "2100-01-01T00:00:00Z")
        for d in (plain, late):
            d["run"] = d["inputs"] = None
            d["change"].pop("as_of")
            d["review"] = None  # the second run is revision 2 of the same change (M11), and read the store later
        self.assertEqual(plain, late)


class TimedOut(CliCase):
    def test_a_missed_deadline_exits_4_and_the_json_has_null_findings_and_no_count(self):
        state = {"t": 0.0}

        def clock():
            state["t"] += 400
            return state["t"]

        code, out, err = self.go("--repo", "acme/contracts", "--compare", "main...scenario/s11-contract-field", clock=clock)
        self.assertEqual(code, 4)
        data = self.report()
        self.assertEqual(data["status"], "timed_out")
        self.assertEqual((data["findings"], data["areas"], data["counts"]), (None, None, None))
        self.assertIn("timed_out", [r["kind"] for r in data["incompleteness"]])
        self.assertIn("time limit", err)
        self.assertNotRegex(out + err, r"\d+ (finding|area)")


class NoSecret(CliCase):
    def test_no_credential_in_the_environment_reaches_the_output_or_the_report(self):
        secret = "ghp_THISisAfakeTOKENvalue1234567890"
        env = {**self.env, "GITHUB_TOKEN": secret, "GITLAB_TOKEN": secret + "x", "BITBUCKET_TOKEN": secret + "y"}
        for args in (("--compare", "main...scenario/s13-branch-matrix"), ("--compare", "main...no-such-branch"), ("--pr", "3")):
            code, out, err = self.go("--repo", "acme/api", *args, env=env)
            blob = out + err + "".join(p.read_text() for p in self.reports())
            self.assertNotIn(secret, blob)
            for p in self.reports():
                p.unlink()


if __name__ == "__main__":
    unittest.main()
