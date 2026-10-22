"""The witness harness on the fixture cases (M18), through real git and the real command: the witness branches change no existing sha, the
case files agree with the repositories, the valid set is counted, and each refusal is made in words with nothing counted."""

import re
import tempfile
import unittest
from pathlib import Path

from tests import support_git as sg
from tests import support_witness as sw
from tests.fixtures import build
from tests.fixtures.eval import build as witnesses

VALID = (
    "witness cases: 4 read, 4 analysed",
    "hits: 1 of 4 by findings; 2 of 4 by screens; 2 of 4 by any evidence",
)


def refs(path):
    return dict(line.split()[::-1] for line in build.run_git(path, "for-each-ref", "--format=%(objectname) %(refname)").stdout.splitlines())


class Fixtures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = sw.world()

    def test_no_existing_branch_tag_or_sha_changes_and_only_eval_branches_are_new(self):
        root = Path(tempfile.mkdtemp(prefix="reachmap-plain-"))
        self.addCleanup(sg.remove_tree, root)
        plain = build.build_all(root / "fx")
        added = {}
        for name, path in self.world.repos.items():
            before, after = refs(plain[name]), refs(path)
            self.assertEqual({k: v for k, v in after.items() if k in before}, before, name)  # every ref that was there has the sha it had
            added[name] = sorted(set(after) - set(before))
        self.assertEqual(added["acme/web"], sorted(f"refs/heads/{s['branch']}" for s in witnesses.specs()))
        self.assertEqual([v for k, v in added.items() if k != "acme/web"], [[], [], []])

    def test_a_witness_commit_is_dated_after_the_head_of_the_change_it_follows(self):
        for spec in witnesses.specs():
            after = witnesses.commit_time(self.world.dest, spec["after"]["repo"], spec["after"]["ref"])
            self.assertEqual(self.world.made[spec["id"]]["date"], after + spec["days"] * 86400)

    def test_every_merged_at_of_the_case_files_is_the_date_of_the_head_it_names(self):
        from reachmap.evaluate import witness

        heads = {"s01-shared-component": "acme/web", "s06-nested-mount-handler": "acme/api", "s19-generic-name": "acme/web", "s04-named-export": "acme/web"}
        seen = 0
        for path in sorted(sw.CASES.glob("*.toml")):
            cases, _ = witness.read_cases(path)
            for c in cases:
                ref = re.search(r"scenario/(s\d\d-[a-z-]+)", c.original.value).group(1)
                self.assertEqual(c.merged_at, witnesses.merge_time(self.world.dest, heads[ref], f"scenario/{ref}"), f"{path.name} {c.id}")
                self.assertEqual(c.repo, heads[ref])
                seen += 1
        self.assertEqual(seen, 4 + 2 + 2 + 2 + 2)

    def test_the_revert_witness_is_the_inverse_of_the_scenario(self):
        web = self.world.repos["acme/web"]
        s01 = build.run_git(web, "diff", "main...scenario/s01-shared-component", "--", "src/components/StatusBadge.tsx").stdout
        back = build.run_git(web, "diff", "scenario/s01-shared-component..eval/w05-revert-s01", "--", "src/components/StatusBadge.tsx").stdout
        self.assertTrue(s01 and back)
        self.assertEqual(build.run_git(web, "diff", "main", "eval/w05-revert-s01", "--", "src/components/StatusBadge.tsx").stdout, "")


