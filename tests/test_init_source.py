"""M17: repository specs, and reading a repository through the git layer: the default branch from `ls-remote --symref HEAD`, host specs
(resolved with the plain git transport redirected to local repositories: no network), and every way a repository cannot be read."""

import tempfile
import unittest
from pathlib import Path

from reachmap.gitlayer import Git
from reachmap.init import detect
from reachmap.init.guess import SpecError
from reachmap.init.source import name_collisions, open_repo, parse_spec
from reachmap.model import Read
from tests import support_git as sg

FILES = {"src/a.ts": "export const a = 1;\n", "src/a.test.ts": "", "package.json": "{}"}


class Parse(unittest.TestCase):
    def test_every_form(self):
        cases = {
            "github:acme/web": ("github", "acme/web", "acme/web"),
            "gitlab:group/sub/name": ("gitlab", "group/sub/name", "group/sub/name"),
            "bitbucket:ws/repo": ("bitbucket", "ws/repo", "ws/repo"),
            "git:https://example.com/acme/web.git": ("git", "https://example.com/acme/web.git", "acme/web"),
            "git:git@example.com:acme/web.git": ("git", "git@example.com:acme/web.git", "acme/web"),
            "file:///tmp/work/acme-web": ("local", "/tmp/work/acme-web", "acme-web"),
            "/tmp/work/acme-web.git": ("local", "/tmp/work/acme-web.git", "acme-web"),
            "acme/web=/tmp/work/x": ("local", "/tmp/work/x", "acme/web"),
            "acme/web=github:other/name": ("github", "other/name", "acme/web"),
        }
        for text, (kind, ref, name) in cases.items():
            with self.subTest(spec=text):
                spec = parse_spec(text)
                self.assertEqual((spec.kind, spec.ref, spec.name), (kind, ref, name))

    def test_a_relative_path_is_made_absolute_and_a_name_with_an_equals_sign_in_a_path_is_still_a_path(self):
        self.assertTrue(Path(parse_spec("./some/where").ref).is_absolute())
        self.assertEqual(parse_spec("/a/b=c").kind, "local")
        self.assertEqual(parse_spec("git:https://example.com/x.git?a=b").kind, "git")

    def test_what_is_no_spec(self):
        for bad in ("", "  ", "github:one", "github:a/b/c", "bitbucket:a", "git:", "https://github.com/a/b", "git@github.com:a/b.git", "-x", "file://host/x", "git:-x"):
            with self.subTest(spec=bad), self.assertRaises(ValueError):
                parse_spec(bad)

    def test_two_repositories_with_one_name_are_refused_with_the_way_out(self):
        clash = name_collisions([parse_spec("/a/web"), parse_spec("/b/web")])
        self.assertEqual(len(clash), 1)
        self.assertIn("NAME=SPEC", clash[0])

    def test_a_credential_in_a_url_is_never_shown(self):
        spec = parse_spec("git:https://user:s3cretvalue@example.com/acme/web.git")
        self.assertNotIn("s3cretvalue", spec.shown)
        self.assertNotIn("s3cretvalue", spec.name)


