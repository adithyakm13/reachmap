"""The git layer (M4): every read answers found / absent / unreadable, every cut and failure
is recorded, everything is pinned to a commit. Shape A traps of context section 5 are
named trap01 to trap11 after the numbered list in docs/briefs/M4-git-hosts.md.

Remotes are the M1 fixture repositories as file:// URLs (and small repositories built here);
nothing touches a network (GIT_ALLOW_PROTOCOL=file for every git call)."""

import hashlib
import json
import logging
import os
import shutil
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from reachmap import gitlayer, redact
from reachmap.errors import ModelError, UnreadableError
from reachmap.gitlayer import Git, IncompleteSearch, ReadFailed, RefNotFound
from reachmap.model import Analysis, Read
from tests import support_git as sg
from tests.fixtures import build

SECRET = "tok_LIVE_0123456789abcdefXYZ"


SHORT = 0.3  # seconds: the timeout of a git that is made to hang, long enough for a working git call even on a busy machine


class GitCase(sg.TempDirCase):
    def setUp(self):
        super().setUp()
        self.repos = sg.fixture_repos()
        self.web = self.repos["acme/web"]
        self.api = self.repos["acme/api"]
        self.web_url = sg.remote_url(self.web)
        self.api_url = sg.remote_url(self.api)

    def make_git(self, **kw):
        kw.setdefault("env", sg.isolated_env(self.tmp / "home"))
        kw.setdefault("cache", self.tmp / "cache")
        cache = kw.pop("cache")
        return Git(cache, **kw)

    def wrapper(self, slow_on=(), log=None):
        """A `git` that logs its arguments and sleeps first for the named subcommands."""
        script = self.tmp / "slowgit"
        real = shutil.which("git")
        lines = ["#!/bin/sh"]
        if log:
            lines.append(f'echo "$@" >> "{log}"')
        for name in slow_on:
            lines.append(f'for a in "$@"; do case "$a" in {name}) sleep 30;; esac; done')
        lines.append(f'exec "{real}" "$@"')
        script.write_text("\n".join(lines) + "\n")
        script.chmod(0o755)
        return str(script)

    def kinds(self, git):
        return [(r.kind, r.blocks_selection) for r in git.incompleteness]


# ------------------------------------------------------------------------ Shape A traps


class Trap01GrepTimeout(GitCase):
    def test_trap01_grep_killed_by_timeout_is_unreadable_not_zero_matches(self):
        sha = sg.rev(self.web, "main")
        co = self.make_git().checkout(self.web_url, sha)  # made with a working git: the slow one below is asked only for the search
        git = self.make_git(git=self.wrapper(slow_on=["grep"]), timeout=SHORT)
        res = git.grep(co, "StatusBadge", fixed=True)
        self.assertEqual(res.state, Read.UNREADABLE)
        self.assertIn(f"timed out after {SHORT}s", res.detail)
        with self.assertRaises(UnreadableError):
            res.matches
        self.assertFalse(res.complete)
        self.assertEqual(self.kinds(git), [("read_unreadable", True)])
        self.assertIn("StatusBadge", git.incompleteness[0].subject)
        # the same search with a working git finds matches, so zero was never the answer
        good = self.make_git()
        found = good.grep(good.checkout(self.web_url, sha), "StatusBadge", fixed=True)
        self.assertEqual(found.state, Read.FOUND)
        self.assertGreater(len(found.matches), 3)
        self.assertEqual(good.incompleteness, [])


class Trap02ExamineCap(GitCase):
    def setUp(self):
        super().setUp()
        self.sha = sg.rev(self.web, "main")
        self.git = self.make_git()
        self.co = self.git.checkout(self.web_url, self.sha)

    def test_trap02_examine_cap_is_recorded_with_counts_and_the_partial_list_is_not_complete(self):
        total = len(self.co.files)
        self.assertGreater(total, 60)
        full = self.git.grep(self.co, "import", fixed=True)
        self.assertIsNone(full.truncated_by)
        self.assertTrue(full.complete)
        self.assertEqual((full.examined, full.total), (total, total))
        self.assertEqual(self.git.incompleteness, [])

        part = self.git.grep(self.co, "import", fixed=True, examine_cap=10)
        self.assertEqual(part.truncated_by, "examine_cap")
        self.assertEqual((part.examined, part.total), (10, total))
        self.assertTrue(part.at_least)
        self.assertFalse(part.complete)
        self.assertLess(len(part.partial_matches), len(full.matches))
        self.assertTrue({(m.path, m.line) for m in part.partial_matches} <= {(m.path, m.line) for m in full.matches})
        with self.assertRaises(IncompleteSearch):
            part.matches  # the complete list does not exist for a search that was cut
        self.assertEqual(len(full.matches), len(full.partial_matches))
        (rec,) = self.git.incompleteness
        self.assertEqual((rec.kind, rec.blocks_selection), ("search_truncated", True))
        self.assertIn(f"examined 10 of {total} files", rec.detail)

    def test_examine_cap_with_no_match_in_the_examined_files_is_not_an_absence(self):
        res = self.git.grep(self.co, "Widget55", fixed=True, examine_cap=3)
        self.assertEqual(res.state, Read.UNREADABLE)
        self.assertEqual(res.truncated_by, "examine_cap")
        with self.assertRaises(UnreadableError):
            res.matches
        self.assertEqual(self.kinds(self.git), [("search_truncated", True)])

    def test_a_cap_at_or_above_the_file_count_cuts_nothing(self):
        total = len(self.co.files)
        res = self.git.grep(self.co, "import", fixed=True, examine_cap=total)
        self.assertIsNone(res.truncated_by)
        self.assertEqual(self.git.incompleteness, [])

    def test_no_match_over_everything_is_absent(self):
        res = self.git.grep(self.co, "zzz-nothing-has-this-zzz", fixed=True)
        self.assertEqual(res.state, Read.ABSENT)
        self.assertEqual(res.matches, ())
        self.assertTrue(res.complete)
        self.assertEqual(self.git.incompleteness, [])


