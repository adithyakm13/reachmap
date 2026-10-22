"""The package edge (M8): consumers listed from their own repositories, pins read per branch, adoption by ancestry
(never by version number), and every failure to read an incompleteness on the edge, never "not adopted"."""

import unittest
from unittest import mock

from reachmap.versions import Tags
from tests.support_cross import CrossCase

WORKSPACE = """
workspace = "t"
[limits]
hops = 3
distinct_max = 3

[repos."t/lib"]
host = "git"
remote = "{lib}"
language = "python"
roles = ["contract"]
branches = ["main"]
contract = {{ kind = "pydantic", root = "lib", package = "acme_lib", versions = "git-tags", tag_prefix = "{prefix}" }}

[repos."t/app"]
host = "git"
remote = "{app}"
language = "python"
roles = ["backend"]
branches = ["main", "release/*"]

[repos."t/worker"]
host = "git"
remote = "{worker}"
language = "python"
roles = ["backend"]
branches = ["main"]

[[edges]]
kind = "package"
producer = "t/lib"
consumer = "t/app"
manifest = "requirements.txt"
pin_pattern = '^acme-lib==(.+)$'
import_marker = "acme_lib"

[[edges]]
kind = "package"
producer = "t/lib"
consumer = "t/worker"
manifest = "requirements.txt"
pin_pattern = '^acme-lib==(.+)$'
import_marker = "acme_lib"
"""

MODEL = "class Invoice:\n    number: str\n"
CHANGED = "class Invoice:\n    number: str\n    due: str = ''\n"
PYPROJECT = '[project]\nname = "acme-lib"\n# Placeholder: the real version is the git tag.\nversion = "0.0.0"\n'


def pin(version):
    return f"acme-lib=={version}\nfastapi==0.110.0\n"


class Package(CrossCase):
    def lib(self, *commits):
        self.repos(lib=[{"files": {"lib/models.py": MODEL, "pyproject.toml": PYPROJECT}}, *commits])

    def build(self, lib, app, worker=None, prefix="v"):
        self.repos(lib=lib, app=app, worker=worker or [{"files": {"requirements.txt": pin("1.0.0")}}])
        return self.workspace(WORKSPACE, prefix=prefix)

    def consumers(self, result):
        return sorted((c.repo, c.branch, c.pin, c.adoption, c.contains_change, c.state) for c in result.consumers)

    def run_change(self, ws, spec="main~1...main", kind="compare"):
        return self.propagate(ws, "t/lib", spec, kind=kind)


