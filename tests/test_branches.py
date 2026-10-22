"""The branch matrix (M9): for every configured branch of the changed repository, whether the change is there. Text
comparison on its own first (exact lines, counting multiplicity), then git: small repositories with a release branch for
each state, branch names with slashes, a renamed file, files git cannot read, a branch read as of a time."""

import atexit
import shutil
import stat
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from reachmap import branches, config
from reachmap.changeset import resolve
from reachmap.gitlayer import Git
from reachmap.model import BranchState
from tests import scenario_support as ss
from tests import support_git as sg
from tests.fixtures import build
from tests.support_cross import build_repo

BEFORE = "def total(items):\n    amount = sum(items)\n    return round(amount, 2)\n"
AFTER = "def total(items):\n    amount = sum(items)\n    return amount.quantize(0.01)\n    # half up\n"


class Classify(unittest.TestCase):
    """The comparison of one file at a branch tip with what the change added and removed. An added line counts as found at a
    tip when the tip holds more copies of it than the file had before the change (less the copies the change removed)."""

    def go(self, added, removed, tip, pre=None, added_at=None):
        before = "\n".join(removed) if pre is None else pre  # what the file was: at least the lines the change removes
        return branches.classify_text(added, removed, tip.split("\n"), before.split("\n"), added_at)

    def test_every_added_line_found_is_present(self):
        c = self.go(["a = 1", "b = 2"], ["a = 0"], "x\na = 1\ny\nb = 2\n")
        self.assertEqual((c.state, c.added_found, c.added), ("present", 2, 2))
        self.assertEqual([n for n, _ in c.found], [2, 4])  # the lines at the tip, numbered from 1

    def test_some_of_the_added_lines_is_partial(self):
        c = self.go(["a = 1", "b = 2"], ["a = 0"], "a = 1\nz\n")
        self.assertEqual((c.state, c.added_found), ("partial", 1))

    def test_none_of_the_added_lines_and_the_removed_ones_still_there_is_pre_change(self):
        c = self.go(["a = 1"], ["a = 0", "b = 0"], "a = 0\nb = 0\nc\n")
        self.assertEqual((c.state, c.added_found, c.removed_found), ("pre_change", 0, 2))

    def test_neither_is_diverged(self):
        c = self.go(["a = 1"], ["a = 0", "b = 0"], "a = 0\nsomething else\n")
        self.assertEqual((c.state, c.removed_found), ("diverged", 1))
        c = self.go(["a = 1"], ["a = 0"], "nothing like it\n")
        self.assertEqual((c.state, c.added_found, c.removed_found), ("diverged", 0, 0))

    def test_a_pure_addition_that_is_not_there_is_pre_change(self):
        c = self.go(["new line"], [], "old\n", pre="old\n")
        self.assertEqual(c.state, "pre_change")

    def test_multiplicity_counts(self):
        """Two added copies of a line and one at the tip: one found, so partial."""
        c = self.go(["    return None", "    return None"], [], "    return None\nx\n", pre="x\n")
        self.assertEqual((c.state, c.added_found, c.added), ("partial", 1, 2))
        c = self.go(["    return None", "    return None"], [], "    return None\n    return None\n", pre="")
        self.assertEqual(c.state, "present")

    def test_a_line_the_file_already_had_is_not_found_by_the_copy_that_was_there(self):
        """Shape B: the fix adds `if x is None:` to g, f already has it. At the old tip the one copy is f's, not the change's."""
        lines = ["    if x is None:", "        return None"]
        old_tip = "def f(x):\n    if x is None:\n        return None\n    return x\n\ndef g(x):\n    return x\n"
        c = self.go(lines, [], old_tip, pre=old_tip)
        self.assertEqual((c.state, c.added_found), ("pre_change", 0))
        fixed = old_tip.replace("def g(x):\n", "def g(x):\n    if x is None:\n        return None\n")
        c = self.go(lines, [], fixed, pre=old_tip)
        self.assertEqual((c.state, c.added_found), ("present", 2))
        self.assertEqual([n for n, _ in c.found], [7, 8])  # the new copies, not the ones in f (lines 2 and 3)

    def test_a_tip_with_fewer_copies_than_before_finds_none_and_never_a_negative_count(self):
        c = self.go(["    return None"], [], "x\n", pre="    return None\n    return None\n")
        self.assertEqual((c.state, c.added_found), ("pre_change", 0))

    def test_a_line_the_change_also_removes_is_not_distinctive_and_is_left_out(self):
        c = self.go(["a = 1", "moved = 2"], ["moved = 2", "a = 0"], "a = 0\nmoved = 2\n")
        self.assertEqual((c.state, c.added, c.added_found), ("pre_change", 1, 0))  # only `a = 1` is compared

    def test_a_file_whose_every_added_line_is_also_removed_has_no_state_and_says_why(self):
        c = self.go(["moved = 2"], ["moved = 2"], "moved = 2\n")
        self.assertIsNone(c.state)
        self.assertIn("removed", c.reason)

    def test_the_cited_copies_are_those_nearest_to_where_the_change_put_them(self):
        pre = "r\n    request: Request,\nq\n    request: Request,\nz\n"
        tip = pre + "tail\n    request: Request,\n"
        c = self.go(["    request: Request,"], [], tip, pre=pre, added_at=[7])
        self.assertEqual((c.state, [n for n, _ in c.found]), ("present", [7]))

    def test_trailing_whitespace_and_a_carriage_return_do_not_matter_and_leading_whitespace_does(self):
        self.assertEqual(self.go(["a = 1   "], [], "a = 1\r\n", pre="").state, "present")
        self.assertEqual(self.go(["a = 1"], ["z"], "    a = 1\nz\n").state, "pre_change")  # indented differently: another line

    def test_blank_lines_are_not_compared(self):
        c = self.go(["", "a = 1", "   "], [], "a = 1\n", pre="")
        self.assertEqual((c.state, c.added), ("present", 1))

    def test_the_found_lines_are_verbatim_and_never_blank(self):
        c = self.go(["a = 1"], [], "a = 1   \n", pre="")
        self.assertEqual(c.found, ((1, "a = 1   "),))

    def test_a_change_that_only_removes_lines_is_never_present(self):
        gone = self.go([], ["old"], "nothing\n")
        self.assertIsNone(gone.state)  # not shown as present: there is nothing added to find
        there = self.go([], ["old"], "old\nx\n")
        self.assertEqual(there.state, "pre_change")
        part = self.go([], ["old", "older"], "old\n")
        self.assertEqual(part.state, "diverged")

    def test_a_change_with_nothing_to_compare_has_no_state(self):
        self.assertIsNone(self.go(["", " "], [], "x\n", pre="x\n").state)

    def test_every_found_line_is_cited_up_to_a_cap(self):
        lines = [f"line {i}" for i in range(80)]
        c = branches.classify_text(lines, [], lines, [])
        self.assertEqual((c.state, c.added_found, len(c.found)), ("present", 80, branches.MAX_EVIDENCE_PER_FILE))
        self.assertTrue(c.cut)


