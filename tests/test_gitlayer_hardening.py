"""Git layer: failures that used to read as success, and a user's git configuration that used
to change the output (the review of M4). Each test names the failure it pins."""

import json
import os
import shutil
import subprocess
from pathlib import Path

from reachmap import gitlayer, redact
from reachmap.errors import ModelError, UnreadableError
from reachmap.gitlayer import Git, IncompleteSearch, ReadFailed
from reachmap.model import Read
from tests import support_git as sg
from tests.test_gitlayer import GitCase


class GrepStderr(GitCase):
    """A git grep that reports errors, whatever its exit code, is not a complete search."""

    def setup_repo(self):
        repo = sg.make_repo(self.tmp, "g", [{"files": {"a.txt": "needle one\n", "b.txt": "needle two\n", "c.txt": "none\n"}}])
        return repo, sg.rev(repo, "HEAD")

    def test_grep_that_exits_0_with_errors_on_stderr_is_unreadable(self):
        repo, sha = self.setup_repo()
        real = shutil.which("git")
        script = self.tmp / "noisy"
        script.write_text('#!/bin/sh\n"%s" "$@"\nrc=$?\nfor a in "$@"; do case "$a" in grep) echo "error: unable to read 0976447" >&2;; esac; done\nexit $rc\n' % real)
        script.chmod(0o755)
        git = self.make_git(git=str(script))
        co = git.checkout(sg.remote_url(repo), sha)
        res = git.grep(co, "needle", fixed=True)
        self.assertEqual(res.state, Read.UNREADABLE)
        self.assertIn("unable to read", res.detail)
        with self.assertRaises(UnreadableError):
            res.matches
        with self.assertRaises(UnreadableError):
            res.partial_matches
        self.assertEqual([r.kind for r in git.incompleteness], ["read_unreadable"])

    def test_grep_that_exits_128_is_unreadable(self):
        repo, sha = self.setup_repo()
        real = shutil.which("git")
        script = self.tmp / "dying"
        script.write_text('#!/bin/sh\nfor a in "$@"; do case "$a" in grep) echo "fatal: boom" >&2; exit 128;; esac; done\nexec "%s" "$@"\n' % real)
        script.chmod(0o755)
        git = self.make_git(git=str(script))
        co = git.checkout(sg.remote_url(repo), sha)
        res = git.grep(co, "needle", fixed=True)
        self.assertEqual(res.state, Read.UNREADABLE)
        self.assertIn("boom", res.detail)

    def test_a_blob_that_was_deleted_from_the_cache_makes_the_search_unreadable(self):
        repo, sha = self.setup_repo()
        git = self.make_git()
        url = sg.remote_url(repo)
        co = git.checkout(url, sha)
        bare = git.cache_path(url)
        # a plain repository of loose objects, then lose one blob
        for key in ("remote.origin.promisor", "remote.origin.partialclonefilter"):
            subprocess.run(["git", "--git-dir", str(bare), "config", "--unset", key], capture_output=True)
        for pack in list((bare / "objects" / "pack").glob("*.pack")):
            data = pack.read_bytes()
            for ext in (".pack", ".idx", ".rev", ".promisor"):
                pack.with_suffix(ext).unlink(missing_ok=True)
            done = subprocess.run(["git", "--git-dir", str(bare), "unpack-objects", "-q"], input=data, capture_output=True)
            self.assertEqual(done.returncode, 0, done.stderr)
        oid = sg.git_raw(bare, "rev-parse", f"{sha}:b.txt")
        (bare / "objects" / oid[:2] / oid[2:]).unlink()
        raw = subprocess.run(["git", "--git-dir", str(bare), "grep", "-n", "-I", "-F", "-e", "needle", sha, "--"], capture_output=True)
        self.assertEqual(raw.returncode, 0)  # git itself says "found" and complains on stderr
        self.assertTrue(raw.stderr)
        res = git.grep(co, "needle", fixed=True)
        self.assertEqual(res.state, Read.UNREADABLE)
        capped = git.grep(co, "needle", fixed=True, result_cap=5)
        self.assertEqual(capped.state, Read.UNREADABLE)
        self.assertTrue(git.incompleteness)
        self.assertTrue(all(r.kind == "read_unreadable" and r.blocks_selection for r in git.incompleteness))

    def test_exit_1_with_nothing_on_stderr_is_the_only_no_match(self):
        repo, sha = self.setup_repo()
        git = self.make_git()
        co = git.checkout(sg.remote_url(repo), sha)
        self.assertEqual(git.grep(co, "zzzz", fixed=True).state, Read.ABSENT)
        bad = git.grep(co, "(unclosed", fixed=False)  # an invalid expression is an error, not "no matches"
        self.assertEqual(bad.state, Read.UNREADABLE)