class Trap03ResultCap(GitCase):
    def test_trap03_result_cap_records_nothing_and_marks_at_least(self):
        sha = sg.rev(self.web, "main")
        git = self.make_git()
        co = git.checkout(self.web_url, sha)
        full = git.grep(co, "import", fixed=True)
        self.assertGreater(len(full.matches), 5)
        res = git.grep(co, "import", fixed=True, result_cap=2)
        self.assertEqual(res.state, Read.FOUND)
        self.assertEqual(len(res.partial_matches), 2)
        self.assertEqual(res.partial_matches, full.matches[:2])
        self.assertEqual(res.truncated_by, "result_cap")
        self.assertTrue(res.at_least)
        self.assertFalse(res.complete)
        with self.assertRaises(IncompleteSearch):
            res.matches
        self.assertEqual(git.incompleteness, [])  # a result cap claims nothing absent: nothing recorded


class Trap04BlobMissing(GitCase):
    def test_trap04_missing_blob_with_the_remote_gone_is_unreadable_not_absent(self):
        remote = self.tmp / "gone-remote"
        shutil.copytree(self.web, remote)
        url = sg.remote_url(remote)
        git = self.make_git()
        sha = git.pin(url, "main")  # fetches the commit and trees, not the blobs
        shutil.rmtree(remote)
        res = git.file_at(url, "src/lib/format.ts", sha)
        self.assertEqual(res.state, Read.UNREADABLE)
        self.assertTrue(res.detail)
        with self.assertRaises(UnreadableError):
            res.text_or_none()
        (rec,) = git.incompleteness
        self.assertEqual((rec.kind, rec.blocks_selection), ("read_unreadable", True))
        self.assertIn("src/lib/format.ts", rec.subject)
        # the tree itself was read, so a path that is not in it is positively absent
        gone = git.file_at(url, "src/lib/does-not-exist.ts", sha)
        self.assertEqual(gone.state, Read.ABSENT)
        self.assertEqual(len(git.incompleteness), 1)

    def test_a_commit_the_remote_does_not_have_is_unreadable_never_absent(self):
        git = self.make_git()
        res = git.file_at(self.web_url, "README.md", "0" * 39 + "1")
        self.assertEqual(res.state, Read.UNREADABLE)
        self.assertEqual(self.kinds(git), [("read_unreadable", True)])


class Trap05FileAt(GitCase):
    def test_trap05_absent_path_is_absent_and_large_files_are_read_whole(self):
        git = self.make_git()
        sha = sg.rev(self.web, "main")
        absent = git.file_at(self.web_url, "src/nothing/here.ts", sha)
        self.assertEqual(absent.state, Read.ABSENT)
        self.assertIsNone(absent.text_or_none())
        self.assertEqual(git.incompleteness, [])
        found = git.file_at(self.web_url, "src/lib/format.ts", sha)
        self.assertTrue(found.is_found)
        self.assertEqual(found.text, build.file_at(self.web, sha, "src/lib/format.ts")[1])

        big_text = "".join(f"line {i:07d} of the two megabyte file\n" for i in range(55_000))
        self.assertGreater(len(big_text.encode()), 2_000_000)
        repo = sg.make_repo(self.tmp, "big", [
            {"files": {"big.txt": big_text, "small.txt": "hi\n"}},
            {"files": {"huge.txt": "x" * (6 * 1024 * 1024)}},
        ])
        url = sg.remote_url(repo)
        first, second = sg.rev(repo, "HEAD~1"), sg.rev(repo, "HEAD")
        res = git.file_at(url, "big.txt", first)
        self.assertTrue(res.is_found)
        self.assertEqual(res.text, big_text)  # a 2 MB file, the whole of it (the contents API would give nothing)
        self.assertEqual(git.incompleteness, [])

        over = git.file_at(url, "huge.txt", second)
        self.assertEqual(over.state, Read.UNREADABLE)
        self.assertIn("max_bytes", over.detail)
        self.assertEqual(self.kinds(git), [("read_unreadable", True)])
        raised = self.make_git(max_bytes=10 * 1024 * 1024)
        self.assertTrue(raised.file_at(url, "huge.txt", second).is_found)

    def test_files_that_cannot_be_read_as_text_are_unreadable(self):
        repo = sg.make_repo(self.tmp, "odd", [
            {"files": {"bin.dat": b"\xff\xfe\x00\x01 not utf-8", "d/f.txt": "x\n"}},
        ])
        git = self.make_git()
        url, sha = sg.remote_url(repo), sg.rev(repo, "HEAD")
        self.assertEqual(git.file_at(url, "bin.dat", sha).state, Read.UNREADABLE)
        directory = git.file_at(url, "d", sha)
        self.assertEqual(directory.state, Read.UNREADABLE)
        self.assertIn("directory", directory.detail)
        self.assertEqual(git.file_at(url, "d/f.txt/inside", sha).state, Read.ABSENT)
        self.assertEqual(git.file_at(url, "../etc/passwd", sha).state, Read.UNREADABLE)
        self.assertEqual(git.file_at(url, "/etc/passwd", sha).state, Read.UNREADABLE)
        self.assertEqual(len(git.incompleteness), 4)

    def test_file_at_accepts_a_checkout(self):
        git = self.make_git()
        sha = sg.rev(self.web, "main")
        co = git.checkout(self.web_url, sha)
        self.assertTrue(git.file_at(co, "src/lib/format.ts").is_found)
        self.assertTrue(git.file_at(co, "src/lib/format.ts", sha).is_found)
        with self.assertRaises(ValueError):
            git.file_at(co, "src/lib/format.ts", sg.rev(self.web, "release/2.0"))