STANDARD = [
            {"files": {"calc.py": BEFORE, "other.py": "x = 1\n"}, "message": "start"},
            {"branch": "fix", "from": "main", "files": {"calc.py": AFTER}, "message": "the change"},
            {"branch": "release/1.x", "from": "main", "files": {"calc.py": AFTER}, "message": "backport"},
            {"branch": "release/2.0/rc", "from": "main", "files": {"calc.py": BEFORE.replace("round(amount, 2)", "round(amount, 3)")}, "message": "diverged"},
            {"branch": "release/3", "from": "main", "files": {"calc.py": BEFORE.replace("return round(amount, 2)", "return amount.quantize(0.01)")}, "message": "partial"},
            {"branch": "release/old", "from": "main", "delete": ["calc.py"], "files": {"gone.py": "y = 1\n"}, "message": "no calc"},
]
_SHARED: dict = {}


def shared_standard():
    """The standard repository, built once for the module (the tests that use it only read it), with one git cache."""
    if not _SHARED:
        root = Path(tempfile.mkdtemp(prefix="reachmap-branches-"))
        atexit.register(sg.remove_tree, root)
        _SHARED.update(root=root, path=build_repo(root, "repo", STANDARD))
    return _SHARED


class MatrixCase(sg.TempDirCase):
    """Small repositories with a change on a branch and release branches in every state."""

    def make(self, commits, name="repo"):
        self.path = build_repo(self.tmp, name, commits)
        self.url = sg.remote_url(self.path)
        return self.path

    def standard(self):
        """The change on `fix`, and a release branch for each state (built once for the module)."""
        shared = shared_standard()
        self.path, self.url = shared["path"], sg.remote_url(shared["path"])
        self.cache = shared["root"] / "cache"
        return self.path

    def workspace(self, branches_list=("main", "release/*")):
        text = f'''
workspace = "t"
[repos."t/app"]
host = "git"
remote = "{self.url}"
language = "python"
roles = ["backend"]
branches = {list(branches_list)!r}
'''.replace("'", '"')
        return config.loads(text, fmt="toml", env={})

    def git(self, **kw):
        return Git(getattr(self, "cache", self.tmp / "cache"), env=sg.isolated_env(self.tmp / "home"), **kw)

    def run_matrix(self, *, spec="main...fix", branches_list=("main", "release/*"), head_ref="fix", as_of=None, git=None, **kw):
        ws = self.workspace(branches_list)
        git = git or self.git()
        change = resolve(ws, "t/app", compare=spec, git=git)
        return branches.matrix(git, self.url, ws.repos["t/app"], change, head_ref=head_ref, as_of=as_of, **kw), git, change