class AbbreviatedShas(GitCase):
    def test_an_abbreviation_on_a_fresh_cache_is_expanded_not_absent(self):
        repo = sg.make_repo(self.tmp, "a", [{"files": {"a": "1\n"}}, {"files": {"a": "2\n"}}])
        full = sg.rev(repo, "HEAD")
        git = self.make_git()
        res = git.pin_read(sg.remote_url(repo), full[:10])
        self.assertEqual((res.state, res.text if res.is_found else None), (Read.FOUND, full))
        self.assertEqual(git.incompleteness, [])
        old = sg.rev(repo, "HEAD~1")
        self.assertEqual(self.make_git(cache=self.tmp / "fresh").pin(sg.remote_url(repo), old[:8]), old)

    def test_an_abbreviation_that_cannot_be_expanded_is_unreadable_never_absent(self):
        repo = sg.make_repo(self.tmp, "a", [{"files": {"a": "1\n"}}])
        tree = sg.git_raw(repo, "rev-parse", "HEAD^{tree}")
        dangling = sg.git_raw(repo, "commit-tree", tree, "-m", "nobody points here")  # on no branch or tag
        git = self.make_git()
        res = git.pin_read(sg.remote_url(repo), dangling[:10])
        self.assertEqual(res.state, Read.UNREADABLE)
        self.assertIn("not expanded", res.detail)
        self.assertEqual([r.kind for r in git.incompleteness], ["read_unreadable"])

    def test_an_ambiguous_abbreviation_is_unreadable(self):
        repo = sg.make_repo(self.tmp, "a", [{"files": {"a": "1\n"}}])
        real = shutil.which("git")
        script = self.tmp / "ambiguous"
        script.write_text('#!/bin/sh\nfor a in "$@"; do case "$a" in abcdef1*) echo "error: short object ID abcdef1 is ambiguous" >&2; exit 128;; esac; done\nexec "%s" "$@"\n' % real)
        script.chmod(0o755)
        git = self.make_git(git=str(script))
        res = git.pin_read(sg.remote_url(repo), "abcdef1")
        self.assertEqual(res.state, Read.UNREADABLE)
        self.assertIn("ambiguous", res.detail)