class Adoption(Package):
    def test_an_unreleased_change_lists_each_consumer_once_from_its_primary_branch_on_adoption(self):
        lib = [{"files": {"lib/models.py": MODEL}, "tag": "v1.0.0"}, {"files": {"lib/models.py": CHANGED}}]
        app = [{"files": {"requirements.txt": pin("1.0.0")}}, {"files": {"requirements.txt": pin("1.0.0")}, "branch": "release/1.x", "from": "main"}]
        result = self.run_change(self.build(lib, app))
        self.assertEqual(self.consumers(result), [("t/app", "main", "1.0.0", "on_adoption", False, "pinned"), ("t/worker", "main", "1.0.0", "on_adoption", False, "pinned")])
        c = result.consumers[0]
        self.assertEqual([(e.repo, e.path, e.line, e.detail, e.ref) for e in c.evidence], [("t/app", "requirements.txt", 1, "acme-lib==1.0.0", self.sha("app"))])
        self.assertFalse(c.released)
        self.assertEqual(self.kinds(), [])
        self.assert_evidence_resolves(result)

    def test_a_released_change_lists_every_configured_branch_that_exists_decided_by_the_ancestry_of_the_pinned_tag(self):
        lib = [
            {"files": {"lib/models.py": MODEL}, "tag": "v1.9.0"},
            {"files": {"lib/models.py": CHANGED}, "tag": "v1.10.0"},
        ]
        app = [
            {"files": {"requirements.txt": pin("1.10.0")}},
            {"files": {"requirements.txt": pin("1.9.0")}, "branch": "release/1.x", "from": "main"},
            {"files": {"requirements.txt": pin("1.9.0")}, "branch": "feature/x", "from": "main"},
        ]
        result = self.run_change(self.build(lib, app), "v1.9.0..v1.10.0", kind="range")
        self.assertEqual(self.consumers(result), [
            ("t/app", "main", "1.10.0", "adopted", True, "pinned"),
            ("t/app", "release/1.x", "1.9.0", "on_adoption", False, "pinned"),  # a branch with a slash is read; feature/x is not a configured branch
            ("t/worker", "main", "1.0.0", "undecided", None, "pinned"),  # its pin names no tag
        ])
        self.assertTrue(all(c.released for c in result.consumers))
        self.assertEqual([(k, s) for k, s, _b in self.kinds()], [("edge_incomplete", "edge:package:t/lib:t/worker"), ("pin_unresolved", "t/worker:main")])  # an undecided adoption is an incomplete edge
        self.assertFalse(result.complete)

    def test_what_a_tag_contains_is_ancestry_not_its_number(self):
        """The change is on main. v2.0.0 was cut from a release line before it, so a higher number does not contain it;
        v1.10.0 is a lower number made after it and does."""
        lib = [
            {"files": {"lib/models.py": MODEL}, "tag": "v1.9.0"},
            {"files": {"lib/models.py": MODEL, "RELEASE": "2\n"}, "branch": "release/2", "from": "main", "tag": "v2.0.0"},
            {"files": {"lib/models.py": CHANGED}, "branch": "main"},
            {"files": {"CHANGELOG": "x\n"}, "branch": "main", "tag": "v1.10.0"},
        ]
        app = [{"files": {"requirements.txt": pin("2.0.0")}}, {"files": {"requirements.txt": pin("1.10.0")}, "branch": "release/1.x", "from": "main"}]
        worker = [{"files": {"requirements.txt": pin("1.10.0")}}]
        ws = self.build(lib, app, worker)
        result = self.propagate(ws, "t/lib", "main~2...main")
        self.assertEqual(self.consumers(result), [
            ("t/app", "main", "2.0.0", "on_adoption", False, "pinned"),   # the larger number, and not in it
            ("t/app", "release/1.x", "1.10.0", "adopted", True, "pinned"),
            ("t/worker", "main", "1.10.0", "adopted", True, "pinned"),
        ])
        self.assertEqual({c.tag for c in result.consumers}, {"v2.0.0", "v1.10.0"})

    def test_the_pin_matches_the_tag_by_the_configured_prefix(self):
        lib = [{"files": {"lib/models.py": MODEL}, "tag": "rel-1.2.0"}, {"files": {"lib/models.py": CHANGED}, "tag": "rel-1.3.0"}]
        app = [{"files": {"requirements.txt": pin("1.3.0")}}]
        worker = [{"files": {"requirements.txt": pin("1.2.0")}}]
        result = self.run_change(self.build(lib, app, worker, prefix="rel-"), "rel-1.2.0..rel-1.3.0", kind="range")
        self.assertEqual([(c.repo, c.tag, c.adoption) for c in result.consumers], [("t/app", "rel-1.3.0", "adopted"), ("t/worker", "rel-1.2.0", "on_adoption")])

    def test_the_placeholder_version_of_the_producers_manifest_is_never_a_version(self):
        lib = [{"files": {"lib/models.py": MODEL}, "tag": "v1.0.0"}, {"files": {"lib/models.py": CHANGED}, "tag": "v1.1.0"}]
        app = [{"files": {"requirements.txt": pin("0.0.0")}}]
        result = self.run_change(self.build(lib, app), "v1.0.0..v1.1.0", kind="range")
        (c,) = [c for c in result.consumers if c.repo == "t/app"]
        self.assertEqual((c.pin, c.adoption, c.tag), ("0.0.0", "undecided", None))  # no tag names it; the manifest of the producer says 0.0.0 and is not read
        self.assertIn(("pin_unresolved", "t/app:main", False), self.kinds())