class Rows(MatrixCase):
    def setUp(self):
        super().setUp()
        self.standard()

    def test_each_branch_has_its_state_and_the_primary_branch_comes_first(self):
        m, git, change = self.run_matrix()
        got = {r.branch: r.state for r in m.rows}
        self.assertEqual(got, {"main": "pre_change", "release/1.x": "present", "release/2.0/rc": "diverged", "release/3": "partial", "release/old": "file_absent"})
        self.assertEqual([r.branch for r in m.rows][0], "main")
        self.assertEqual(list(m.rows), sorted(m.rows, key=lambda r: (r.branch != "main", r.branch)))
        self.assertTrue(m.complete)
        self.assertEqual(git.incompleteness, [])
        for r in m.rows:
            self.assertIsInstance(r, BranchState)
            self.assertEqual(r.commit, sg.rev(self.path, r.branch))  # the tip that was read, a full sha

    def test_the_evidence_is_the_found_lines_at_the_tip(self):
        m, _, _ = self.run_matrix()
        rows = {r.branch: r for r in m.rows}
        present = rows["release/1.x"]
        self.assertEqual(present.added_lines_found, 2)
        self.assertEqual([(e.path, e.line, e.detail) for e in present.evidence], [("calc.py", 3, "    return amount.quantize(0.01)"), ("calc.py", 4, "    # half up")])
        self.assertTrue(all(e.ref == present.commit and e.repo == "t/app" for e in present.evidence))
        self.assertEqual([(e.line, e.detail) for e in rows["release/3"].evidence], [(3, "    return amount.quantize(0.01)")])  # partial: one of two
        self.assertEqual(rows["release/3"].added_lines_found, 1)
        pre = rows["main"]
        self.assertEqual(pre.added_lines_found, 0)
        self.assertEqual([(e.line, e.detail) for e in pre.evidence], [(3, "    return round(amount, 2)")])  # the removed line, still there
        self.assertEqual(rows["release/old"].evidence, ())  # a path git says is absent has nothing to cite

    def test_every_evidence_resolves_at_its_tip(self):
        m, _, _ = self.run_matrix()
        found = [e for r in m.rows for e in r.evidence]
        self.assertGreater(len(found), 3)
        self.assertEqual(ss.evidence_problems({"t/app": self.path}, found), [])

    def test_the_changes_own_head_branch_is_left_out(self):
        m, _, _ = self.run_matrix(branches_list=("main", "release/*", "fix"))
        self.assertNotIn("fix", {r.branch for r in m.rows})
        self.assertTrue(any("fix is left out" in n and "own head branch" in n for n in m.notes), m.notes)  # said, not dropped quietly
        m2, _, _ = self.run_matrix(branches_list=("main", "release/*", "fix"), head_ref=None)
        self.assertEqual({r.branch: r.state for r in m2.rows}["fix"], "present")  # without the name it is a branch like any other
        self.assertFalse(any("left out" in n for n in m2.notes))

    def test_a_branch_of_the_heads_name_whose_tip_is_something_else_is_not_left_out(self):
        """A pull request from a fork's `main`: the head ref is called main, the head commit is not main's tip."""
        m, _, _ = self.run_matrix(head_ref="main")
        self.assertIn("main", {r.branch for r in m.rows})
        self.assertFalse(any("left out" in n for n in m.notes), m.notes)

    def test_a_file_that_was_there_and_is_absent_at_a_tip_is_file_absent_and_not_unreadable(self):
        m, git, _ = self.run_matrix()
        self.assertEqual({r.branch: r.state for r in m.rows}["release/old"], "file_absent")
        self.assertEqual(git.incompleteness, [])

    def test_the_matrix_says_what_each_file_was(self):
        m, _, _ = self.run_matrix()
        files = {(f.branch, f.path): f for f in m.files}
        self.assertEqual(files[("release/1.x", "calc.py")].state, "present")
        self.assertEqual(files[("release/old", "calc.py")].state, "file_absent")
        self.assertEqual(sorted({p for _, p in files}), ["calc.py"])

    def test_as_of_reads_the_tips_as_they_were(self):
        """Commits are an hour apart (the third is the backport on release/1.x): at the time just after it, the branches cut
        later read as the commit they were cut from."""
        stamp = build.EPOCH + 3600 * 3 + 10
        iso = datetime.fromtimestamp(stamp, timezone.utc).isoformat()
        m, _, _ = self.run_matrix(as_of=iso)
        got = {r.branch: r for r in m.rows}
        self.assertEqual({b: r.state for b, r in got.items()}, {
            "main": "pre_change", "release/1.x": "present", "release/2.0/rc": "pre_change", "release/3": "pre_change", "release/old": "pre_change",
        })
        self.assertEqual(got["release/old"].commit, sg.rev(self.path, "main"))  # its own commit is later than the time
        self.assertEqual(got["release/1.x"].commit, sg.rev(self.path, "release/1.x"))
        later, _, _ = self.run_matrix()
        self.assertEqual({r.branch: r.state for r in later.rows}["release/old"], "file_absent")
        before, _, _ = self.run_matrix(as_of="2000-01-01T00:00:00Z")
        self.assertEqual(before.rows, ())  # nothing existed yet: no row, and a note says why
        self.assertTrue(before.notes)