class UserConfiguration(GitCase):
    """The user's global git config must not change what the layer reads."""

    # Groups of options a user might have in ~/.gitconfig; each group is tried as one file.
    HOSTILE = [
        "[diff]\n\tsubmodule = log\n\tignoreSubmodules = all\n\tnoprefix = true\n\tmnemonicPrefix = true\n\trelative = true\n"
        "\tinterHunkContext = 10\n\texternal = /bin/false\n\trenames = false\n\talgorithm = patience\n",
        "[grep]\n\tcolumn = true\n\tfullName = true\n\tpatternType = perl\n\tlineNumber = false\n"
        "[log]\n\tshowSignature = true\n",
        "[core]\n\tquotePath = false\n\tpager = /bin/false\n[color]\n\tui = always\n[diff]\n\tsubmodule = diff\n",
    ]

    def build(self):
        old, new = "x\n" * 10 + "keep\n" + "y\n" * 10, "x\n" * 10 + "keep\n" + "y\n" * 10
        repo, shas = sg.make_raw_repo(self.tmp, "u", [
            [("100644", b"a.txt", b"one\ntwo\n"), ("100644", b"src/f.txt", b"1\n2\n3\n4\n5\n6\n7\n8\n9\n"),
             ("100644", b"caf\xc3\xa9.txt", b"one\n"), ("160000", b"sub", "1" * 40), ("100644", b"g.txt", b"xx needle\n")],
            [("100644", b"a.txt", b"one\nTWO\n"), ("100644", b"src/f.txt", b"1\n2\nthree\n4\n5\n6\n7\nEIGHT\n9\n"),
             ("100644", b"caf\xc3\xa9.txt", b"two\n"), ("160000", b"sub", "2" * 40), ("100644", b"g.txt", b"xx needle\n")],
        ])
        return repo, shas

    def snapshot(self, git, repo, shas, url):
        cs = git.diff(url, shas[0], shas[1])
        files = [(f.path, f.status, [(h.old_start, [(l.number, l.text) for l in h.removed], [(l.number, l.text) for l in h.added]) for h in f.hunks]) for f in cs.files]
        co = git.checkout(url, shas[1])
        found = git.grep(co, "needle", fixed=True)
        return files, [(m.path, m.line, m.text) for m in found.matches], git.commit_subject(url, shas[1]).text

    def test_hostile_user_configuration_changes_nothing(self):
        repo, shas = self.build()
        url = sg.remote_url(repo)
        baseline = self.snapshot(self.make_git(cache=self.tmp / "c0"), repo, shas, url)
        self.assertIn("sub", [f[0] for f in baseline[0]])  # the submodule change is a changed file
        self.assertEqual(baseline[1], [("g.txt", 1, "xx needle")])
        self.assertEqual(len(baseline[0]), 4)
        for n, config in enumerate(self.HOSTILE):
            with self.subTest(config.strip()):
                cfg = self.tmp / f"gitconfig{n}"
                cfg.write_text(config)
                env = sg.isolated_env(self.tmp / "home")
                env["GIT_CONFIG_GLOBAL"] = str(cfg)
                got = self.snapshot(self.make_git(cache=self.tmp / f"c{n + 1}", env=env), repo, shas, url)
                self.assertEqual(got, baseline)


class NonUtf8Names(GitCase):
    def build(self):
        return sg.make_raw_repo(self.tmp, "latin", [
            [("100644", b"caf\xe9.txt", b"one\n"), ("100644", b"ok.txt", b"ok\n")],
            [("100644", b"caf\xe9.txt", b"two\n"), ("100644", b"ok.txt", b"ok\n")],
        ])

    def test_a_name_that_is_not_utf8_survives_the_diff_and_is_read_back(self):
        repo, shas = self.build()
        url = sg.remote_url(repo)
        git = self.make_git()
        cs = git.diff(url, shas[0], shas[1])
        (change,) = cs.files
        self.assertFalse(change.path_is_text)
        self.assertEqual(change.path.encode("utf-8", "surrogateescape"), b"caf\xe9.txt")
        res = git.file_at(url, change.path, shas[1])
        self.assertEqual((res.state, res.text if res.is_found else None), (Read.FOUND, "two\n"))  # never absent
        self.assertEqual(git.incompleteness, [])

    def test_a_path_that_cannot_be_turned_back_into_bytes_is_unreadable_and_recorded(self):
        repo, shas = self.build()
        git = self.make_git()
        res = git.file_at(sg.remote_url(repo), "bad\ud800name", shas[1])  # a lone high surrogate has no byte form
        self.assertEqual(res.state, Read.UNREADABLE)
        (rec,) = git.incompleteness
        rec.to_dict()  # the record itself can be written out
        json.dumps(rec.to_dict())

    def test_checkout_skips_the_name_and_says_so(self):
        repo, shas = self.build()
        git = self.make_git()
        co = git.checkout(sg.remote_url(repo), shas[1])
        self.assertEqual(co.files, ("ok.txt",))
        self.assertEqual(len(co.skipped), 1)
        self.assertIn("UTF-8", co.skipped[0][1])
        self.assertEqual(git.incompleteness, [])  # still readable with file_at, so not a lost read

    def test_grep_searches_such_a_file_too(self):
        repo, shas = sg.make_raw_repo(self.tmp, "latin2", [[("100644", b"caf\xe9.txt", b"needle\n"), ("100644", b"ok.txt", b"nothing\n")]])
        git = self.make_git()
        co = git.checkout(sg.remote_url(repo), shas[0])
        res = git.grep(co, "needle", fixed=True)
        self.assertEqual([m.path.encode("utf-8", "surrogateescape") for m in res.matches], [b"caf\xe9.txt"])
        capped = git.grep(co, "needle", fixed=True, examine_cap=2)  # a cap equal to the file count cuts nothing, and counts both files
        self.assertEqual((capped.examined, capped.total, capped.truncated_by), (2, 2, None))