class FilesAt(GitCase):
    """`files_at` answers each path as `file_at` does, in a few git calls for all of them."""

    def repo(self):
        big = "x" * 2000  # over the max_bytes of 1000 the tests ask with
        repo, shas = sg.make_raw_repo(self.tmp, "mixed", [[
            ("100644", b"a.txt", b"alpha\n"), ("100644", b"b.txt", b"beta\n"), ("100644", b"bin.dat", b"\xff\xfe\x00 not utf-8"),
            ("100644", b"huge.txt", big.encode()), ("100644", b"caf\xe9.txt", b"latin\n"),
            ("40000", b"d", [("100644", b"inner.txt", b"inner\n")]), ("160000", b"sub", "1" * 40),
        ]])
        return repo, shas[0]

    PATHS = ["a.txt", "b.txt", "missing.txt", "d", "sub", "bin.dat", "huge.txt", "d/inner.txt", "d/inner.txt/below", "../etc/passwd", "/etc/passwd", "caf\udce9.txt"]

    def test_every_path_gets_the_answer_file_at_gives(self):
        repo, sha = self.repo()
        url = sg.remote_url(repo)
        many, one = self.make_git(cache=self.tmp / "c1"), self.make_git(cache=self.tmp / "c2")
        got = many.files_at(url, self.PATHS, sha, max_bytes=1000)
        self.assertEqual(list(got), self.PATHS)  # in the order asked
        for path in self.PATHS:
            expect = one.file_at(url, path, sha, max_bytes=1000)
            with self.subTest(path=path):
                self.assertEqual((got[path].state, got[path]._text), (expect.state, expect._text))
        states = {p: r.state for p, r in got.items()}
        self.assertEqual([p for p, st in states.items() if st == "found"], ["a.txt", "b.txt", "d/inner.txt", "caf\udce9.txt"])
        self.assertEqual([p for p, st in states.items() if st == "absent"], ["missing.txt", "d/inner.txt/below"])
        self.assertEqual(sorted(p for p, st in states.items() if st == "unreadable"), sorted(["d", "sub", "bin.dat", "huge.txt", "../etc/passwd", "/etc/passwd"]))
        self.assertEqual(sorted((r.kind, r.blocks_selection) for r in many.incompleteness), sorted((r.kind, r.blocks_selection) for r in one.incompleteness))
        self.assertIn("directory", got["d"].detail)
        self.assertIn("submodule", got["sub"].detail)
        self.assertIn("max_bytes", got["huge.txt"].detail)
        self.assertIn("UTF-8", got["bin.dat"].detail)

    def test_the_cost_is_a_few_git_calls_whatever_the_number_of_paths(self):
        log = self.tmp / "calls.log"
        git = self.make_git(git=self.wrapper(log=log))
        sha = sg.rev(self.web, "main")
        paths = [p for p in build.run_git(self.web, "ls-tree", "-r", "--name-only", sha).stdout.splitlines() if p.endswith((".ts", ".tsx"))][:40]
        self.assertGreaterEqual(len(paths), 30)
        git.checkout  # (the cache is filled by the first call below)
        git.files_at(self.web_url, ["src/lib/format.ts"], sha)  # fetches the commit
        log.write_text("")
        got = git.files_at(self.web_url, paths, sha)
        self.assertTrue(all(r.is_found for r in got.values()))
        calls = [line.split() for line in log.read_text().splitlines()]
        listing = [c for c in calls if "ls-tree" in c]
        batch = [c for c in calls if "cat-file" in c and "--batch" in c]
        self.assertEqual((len(listing), len(batch)), (1, 1), calls)
        self.assertLessEqual(len(calls), 5)

    def test_a_long_list_is_listed_in_chunks(self):
        repo, shas = sg.make_raw_repo(self.tmp, "wide", [[("100644", f"f{i}.txt".encode(), f"{i}\n".encode()) for i in range(7)]])
        log = self.tmp / "wide.log"
        git = self.make_git(git=self.wrapper(log=log))
        url = sg.remote_url(repo)
        git.files_at(url, ["f0.txt"], shas[0])
        log.write_text("")
        with mock.patch("reachmap.gitlayer.FILES_AT_CHUNK", 3):
            got = git.files_at(url, [f"f{i}.txt" for i in range(7)] + ["nope.txt"], shas[0])
        self.assertEqual(sum(r.is_found for r in got.values()), 7)
        self.assertEqual(got["nope.txt"].state, Read.ABSENT)
        self.assertEqual(got["f6.txt"].text, "6\n")
        self.assertEqual(sum("ls-tree" in line.split() for line in log.read_text().splitlines()), 3)  # 3 + 3 + 2 of the 8 paths

    def test_a_commit_that_cannot_be_had_makes_every_path_unreadable_and_recorded(self):
        git = self.make_git()
        got = git.files_at(self.web_url, ["a.txt", "b.txt"], "f" * 40)
        self.assertEqual([r.state for r in got.values()], ["unreadable", "unreadable"])
        self.assertEqual(self.kinds(git), [("read_unreadable", True)] * 2)

    def test_a_cat_file_that_fails_leaves_the_files_unreadable_and_the_absent_ones_absent(self):
        repo, sha = self.repo()
        script = self.tmp / "nocat"
        script.write_text(f'#!/bin/sh\ncase "$*" in *"cat-file --batch"*) echo "boom" >&2; exit 1;; esac\nexec "{shutil.which("git")}" "$@"\n')
        script.chmod(0o755)
        git = self.make_git(git=str(script))
        got = git.files_at(sg.remote_url(repo), ["a.txt", "missing.txt"], sha)
        self.assertEqual((got["a.txt"].state, got["missing.txt"].state), (Read.UNREADABLE, Read.ABSENT))  # an unreadable file is never an absence
        self.assertEqual(self.kinds(git), [("read_unreadable", True)])

    def test_duplicates_are_answered_once_and_a_checkout_is_a_source(self):
        git = self.make_git()
        sha = sg.rev(self.web, "main")
        got = git.files_at(self.web_url, ["src/lib/format.ts", "src/lib/format.ts"], sha)
        self.assertEqual(list(got), ["src/lib/format.ts"])
        co = git.checkout(self.web_url, sha)
        self.assertTrue(git.files_at(co, ["src/lib/format.ts"])["src/lib/format.ts"].is_found)
        with self.assertRaises(ValueError):
            git.files_at(co, ["src/lib/format.ts"], sg.rev(self.web, "release/2.0"))
        with self.assertRaises(ValueError):
            git.files_at(self.web_url, ["a"], "main")

    def test_no_path_is_an_empty_answer(self):
        self.assertEqual(self.make_git().files_at(self.web_url, [], sg.rev(self.web, "main")), {})