class Renames(MatrixCase):
    def setUp(self):
        super().setUp()
        old = "def a():\n    return 1\n    # a\n    # b\n    # c\n    # d\n"
        new = old.replace("return 1", "return 2")
        self.make([
            {"files": {"old_name.py": old}, "message": "start"},
            {"branch": "ren", "from": "main", "delete": ["old_name.py"], "files": {"new_name.py": new}, "message": "rename and edit"},
            {"branch": "release/1.0", "from": "main", "files": {"NOTES": "a\n"}, "message": "still old"},
            {"branch": "release/1.1", "from": "ren", "files": {"NOTES": "a\n"}, "message": "has it"},
        ], "ren-repo")

    def test_a_renamed_file_is_read_at_its_new_path_and_its_old_path(self):
        m, _, change = self.run_matrix(spec="main...ren", head_ref="ren")
        self.assertEqual([(f.path, f.old_path, f.status) for f in change.files], [("new_name.py", "old_name.py", "renamed")])
        got = {r.branch: r.state for r in m.rows}
        self.assertEqual(got, {"main": "pre_change", "release/1.0": "pre_change", "release/1.1": "present"})
        old_row = [r for r in m.rows if r.branch == "release/1.0"][0]
        self.assertEqual({e.path for e in old_row.evidence}, {"old_name.py"})  # the removed line is cited where it still is
        new_row = [r for r in m.rows if r.branch == "release/1.1"][0]
        self.assertEqual({e.path for e in new_row.evidence}, {"new_name.py"})
        self.assertEqual(ss.evidence_problems({"t/app": self.path}, [e for r in m.rows for e in r.evidence]), [])