class ValidCases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = sw.run("valid.toml")

    def test_the_command_exits_0_and_counts_per_axis(self):
        code, out, err = self.result
        self.assertEqual((code, err), (0, ""), out)
        for line in VALID:
            self.assertIn(line, out)
        self.assertNotIn("%", out)

    def test_each_case_is_what_the_fixture_was_built_to_show(self):
        _, out, _ = self.result
        lines = {m.group(1): m.group(0) for m in re.finditer(r"^  (c\d\d)-.*$", out, re.M)}
        self.assertIn("hit by findings, screens, any evidence", lines["c01"])
        self.assertIn("cited: acme/web:src/modules/billing/pages/InvoiceListPage.tsx", lines["c01"])
        self.assertIn("hit by screens, any evidence", lines["c02"])  # only the second chain of a screen cites the file: no finding does
        self.assertIn("cited: acme/web:src/modules/cases/routes.tsx", lines["c02"])
        self.assertIn("no witness file cited", lines["c03"])
        self.assertIn("no witness file cited", lines["c04"])

    def test_a_file_that_only_an_exclusion_cites_is_not_a_hit_and_is_said(self):
        _, out, _ = self.result
        self.assertIn("cited only by an exclusion, which is not a prediction and does not count: acme/web:src/modules/reports/pages/ReportsHomePage.tsx", out)

    def test_it_says_what_as_of_does_not_pin(self):
        _, out, _ = self.result
        self.assertIn("It does not pin the tags of a producer, the commits a host reports for a pull request, or the hosted test sources", out)

    def test_it_says_small_samples_are_weak_and_that_no_recall_number_exists(self):
        _, out, _ = self.result
        self.assertIn("small samples are weak", out)
        self.assertIn("No recall number exists without defects linked to the changes that caused them", out)


class NotAnalysed(unittest.TestCase):
    def test_when_a_case_is_not_analysed_the_counts_are_printed_and_the_exit_code_is_1(self):
        from types import SimpleNamespace
        from unittest import mock

        failed = SimpleNamespace(status="failed")
        with mock.patch("reachmap.pipeline.build", return_value=failed):
            code, out, err = sw.run("valid.toml")
        self.assertEqual((code, err), (1, ""))
        self.assertIn("witness cases: 4 read, 0 analysed, 4 not analysed", out)
        self.assertIn("hits: 0 of 0 by findings", out)
        self.assertIn("not analysed (the run failed); left out of every count", out)

    def test_a_case_file_with_a_repo_on_the_original_is_refused_before_anything_is_read(self):
        d = Path(tempfile.mkdtemp(prefix="reachmap-orig-"))
        self.addCleanup(sg.remove_tree, d)
        p = d / "cases.toml"
        p.write_text('[[case]]\nid = "x"\nrepo = "acme/web"\nmerged_at = "2023-11-15T09:13:20Z"\n'
                     'original = { compare = "main...scenario/s01-shared-component", repo = "acme/api" }\n'
                     'witness = { compare = "main...eval/w03-schedule-report" }\n', encoding="utf-8")
        code, out, err = sw.run(str(p))
        self.assertEqual((code, out), (2, ""))
        self.assertIn("the original change is in the case's repo", err)