class Trap09SlashBranch(GitCase):
    def test_trap09_release_slash_branch_matches_pins_and_reads(self):
        git = self.make_git()
        heads = git.heads(self.api_url, ["release/*"])
        self.assertEqual(heads.state, Read.FOUND)
        self.assertEqual(set(heads.heads), {"release/1.x", "release/1.2"})
        self.assertEqual(heads.heads["release/1.x"], sg.rev(self.api, "release/1.x"))
        sha = git.pin(self.api_url, "release/1.x")
        self.assertEqual(sha, sg.rev(self.api, "release/1.x"))
        text = git.file_at(self.api_url, "requirements.txt", sha)
        self.assertIn("acme-contracts==1.9.0", text.text)
        everything = git.heads(self.api_url, [])
        self.assertEqual(set(everything.heads) >= {"main", "release/1.x", "release/1.2"}, True)

    def test_heads_with_no_match_is_absent_and_a_failure_is_unreadable(self):
        git = self.make_git()
        none = git.heads(self.web_url, ["nothing/*"])
        self.assertEqual(none.state, Read.ABSENT)
        self.assertEqual(git.incompleteness, [])
        with self.assertRaises(ModelError):
            none.heads
        broken = git.heads(sg.remote_url(self.tmp / "no-such-repo"), ["*"])
        self.assertEqual(broken.state, Read.UNREADABLE)
        with self.assertRaises(UnreadableError):
            broken.heads
        self.assertEqual(self.kinds(git), [("read_unreadable", True)])

    def test_heads_timeout_is_unreadable(self):
        git = self.make_git(git=self.wrapper(slow_on=["ls-remote"]), timeout=SHORT, network_timeout=SHORT)
        res = git.heads(self.web_url, ["*"])
        self.assertEqual(res.state, Read.UNREADABLE)
        self.assertIn(f"timed out after {SHORT}s", res.detail)
        self.assertEqual(self.kinds(git), [("read_unreadable", True)])


class Trap11Pin(GitCase):
    def commits(self, repo, ref="main"):
        proc = build.run_git(repo, "log", "--first-parent", "--format=%H %ct", ref)
        return [(line.split()[0], int(line.split()[1])) for line in proc.stdout.splitlines()]

    @staticmethod
    def iso(ts):
        return datetime.fromtimestamp(ts, timezone.utc).isoformat()

    def test_trap11_pin_is_always_a_sha_never_a_branch_name(self):
        git = self.make_git()
        for ref in ("main", "release/2.0", "v2.1.0", "refs/heads/release/2.1", "HEAD"):
            sha = git.pin(self.web_url, ref)
            self.assertRegex(sha, r"^[0-9a-f]{40}$")
        self.assertEqual(git.pin(self.web_url, "release/2.0"), sg.rev(self.web, "release/2.0"))
        self.assertEqual(git.pin(self.web_url, "v2.1.0"), sg.rev(self.web, "v2.1.0"))
        full = sg.rev(self.web, "main")
        self.assertEqual(git.pin(self.web_url, full), full)  # a sha pins to itself, after checking it exists
        self.assertEqual(git.pin(self.web_url, full[:10]), full)  # an abbreviation is expanded
        self.assertEqual(git.incompleteness, [])

    def test_trap11_as_of_before_the_first_commit_is_absent(self):
        git = self.make_git()
        history = self.commits(self.web)
        first_ts = history[-1][1]
        res = git.pin_read(self.web_url, "main", as_of=self.iso(first_ts - 1))
        self.assertEqual(res.state, Read.ABSENT)
        with self.assertRaises(RefNotFound):
            git.pin(self.web_url, "main", as_of=self.iso(first_ts - 1))
        self.assertEqual(git.incompleteness, [])

    def test_as_of_in_1970_is_before_every_commit_and_not_ignored(self):
        """git reads `--before=@86400` (a small epoch) as another kind of date and returns the newest commit: the zone fixes it."""
        git = self.make_git()
        for early in ("1970-01-02T00:00:00Z", "1972-12-31T23:59:59Z"):
            self.assertEqual(git.pin_read(self.web_url, "main", as_of=early).state, Read.ABSENT, early)
        self.assertTrue(git.pin_read(self.web_url, "main", as_of="2000-01-01T00:00:00Z").is_absent)

    def test_as_of_picks_the_last_commit_at_or_before_the_time(self):
        git = self.make_git()
        history = self.commits(self.web)
        self.assertGreater(len(history), 3)
        sha_k, ts_k = history[2]
        sha_before = history[3][0]
        self.assertEqual(git.pin(self.web_url, "main", as_of=self.iso(ts_k)), sha_k)  # at the commit's own second
        self.assertEqual(git.pin(self.web_url, "main", as_of=self.iso(ts_k - 1)), sha_before)
        self.assertEqual(git.pin(self.web_url, "main", as_of=self.iso(ts_k + 10 ** 7)), history[0][0])
        z = self.iso(ts_k).replace("+00:00", "Z")
        self.assertEqual(git.pin(self.web_url, "main", as_of=z), sha_k)
        naive = datetime.fromtimestamp(ts_k, timezone.utc).replace(tzinfo=None).isoformat()
        self.assertEqual(git.pin(self.web_url, "main", as_of=naive), sha_k)  # naive means UTC
        with self.assertRaises(ValueError):
            git.pin(self.web_url, "main", as_of="yesterday")

    def test_branch_and_tag_with_the_same_name_are_never_guessed(self):
        repo = sg.make_repo(self.tmp, "dup", [
            {"files": {"a": "1\n"}, "tag": "dup"},
            {"files": {"a": "2\n"}},
            {"files": {"a": "3\n"}, "branch": "dup", "from": "main"},
        ])
        git = self.make_git()
        url = sg.remote_url(repo)
        branch_sha = sg.rev(repo, "refs/heads/dup")
        tag_sha = sg.rev(repo, "refs/tags/dup")
        self.assertNotEqual(branch_sha, tag_sha)
        ambiguous = git.pin_read(url, "dup")
        self.assertEqual(ambiguous.state, Read.UNREADABLE)
        self.assertIn("both a branch and a tag", ambiguous.detail)
        with self.assertRaises(ReadFailed):
            git.pin(url, "dup")
        self.assertEqual(git.pin(url, "dup", kind="branch"), branch_sha)
        self.assertEqual(git.pin(url, "dup", kind="tag"), tag_sha)
        self.assertEqual(git.pin(url, "refs/tags/dup"), tag_sha)
        self.assertEqual(self.kinds(git), [("read_unreadable", True)] * 1)  # the two failed pins are one record

    def test_annotated_tag_pins_to_its_commit(self):
        repo = sg.make_repo(self.tmp, "ann", [{"files": {"a": "1\n"}}, {"files": {"a": "2\n"}}])
        self.assertEqual(build.run_git(repo, "tag", "-a", "-m", "release", "rel", "HEAD~1").returncode, 0)
        git = self.make_git()
        self.assertEqual(git.pin(sg.remote_url(repo), "rel"), sg.rev(repo, "HEAD~1"))

    def test_missing_ref_is_absent_and_a_broken_remote_is_unreadable(self):
        git = self.make_git()
        self.assertEqual(git.pin_read(self.web_url, "no-such-branch").state, Read.ABSENT)
        with self.assertRaises(RefNotFound):
            git.pin(self.web_url, "no-such-branch")
        self.assertEqual(git.incompleteness, [])
        res = git.pin_read(sg.remote_url(self.tmp / "nowhere"), "main")
        self.assertEqual(res.state, Read.UNREADABLE)
        with self.assertRaises(ReadFailed):
            git.pin(sg.remote_url(self.tmp / "nowhere"), "main")

    def test_a_sha_that_is_not_a_commit_is_never_returned(self):
        git = self.make_git()
        # the tree of main: a real object, but not a commit
        tree = build.run_git(self.web, "rev-parse", "main^{tree}").stdout.strip()
        res = git.pin_read(self.web_url, tree)
        self.assertEqual(res.state, Read.UNREADABLE)