class SeveralFiles(MatrixCase):
    def setUp(self):
        super().setUp()
        a, b = "a1 = 1\na2 = 2\n", "b1 = 1\nb2 = 2\n"
        self.make([
            {"files": {"a.py": a, "b.py": b}, "message": "start"},
            {"branch": "two", "from": "main", "files": {"a.py": a.replace("1", "10"), "b.py": b.replace("1", "10")}, "message": "two files"},
            {"branch": "release/all", "from": "two", "files": {"NOTES": "a\n"}, "message": "both"},
            {"branch": "release/half", "from": "main", "files": {"a.py": a.replace("1", "10")}, "message": "one of two"},
            {"branch": "release/mix", "from": "main", "delete": ["b.py"], "files": {"NOTES": "a\n"}, "message": "b is gone, a is old"},
        ], "two-repo")

    def test_files_that_disagree_make_the_branch_partial_and_each_file_is_kept(self):
        m, _, _ = self.run_matrix(spec="main...two", head_ref="two")
        got = {r.branch: r.state for r in m.rows}
        self.assertEqual(got["release/all"], "present")
        self.assertEqual(got["release/half"], "partial")
        half = [r for r in m.rows if r.branch == "release/half"][0]
        self.assertIn("a.py", half.detail)
        self.assertIn("b.py", half.detail)
        files = {(f.branch, f.path): f.state for f in m.files}
        self.assertEqual((files[("release/half", "a.py")], files[("release/half", "b.py")]), ("present", "pre_change"))

    def test_a_mix_with_no_added_line_found_anywhere_is_not_partial(self):
        """`partial` says some of the change is there. Files that are old, absent or changed another way show none of it."""
        m, _, _ = self.run_matrix(spec="main...two", head_ref="two")
        mix = [r for r in m.rows if r.branch == "release/mix"]
        self.assertEqual([(r.state, r.added_lines_found) for r in mix], [("diverged", 0)])
        self.assertTrue(mix[0].evidence)  # the old line of a.py that is still there
        self.assertIn("a.py: pre_change", mix[0].detail)
        self.assertIn("b.py: file_absent", mix[0].detail)