class CheckoutCollisions(GitCase):
    def test_paths_that_collide_on_a_case_insensitive_filesystem_are_written_nowhere_and_recorded(self):
        repo, shas = sg.make_raw_repo(self.tmp, "case", [[
            ("100644", b"README", b"upper\n"), ("100644", b"readme", b"lower\n"), ("100644", b"ok.txt", b"ok\n"),
            ("100644", b"Docs", b"a file\n"), ("40000", b"docs", [("100644", b"x.md", b"x\n")]),
            ("100644", b"caf\xc3\xa9", b"composed\n"), ("100644", b"cafe\xcc\x81", b"decomposed\n"),
        ]])
        git = self.make_git()
        co = git.checkout(sg.remote_url(repo), shas[0])
        self.assertEqual(co.files, ("ok.txt",))
        skipped = dict(co.skipped)
        for name in ("README", "readme", "Docs", "docs/x.md", "café", "café"):
            self.assertIn("collid", skipped[name], name)
        self.assertEqual(sorted(os.listdir(co.path)), ["ok.txt"])
        (rec,) = git.incompleteness
        self.assertEqual((rec.kind, rec.blocks_selection), ("read_unreadable", True))
        self.assertIn("README", rec.detail)
        # git still has both, and file_at reads each
        self.assertEqual(git.file_at(co, "README").text, "upper\n")
        self.assertEqual(git.file_at(co, "readme").text, "lower\n")

    def test_a_file_is_never_written_over_another(self):
        repo = sg.make_repo(self.tmp, "w", [{"files": {"a.txt": "x\n"}}])
        git = self.make_git()
        sha = sg.rev(repo, "HEAD")
        bare = git._ensure_commit(sg.remote_url(repo), sha)
        dest = self.tmp / "dest"
        dest.mkdir()
        oid = sg.git_raw(bare, "rev-parse", f"{sha}:a.txt")
        with self.assertRaises(FileExistsError):
            git._write_blobs(bare, dest, [("a.txt", oid, False), ("a.txt", oid, False)])

    def test_crafted_tree_with_dotdot_dotgit_and_odd_entries_writes_nothing_outside(self):
        repo, shas = sg.make_raw_repo(self.tmp, "evil", [[
            ("100644", b"..", b"escape\n"), ("100644", b"ok.txt", b"ok\n"),
            ("40000", b".git", [("100644", b"config", b"[core]\n\thooksPath = /x\n")]),
            ("40000", b"a", [("40000", b".GIT", [("100644", b"hooks", b"x\n")])]),
            ("160000", b"sub", "1" * 40), ("120000", b"link", b"ok.txt"),
        ]])
        git = self.make_git()
        co = git.checkout(sg.remote_url(repo), shas[0])
        self.assertEqual(co.files, ("ok.txt",))
        reasons = dict(co.skipped)
        self.assertEqual(reasons[".."], "unsafe path")
        self.assertEqual(reasons[".git/config"], "unsafe path")
        self.assertEqual(reasons["a/.GIT/hooks"], "unsafe path")
        self.assertEqual(reasons["sub"], "submodule")
        self.assertEqual(reasons["link"], "symlink")
        self.assertEqual(sorted(os.listdir(co.path)), ["ok.txt"])
        self.assertFalse((co.path.parent / "..").is_symlink())
        self.assertFalse((co.path.parent / sg.rev(repo, "HEAD") / ".git").exists())
        self.assertFalse(any(p.name == ".git" for p in co.path.rglob("*")))