class MergeBase(GitCase):
    def test_merge_base_found_absent_and_shallow(self):
        git = self.make_git()
        main = sg.rev(self.web, "main")
        s02 = sg.rev(self.web, "scenario/s02-hub-cutoff")
        found = git.merge_base(self.web_url, main, s02)
        self.assertEqual(found.text, main)

        orphan = sg.rev(self.web, "scenario/s16-no-merge-base")  # fixture s16: unrelated histories
        absent = git.merge_base(self.web_url, main, orphan)
        self.assertEqual(absent.state, Read.ABSENT)
        self.assertEqual(git.incompleteness, [])
        with self.assertRaises(ValueError):
            git.merge_base(self.web_url, "main", orphan)

    def test_a_history_that_is_too_short_is_unreadable_with_the_depth(self):
        repo = sg.make_repo(self.tmp, "deep", [
            {"files": {"a": "1\n"}},
            {"files": {"a": "2\n"}, "branch": "dev", "from": "main"},
            {"files": {"a": "3\n"}, "branch": "dev"},
            {"files": {"a": "4\n"}, "branch": "dev"},
            {"files": {"b": "x\n"}, "branch": "main"},
            {"files": {"b": "y\n"}, "branch": "main"},
            {"files": {"b": "z\n"}, "branch": "main"},
        ])
        url = sg.remote_url(repo)
        git = self.make_git()
        seeded = git.cache_path(url)
        seeded.parent.mkdir(parents=True)
        env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_ALLOW_PROTOCOL": "file"}
        done = subprocess.run(
            ["git", "clone", "-q", "--bare", "--depth=2", "--no-single-branch", url, str(seeded)],
            env=env, capture_output=True, text=True,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        res = git.merge_base(url, sg.rev(repo, "main"), sg.rev(repo, "dev"))
        self.assertEqual(res.state, Read.UNREADABLE)
        self.assertIn("shallow", res.detail)
        self.assertRegex(res.detail, r"\b2 commits\b")
        self.assertEqual(self.kinds(git), [("read_unreadable", True)])


class TagsAndAncestry(GitCase):
    """M8: the tags of a remote (peeled, by prefix) and whether one commit is an ancestor of another."""

    def test_tags_are_listed_by_prefix_and_an_annotated_tag_names_its_commit(self):
        repo = sg.make_repo(self.tmp, "tagged", [
            {"files": {"a": "1\n"}, "tag": "v1.0.0"},
            {"files": {"a": "2\n"}, "tag": "v1.1.0"},
            {"files": {"a": "3\n"}, "tag": "other-1"},
        ])
        first = sg.rev(repo, "v1.0.0")
        build.run_git(repo, "tag", "-a", "-m", "annotated", "v2.0.0", first)
        git = self.make_git()
        url = sg.remote_url(repo)
        every = git.tags(url)
        self.assertEqual(sorted(every.tags), ["other-1", "v1.0.0", "v1.1.0", "v2.0.0"])
        self.assertEqual(every.tags["v2.0.0"], first)  # peeled to the commit, not the tag object
        self.assertEqual(sorted(git.tags(url, "v").tags), ["v1.0.0", "v1.1.0", "v2.0.0"])
        self.assertEqual(git.tags(url, "x").state, Read.ABSENT)
        self.assertEqual(git.incompleteness, [])

    def test_a_remote_that_cannot_be_listed_is_unreadable_not_without_tags(self):
        git = self.make_git()
        res = git.tags(sg.remote_url(self.tmp / "nowhere"))
        self.assertEqual(res.state, Read.UNREADABLE)
        with self.assertRaises(UnreadableError):
            res.tags
        self.assertEqual(self.kinds(git), [("read_unreadable", True)])

    def test_ancestry_is_read_from_the_history(self):
        repo = sg.make_repo(self.tmp, "anc", [
            {"files": {"a": "1\n"}},
            {"files": {"a": "2\n"}, "branch": "dev", "from": "main"},
            {"files": {"b": "x\n"}, "branch": "main"},
        ])
        url = sg.remote_url(repo)
        git = self.make_git()
        first, main, dev = sg.rev(repo, "main~1"), sg.rev(repo, "main"), sg.rev(repo, "dev")
        self.assertEqual(git.is_ancestor(url, first, main).text, "true")
        self.assertEqual(git.is_ancestor(url, first, dev).text, "true")
        self.assertEqual(git.is_ancestor(url, dev, main).text, "false")  # diverged lines
        self.assertEqual(git.is_ancestor(url, main, first).text, "false")
        self.assertEqual(git.is_ancestor(url, main, main).text, "true")
        with self.assertRaises(ValueError):
            git.is_ancestor(url, "main", main)
        self.assertEqual(git.incompleteness, [])

    def test_the_tags_that_contain_a_commit_come_from_one_listing_of_the_cache(self):
        repo = sg.make_repo(self.tmp, "cont", [
            {"files": {"a": "1\n"}, "tag": "v1"}, {"files": {"a": "2\n"}, "tag": "v2"}, {"files": {"a": "3\n"}, "tag": "v3"},
            {"files": {"b": "x\n"}, "branch": "side", "from": "main~2", "tag": "v-side"},
        ])
        url = sg.remote_url(repo)
        git = self.make_git()
        tags = git.tags(url, prefetch=True).tags
        middle = sg.rev(repo, "v2")
        contained, undecided = git.tags_containing(url, middle, tags)
        self.assertEqual((contained, undecided), (["v2", "v3"], []))  # v1 is before it and v-side was cut from v1
        for name in tags:  # the same answer as asking each tag
            asked = git.is_ancestor(url, middle, tags[name]).text == "true"
            self.assertEqual(name in contained, asked, name)
        # a tag the cache does not hold at the commit the remote names is undecided, never "does not contain"
        moved = {**tags, "v3": tags["v1"]}
        contained, undecided = git.tags_containing(url, middle, moved)
        self.assertEqual((contained, undecided), (["v2"], ["v3"]))
        self.assertEqual(git.incompleteness, [])

    def test_a_failure_to_list_the_tags_of_the_cache_is_every_tag_undecided_and_recorded(self):
        repo = sg.make_repo(self.tmp, "cont2", [{"files": {"a": "1\n"}, "tag": "v1"}])
        url = sg.remote_url(repo)
        warm = self.make_git()
        warm.pin(url, "main")  # the commit is in the cache: the slow git below is asked only for the listing
        git = self.make_git(git=self.wrapper(slow_on=["for-each-ref"]), timeout=SHORT)
        tags = {"v1": sg.rev(repo, "v1")}
        self.assertEqual(git.tags_containing(url, sg.rev(repo, "main"), tags), ([], ["v1"]))
        self.assertEqual(self.kinds(git), [("read_unreadable", True)])

    def test_a_commit_that_cannot_be_fetched_is_unreadable_not_false(self):
        repo = sg.make_repo(self.tmp, "anc2", [{"files": {"a": "1\n"}}])
        url = sg.remote_url(repo)
        git = self.make_git()
        res = git.is_ancestor(url, "0" * 40, sg.rev(repo, "main"))
        self.assertEqual(res.state, Read.UNREADABLE)
        self.assertEqual(self.kinds(git), [("read_unreadable", True)])


class Diff(GitCase):
    def test_diff_with_renames_and_line_numbers(self):
        old_lines = "".join(f"line {i}\n" for i in range(1, 21))
        new_lines = old_lines.replace("line 7\n", "line seven\n")
        repo = sg.make_repo(self.tmp, "d", [
            {"files": {
                "a.txt": "one\ntwo\nthree\n", "old name.txt": old_lines, "img.bin": b"\x00\x01\x02",
                "del.txt": "bye\n", "mode.sh": "echo\n",
            }},
            {"files": {
                "a.txt": "one\nTWO\nthree\nfour\n", "new/name with space.txt": new_lines, "img.bin": b"\x00\x09\x02",
                "added.txt": "fresh\n", "caf\u00e9.txt": "x\n",
            }, "delete": ["old name.txt", "del.txt"]},
        ])
        url = sg.remote_url(repo)
        base, head = sg.rev(repo, "HEAD~1"), sg.rev(repo, "HEAD")
        git = self.make_git()
        cs = git.diff(url, base, head)
        by_path = {f.path: f for f in cs.files}
        self.assertEqual((cs.base_sha, cs.head_sha), (base, head))
        self.assertEqual(cs.file_count, 6)  # a, the rename, img, the deletion, the addition, cafe
        self.assertEqual(set(by_path), {"a.txt", "new/name with space.txt", "img.bin", "del.txt", "added.txt", "caf\u00e9.txt"})
        renamed = by_path["new/name with space.txt"]
        self.assertEqual((renamed.status, renamed.old_path), ("renamed", "old name.txt"))
        self.assertEqual([(l.number, l.text) for l in renamed.removed_lines], [(7, "line 7")])
        self.assertEqual([(l.number, l.text) for l in renamed.added_lines], [(7, "line seven")])
        a = by_path["a.txt"]
        self.assertEqual([(l.number, l.text) for l in a.removed_lines], [(2, "two")])
        self.assertEqual([(l.number, l.text) for l in a.added_lines], [(2, "TWO"), (4, "four")])
        self.assertTrue(by_path["img.bin"].binary)
        self.assertFalse(by_path["img.bin"].content_available)
        self.assertEqual(by_path["del.txt"].status, "deleted")
        self.assertEqual(by_path["added.txt"].status, "added")
        self.assertEqual(by_path["caf\u00e9.txt"].status, "added")
        self.assertEqual(git.incompleteness, [])

    def test_diff_of_fixture_scenario_matches_git(self):
        git = self.make_git()
        base, head = sg.rev(self.web, "main"), sg.rev(self.web, "scenario/s02-hub-cutoff")
        cs = git.diff(self.web_url, base, head)
        names = build.run_git(self.web, "diff", "--name-only", base, head).stdout.splitlines()
        self.assertEqual(sorted(cs.paths), sorted(names))

    def test_diff_that_cannot_be_read_raises_with_a_record(self):
        git = self.make_git()
        with self.assertRaises(ReadFailed):
            git.diff(self.web_url, "0" * 40, sg.rev(self.web, "main"))
        self.assertEqual(self.kinds(git), [("read_unreadable", True)])


class Checkout(GitCase):
    def test_checkout_materialises_exactly_the_tree_at_the_sha_and_is_cached(self):
        log = self.tmp / "git.log"
        git = self.make_git(git=self.wrapper(log=str(log)))
        sha = sg.rev(self.web, "release/2.0")
        co = git.checkout(self.web_url, sha)
        listed = build.run_git(self.web, "ls-tree", "-r", "--name-only", sha).stdout.splitlines()
        self.assertEqual(sorted(co.files), sorted(listed))
        self.assertEqual(co.sha, sha)
        for rel in ("src/lib/format.ts", "package.json"):
            self.assertEqual((co.path / rel).read_text(encoding="utf-8"), build.file_at(self.web, sha, rel)[1])
        mode = (co.path / "src/lib/format.ts").stat().st_mode
        self.assertEqual(mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH), 0)  # read only
        self.assertTrue(str(co.path).startswith(str(self.tmp / "cache")))
        lines_before = log.read_text().count("\n")
        again = git.checkout(self.web_url, sha)
        self.assertEqual(again.path, co.path)
        self.assertEqual(sorted(again.files), sorted(co.files))
        new_calls = log.read_text().splitlines()[lines_before:]
        self.assertTrue(new_calls)  # it did check that the commit is there
        self.assertFalse([c for c in new_calls if "--batch" in c or " fetch " in c or "ls-tree" in c])  # nothing read or fetched again

    def test_symlinks_and_submodules_are_listed_as_skipped(self):
        repo = sg.make_repo(self.tmp, "links", [{"files": {"real.txt": "x\n"}}])
        os.symlink("real.txt", repo / "link.txt")
        build.run_git(repo, "add", "-A")
        build.run_git(repo, "commit", "-q", "-m", "link")
        git = self.make_git()
        co = git.checkout(sg.remote_url(repo), sg.rev(repo, "HEAD"))
        self.assertEqual(co.files, ("real.txt",))
        self.assertEqual([p for p, why in co.skipped], ["link.txt"])
        self.assertFalse((co.path / "link.txt").exists())
        # file_at still reads the link from git itself
        self.assertEqual(git.file_at(co, "link.txt").text, "real.txt")

    def test_unsafe_paths_are_refused(self):
        for bad in ("../x", "a/../b", "/abs", ".git/config", "a/.GIT/x", "", "a//b", "./x"):
            self.assertFalse(gitlayer.is_safe_relpath(bad), bad)
        for good in ("a", "a/b.ts", "dir with space/f", "...", ".github/x.yml", "a/.gitignore"):
            self.assertTrue(gitlayer.is_safe_relpath(good), good)

    def test_checkout_of_an_unavailable_commit_raises_with_a_record(self):
        git = self.make_git()
        with self.assertRaises(ReadFailed):
            git.checkout(self.web_url, "1" * 40)
        self.assertEqual(self.kinds(git), [("read_unreadable", True)])