class Unreadable(MatrixCase):
    def setUp(self):
        super().setUp()
        self.standard()

    def failing_git(self, tip_ref):
        """A git whose blob read fails for the tip of one ref (the read of a branch is one `ls-tree` and one `cat-file --batch`:
        the script marks the `ls-tree` of that tip and fails the batch that follows) and is the real git otherwise."""
        sha, flag = sg.rev(self.path, tip_ref), self.tmp / "flag"
        script = self.tmp / "failgit"
        script.write_text(
            f'#!/bin/sh\ncase "$*" in\n  *ls-tree*{sha}*) touch "{flag}";;\n'
            f'  *"cat-file --batch") if [ -f "{flag}" ]; then rm "{flag}"; echo "boom" >&2; exit 1; fi;;\nesac\nexec "{shutil.which("git")}" "$@"\n')
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        return self.git(git=str(script))

    def test_an_unreadable_file_is_an_unreadable_row_and_a_blocking_record_and_not_an_absence(self):
        git = self.failing_git("release/1.x")
        m, git, _ = self.run_matrix(git=git)
        got = {r.branch: r.state for r in m.rows}
        self.assertEqual(got["release/1.x"], "unreadable")
        self.assertEqual(got["main"], "pre_change")  # the other branches are still compared: unreadable blocks that branch only
        self.assertFalse(m.complete)
        row = [r for r in m.rows if r.state == "unreadable"][0]
        self.assertTrue(row.detail and "calc.py" in row.detail)
        self.assertEqual(row.evidence, ())
        self.assertEqual(row.commit, sg.rev(self.path, "release/1.x"))
        blocking = [r for r in git.incompleteness if r.kind == "read_unreadable" and r.blocks_selection]
        self.assertTrue(blocking)

    def test_a_branch_whose_changed_files_are_all_unreadable_has_one_row_per_file(self):
        a, b = "a1 = 1\n", "b1 = 1\n"
        path = build_repo(self.tmp, "u-repo", [
            {"files": {"a.py": a, "b.py": b}, "message": "start"},
            {"branch": "two", "from": "main", "files": {"a.py": "a1 = 2\n", "b.py": "b1 = 2\n"}, "message": "both"},
            {"branch": "release/x", "from": "two", "files": {"NOTES": "a\n"}, "message": "has both"},
        ])
        self.path = path
        script_git = self.failing_git("release/x")
        url = sg.remote_url(path)
        ws = config.loads(f'workspace = "t"\n[repos."t/app"]\nhost = "git"\nremote = "{url}"\nlanguage = "python"\nroles = ["backend"]\nbranches = ["main", "release/*"]\n', fmt="toml", env={})
        git = Git(self.tmp / "cache2", env=sg.isolated_env(self.tmp / "home"), git=script_git.runner.git)
        change = resolve(ws, "t/app", compare="main...two", git=git)
        m = branches.matrix(git, url, ws.repos["t/app"], change, head_ref="two")
        rows = [r for r in m.rows if r.branch == "release/x"]
        self.assertEqual([(r.state, "a.py" in r.detail, "b.py" in r.detail) for r in rows], [("unreadable", True, False), ("unreadable", False, True)])
        self.assertEqual([r.state for r in m.rows if r.branch == "main"], ["pre_change"])

    def test_branch_heads_that_cannot_be_listed_are_unreadable_rows_one_per_pattern(self):
        script = self.tmp / "nolist"
        script.write_text(f'#!/bin/sh\ncase "$*" in *ls-remote*) echo "network down" >&2; exit 128;; esac\nexec "{shutil.which("git")}" "$@"\n')
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        ws = self.workspace()
        change = resolve(ws, "t/app", compare="main...fix", git=self.git())  # resolved with the real git: the listing is what fails below
        git = self.git(git=str(script))
        m = branches.matrix(git, self.url, ws.repos["t/app"], change, head_ref="fix")
        self.assertEqual([(r.branch, r.state, r.commit) for r in m.rows], [("main", "unreadable", ""), ("release/*", "unreadable", "")])
        self.assertFalse(m.complete)
        self.assertTrue(any(r.kind == "read_unreadable" and r.blocks_selection for r in git.incompleteness))