class Remotes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix="reachmap-init-src-"))
        cls.addClassCleanup(sg.remove_tree, cls.root)
        cls.home = cls.root / "home"
        cls.cache = cls.root / "cache"
        cls.web = sg.make_repo(cls.root / "fx", "web", [{"files": FILES}])
        cls.trunk = sg.make_repo(cls.root / "fx", "trunk", [{"files": FILES}, {"files": {"x.ts": ""}, "branch": "trunk", "from": "main"}])
        sg.git_raw(cls.trunk, "symbolic-ref", "HEAD", "refs/heads/trunk")
        cls.detached = sg.make_repo(cls.root / "fx", "detached", [{"files": FILES}])
        sg.git_raw(cls.detached, "checkout", "-q", "--detach")
        cls.empty = cls.root / "fx" / "empty"
        cls.empty.mkdir(parents=True)
        sg.git_raw(cls.empty, "init", "-q")
        cls.plain = cls.root / "fx" / "plain"
        cls.plain.mkdir()
        # hosts: `github:acme/hosted` is https://github.com/acme/hosted.git, redirected by git itself to a local repository
        cls.hosted = sg.make_repo(cls.root / "fx" / "acme", "hosted.git", [{"files": FILES}])

    def git(self, *config):
        return Git(self.cache, env=sg.isolated_env(self.home), extra_config=config, record_to=[])

    def test_default_branch_is_what_head_names(self):
        self.assertEqual(self.git().default_branch(str(self.web)).text, "main")
        self.assertEqual(self.git().default_branch(str(self.trunk)).text, "trunk")

    def test_a_detached_head_names_no_branch_and_a_missing_remote_is_unreadable_and_recorded(self):
        g = self.git()
        self.assertEqual(g.default_branch(str(self.detached)).state, Read.ABSENT)
        gone = g.default_branch(str(self.root / "nope"))
        self.assertEqual(gone.state, Read.UNREADABLE)
        self.assertEqual([r.kind for r in g.incompleteness], ["read_unreadable"])

    def test_a_repository_is_opened_at_its_default_branch_with_its_files(self):
        opened = open_repo(parse_spec(str(self.web)), self.git())
        self.assertEqual((opened.branch, opened.local, opened.config_remote, opened.branch_note), ("main", True, str(self.web), None))
        self.assertIn("src/a.ts", opened.tree.files)
        self.assertEqual(opened.tree.text("package.json"), "{}")

    def test_the_default_branch_is_the_one_head_names_not_main(self):
        self.assertEqual(open_repo(parse_spec(str(self.trunk)), self.git()).branch, "trunk")

    def test_a_detached_head_falls_back_to_a_named_branch_and_says_so(self):
        opened = open_repo(parse_spec(str(self.detached)), self.git())
        self.assertEqual(opened.branch, "main")
        self.assertIn("not a symbolic ref", opened.branch_note)

    def test_a_host_spec_is_cloned_from_the_hosts_web_address_with_no_host_api_call(self):
        git = self.git(f"url.file://{self.root / 'fx'}/.insteadOf=https://github.com/")
        opened = open_repo(parse_spec("github:acme/hosted"), git)
        self.assertEqual((opened.remote, opened.config_remote, opened.local), ("https://github.com/acme/hosted.git", None, False))
        guess = detect("github:acme/hosted", git)
        self.assertEqual((guess.host, guess.remote, guess.name), ("github", None, "acme/hosted"))

    def test_every_way_a_repository_cannot_be_read_is_a_spec_error_with_a_reason(self):
        for spec, why in (
            (str(self.root / "missing"), "no such directory"),
            (f"file://{self.root / 'missing'}", "no such directory"),
            (str(self.plain), "could not be read"),
            (str(self.empty), "no branches"),
            (f"git:file://{self.root}/nowhere.git", "could not be read"),
        ):
            with self.subTest(spec=spec), self.assertRaises(SpecError) as cm:
                open_repo(parse_spec(spec), self.git())
            self.assertIn(why, cm.exception.reason)

    def test_a_credential_in_a_remote_is_removed_before_it_reaches_the_config_value(self):
        class Fake:
            def heads(self, remote):
                self.remote = remote
                from reachmap.gitlayer import HeadsResult
                return HeadsResult(Read.FOUND, {"main": "1" * 40})

            def default_branch(self, remote):
                from reachmap.model import ReadResult
                return ReadResult.found("main")

            def pin_read(self, remote, ref, as_of=None, *, kind="any"):
                from reachmap.model import ReadResult
                return ReadResult.found("1" * 40)

            def checkout(self, remote, sha):
                from reachmap.gitlayer import Checkout
                return Checkout(remote, sha, Path(tempfile.mkdtemp()), ())

        opened = open_repo(parse_spec("git:https://user:s3cretvalue@example.com/acme/web.git"), Fake())
        self.assertNotIn("s3cretvalue", opened.config_remote)
        self.assertNotIn("s3cretvalue", " ".join(opened.notes))
        self.assertTrue(any("credentials were removed" in n for n in opened.notes))

    def test_a_failure_reason_never_holds_a_credential(self):
        with self.assertRaises(SpecError) as cm:
            open_repo(parse_spec("git:https://user:s3cretvalue@example.invalid/acme/web.git"), self.git())
        self.assertNotIn("s3cretvalue", cm.exception.reason + cm.exception.spec)

    def test_a_repository_in_no_supported_language_is_listed_not_guessed(self):
        go = sg.make_repo(self.root / "fx", "gorepo", [{"files": {"main.go": "package main\n", "x.go": ""}}])
        with self.assertRaises(SpecError) as cm:
            detect(str(go), self.git())
        self.assertIn("2 go", cm.exception.reason)


if __name__ == "__main__":
    unittest.main()