class Commits(GitCase):
    def test_first_parent_and_subject(self):
        repo = sg.make_repo(self.tmp, "c", [
            {"files": {"a": "1\n"}, "message": "First (#1)"},
            {"files": {"a": "2\n"}, "message": "Second: a longer subject\n\nWith a body."},
        ])
        git = self.make_git()
        url = sg.remote_url(repo)
        head, root = sg.rev(repo, "HEAD"), sg.rev(repo, "HEAD~1")
        self.assertEqual(git.first_parent(url, head).text, root)
        self.assertEqual(git.first_parent(url, root).state, Read.ABSENT)  # a root commit has no parent
        self.assertEqual(git.commit_subject(url, head).text, "Second: a longer subject")
        self.assertEqual(git.incompleteness, [])
        missing = "0" * 39 + "1"
        self.assertEqual(git.first_parent(url, missing).state, Read.UNREADABLE)
        self.assertEqual(git.commit_subject(url, missing).state, Read.UNREADABLE)
        self.assertEqual(self.kinds(git), [("read_unreadable", True)] * 2)
        with self.assertRaises(ValueError):
            git.first_parent(url, "main")


class Cache(GitCase):
    def test_cache_is_named_by_the_hash_of_the_url_without_userinfo(self):
        git = self.make_git()
        a = git.cache_path("https://user:pw@example.invalid/acme/web.git")
        b = git.cache_path("https://example.invalid/acme/web.git")
        self.assertEqual(a, b)
        digest = hashlib.sha256(b"https://example.invalid/acme/web.git").hexdigest()
        self.assertEqual(a.name, digest + ".git")
        self.assertEqual(a.parent.parent, self.tmp / "cache")

    def test_default_cache_root_comes_from_the_environment(self):
        with mock.patch.dict(os.environ, {"REACHMAP_CACHE": str(self.tmp / "envcache")}):
            self.assertEqual(Git().cache, self.tmp / "envcache")
        env = dict(os.environ)
        env.pop("REACHMAP_CACHE", None)
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(Git().cache, Path.home() / ".cache" / "reachmap")

    def test_the_cache_may_not_sit_inside_an_analysed_repository(self):
        git = Git(self.web / "cache", env=sg.isolated_env(self.tmp / "home"))
        with self.assertRaises(ValueError):
            git.heads(self.web_url, ["*"])
        self.assertFalse((self.web / "cache").exists())

    def test_a_record_target_can_be_an_analysis(self):
        analysis = Analysis(change={"kind": "compare"}, ref="a" * 40)
        git = self.make_git(record_to=analysis)
        git.file_at(self.web_url, "x", "0" * 39 + "1")
        self.assertEqual([r.kind for r in analysis.incompleteness], ["read_unreadable"])
        self.assertEqual([r.kind for r in git.incompleteness], ["read_unreadable"])