class Notes(MatrixCase):
    def test_a_file_the_change_only_removes_lines_from_is_noted_where_they_are_gone_and_never_dropped_silently(self):
        self.make([
            {"files": {"a.py": "a1 = 1\n", "c.py": "c1 = 1\nc2 = 2\n"}, "message": "start"},
            {"branch": "chg", "from": "main", "files": {"a.py": "a1 = 2\n", "c.py": "c1 = 1\n"}, "message": "edit a, trim c"},
            {"branch": "release/1", "from": "chg", "files": {"NOTES": "a\n"}, "message": "has it"},
            {"branch": "release/0", "from": "main", "files": {"NOTES": "a\n"}, "message": "does not"},
        ], "n-repo")
        m, _, change = self.run_matrix(spec="main...chg", head_ref="chg")
        self.assertEqual([(f.path, f.status) for f in change.files], [("a.py", "modified"), ("c.py", "modified")])
        got = {r.branch: r.state for r in m.rows}
        self.assertEqual(got, {"main": "pre_change", "release/0": "pre_change", "release/1": "present"})  # the rows are over what could be compared: a.py
        self.assertTrue(any("c.py" in n and "release/1" in n and "not shown as present" in n for n in m.notes), m.notes)
        files = {(f.branch, f.path): f.state for f in m.files}
        self.assertEqual(files[("release/1", "a.py")], "present")
        self.assertEqual(files[("release/1", "c.py")], "not_compared")
        self.assertEqual(files[("release/0", "c.py")], "pre_change")  # the removed line is still there: that is a state

    def test_a_deleted_file_that_is_absent_at_a_tip_is_file_absent_and_one_that_is_there_is_pre_change(self):
        self.make([
            {"files": {"b.py": "b1 = 1\nb2 = 2\n", "k.py": "k = 1\n"}, "message": "start"},
            {"branch": "del", "from": "main", "delete": ["b.py"], "message": "delete b"},
            {"branch": "release/1", "from": "del", "files": {"NOTES": "a\n"}, "message": "deleted there too"},
        ], "d-repo")
        m, _, _ = self.run_matrix(spec="main...del", head_ref="del")
        self.assertEqual({r.branch: r.state for r in m.rows}, {"main": "pre_change", "release/1": "file_absent"})

    def test_a_file_name_that_is_not_text_is_noted_and_not_compared(self):
        path, shas = sg.make_raw_repo(self.tmp, "raw-repo", [[("100644", b"ok.py", b"x = 1\n")], [("100644", b"ok.py", b"x = 2\n"), ("100644", b"caf\xe9.py", b"y = 1\n")]])
        url = sg.remote_url(path)
        ws = config.loads(f'workspace = "t"\n[repos."t/app"]\nhost = "git"\nremote = "{url}"\nlanguage = "python"\nroles = ["backend"]\nbranches = ["main"]\n', fmt="toml", env={})
        git = Git(self.tmp / "cache3", env=sg.isolated_env(self.tmp / "home"))
        change = resolve(ws, "t/app", range=f"{shas[0]}..{shas[1]}", git=git)
        self.assertFalse(all(f.path_is_text for f in change.files))
        m = branches.matrix(git, url, ws.repos["t/app"], change, head_ref=None)
        self.assertTrue(any("not valid text" in n for n in m.notes), m.notes)
        self.assertEqual([r.state for r in m.rows], ["present"])  # ok.py is compared; the other name is said


