"""The citation audit on the reports of the 23 M1 scenarios, through real git and the real command: every citation resolves, and a report
that was edited fails with the right category. (The four M16 workspaces are in `test_eval_audit_m16`.)"""

import copy
import io
import json
import re
import tempfile
import unittest
from pathlib import Path

from reachmap import cli, config, gitlayer
from reachmap.evaluate import audit
from reachmap.evaluate.audit import ABSENT, MISMATCH, OK, UNREADABLE
from tests import scenario_support as ss
from tests import support_eval as se
from tests import support_git as sg
from tests.fixtures import build

S01 = "s01-shared-component"


def audit_cli(world, paths, *extra):
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(["audit", "--config", str(world.config), *extra, *map(str, paths)], env=world.env, stdout=out, stderr=err, git_env=world.git_env)
    return code, out.getvalue(), err.getvalue()


def direct(world, report):
    git = gitlayer.Git(gitlayer.default_cache(world.env), env=world.git_env)
    return audit.audit_report(report, audit.GitReader(config.load(world.config, env=world.env), git), "mutated.json")


class EveryScenario(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = se.world("acme")

    def test_all_23_scenarios_are_here(self):
        self.assertEqual(sorted(k.split("-")[0] for k in self.world.reports), [f"s{n:02d}" for n in range(1, 24)])

    def test_the_command_passes_on_every_report_with_nothing_but_ok(self):
        code, out, err = audit_cli(self.world, self.world.paths())
        self.assertEqual((code, err), (0, ""), out[-3000:])
        last = [line for line in out.splitlines() if line.startswith("23 reports")][0]
        self.assertRegex(last, r"^23 reports: \d+ distinct citations \(ok \d+, mismatch 0, absent 0, unreadable 0\), 0 structure problems; 0 report\(s\) failed$")
        self.assertEqual(out.count(": passed"), 23)
        self.assertNotIn("%", out)

    def test_each_report_has_citations_all_ok_and_no_structure_problem(self):
        for sid in self.world.reports:
            with self.subTest(sid):
                result = direct(self.world, self.world.report(sid))
                self.assertEqual(result.structure, [])
                self.assertEqual((result.counts[MISMATCH], result.counts[ABSENT], result.counts[UNREADABLE]), (0, 0, 0))
                if sid != "s16-no-merge-base":  # a change with no merge base has no area, no finding and no branch row to cite
                    self.assertGreater(result.counts[OK], 0)

    def test_what_the_reports_cite_is_pinned_per_part_of_the_report(self):
        self.assertEqual(se.tally(self.world), se.PINNED["acme"])

    def test_the_count_of_the_command_is_the_sum_over_the_reports(self):
        total = sum(direct(self.world, self.world.report(sid)).distinct for sid in self.world.reports)
        _, out, _ = audit_cli(self.world, self.world.paths())
        self.assertIn(f"23 reports: {total} distinct citations (ok {total},", out)


class Mutated(unittest.TestCase):
    """A report of s01 edited in one place; it was audited as it is first, so what fails is the edit."""

    @classmethod
    def setUpClass(cls):
        cls.world = se.world("acme")
        cls.base = cls.world.report(S01)
        cls.repo = ss.env().repos["acme/web"]
        cls.first_commit = build.run_git(cls.repo, "rev-list", "--max-parents=0", "main").stdout.split()[0]

    def mutated(self, change):
        report = copy.deepcopy(self.base)
        change(report)
        return direct(self.world, report)

    def categories(self, result):
        return {k: v for k, v in result.counts.items() if v and k != OK}

    def test_the_unedited_report_passes(self):
        self.assertEqual(self.categories(direct(self.world, self.base)), {})

    def test_an_edited_detail_is_a_mismatch_that_shows_both_lines(self):
        result = self.mutated(lambda r: r["areas"][0]["route_line"].update(detail=r["areas"][0]["route_line"]["detail"] + " // edited"))
        self.assertEqual(self.categories(result), {MISMATCH: 1})
        self.assertIn("// edited", result.failures[0].citation.detail)
        self.assertNotIn("// edited", result.failures[0].found)

    def test_a_shifted_line_is_a_mismatch(self):
        result = self.mutated(lambda r: r["areas"][0]["route_line"].update(line=r["areas"][0]["route_line"]["line"] + 1))
        self.assertEqual(self.categories(result), {MISMATCH: 1})

    def test_a_line_past_the_end_of_the_file_is_a_mismatch_too(self):
        result = self.mutated(lambda r: r["areas"][0]["route_line"].update(line=500))
        self.assertEqual(self.categories(result), {MISMATCH: 1})
        self.assertIn("the file has", result.failures[0].found)

    def test_a_ref_changed_to_another_commit_where_the_file_has_another_text_is_a_mismatch_and_where_it_is_not_there_absent(self):
        path = "src/components/StatusBadge.tsx"
        base, head = self.base["change"]["base_sha"], self.base["change"]["head_sha"]
        before = build.file_at(self.repo, base, path)[1].split("\n")
        after = build.file_at(self.repo, head, path)[1].split("\n")
        n = next(i for i, (a, b) in enumerate(zip(after, before)) if a != b)  # a line the change edited

        def cite(ref):
            def change(r):
                r["areas"][0]["route_line"].update(path=path, line=n + 1, detail=after[n], ref=ref)
            return change

        self.assertEqual(self.categories(self.mutated(cite(head))), {})
        self.assertEqual(self.categories(self.mutated(cite(base))), {MISMATCH: 1})
        self.assertEqual(self.categories(self.mutated(cite(self.first_commit))), {ABSENT: 1})

    def test_a_ref_that_is_a_commit_nobody_can_fetch_is_unreadable_not_absent_and_not_a_mismatch(self):
        result = self.mutated(lambda r: r["areas"][0]["route_line"].update(ref="1" * 40))
        self.assertEqual(self.categories(result), {UNREADABLE: 1})
        self.assertTrue(result.failures[0].found)

    def test_a_path_changed_to_another_file_is_a_mismatch_and_to_a_file_that_is_not_there_is_absent(self):
        other = "src/modules/cases/routes.tsx"
        r1 = self.mutated(lambda r: r["areas"][0]["route_line"].update(path=other, line=1))
        self.assertEqual(self.categories(r1), {MISMATCH: 1})
        r2 = self.mutated(lambda r: r["areas"][0]["route_line"].update(path="src/nowhere/Gone.tsx"))
        self.assertEqual(self.categories(r2), {ABSENT: 1})

    def test_a_repository_the_config_does_not_have_is_unreadable(self):
        result = self.mutated(lambda r: r["areas"][0]["route_line"].update(repo="acme/elsewhere"))
        self.assertEqual(self.categories(result), {UNREADABLE: 1})
        self.assertIn("not in the configuration", result.failures[0].found)

    def test_a_finding_without_evidence_a_changed_id_and_an_edited_fingerprint_are_found_as_structure(self):
        def empty(r):
            r["findings"][0]["evidence"] = []

        def bad_id(r):
            r["areas"][0]["id"] = "a-0000000000000000"

        def bad_print(r):
            r["findings"][0]["fingerprint"] = "0" * 64

        for change in (empty, bad_id, bad_print):
            with self.subTest(change.__name__):
                result = self.mutated(change)
                self.assertEqual(len(result.structure), 1, result.structure)
                self.assertFalse(result.passed())

    def test_the_command_exits_1_for_each_category_and_0_when_unreadable_is_allowed_only_for_unreadable(self):
        tmp = Path(tempfile.mkdtemp(prefix="reachmap-mut-"))
        self.addCleanup(sg.remove_tree, tmp)

        def write(name, change):
            report = copy.deepcopy(self.base)
            change(report)
            path = tmp / name
            path.write_text(json.dumps(report), encoding="utf-8")
            return path

        unreadable = write("u.json", lambda r: r["areas"][0]["route_line"].update(ref="2" * 40))
        mismatch = write("m.json", lambda r: r["areas"][0]["route_line"].update(detail="x"))
        code, out, _ = audit_cli(self.world, [unreadable])
        self.assertEqual(code, 1)
        self.assertIn("unreadable 1", out)
        code, out, _ = audit_cli(self.world, [unreadable], "--allow", "unreadable")
        self.assertEqual(code, 0)
        self.assertIn("unreadable (allowed, not ok): 1 citations were not read", out)
        code, out, _ = audit_cli(self.world, [mismatch], "--allow", "unreadable")
        self.assertEqual(code, 1)
        self.assertIn("mismatch 1", out)
        code, out, _ = audit_cli(self.world, [mismatch, unreadable, self.world.reports[S01]], "--show", "1")
        self.assertEqual(code, 1)
        self.assertEqual(len(re.findall(r"^  (mismatch|unreadable): ", out, re.M)), 2)  # one per failing report
        self.assertIn("2 report(s) failed", out)


class Usage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = se.world("acme")

    def test_a_file_that_is_not_a_report_a_missing_file_and_a_bad_config_are_exit_2_with_a_message_and_no_traceback(self):
        tmp = Path(tempfile.mkdtemp(prefix="reachmap-use-"))
        self.addCleanup(sg.remove_tree, tmp)
        junk = tmp / "junk.json"
        junk.write_text("not json", encoding="utf-8")
        for args in ([junk], [tmp / "missing.json"]):
            code, out, err = audit_cli(self.world, args)
            self.assertEqual((code, out), (2, ""))
            self.assertTrue(err.startswith("reachmap: error: "))
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(["audit", "--config", str(tmp / "none.toml"), str(next(iter(self.world.paths())))], env=self.world.env, stdout=out, stderr=err, git_env=self.world.git_env)
        self.assertEqual(code, 2)
        self.assertIn("config error", err.getvalue())
        code, out, err = audit_cli(self.world, self.world.paths()[:1], "--show", "-1")
        self.assertEqual(code, 2)

    def test_a_run_that_failed_or_timed_out_is_not_audited_and_the_command_exits_1(self):
        from tests import support_report as sr

        for name in ("failed", "timed_out"):
            path = Path(sr.FIXTURES) / f"{name}.json"
            code, out, err = audit_cli(self.world, [path])
            self.assertEqual((code, err), (1, ""), out)
            self.assertIn(": NOT AUDITED: the run did not complete", out)
            self.assertNotIn(": passed", out)
            self.assertIn("(1 not audited)", out)
            code, out, _ = audit_cli(self.world, [path], "--allow", "unreadable")
            self.assertEqual(code, 1)

    def test_a_completed_report_without_its_lists_is_exit_2(self):
        tmp = Path(tempfile.mkdtemp(prefix="reachmap-bare-"))
        self.addCleanup(sg.remove_tree, tmp)
        bare = tmp / "bare.json"
        bare.write_text('{"schema": 1, "status": "completed"}', encoding="utf-8")
        code, out, err = audit_cli(self.world, [bare])
        self.assertEqual((code, out), (2, ""))
        self.assertIn("not a report reachmap can read", err)

    def test_a_report_made_from_another_workspace_is_unreadable_not_ok(self):
        # the same report audited with a config that has none of its repositories
        other = Path(tempfile.mkdtemp(prefix="reachmap-cfg-"))
        self.addCleanup(sg.remove_tree, other)
        cfg = other / "other.toml"
        cfg.write_text('workspace = "other"\n[limits]\nhops = 3\nhub_importers = 50\ndistinct_max = 3\ntimeout_s = 900\n'
                       '[repos."x/y"]\nhost = "git"\nremote = "file:///nonexistent"\nlanguage = "python"\nroles = ["backend"]\nbranches = ["main"]\n', encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(["audit", "--config", str(cfg), str(self.world.reports[S01])], env=self.world.env, stdout=out, stderr=err, git_env=self.world.git_env)
        self.assertEqual(code, 1, err.getvalue())
        self.assertNotIn("ok 0, mismatch 0, absent 0, unreadable 0", out.getvalue())
        self.assertIn("unreadable", out.getvalue())
        self.assertIn("not in the configuration", out.getvalue())


class ModelNotes(unittest.TestCase):
    """A verified proposed file in the note store beside a report is read again with the report's citations."""

    def test_a_verified_note_of_the_store_beside_the_report_is_read_again_and_a_wrong_line_fails(self):
        from reachmap import notes

        world = se.world("acme")
        out_dir = Path(tempfile.mkdtemp(prefix="reachmap-notes-"))
        self.addCleanup(sg.remove_tree, out_dir)
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(["analyse", "--config", str(world.config), "--repo", "acme/web", "--compare", f"main...scenario/{S01}", "--out", str(out_dir)], env=world.env, stdout=out,
                        stderr=err, git_env=world.git_env)
        self.assertEqual(code, 0, err.getvalue())
        path = next(out_dir.glob("*.json"))
        report = json.loads(path.read_text(encoding="utf-8"))
        code, text, _ = audit_cli(world, [path])
        self.assertEqual(code, 0, text)
        self.assertNotIn("model note lines", text)  # no store yet: nothing about notes
        client = notes.client_of("audit-test", "1")

        def add(lines):
            verification = {"status": "verified", "reason": "verified", "read": "found", "sha": report["change"]["head_sha"], "symbol": "StatusBadge", "lines": lines, "detail": None}
            proposal = {"repo": "acme/web", "path": "src/components/StatusBadge.tsx", "symbol": "StatusBadge"}
            note = notes.new_note(path.name, report, report["areas"][0]["id"], "proposed_file", "look here", client, "2026-01-01T00:00:00Z", verification=verification, proposal=proposal)
            notes.append(out_dir, report["review"]["key"], note)

        add([3])  # the line of the definition of StatusBadge
        code, text, _ = audit_cli(world, [path])
        self.assertEqual(code, 0, text)
        self.assertIn("1 are model note lines", text)
        add([1])  # a line that does not hold the name
        code, text, _ = audit_cli(world, [path])
        self.assertEqual(code, 1, text)
        self.assertIn("mismatch: acme/web src/components/StatusBadge.tsx:1", text)
        self.assertIn("symbol: StatusBadge", text)


if __name__ == "__main__":
    unittest.main()