class Refused(unittest.TestCase):
    def refused(self, name):
        code, out, err = sw.run(name)
        self.assertEqual((code, out), (2, ""), out)
        self.assertTrue(err.startswith("reachmap: error: the case set is refused, so nothing was counted:\n"), err)
        self.assertNotIn("hits:", err)
        return err

    def test_two_cases_with_one_witness_are_refused_and_the_witness_is_named(self):
        err = self.refused("refused-shared-witness.toml")
        self.assertIn("cases c01-shared-badge and c05-case-table-again share a witness (the same head commit", err)
        self.assertEqual(err.count("\n  cases "), 1)

    def test_two_cases_with_one_subject_are_refused(self):
        err = self.refused("refused-shared-subject.toml")
        self.assertIn("cases c01-shared-badge and c06-shared-badge-schedule share a subject (the same head commit", err)

    def test_a_revert_is_refused(self):
        err = self.refused("refused-revert.toml")
        self.assertIn("case c07-badge-reverted: the witness holds the inverse of the original's diff for every file of the original", err)
        self.assertNotIn("c03", err)

    def test_a_witness_that_reverts_the_original_and_fixes_another_file_is_refused_too(self):
        err = self.refused("refused-revert-and-fix.toml")
        self.assertIn("case c08-badge-reverted-and-fixed: the witness holds the inverse of the original's diff", err)
        self.assertNotIn("c03", err)

    def write(self, text):
        d = Path(tempfile.mkdtemp(prefix="reachmap-wcase-"))
        self.addCleanup(sg.remove_tree, d)
        p = d / "cases.toml"
        p.write_text(text, encoding="utf-8")
        return p

    CASE = '[[case]]\nid = "x"\nrepo = "acme/web"\nmerged_at = "{when}"\noriginal = {{ compare = "main...scenario/s01-shared-component" }}\nwitness = {{ {witness} }}\n'

    def test_a_witness_that_is_inside_the_original_diff_or_not_later_than_the_merge_is_refused(self):
        inside = self.write(self.CASE.format(when="2023-11-15T09:13:20Z", witness='range = "scenario/s01-shared-component..eval/w06-badge-tweak"'))
        self.assertIn("every file of the witness is inside the original diff", self.refused(str(inside)))
        early = self.write(self.CASE.format(when="2023-12-31T00:00:00Z", witness='range = "scenario/s01-shared-component..eval/w01-invoice-list-column"'))
        self.assertIn("is not later than the merge time 2023-12-31T00:00:00Z", self.refused(str(early)))

    def test_a_change_that_cannot_be_read_is_refused_and_the_repository_must_be_in_the_config(self):
        gone = self.write(self.CASE.format(when="2023-11-15T09:13:20Z", witness='range = "scenario/s01-shared-component..eval/nothing"'))
        self.assertIn("cannot be read", self.refused(str(gone)))
        unknown = self.write(self.CASE.format(when="2023-11-15T09:13:20Z", witness='compare = "main...x", repo = "acme/other"'))
        code, out, err = sw.run(str(unknown))
        self.assertEqual((code, out), (2, ""))
        self.assertIn("'acme/other' is not in the config", err)

    def test_a_malformed_file_or_a_missing_config_is_exit_2_with_a_message_and_no_traceback(self):
        bad = self.write("not [ toml")
        code, out, err = sw.run(str(bad))
        self.assertEqual((code, out), (2, ""))
        self.assertTrue(err.startswith("reachmap: error: "))
        self.assertNotIn("Traceback", err)
        code, out, err = sw.run(str(sw.CASES / "valid.toml"), "--config", "/nonexistent/reachmap.toml")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("config error", err)


class AsOf(unittest.TestCase):
    """The original is analysed as of its merge time: a second earlier the change is not on its branch yet."""

    def write(self, when):
        d = Path(tempfile.mkdtemp(prefix="reachmap-asof-"))
        self.addCleanup(sg.remove_tree, d)
        p = d / "cases.toml"
        p.write_text(
            f'[[case]]\nid = "x"\nrepo = "acme/web"\nmerged_at = "{when}"\noriginal = {{ compare = "main...scenario/s01-shared-component" }}\n'
            'witness = { range = "scenario/s01-shared-component..eval/w01-invoice-list-column" }\n', encoding="utf-8")
        return p

    def test_at_the_merge_time_the_change_is_analysed_and_hits_and_a_second_before_it_is_not_there(self):
        code, out, err = sw.run(str(self.write("2023-11-15T09:13:20Z")))
        self.assertEqual(code, 0, err)
        self.assertIn("hits: 1 of 1 by findings; 1 of 1 by screens; 1 of 1 by any evidence", out)
        code, out, err = sw.run(str(self.write("2023-11-15T09:13:19Z")))
        self.assertEqual(code, 0, err)
        self.assertIn("hits: 0 of 1 by findings; 0 of 1 by screens; 0 of 1 by any evidence", out)

    def test_before_any_commit_of_the_repository_the_change_cannot_be_read_and_the_set_is_refused(self):
        code, out, err = sw.run(str(self.write("2023-11-14T22:00:00Z")))
        self.assertEqual((code, out), (2, ""))
        self.assertIn("no commit on 'main' at or before 2023-11-14T22:00:00Z", err)


if __name__ == "__main__":
    unittest.main()