class AgainstThePreChangeFile(MatrixCase):
    """An added line is found at a tip only above the copies the file had before the change."""

    BASE = "def f(x):\n    if x is None:\n        return None\n    return x + 1\n\n\ndef g(x):\n    return x * 2\n"
    FIXED = "def f(x):\n    if x is None:\n        return None\n    return x + 1\n\n\ndef g(x):\n    if x is None:\n        return None\n    return x * 2\n"

    def test_lines_that_also_occur_in_another_function_do_not_make_the_old_tip_present(self):
        """The null guard backported to a release branch: its lines are in f at main, so main was `present` and `branch-missing` never fired."""
        self.make([
            {"files": {"calc.py": self.BASE}, "message": "start"},
            {"branch": "release/1.x", "from": "main", "files": {"calc.py": self.FIXED}, "message": "fix on the release line only"},
            {"branch": "fix", "from": "main", "files": {"calc.py": self.FIXED}, "message": "the change"},
        ])
        m, _, _ = self.run_matrix()
        self.assertEqual({r.branch: r.state for r in m.rows}, {"main": "pre_change", "release/1.x": "present"})
        rel = [r for r in m.rows if r.branch == "release/1.x"][0]
        self.assertEqual([e.line for e in rel.evidence], [8, 9])  # the new copies in g, not the ones in f (lines 2 and 3)
        self.assertEqual(ss.evidence_problems({"t/app": self.path}, [e for r in m.rows for e in r.evidence]), [])
        from reachmap import findings
        self.assertEqual([f.title for f in findings.branch_missing(m, "t/app")], ["The change is on release/1.x and not on main"])

    def test_a_change_with_no_pre_change_version_compares_nothing_and_says_so(self):
        import dataclasses
        self.make([
            {"files": {"calc.py": self.BASE}, "message": "start"},
            {"branch": "fix", "from": "main", "files": {"calc.py": self.FIXED}, "message": "the change"},
        ])
        ws = self.workspace()
        git = self.git()
        change = dataclasses.replace(resolve(ws, "t/app", compare="main...fix", git=git), pre_change_sha=None)
        m = branches.matrix(git, self.url, ws.repos["t/app"], change, head_ref="fix")
        self.assertEqual(m.rows, ())
        self.assertTrue(any("pre-change" in n for n in m.notes), m.notes)
        self.assertEqual({f.state for f in m.files}, {"not_compared"})

    def test_a_file_whose_added_lines_are_all_also_removed_is_not_compared_and_says_why(self):
        self.make([
            {"files": {"a.py": "x = 1\ny = 2\n", "b.py": "k = 1\n"}, "message": "start"},
            {"branch": "fix", "from": "main", "files": {"a.py": "y = 2\nx = 1\n", "b.py": "k = 2\n"}, "message": "reorder a, change b"},
            {"branch": "release/1", "from": "main", "files": {"NOTES": "a\n"}, "message": "old"},
        ])
        m, _, _ = self.run_matrix(spec="main...fix", head_ref="fix")
        self.assertEqual({(f.branch, f.path): f.state for f in m.files}[("release/1", "a.py")], "not_compared")
        self.assertTrue(any("a.py" in n and "removed" in n for n in m.notes), m.notes)
        self.assertEqual({r.branch: r.state for r in m.rows}, {"main": "pre_change", "release/1": "pre_change"})  # b.py decides

    def test_a_file_the_change_adds_is_pre_change_where_it_is_absent_and_the_branch_can_still_be_missing_the_change(self):
        """A fix with its test: the new test file is absent on main, which made main `diverged` and hid `branch-missing`."""
        base = "def total(items):\n    amount = sum(items)\n    return round(amount, 2)\n"
        fixed = "def total(items):\n    amount = sum(items)\n    return amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)\n"
        test = "from calc import total\n\n\ndef test_total_rounds_half_up():\n    assert str(total([0.125])) == '0.13'\n"
        self.make([
            {"files": {"calc.py": base}, "message": "start"},
            {"branch": "release/1.x", "from": "main", "files": {"calc.py": fixed, "test_calc.py": test}, "message": "fix with its test, release only"},
            {"branch": "fix", "from": "main", "files": {"calc.py": fixed, "test_calc.py": test}, "message": "the change"},
        ])
        m, _, change = self.run_matrix()
        self.assertEqual([(f.path, f.status) for f in change.files], [("calc.py", "modified"), ("test_calc.py", "added")])
        self.assertEqual({r.branch: r.state for r in m.rows}, {"main": "pre_change", "release/1.x": "present"})
        files = {(f.branch, f.path): f.state for f in m.files}
        self.assertEqual(files[("main", "test_calc.py")], "pre_change")
        from reachmap import findings
        (f,) = findings.branch_missing(m, "t/app")
        self.assertEqual((f.confidence.value, "2 of 2" in f.confidence_basis), ("high", True))

    def test_a_branch_where_every_changed_file_is_a_new_one_that_is_absent_is_file_absent_with_nothing_to_cite(self):
        self.make([
            {"files": {"keep.py": "k = 1\n"}, "message": "start"},
            {"branch": "fix", "from": "main", "files": {"new.py": "n = 1\n"}, "message": "a new file"},
            {"branch": "release/1", "from": "fix", "files": {"NOTES": "a\n"}, "message": "has it"},
        ])
        m, _, _ = self.run_matrix(spec="main...fix", head_ref="fix")
        self.assertEqual({r.branch: r.state for r in m.rows}, {"main": "file_absent", "release/1": "present"})
        self.assertEqual({(f.branch, f.path): f.state for f in m.files}[("main", "new.py")], "pre_change")  # the file-level state
        from reachmap import findings
        self.assertEqual([f.title for f in findings.branch_missing(m, "t/app")], ["The change is on release/1 and not on main"])


class NoBranch(MatrixCase):
    def setUp(self):
        super().setUp()
        self.standard()

    def test_no_branch_matching_the_patterns_is_a_note_and_no_row(self):
        m, _, _ = self.run_matrix(branches_list=("fix", "nothing/*"), head_ref="fix")  # fix is the change's own head; nothing matches the rest
        self.assertEqual(m.rows, ())
        self.assertTrue(any("own head branch" in n for n in m.notes), m.notes)


if __name__ == "__main__":
    unittest.main()