class Trap10Secrets(GitCase):
    """Part of trap 10 that belongs to the git layer (the host layer has its own test)."""

    def test_trap10_a_token_in_a_remote_url_reaches_nothing(self):
        redact.forget_secrets()
        log = self.tmp / "argv.log"
        url = f"https://x-access-token:{SECRET}@example.invalid/acme/web.git"

        records = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        handler = Capture(level=logging.DEBUG)
        root = logging.getLogger()
        old_level = root.level
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        self.addCleanup(root.removeHandler, handler)
        self.addCleanup(root.setLevel, old_level)

        analysis = Analysis(change={"kind": "compare"}, ref="a" * 40)
        git = self.make_git(git=self.wrapper(log=str(log)), record_to=analysis)
        outputs = []
        heads = git.heads(url, ["*"])
        self.assertEqual(heads.state, Read.UNREADABLE)  # git refuses the https transport: no socket is opened
        outputs.append(heads.detail)
        for call in (lambda: git.pin(url, "main"), lambda: git.checkout(url, "1" * 40), lambda: git.diff(url, "1" * 40, "2" * 40)):
            with self.assertRaises(ReadFailed) as caught:
                call()
            outputs.append(str(caught.exception))
            outputs.append(caught.exception.detail or "")
            outputs.append(repr(caught.exception))
        outputs.append(json.dumps([r.to_dict() for r in analysis.incompleteness]))
        outputs.append(git.file_at(url, "README.md", "1" * 40).detail)
        outputs.append(git.cache_path(url).as_posix())

        for text in outputs:
            self.assertNotIn(SECRET, text)
        self.assertTrue(log.exists())
        argv = log.read_text()
        self.assertNotIn(SECRET, argv)
        self.assertNotIn("x-access-token", argv)
        self.assertIn("https://example.invalid/acme/web.git", argv)
        self.assertNotIn(SECRET, "\n".join(records))
        digest = hashlib.sha256(b"https://example.invalid/acme/web.git").hexdigest()
        self.assertEqual(git.cache_path(url).name, digest + ".git")
        for dirpath, dirnames, filenames in os.walk(self.tmp / "cache"):
            for name in (*dirnames, *filenames):
                self.assertNotIn(SECRET, name)
                path = Path(dirpath) / name
                if path.is_file() and not path.is_symlink():
                    self.assertNotIn(SECRET.encode(), path.read_bytes(), str(path))