class Undecided(Package):
    LIB = [{"files": {"lib/models.py": MODEL}, "tag": "v1.9.0"}, {"files": {"lib/models.py": CHANGED}, "tag": "v1.10.0"}]

    def test_an_adoption_that_could_not_be_decided_makes_the_edge_incomplete_and_is_not_not_adopted(self):
        """The ancestry of the pinned tag cannot be read: the consumer is `undecided`, with a blocking `edge_incomplete`,
        and the propagation is incomplete (a blocking record never sits beside complete=True)."""
        from reachmap.gitlayer import Git

        ws = self.build(self.LIB, [{"files": {"requirements.txt": pin("1.9.0")}}])
        real = Git.tags_containing

        def fails(git, remote, sha, tags):
            git._unreadable("which tags contain it", "injected failure")
            return [], list(tags)

        def fails_ancestry(git, remote, a, d):
            return git._unreadable("whether it is an ancestor", "injected failure")

        with mock.patch.object(Git, "tags_containing", fails), mock.patch.object(Git, "is_ancestor", fails_ancestry):
            result = self.run_change(ws, "v1.9.0..v1.10.0", kind="range")
        mine = [c for c in result.consumers if c.repo == "t/app"]
        self.assertEqual([(c.adoption, c.contains_change) for c in mine], [("undecided", None)])  # the change is not read as unreleased either
        self.assertIn(("edge_incomplete", "edge:package:t/lib:t/app", True), self.kinds())
        self.assertFalse(result.complete)
        self.assertIsNotNone(real)

    def test_a_pin_whose_ancestry_cannot_be_read_but_the_release_can_is_undecided_and_incomplete(self):
        from reachmap.gitlayer import Git

        ws = self.build(self.LIB, [{"files": {"requirements.txt": pin("1.10.0")}}])
        real = Git.is_ancestor
        git_calls = []

        def flaky(git, remote, a, d):
            git_calls.append((a, d))
            return git._unreadable("whether it is an ancestor", "injected failure")

        real_containing = Git.tags_containing

        def undecided_pinned(git, remote, sha, tags):
            contained, undecided = real_containing(git, remote, sha, tags)
            return [t for t in contained if t != "v1.10.0"], sorted({*undecided, "v1.10.0"})  # the pinned tag cannot be decided from the cache

        with mock.patch.object(Git, "tags_containing", undecided_pinned), mock.patch.object(Git, "is_ancestor", flaky):
            result = self.run_change(ws, "v1.9.0..v1.10.0", kind="range")
        # v1.10.0 is undecided for the release check too, so nothing is known to contain the change: it is not "unreleased"
        (c,) = [c for c in result.consumers if c.repo == "t/app"]
        self.assertEqual((c.adoption, c.contains_change), ("undecided", None))
        self.assertFalse(result.complete)
        self.assertTrue(git_calls)
        self.assertIsNotNone(real)


