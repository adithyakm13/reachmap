"""The reach-beyond-the-diff harness (M18) without a repository: what counts as a hit, the refusals, the output. The fixture cases through real
git are in `test_eval_witness_fixtures`."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from reachmap import wording
from reachmap.diffparse import DiffLine, FileChange, Hunk
from reachmap.evaluate import UsageError, witness
from reachmap.evaluate.witness import Case, Change, Resolved

SHA = lambda c: c * 40  # noqa: E731


def file(path, added=(), removed=(), old_path=None, status="modified", binary=False):
    hunk = Hunk(1, len(removed), 1, len(added), tuple(DiffLine(i + 1, t) for i, t in enumerate(removed)), tuple(DiffLine(i + 1, t) for i, t in enumerate(added)))
    return FileChange(path, old_path, status, (hunk,), binary, not binary)


def case(cid, repo="acme/web", original=("compare", "main...a"), wit=("compare", "main...b"), wrepo=None, merged="2024-01-01T00:00:00Z"):
    return Case(cid, repo, merged, Change(repo, *original), Change(wrepo or repo, *wit))


def ev(repo, path):
    return SimpleNamespace(repo=repo, path=path)


def finding(*paths, exclusion=False, repo="acme/web", kind="direct"):
    return SimpleNamespace(is_exclusion=exclusion, kind=kind, evidence=[ev(repo, p) for p in paths])


def area(*paths, by_name=False, repo="acme/web"):
    return SimpleNamespace(by_name=by_name, all_evidence=lambda: [ev(repo, p) for p in paths])


def analysis(findings=(), areas=(), status="completed", blocking=False):
    return SimpleNamespace(status=status, findings=list(findings), areas=list(areas), blocking=lambda: [1] if blocking else [])


class Paths(unittest.TestCase):
    def test_a_path_matches_only_itself_never_a_suffix_or_a_substring(self):
        yes = [("a/b/c.ts", "a/b/c.ts"), ("index.ts", "index.ts")]
        no = [("ab/c.ts", "b/c.ts"), ("test/x.ts", "backtest/x.ts"), ("a/b/c.ts", "b/c.ts"), ("a/b/c.ts", "c.ts"), ("web/src/a.ts", "src/a.ts"), ("src/a.ts", "web/src/a.ts"),
              ("src/a.ts", "src/b.ts"), ("src/a.ts", "src/a.tsx"), ("src/a.ts", "xsrc/a.ts"), ("a/bc.ts", "b/c.ts")]
        for a, b in yes:
            with self.subTest(f"{a} {b}"):
                self.assertTrue(witness.path_matches(a, b))
        for a, b in no:
            with self.subTest(f"{a} {b}"):
                self.assertFalse(witness.path_matches(a, b))

    def test_a_file_matches_only_in_its_own_repository(self):
        self.assertTrue(witness.matches(("acme/web", "src/a.ts"), ("acme/web", "src/a.ts")))
        self.assertFalse(witness.matches(("acme/web", "src/a.ts"), ("acme/api", "src/a.ts")))
        self.assertFalse(witness.matches(("acme/web", "src/a.ts"), ("acme/web", "web/src/a.ts")))


class CaseFiles(unittest.TestCase):
    def read(self, text):
        d = Path(tempfile.mkdtemp(prefix="reachmap-cases-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        p = d / "cases.toml"
        p.write_text(text, encoding="utf-8")
        return witness.read_cases(p)

    GOOD = '''
config = "../reachmap.toml"
[[case]]
id = "one"
repo = "acme/web"
merged_at = "2024-01-02T03:04:05Z"
original = { pr = 12 }
witness = { compare = "main...fix", repo = "acme/api" }
[[case]]
id = "two"
repo = "acme/web"
merged_at = 2024-01-02T03:04:05
original = { range = "a..b" }
witness = { pr = "#13" }
'''

    def test_a_file_is_read_into_cases_with_the_witness_repository_defaulting_to_the_cases(self):
        cases, config = self.read(self.GOOD)
        self.assertEqual(config, "../reachmap.toml")
        self.assertEqual([c.id for c in cases], ["one", "two"])
        self.assertEqual(cases[0].original, Change("acme/web", "pr", "12"))
        self.assertEqual(cases[0].witness, Change("acme/api", "compare", "main...fix"))
        self.assertEqual(cases[1].witness, Change("acme/web", "pr", "#13"))
        self.assertEqual(cases[1].merged_at, "2024-01-02T03:04:05Z")  # a TOML time with no zone is UTC

    def test_every_malformed_file_is_a_usage_error_that_says_what_to_fix(self):
        base = '[[case]]\nid = "x"\nrepo = "r"\nmerged_at = "2024-01-01T00:00:00Z"\noriginal = {original}\nwitness = {witness}\n'
        ok = '{ compare = "a...b" }'
        bad = {
            "no case": "",
            "bad toml": "[[case",
            "unknown top level": 'x = 1\n' + base.format(original=ok, witness=ok),
            "missing key": '[[case]]\nid = "x"\nrepo = "r"\n',
            "unknown key": base.format(original=ok, witness=ok) + 'extra = 1\n',
            "two kinds": base.format(original='{ pr = 1, compare = "a...b" }', witness=ok),
            "no kind": base.format(original="{ }", witness=ok),
            "unknown change key": base.format(original='{ compare = "a...b", when = 1 }', witness=ok),
            "a repo on the original": base.format(original='{ compare = "a...b", repo = "acme/api" }', witness=ok),
            "bad time": base.replace("2024-01-01T00:00:00Z", "yesterday").format(original=ok, witness=ok),
            "time before 1970": base.replace("2024-01-01T00:00:00Z", "1960-01-01T00:00:00Z").format(original=ok, witness=ok),
            "boolean pr": base.format(original="{ pr = true }", witness=ok),
            "duplicate id": base.format(original=ok, witness=ok) + base.format(original=ok, witness=ok),
        }
        for name, text in bad.items():
            with self.subTest(name):
                with self.assertRaises(UsageError):
                    self.read(text)

    def test_a_file_that_cannot_be_read_is_a_usage_error(self):
        with self.assertRaises(UsageError):
            witness.read_cases("/nonexistent/cases.toml")


class Reverts(unittest.TestCase):
    def test_the_same_files_with_the_inverse_diff_is_a_revert(self):
        original = [file("a.ts", added=["new"], removed=["old"]), file("b.ts", added=["x", "x"])]
        undone = [file("a.ts", added=["old"], removed=["new"]), file("b.ts", removed=["x", "x"])]
        self.assertTrue(witness.is_revert(original, undone))

    def test_a_revert_with_other_files_beside_it_is_still_a_revert(self):
        original = [file("a.ts", added=["new"], removed=["old"])]
        self.assertTrue(witness.is_revert(original, [file("a.ts", added=["old", "more"], removed=["new", "gone"]), file("b.ts", added=["fix"])]))

    def test_the_inverse_of_only_some_of_the_files_is_not_a_revert(self):
        original = [file("a.ts", added=["new"], removed=["old"]), file("b.ts", added=["x"])]
        self.assertFalse(witness.is_revert(original, [file("a.ts", added=["old"], removed=["new"])]))
        self.assertFalse(witness.is_revert(original, [file("a.ts", added=["old"], removed=["new"]), file("b.ts", added=["x"])]))

    def test_the_same_files_with_another_change_is_not_a_revert(self):
        original = [file("a.ts", added=["new"], removed=["old"])]
        for other in ([file("a.ts", added=["old"], removed=["newer"])], [file("a.ts", added=["new"], removed=["old"])], [file("a.ts", added=["old"])]):
            self.assertFalse(witness.is_revert(original, other))

    def test_nothing_to_compare_is_not_a_revert(self):
        original = [file("a.ts", added=["new"], removed=["old"])]
        self.assertFalse(witness.is_revert(original, []))
        self.assertFalse(witness.is_revert(original, [file("b.ts", added=["old"], removed=["new"])]))
        self.assertFalse(witness.is_revert([], []))

    def test_files_with_no_text_are_not_compared_and_a_change_of_only_such_files_is_not_called_a_revert(self):
        blob = [file("a.png", binary=True)]
        self.assertFalse(witness.is_revert(blob, blob))
        mixed = [file("a.ts", added=["new"], removed=["old"]), file("a.png", binary=True)]
        self.assertTrue(witness.is_revert(mixed, [file("a.ts", added=["old"], removed=["new"]), file("a.png", binary=True)]))


class Outside(unittest.TestCase):
    def test_the_files_of_the_witness_that_the_original_did_not_touch(self):
        original = [file("src/a.ts"), file("src/new.ts", old_path="src/old.ts", status="renamed")]
        self.assertEqual(witness.outside(original, [file("src/a.ts"), file("src/b.ts"), file("src/old.ts")], True), ["src/b.ts"])

    def test_in_another_repository_nothing_is_inside(self):
        self.assertEqual(witness.outside([file("src/a.ts")], [file("src/a.ts")], False), ["src/a.ts"])


def resolved(head, paths, pr=None):
    return Resolved(head, tuple(file(p) for p in paths), pr)


class Independence(unittest.TestCase):
    def check(self, cases, pairs):
        return witness.independence(cases, {c.id: p for c, p in zip(cases, pairs)})

    def test_two_cases_that_share_a_witness_are_named_with_what_they_share(self):
        a, b = case("a"), case("b", original=("compare", "main...c"))
        problems = self.check([a, b], [(resolved(SHA("1"), ["x.ts"]), resolved(SHA("9"), ["w.ts"])), (resolved(SHA("2"), ["y.ts"]), resolved(SHA("9"), ["v.ts"]))])
        self.assertEqual(len(problems), 1)
        self.assertIn("cases a and b share a witness (the same head commit 999999999999 in acme/web)", problems[0])

    def test_the_same_set_of_witness_files_is_one_witness_even_with_another_commit(self):
        a, b = case("a"), case("b")
        problems = self.check([a, b], [(resolved(SHA("1"), ["x.ts"]), resolved(SHA("8"), ["w.ts", "v.ts"])), (resolved(SHA("2"), ["y.ts"]), resolved(SHA("9"), ["v.ts", "w.ts"]))])
        self.assertEqual(len(problems), 1)
        self.assertIn("share a witness (the same 2 changed files in acme/web)", problems[0])

    def test_two_cases_with_the_same_subject_by_head_by_files_or_by_pull_request(self):
        for first, second, words in (
            (resolved(SHA("1"), ["x.ts"]), resolved(SHA("1"), ["z.ts"]), "the same head commit"),
            (resolved(SHA("1"), ["x.ts", "y.ts"]), resolved(SHA("2"), ["y.ts", "x.ts"]), "the same 2 changed files"),
            (resolved(SHA("1"), ["x.ts"], ("acme/web", 7)), resolved(SHA("2"), ["z.ts"], ("acme/web", 7)), "the same pull request"),
        ):
            with self.subTest(words):
                a, b = case("a"), case("b")
                problems = self.check([a, b], [(first, resolved(SHA("8"), ["w.ts"])), (second, resolved(SHA("9"), ["v.ts"]))])
                self.assertEqual(len(problems), 1)
                self.assertIn("cases a and b share a subject (" + words, problems[0])

    def test_the_same_head_in_two_repositories_and_overlapping_but_different_file_sets_are_independent(self):
        a, b = case("a"), case("b", repo="acme/api", original=("compare", "main...c"), wit=("compare", "main...d"))
        self.assertEqual(self.check([a, b], [(resolved(SHA("1"), ["x.ts"]), resolved(SHA("8"), ["w.ts"])), (resolved(SHA("1"), ["x.ts"]), resolved(SHA("8"), ["w.ts"]))]), [])
        a, b = case("a"), case("b")
        self.assertEqual(self.check([a, b], [(resolved(SHA("1"), ["x.ts", "y.ts"]), resolved(SHA("8"), ["w.ts"])), (resolved(SHA("2"), ["x.ts"]), resolved(SHA("9"), ["w.ts", "v.ts"]))]), [])

    def test_every_pair_is_checked(self):
        cases = [case("a"), case("b"), case("c")]
        same = resolved(SHA("9"), ["w.ts"])
        problems = self.check(cases, [(resolved(SHA("1"), ["x.ts"]), same), (resolved(SHA("2"), ["y.ts"]), same), (resolved(SHA("3"), ["z.ts"]), same)])
        self.assertEqual(len(problems), 3)


class Scoring(unittest.TestCase):
    FILES = [("acme/web", "src/w.ts")]

    def score(self, a, files=None):
        return witness.score(case("a"), self.FILES if files is None else files, a)

    def test_a_finding_that_cites_the_witness_file_is_a_hit_by_findings_and_by_any_evidence_only(self):
        o = self.score(analysis(findings=[finding("src/a.ts", "src/w.ts")]))
        self.assertEqual((bool(o.by_findings), bool(o.by_screens), bool(o.by_any)), (True, False, True))

    def test_a_screen_that_cites_it_is_a_hit_by_screens_and_by_any_evidence_not_by_findings(self):
        o = self.score(analysis(areas=[area("src/p.ts", "src/w.ts")]))
        self.assertEqual((bool(o.by_findings), bool(o.by_screens), bool(o.by_any)), (False, True, True))

    def test_a_candidate_found_by_name_is_any_evidence_but_not_a_screen(self):
        o = self.score(analysis(areas=[area("src/w.ts", by_name=True)]))
        self.assertEqual((bool(o.by_findings), bool(o.by_screens), bool(o.by_any)), (False, False, True))

    def test_an_exclusion_is_never_a_hit_and_is_said_apart(self):
        o = self.score(analysis(findings=[finding("src/w.ts", exclusion=True)]))
        self.assertEqual((o.by_findings, o.by_screens, o.by_any), ([], [], []))
        self.assertEqual(o.only_exclusions, self.FILES)

    def test_a_file_an_exclusion_and_a_finding_both_cite_is_a_hit_and_not_said_as_exclusion_only(self):
        o = self.score(analysis(findings=[finding("src/w.ts", exclusion=True), finding("src/w.ts")]))
        self.assertEqual(o.by_findings, self.FILES)
        self.assertEqual(o.only_exclusions, [])

    def test_a_file_cited_in_another_repository_or_inside_a_longer_name_is_not_a_hit(self):
        for a in (analysis(findings=[finding("src/w.ts", repo="acme/api")]), analysis(findings=[finding("src/w.ts.bak", "xsrc/w.ts")])):
            self.assertEqual(self.score(a).by_any, [])

    def test_a_longer_path_that_ends_in_the_witness_path_is_not_a_hit(self):
        o = self.score(analysis(findings=[finding("packages/web/src/w.ts")]))
        self.assertEqual(o.by_findings, [])

    def test_a_release_finding_is_never_a_hit_and_is_said_apart(self):
        o = self.score(analysis(findings=[finding("src/w.ts", kind="release")]))
        self.assertEqual((o.by_findings, o.by_screens, o.by_any), ([], [], []))
        self.assertEqual(o.only_releases, self.FILES)

    def test_a_file_a_release_finding_and_a_screen_both_cite_is_a_hit_by_the_screen_only(self):
        o = self.score(analysis(findings=[finding("src/w.ts", kind="release")], areas=[area("src/w.ts")]))
        self.assertEqual((o.by_findings, o.by_screens, o.by_any, o.only_releases), ([], self.FILES, self.FILES, []))

    def test_a_run_that_did_not_complete_is_not_a_miss_and_is_not_counted(self):
        for status in ("failed", "timed_out"):
            o = self.score(analysis(status=status))
            self.assertEqual((o.status, o.run), ("not_analysed", status))

    def test_a_run_with_blocking_records_is_counted_and_said_to_be_incomplete(self):
        o = self.score(analysis(blocking=True))
        self.assertEqual((o.status, o.incomplete, o.by_any), ("analysed", True, []))


class Output(unittest.TestCase):
    def outcomes(self, hits, misses, skipped=0, incomplete=0):
        out = []
        for i in range(hits):
            out.append(witness.score(case(f"h{i}"), [("acme/web", "w.ts")], analysis(findings=[finding("w.ts")], areas=[area("w.ts")])))
        for i in range(misses):
            out.append(witness.score(case(f"m{i}"), [("acme/web", "w.ts")], analysis(areas=[area("p.ts")], blocking=i < incomplete)))
        for i in range(skipped):
            out.append(witness.score(case(f"s{i}"), [("acme/web", "w.ts")], analysis(status="failed")))
        return out

    def test_the_counts_are_per_axis_as_n_of_m_and_there_is_no_percentage(self):
        text = "\n".join(witness.render(self.outcomes(3, 6)))
        self.assertIn("hits: 3 of 9 by findings; 3 of 9 by screens; 3 of 9 by any evidence", text)
        self.assertNotIn("%", text)
        self.assertNotRegex(text, r"\d\.\d|\bpercent")

    def test_the_hits_by_screens_and_by_findings_are_counted_separately(self):
        out = [witness.score(case("a"), [("acme/web", "w.ts")], analysis(areas=[area("w.ts")])), witness.score(case("b"), [("acme/web", "w.ts")], analysis(findings=[finding("w.ts")]))]
        self.assertIn("hits: 1 of 2 by findings; 1 of 2 by screens; 2 of 2 by any evidence", "\n".join(witness.render(out)))

    def test_it_says_small_samples_are_weak_and_that_no_recall_number_exists_without_linked_defects(self):
        text = "\n".join(witness.render(self.outcomes(1, 1)))
        self.assertIn("small samples are weak", text)
        self.assertIn("No recall number exists without defects linked to the changes that caused them", text)

    def test_cases_not_analysed_are_listed_and_left_out_of_every_count(self):
        text = "\n".join(witness.render(self.outcomes(1, 1, skipped=2)))
        self.assertIn("witness cases: 4 read, 2 analysed, 2 not analysed", text)
        self.assertIn("s0 (acme/web, as of 2024-01-01T00:00:00Z): not analysed (the run failed); left out of every count", text)
        self.assertIn("1 of 2 by findings", text)

    def test_incomplete_runs_are_said(self):
        text = "\n".join(witness.render(self.outcomes(0, 2, incomplete=1)))
        self.assertIn("1 of 2 analysed runs were incomplete", text)
        self.assertIn("the run was incomplete (blocking records)", text)

    def test_the_output_says_what_as_of_does_not_pin_and_that_release_findings_do_not_count(self):
        text = "\n".join(witness.render(self.outcomes(1, 1)))
        self.assertIn("It does not pin the tags of a producer, the commits a host reports for a pull request, or the hosted test sources", text)
        self.assertIn("release findings are therefore not counted as hits", text)

    def test_a_witness_file_that_is_a_hit_in_two_cases_is_listed(self):
        out = [witness.score(case(cid), [("acme/web", "w.ts")], analysis(findings=[finding("w.ts")])) for cid in ("a", "b")]
        out.append(witness.score(case("c"), [("acme/web", "v.ts")], analysis(findings=[finding("v.ts")])))
        text = "\n".join(witness.render(out))
        self.assertIn("counted for more than one case: acme/web:w.ts (cases a, b)", text)
        self.assertNotIn("v.ts (cases", text)
        self.assertNotIn("counted for more than one case", "\n".join(witness.render(out[1:])))

    def test_a_release_only_file_is_said_apart_in_the_output(self):
        o = witness.score(case("a"), [("acme/web", "w.ts")], analysis(findings=[finding("w.ts", kind="release")]))
        text = "\n".join(witness.render([o]))
        self.assertIn("cited only by a release finding", text)
        self.assertIn("hits: 0 of 1 by findings; 0 of 1 by screens; 0 of 1 by any evidence", text)

    def test_the_exit_code_is_1_when_a_case_was_not_analysed_and_0_otherwise(self):
        self.assertEqual(witness.exit_code(self.outcomes(1, 1)), 0)
        self.assertEqual(witness.exit_code(self.outcomes(1, 1, skipped=1)), 1)
        self.assertEqual(witness.exit_code([]), 0)

    def test_the_words_pass_the_wording_check_and_have_no_dash_or_arrow(self):
        text = "\n".join(witness.render(self.outcomes(2, 2, skipped=1, incomplete=1)))
        self.assertEqual(wording.check(text), [])
        self.assertNotRegex(text, "[\u2014\u2013\u2192\u201c\u201d\u2018\u2019]|->")

    def test_hostile_paths_are_shown_without_acting(self):
        o = witness.score(case("a"), [("acme/web", "w\u202e\x1b.ts")], analysis(findings=[finding("w\u202e\x1b.ts")]))
        text = "\n".join(witness.render([o]))
        self.assertNotRegex(text, "[\u202e\x1b]")
        self.assertIn("\\u202e", text)


class Refusals(unittest.TestCase):
    """`run_cases` refuses before it analyses anything, and says every reason."""

    def setUp(self):
        self.analysed = []
        self.table = {}

    def run_cases(self, cases, later=lambda c, w: True):
        def resolve(c, change, as_of):
            got = self.table[(c.id, "original" if change is c.original else "witness")]
            if isinstance(got, Exception):
                raise got
            return got

        def analyse(c):
            self.analysed.append(c.id)
            return analysis(findings=[finding("w.ts")])

        return witness.run_cases(cases, resolve, later, analyse)

    def good(self, cid, head="1", wit="9", orig_files=("a.ts",), wit_files=("w.ts",)):
        self.table[(cid, "original")] = resolved(SHA(head), orig_files)
        self.table[(cid, "witness")] = resolved(SHA(wit), wit_files)

    def test_independent_cases_are_counted(self):
        self.good("a")
        self.good("b", head="2", wit="8", orig_files=("b.ts",), wit_files=("v.ts",))
        out = self.run_cases([case("a"), case("b")])
        self.assertEqual([o.status for o in out], ["analysed", "analysed"])
        self.assertEqual(self.analysed, ["a", "b"])
        self.assertEqual(out[0].files, [("acme/web", "w.ts")])

    def test_a_dependent_set_is_refused_whole_and_nothing_is_analysed(self):
        self.good("a")
        self.good("b", head="2", orig_files=("b.ts",))  # the witness is the same
        with self.assertRaises(witness.Refused) as ctx:
            self.run_cases([case("a"), case("b")])
        self.assertIn("share a witness", ctx.exception.reasons[0])
        self.assertEqual(self.analysed, [])

    def test_a_revert_is_refused_in_words_and_does_not_reach_the_independence_check(self):
        self.table[("a", "original")] = Resolved(SHA("1"), (file("a.ts", added=["n"], removed=["o"]),), None)
        self.table[("a", "witness")] = Resolved(SHA("9"), (file("a.ts", added=["o"], removed=["n"]),), None)
        with self.assertRaises(witness.Refused) as ctx:
            self.run_cases([case("a")])
        self.assertEqual(len(ctx.exception.reasons), 1)
        self.assertIn("case a: the witness holds the inverse of the original", ctx.exception.reasons[0])
        self.assertEqual(self.analysed, [])

    def test_a_witness_that_holds_a_revert_and_a_fix_is_refused(self):
        self.table[("a", "original")] = Resolved(SHA("1"), (file("a.ts", added=["n"], removed=["o"]),), None)
        self.table[("a", "witness")] = Resolved(SHA("9"), (file("a.ts", added=["o"], removed=["n"]), file("w.ts", added=["fix"])), None)
        with self.assertRaises(witness.Refused) as ctx:
            self.run_cases([case("a")])
        self.assertIn("holds the inverse of the original's diff for every file of the original", ctx.exception.reasons[0])
        self.assertEqual(self.analysed, [])

    def test_a_witness_in_another_repository_is_never_a_revert(self):
        self.table[("a", "original")] = Resolved(SHA("1"), (file("a.ts", added=["n"], removed=["o"]),), None)
        self.table[("a", "witness")] = Resolved(SHA("9"), (file("a.ts", added=["o"], removed=["n"]),), None)
        self.assertEqual(len(self.run_cases([case("a", wrepo="acme/api")])), 1)

    def test_a_witness_with_no_file_outside_the_diff_is_refused(self):
        self.good("a", wit_files=("a.ts",))
        with self.assertRaises(witness.Refused) as ctx:
            self.run_cases([case("a")])
        self.assertIn("every file of the witness is inside the original diff", ctx.exception.reasons[0])

    def test_a_witness_that_is_not_later_or_cannot_be_dated_is_refused(self):
        self.good("a")
        for answer, words in ((False, "is not later than the merge time"), (None, "could not be established")):
            with self.subTest(words):
                with self.assertRaises(witness.Refused) as ctx:
                    self.run_cases([case("a")], later=lambda c, w: answer)
                self.assertIn(words, ctx.exception.reasons[0])

    def test_a_change_that_cannot_be_read_is_refused_and_every_reason_is_listed(self):
        self.table[("a", "original")] = UsageError("acme/web compare x...y cannot be read")
        self.table[("a", "witness")] = resolved(SHA("9"), ["w.ts"])
        self.good("b", wit_files=("b.ts",), orig_files=("b.ts",))
        with self.assertRaises(witness.Refused) as ctx:
            self.run_cases([case("a"), case("b")])
        self.assertEqual(len(ctx.exception.reasons), 2)
        self.assertIn("case a: acme/web compare x...y cannot be read", ctx.exception.reasons[0])
        self.assertIn("case b: every file", ctx.exception.reasons[1])


if __name__ == "__main__":
    unittest.main()