class ShallowHistories(GitCase):
    def shallow(self):
        repo = sg.make_repo(self.tmp, "deep", [
            {"files": {"a": "1\n"}}, {"files": {"a": "2\n"}}, {"files": {"a": "3\n"}}, {"files": {"a": "4\n"}},
        ])
        url = sg.remote_url(repo)
        git = self.make_git()
        seeded = git.cache_path(url)
        seeded.parent.mkdir(parents=True)
        env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_ALLOW_PROTOCOL": "file"}
        done = subprocess.run(["git", "clone", "-q", "--bare", "--depth=2", url, str(seeded)], env=env, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        return repo, url, git

    def test_first_parent_of_a_shallow_boundary_is_unreadable_not_absent(self):
        repo, url, git = self.shallow()
        boundary = sg.rev(repo, "HEAD~1")  # the oldest commit the shallow clone holds
        res = git.first_parent(url, boundary)
        self.assertEqual(res.state, Read.UNREADABLE)
        self.assertIn("shallow", res.detail)
        self.assertEqual(git.first_parent(url, sg.rev(repo, "HEAD")).text, boundary)  # a parent that is visible is fine

    def test_as_of_before_the_visible_history_is_unreadable_not_absent(self):
        repo, url, git = self.shallow()
        res = git.pin_read(url, "main", as_of="2000-01-01T00:00:00+00:00")
        self.assertEqual(res.state, Read.UNREADABLE)
        self.assertIn("shallow", res.detail)


class Arguments(GitCase):
    def test_caps_must_be_positive(self):
        git = self.make_git()
        co = git.checkout(self.web_url, sg_rev(self.web))
        for bad in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                git.grep(co, "import", fixed=True, examine_cap=bad)
            with self.assertRaises(ValueError):
                git.grep(co, "import", fixed=True, result_cap=bad)

    def test_a_result_cap_makes_the_number_examined_unknown(self):
        git = self.make_git()
        co = git.checkout(self.web_url, sg_rev(self.web))
        res = git.grep(co, "import", fixed=True, result_cap=2)
        self.assertIsNone(res.examined)
        self.assertGreater(res.total, 10)

    def test_paths_that_select_no_file_are_a_caller_bug(self):
        git = self.make_git()
        co = git.checkout(self.web_url, sg_rev(self.web))
        with self.assertRaises(ValueError):
            git.grep(co, "import", fixed=True, paths=["no/such/dir"])
        found = git.grep(co, "import", fixed=True, paths=["src/lib"])
        self.assertEqual(found.state, Read.FOUND)
        self.assertTrue(all(m.path.startswith("src/lib/") for m in found.matches))

    def test_symlinks_and_submodules_are_not_searched_by_git_and_are_listed(self):
        repo, shas = sg.make_raw_repo(self.tmp, "sl", [[
            ("100644", b"real.txt", b"nothing\n"), ("120000", b"link", b"needle-target.txt"), ("160000", b"sub", "1" * 40),
        ]])
        git = self.make_git()
        co = git.checkout(sg.remote_url(repo), shas[0])
        whole = git.grep(co, "needle", fixed=True)
        self.assertEqual(whole.state, Read.ABSENT)
        self.assertEqual((whole.total, whole.examined), (1, 1))  # the regular file only
        self.assertEqual(dict(whole.not_searched), {"link": "symlink", "sub": "submodule"})
        with self.assertRaises(ValueError) as caught:
            git.grep(co, "needle", fixed=True, paths=["link"])  # nothing searchable selected: not "absent"
        self.assertIn("link", str(caught.exception))

    def test_a_remote_that_looks_like_an_option_is_refused(self):
        marker = self.tmp / "pwned"
        git = self.make_git()
        for remote in (f"--upload-pack=touch {marker}", "-u"):
            with self.assertRaises(ValueError):
                git.heads(remote, ["*"])
            with self.assertRaises(ValueError):
                git.pin_read(remote, "main")
        self.assertFalse(marker.exists())


class RefsAndEnvironment(GitCase):
    def test_reachmap_refs_are_bounded(self):
        repo = sg.make_repo(self.tmp, "r", [{"files": {"a": str(i)}} for i in range(6)])
        url = sg.remote_url(repo)
        git = self.make_git(max_refs=3)
        bare = git._bare(url)
        shas = sg.git_raw(repo, "rev-list", "HEAD").split()
        self.assertEqual(len(shas), 6)
        git._ensure_commit(url, shas[0])  # fetches the tip: one ref under refs/reachmap
        for sha in shas[1:]:
            subprocess.run(["git", "--git-dir", str(bare), "update-ref", f"refs/reachmap/{sha}", sha], check=True, capture_output=True)
        git._prune_refs(bare)
        left = sg.git_raw(bare, "for-each-ref", "--format=%(refname)", "refs/reachmap").split()
        self.assertLessEqual(len(left), 3)
        self.assertIn(f"refs/reachmap/{shas[0]}", left)  # the newest are kept

    def test_host_token_variables_do_not_reach_git(self):
        from unittest import mock
        log = self.tmp / "env.log"
        real = shutil.which("git")
        script = self.tmp / "envgit"
        script.write_text('#!/bin/sh\nenv >> "%s"\nexec "%s" "$@"\n' % (log, real))
        script.chmod(0o755)
        values = {"GITLAB_TOKEN": "tok_GL_0123456789abcdef", "BITBUCKET_TOKEN": "tok_BB_0123456789abcdef",
                  "MY_CONFIG_TOKEN": "tok_MY_0123456789abcdef", "GH_TOKEN": "tok_GH_0123456789abcdef"}
        with mock.patch.dict(os.environ, values):
            git = self.make_git(git=str(script), drop_env=["MY_CONFIG_TOKEN"])
            git.heads(self.web_url, ["*"])
        seen = log.read_text()
        self.assertNotIn(values["GITLAB_TOKEN"], seen)
        self.assertNotIn(values["BITBUCKET_TOKEN"], seen)
        self.assertNotIn(values["MY_CONFIG_TOKEN"], seen)
        self.assertIn(values["GH_TOKEN"], seen)  # gh's credential helper needs it


class RenameLimit(GitCase):
    def test_inexact_renames_given_up_are_flagged(self):
        body = lambda n: "".join(f"line {i} of file {n}\n" for i in range(40))
        before = {f"old{n}.txt": body(n) for n in range(4)}
        after = {f"new{n}.txt": body(n).replace("line 3 ", "LINE 3 ") for n in range(4)}
        repo = sg.make_repo(self.tmp, "rn", [{"files": before}, {"files": after, "delete": list(before)}])
        url = sg.remote_url(repo)
        base, head = sg.rev(repo, "HEAD~1"), sg.rev(repo, "HEAD")
        normal = self.make_git().diff(url, base, head)
        self.assertEqual(sorted(f.status for f in normal.files), ["renamed"] * 4)
        self.assertFalse(normal.rename_detection_skipped)
        limited = self.make_git(cache=self.tmp / "c2", rename_limit=1).diff(url, base, head)
        self.assertTrue(limited.rename_detection_skipped)
        self.assertEqual(sorted(f.status for f in limited.files), ["added"] * 4 + ["deleted"] * 4)


class ReadsThatFailHalfway(GitCase):
    """Failures between two steps of a read: each used to be a candidate for reading as absent or found."""

    def wrapper(self, body):
        script = self.tmp / "wrapped-git"
        script.write_text('#!/bin/sh\n%s\nexec "%s" "$@"\n' % (body, shutil.which("git")))
        script.chmod(0o755)
        return str(script)

    def repo(self):
        repo = sg.make_repo(self.tmp, "h", [{"files": {"a.txt": "hello world\n"}}])
        return sg.remote_url(repo), sg.rev(repo, "HEAD")

    def test_file_at_with_ls_tree_ok_but_cat_file_failing_is_unreadable(self):
        url, sha = self.repo()
        body = 'case " $* " in *" cat-file blob "*) echo "fatal: cannot read blob" >&2; exit 128;; esac'
        git = self.make_git(git=self.wrapper(body))
        res = git.file_at(url, "a.txt", sha)
        self.assertEqual(res.state, Read.UNREADABLE)
        self.assertIn("cannot read blob", res.detail)
        self.assertEqual([r.kind for r in git.incompleteness], ["read_unreadable"])
        self.assertEqual(git.file_at(url, "missing.txt", sha).state, Read.ABSENT)  # the same wrapper: ls-tree still answers

    def test_file_at_checks_the_byte_count_it_read(self):
        url, sha = self.repo()
        body = 'case " $* " in *" cat-file blob "*) "%s" "$@" | head -c 3; exit 0;; esac' % shutil.which("git")
        git = self.make_git(git=self.wrapper(body))
        res = git.file_at(url, "a.txt", sha)
        self.assertEqual(res.state, Read.UNREADABLE)
        self.assertIn("3 bytes", res.detail)

    def test_pin_never_returns_the_ref_when_the_commit_check_fails(self):
        url, sha = self.repo()
        git = self.make_git(git=self.wrapper('case " $* " in *" rev-parse "*) echo "fatal: no" >&2; exit 128;; esac'))
        res = git.pin_read(url, "main")
        self.assertEqual(res.state, Read.UNREADABLE)
        self.assertEqual([r.kind for r in git.incompleteness], ["read_unreadable"])
        with self.assertRaises(ReadFailed):
            git.pin(url, "main")


class AwkwardNames(GitCase):
    def test_diff_of_awkward_names_matches_git_itself(self):
        names = ["sp ace.txt", 'q"uote.txt', "\u00fcni.txt", "tab\there.txt", " lead.txt", "back\\slash.txt", "nl\nname.txt", "a b/c d.txt", "b/x", "a/b x"]
        before = {n: "one\ntwo\n" for n in names}
        after = {n: "one\nTWO\n" for n in names}
        before["ren src.txt"] = "".join(f"l{i}\n" for i in range(30))
        repo = sg.make_repo(self.tmp, "aw", [{"files": before}, {"files": {**after, "ren dst \u00e9.txt": before["ren src.txt"] + "tail\n"}, "delete": ["ren src.txt"]}])
        base, head = sg.rev(repo, "HEAD~1"), sg.rev(repo, "HEAD")
        cs = self.make_git().diff(sg.remote_url(repo), base, head)
        oracle = sg.git_raw(repo, "-c", "core.quotePath=false", "diff", "--name-status", "-z", "-M", base, head)
        fields = [f for f in oracle.split("\0") if f]
        expected = {}
        i = 0
        while i < len(fields):
            status = fields[i]
            if status.startswith("R"):
                expected[fields[i + 2]] = ("renamed", fields[i + 1])
                i += 3
            else:
                expected[fields[i + 1]] = ({"M": "modified", "A": "added", "D": "deleted"}[status], None)
                i += 2
        got = {f.path: (f.status, f.old_path) for f in cs.files}
        self.assertEqual(got, expected)
        for change in cs.files:
            if change.status == "modified":
                self.assertEqual([(l.number, l.text) for l in change.added_lines], [(2, "TWO")], change.path)
                self.assertEqual([(l.number, l.text) for l in change.removed_lines], [(2, "two")], change.path)


class ProbeCases(GitCase):
    """The reviewer's probes that were not yet tests."""

    def test_grep_with_a_blob_missing_and_the_remote_gone_is_unreadable(self):
        remote = sg.make_repo(self.tmp, "src", [{"files": {"a.txt": "needle one\n", "b.txt": "needle two\n", "c.txt": "nothing\n"}}])
        url, sha = sg.remote_url(remote), sg.rev(remote, "HEAD")
        git = self.make_git()
        git.pin(url, "main")  # commits and trees, no blobs
        self.assertTrue(git.file_at(url, "a.txt", sha).is_found)  # one blob is local
        shutil.rmtree(remote)
        co = gitlayer.Checkout(url, sha, self.tmp / "nowhere", ("a.txt", "b.txt", "c.txt"))
        res = git.grep(co, "needle", fixed=True)
        self.assertEqual(res.state, Read.UNREADABLE)
        self.assertEqual([r.kind for r in git.incompleteness], ["read_unreadable"])

    def test_a_tree_object_missing_from_the_cache_makes_file_at_unreadable_not_absent(self):
        remote = sg.make_repo(self.tmp, "src", [{"files": {"top.txt": "t\n", "d/sub/f.txt": "x\n", "d/g.txt": "g\n"}}])
        url, sha = sg.remote_url(remote), sg.rev(remote, "HEAD")
        git = self.make_git()
        git.pin(url, "main")
        bare = git.cache_path(url)
        subprocess.run(["git", "--git-dir", str(bare), "config", "--unset", "remote.origin.promisor"], capture_output=True)
        for pack in list((bare / "objects" / "pack").glob("*.pack")):
            data = pack.read_bytes()
            for ext in (".pack", ".idx", ".rev", ".promisor"):
                pack.with_suffix(ext).unlink(missing_ok=True)
            subprocess.run(["git", "--git-dir", str(bare), "unpack-objects", "-q"], input=data, capture_output=True, check=True)
        oid = sg.git_raw(bare, "rev-parse", f"{sha}:d/sub")
        shutil.rmtree(remote)
        (bare / "objects" / oid[:2] / oid[2:]).unlink()
        for path in ("d/sub/f.txt", "d/sub/nope.txt"):  # both are behind the missing tree: neither can be called absent
            self.assertEqual(git.file_at(url, path, sha).state, Read.UNREADABLE, path)
        self.assertTrue(git.file_at(url, "d/g.txt", sha).state in (Read.UNREADABLE, Read.FOUND))  # d itself is still readable

    def test_a_small_big_file_threshold_does_not_hide_a_big_file_from_grep(self):
        repo = sg.make_repo(self.tmp, "big", [{"files": {"small.txt": "needle\n", "big.txt": "needle " + "x" * 5000 + "\n"}}])
        cfg = self.tmp / "gitconfig"
        cfg.write_text("[core]\n\tbigFileThreshold = 1k\n")
        env = sg.isolated_env(self.tmp / "home")
        env["GIT_CONFIG_GLOBAL"] = str(cfg)
        git = self.make_git(env=env)
        co = git.checkout(sg.remote_url(repo), sg.rev(repo, "HEAD"))
        res = git.grep(co, "needle", fixed=True)
        self.assertEqual(sorted(m.path for m in res.matches), ["big.txt", "small.txt"])


def sg_rev(repo):
    return sg.rev(repo, "main")


if __name__ == "__main__":
    import unittest
    unittest.main()