class Pins2(Package):
    LIB = [{"files": {"lib/models.py": MODEL}, "tag": "v1.0.0"}, {"files": {"lib/models.py": CHANGED}}]

    def test_a_manifest_that_names_the_package_without_a_matching_line_is_unresolved_not_an_absence(self):
        """A range, an extra or another spelling: the pattern does not match, but the consumer does use the package."""
        for manifest in ("acme-lib>=1.0\n", "acme_lib[extra]==1.0.0\n", "Acme.Lib ~= 1.0\n"):
            with self.subTest(manifest=manifest):
                self.records.clear()
                ws = self.build(self.LIB, [{"files": {"requirements.txt": manifest}}])
                result = self.run_change(ws)
                (c,) = [c for c in result.consumers if c.repo == "t/app"]
                self.assertEqual((c.state, c.adoption), ("unmatched", "undecided"))
                self.assertIn(("pin_unresolved", "t/app:main", False), self.kinds())
                self.assertFalse(result.complete)
                self.assertTrue(any(h.to_repo == "t/app" and not h.complete for h in result.hops))

    def test_a_manifest_that_does_not_name_the_package_is_an_established_no_pin(self):
        ws = self.build(self.LIB, [{"files": {"requirements.txt": "fastapi==0.110.0\nacme-libraries==2.0\n"}}])
        result = self.run_change(ws)
        (c,) = [c for c in result.consumers if c.repo == "t/app"]
        self.assertEqual((c.state, c.adoption), ("no_pin", "undecided"))
        self.assertEqual([k for k, s, _b in self.kinds() if s.startswith("t/app")], [])  # `acme-libraries` is another package
        self.assertTrue(all(h.complete for h in result.hops if h.to_repo == "t/app"))


class Releases(Package):
    def test_the_containing_tag_is_in_the_detail_and_a_prerelease_only_release_is_said(self):
        lib = [{"files": {"lib/models.py": MODEL}, "tag": "v1.0.0"}, {"files": {"lib/models.py": CHANGED}, "tag": "v1.1.0rc1"}]
        app = [{"files": {"requirements.txt": pin("1.1.0rc1")}}]
        worker = [{"files": {"requirements.txt": pin("1.0.0")}}]
        result = self.run_change(self.build(lib, app, worker), "v1.0.0..v1.1.0rc1", kind="range")
        by = {c.repo: c for c in result.consumers}
        self.assertEqual(by["t/app"].first_release, "v1.1.0rc1")
        self.assertIn("first released in v1.1.0rc1 (only pre-release tags contain it)", by["t/app"].detail)
        self.assertIn("v1.0.0 does not contain the change commit", by["t/worker"].detail)
        lib2 = [{"files": {"lib/models.py": MODEL}, "tag": "v1.0.0"}, {"files": {"lib/models.py": CHANGED}, "tag": "v1.1.0"}]
        result = self.run_change(self.build(lib2, app, worker), "v1.0.0..v1.1.0", kind="range")
        self.assertNotIn("pre-release", {c.repo: c for c in result.consumers}["t/worker"].detail)
        self.assertIn("first released in v1.1.0", {c.repo: c for c in result.consumers}["t/worker"].detail)

    def test_a_tag_prefix_that_matches_no_tag_while_the_repository_has_tags_is_recorded_not_quietly_unreleased(self):
        lib = [{"files": {"lib/models.py": MODEL}, "tag": "release-1.0.0"}, {"files": {"lib/models.py": CHANGED}}]
        ws = self.build(lib, [{"files": {"requirements.txt": pin("1.0.0")}}], prefix="v")
        result = self.run_change(ws)
        self.assertIn(("tag_prefix_unmatched", "t/lib", False), self.kinds())
        self.assertFalse(result.complete)
        self.assertTrue(any("matches none of its tags" in why for why in result.reasons))
        # no tag at all is just a producer that has not released
        self.records.clear()
        ws = self.build([{"files": {"lib/models.py": MODEL}}, {"files": {"lib/models.py": CHANGED}}], [{"files": {"requirements.txt": pin("1.0.0")}}], prefix="v")
        result = self.run_change(ws)
        self.assertEqual([k for k, _s, _b in self.kinds() if k == "tag_prefix_unmatched"], [])