class Runner(GitCase):
    def test_runner_isolates_git(self):
        runner = gitlayer.GitRunner(env=sg.isolated_env(self.tmp / "home"))
        env = runner.environment()
        self.assertEqual(env["GIT_CONFIG_NOSYSTEM"], "1")
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(env["LC_ALL"], "C")
        self.assertEqual(env["GIT_PAGER"], "cat")
        self.assertEqual(env["GIT_LITERAL_PATHSPECS"], "1")
        with mock.patch.dict(os.environ, {"GIT_DIR": "/nowhere", "GIT_TRACE": "1", "GIT_ASKPASS": "/bin/true"}):
            env = gitlayer.GitRunner().environment()
        self.assertNotIn("GIT_DIR", env)
        self.assertNotIn("GIT_TRACE", env)
        self.assertEqual(env["GIT_ASKPASS"], "/bin/true")  # the user's own credential helper survives

    def test_runner_reports_a_timeout_and_a_missing_git(self):
        slow = gitlayer.GitRunner(git=self.wrapper(slow_on=["version"]), env=sg.isolated_env(self.tmp / "home"))
        res = slow.run(["version"], timeout=0.3)
        self.assertTrue(res.timed_out)
        self.assertFalse(res.ok)
        missing = gitlayer.GitRunner(git=str(self.tmp / "no-git-here"))
        res = missing.run(["version"])
        self.assertFalse(res.ok)
        self.assertTrue(res.spawn_error)
        ok = gitlayer.GitRunner(env=sg.isolated_env(self.tmp / "home")).run(["version"])
        self.assertTrue(ok.ok)
        self.assertIn(b"git version", ok.stdout)

    def test_runner_can_stop_after_n_lines(self):
        runner = gitlayer.GitRunner(env=sg.isolated_env(self.tmp / "home"))
        res = runner.run(["--git-dir", str(self.web / ".git"), "ls-tree", "-r", "HEAD"], stop_after_lines=3)
        self.assertTrue(res.cut)
        self.assertEqual(res.stdout.count(b"\n"), 3)


if __name__ == "__main__":
    import unittest
    unittest.main()