class Languages(Package):
    def test_a_consumer_in_a_language_without_an_adapter_still_has_its_pin_read_from_its_manifest(self):
        from reachmap import config

        lib = [{"files": {"lib/models.py": MODEL}, "tag": "v1.0.0"}, {"files": {"lib/models.py": CHANGED}}]
        app = [{"files": {"requirements.txt": pin("1.0.0")}}]
        self.repos(lib=lib, app=app, worker=[{"files": {"requirements.txt": pin("1.0.0")}}])
        text = WORKSPACE.replace('language = "python"\nroles = ["backend"]\nbranches = ["main", "release/*"]', 'language = "java"\nroles = ["backend"]\nbranches = ["main", "release/*"]')
        with mock.patch.object(config, "_LANGUAGES", {*config._LANGUAGES, "java"}):
            ws = self.workspace(text, prefix="v")
            result = self.run_change(ws)
        self.assertEqual(self.consumers(result), [("t/app", "main", "1.0.0", "on_adoption", False, "pinned"), ("t/worker", "main", "1.0.0", "on_adoption", False, "pinned")])


class Pins(Package):
    LIB = [{"files": {"lib/models.py": MODEL}, "tag": "v1.0.0"}, {"files": {"lib/models.py": CHANGED}, "tag": "v1.1.0"}]

    def run_range(self, app, worker=None):
        ws = self.build(self.LIB, app, worker)
        return self.run_change(ws, "v1.0.0..v1.1.0", kind="range")

    def test_a_pin_that_is_not_a_version_leaves_adoption_undecided_and_is_recorded(self):
        result = self.run_range([{"files": {"requirements.txt": "acme-lib==git+https://example.invalid/lib@abc123\n"}}])
        (c,) = [c for c in result.consumers if c.repo == "t/app"]
        self.assertEqual((c.state, c.adoption, c.contains_change), ("pinned", "undecided", None))
        self.assertIn(("version_unparsed", "t/app:main", False), self.kinds())

    def test_a_manifest_without_a_pin_line_or_not_on_the_branch_is_no_pin_an_established_absence(self):
        app = [{"files": {"requirements.txt": "fastapi==0.110.0\n"}}, {"files": {"other.txt": "x\n"}, "branch": "release/1.x", "from": "main", "delete": ["requirements.txt"]}]
        result = self.run_range(app)
        mine = {(c.branch, c.state, c.adoption) for c in result.consumers if c.repo == "t/app"}
        self.assertEqual(mine, {("main", "no_pin", "undecided"), ("release/1.x", "no_pin", "undecided")})
        by = {c.branch: c for c in result.consumers if c.repo == "t/app"}
        self.assertIn("does not name the package", by["main"].detail)
        self.assertIn("is not on release/1.x", by["release/1.x"].detail)
        self.assertEqual([k for k, s, _b in self.kinds() if s.startswith("t/app")], [])  # not a record: the reads were made and found nothing
        self.assertTrue(all(h.complete for h in result.hops if h.to_repo == "t/app"))  # an absence that was established is not an incomplete hop

    def test_an_unreadable_manifest_is_an_incompleteness_on_the_edge_never_not_adopted(self):
        app = [{"files": {"requirements.txt/inner.txt": "x\n"}}]  # the manifest path is a folder: git cannot read it as a file
        result = self.run_range(app)
        (c,) = [c for c in result.consumers if c.repo == "t/app"]
        self.assertEqual((c.state, c.adoption, c.contains_change), ("unreadable", "undecided", None))
        self.assertNotEqual(c.adoption, "on_adoption")
        blocking = {(k, s) for k, s, b in self.kinds() if b}
        self.assertIn(("edge_incomplete", "edge:package:t/lib:t/app"), blocking)
        self.assertTrue(any(k == "read_unreadable" for k, _s, _b in self.kinds()))
        self.assertFalse(result.complete)
        self.assertTrue(any(h.kind == "package" and h.to_repo == "t/app" and not h.complete for h in result.hops))

    def test_a_consumer_whose_branches_cannot_be_listed_is_an_incomplete_hop_not_no_consumer(self):
        self.repos(lib=self.LIB, app=[{"files": {"requirements.txt": pin("1.0.0")}}], worker=[{"files": {"requirements.txt": pin("1.0.0")}}])
        self.urls["app"] = "file://" + str(self.tmp / "nowhere")
        ws = self.workspace(WORKSPACE, prefix="v")
        result = self.run_change(ws, "v1.0.0..v1.1.0", kind="range")
        self.assertEqual([c.repo for c in result.consumers], ["t/worker"])
        self.assertFalse(result.complete)
        self.assertIn(("edge_incomplete", "edge:package:t/lib:t/app"), {(k, s) for k, s, _b in self.kinds()})

    def test_only_a_change_with_source_in_the_producer_has_consumers(self):
        lib = [{"files": {"lib/models.py": MODEL}, "tag": "v1.0.0"}, {"files": {"lib/test_models.py": "def test_x():\n    pass\n"}}]
        ws = self.build(lib, [{"files": {"requirements.txt": pin("1.0.0")}}])
        result = self.run_change(ws)
        self.assertEqual(result.consumers, ())  # a change to a test alone releases nothing to consume
        self.assertEqual(result.hops, ())


class Versions(CrossCase):
    def test_tags_are_read_as_versions_ordered_and_the_ones_that_are_not_versions_are_listed(self):
        self.repos(lib=[
            {"files": {"a": "1\n"}, "tag": "v1.9.0"}, {"files": {"a": "2\n"}, "tag": "v1.10.0"}, {"files": {"a": "3\n"}, "tag": "nightly"},
            {"files": {"a": "4\n"}, "tag": "v1.10.0rc1"},
        ])
        from tests import support_git as sg
        from reachmap.gitlayer import Git

        git = Git(self.tmp / "cache", env=sg.isolated_env(self.tmp / "home"))
        tags = Tags.read(git, self.urls["lib"], "v")
        self.assertEqual(tags.state, "found")
        self.assertNotIn("nightly", tags.by_name)  # `nightly` has no prefix `v`: it is not one of this producer's tags
        self.assertEqual(sorted(tags.versions, key=lambda n: tags.versions[n]), ["v1.9.0", "v1.10.0rc1", "v1.10.0"])
        every = Tags.read(git, self.urls["lib"], None)
        self.assertIn("nightly", every.by_name)
        self.assertNotIn("nightly", every.versions)  # a tag that is not a version is not a release
        self.assertEqual(tags.tag_for_pin("1.10.0"), ("v1.10.0", "exact"))
        self.assertEqual(tags.tag_for_pin("1.10"), ("v1.10.0", "version"))  # 1.10 and 1.10.0 are one version
        self.assertEqual(tags.tag_for_pin("1.11.0")[0], None)
        self.assertEqual(tags.tag_for_pin("latest"), (None, "the pin is not a version"))
        absent = Tags.read(git, self.urls["lib"], "zzz")
        self.assertEqual(absent.state, "absent")

    def test_containment_is_asked_of_every_release_tag_lowest_version_first(self):
        self.repos(lib=[
            {"files": {"a": "1\n"}, "tag": "v1.9.0"}, {"files": {"a": "2\n"}}, {"files": {"a": "3\n"}, "tag": "v1.10.0"}, {"files": {"a": "4\n"}, "tag": "v1.11.0"},
        ])
        from tests import support_git as sg
        from reachmap.gitlayer import Git

        git = Git(self.tmp / "cache", env=sg.isolated_env(self.tmp / "home"))
        tags = Tags.read(git, self.urls["lib"], "v")
        change = self.sha("lib", "main~2")  # the commit between v1.9.0 and v1.10.0
        contained, unreadable = tags.containing(git, self.urls["lib"], change)
        self.assertEqual((contained, unreadable), (["v1.10.0", "v1.11.0"], []))
        self.assertEqual(tags.containing(git, self.urls["lib"], self.sha("lib"))[0], ["v1.11.0"])


if __name__ == "__main__":
    unittest.main()
