"""Change sets (M4): a pull request, a compare or a range resolved to pinned shas.

Git runs against the M1 fixture repositories (and small ones built here) as file:// remotes;
the hosts answer from the synthetic recorded responses. Trap numbers refer to
docs/briefs/M4-git-hosts.md."""

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from reachmap import config
from reachmap.changeset import ChangeSetRefused, NoSuchChangeRequest, resolve, title_for_commit
from reachmap.gitlayer import Git, ReadFailed
from reachmap.hosts.bitbucket import Bitbucket
from reachmap.hosts.github import GitHub
from reachmap.hosts.gitlab import GitLab
from reachmap.hosts import get_host
from reachmap.model import Analysis, Read
from tests import support_git as sg
from tests.fixtures import build
from tests.support_hosts import B, H, M, P, Q, FakeTransport, forbid_default_transports, load

GH = "https://api.github.com"
GL = "https://gitlab.com/api/v4"
BB = "https://api.bitbucket.org/2.0"


class ChangeCase(sg.TempDirCase):
    def setUp(self):
        super().setUp()
        for patcher in forbid_default_transports():
            self.addCleanup(patcher.stop)
        self.repos = sg.fixture_repos()
        self.web = self.repos["acme/web"]
        self.web_url = sg.remote_url(self.web)

    def make_git(self, **kw):
        kw.setdefault("env", sg.isolated_env(self.tmp / "home"))
        cache = kw.pop("cache", self.tmp / "cache")
        return Git(cache, **kw)

    def workspace(self, host="git", remote=None, name="acme/web"):
        text = f'''
workspace = "t"
[repos."{name}"]
host = "{host}"
remote = "{remote or self.web_url}"
language = "typescript"
roles = ["frontend"]
'''
        return config.loads(text, fmt="toml")

    def names(self, repo, spec):
        return sorted(build.run_git(repo, "diff", "--name-only", spec).stdout.splitlines())

    def kinds(self, items):
        return [(r.kind, r.blocks_selection) for r in items]


def patched(fixture, subs, **overrides):
    """A recorded response with placeholder shas replaced and some fields overridden."""
    status, headers, body = load(fixture, subs)
    doc = json.loads(body)
    doc.update(overrides)
    return status, headers, json.dumps(doc).encode()


class Compare(ChangeCase):
    def test_compare_uses_three_dots_from_the_merge_base(self):
        git = self.make_git()
        cs = resolve(self.workspace(), "acme/web", compare="release/2.0...main", git=git)
        base, head = sg.rev(self.web, "release/2.0"), sg.rev(self.web, "main")
        merge_base = build.run_git(self.web, "merge-base", base, head).stdout.strip()
        self.assertNotEqual(merge_base, base)  # the branches diverged: this is not a plain tree comparison
        self.assertEqual((cs.kind, cs.base_sha, cs.head_sha, cs.pre_change_sha), ("compare", base, head, merge_base))
        self.assertEqual(sorted(cs.paths), self.names(self.web, "release/2.0...main"))
        self.assertNotEqual(sorted(cs.paths), self.names(self.web, "release/2.0..main"))
        self.assertIn("merge base", cs.how["pre_change"])
        self.assertEqual(cs.repo, "acme/web")
        self.assertEqual(cs.incompleteness, ())
        for sha in (cs.base_sha, cs.head_sha, cs.pre_change_sha):
            self.assertRegex(sha, r"^[0-9a-f]{40}$")

    def test_compare_accepts_a_pair_and_rejects_bad_forms(self):
        ws, git = self.workspace(), self.make_git()
        cs = resolve(ws, "acme/web", compare=("main", "scenario/s02-hub-cutoff"), git=git)
        self.assertEqual(cs.pre_change_sha, sg.rev(self.web, "main"))
        for bad in ("main..other", "main", "...x", ("a",), ("", "x")):
            with self.assertRaises(ValueError):
                resolve(ws, "acme/web", compare=bad, git=git)
        with self.assertRaises(ValueError):
            resolve(ws, "acme/web", git=git)
        with self.assertRaises(ValueError):
            resolve(ws, "acme/web", compare="a...b", range="a..b", git=git)
        with self.assertRaises(ValueError):
            resolve(ws, "acme/nope", compare="a...b", git=git)

    def test_an_unknown_ref_is_refused(self):
        with self.assertRaises(ChangeSetRefused) as caught:
            resolve(self.workspace(), "acme/web", compare="main...no-such-branch", git=self.make_git())
        self.assertIn("no-such-branch", str(caught.exception))

    def test_trap08_open_change_with_unrelated_histories_has_no_pre_change_version(self):
        analysis = Analysis(change={"kind": "compare"}, ref="a" * 40)
        git = self.make_git(record_to=analysis)
        head_ref = "scenario/s16-no-merge-base"  # fixture s16: an orphan branch
        cs = resolve(self.workspace(), "acme/web", compare=f"main...{head_ref}", git=git)
        self.assertIsNone(cs.pre_change_sha)
        self.assertIn("unavailable", cs.how["pre_change"])
        self.assertEqual(self.kinds(cs.incompleteness), [("pre_change_unavailable", True)])
        (rec,) = cs.incompleteness
        self.assertEqual(rec.subject, head_ref)  # the head ref, the subject format s16 expects
        self.assertEqual(self.kinds(analysis.incompleteness), [("pre_change_unavailable", True)])
        self.assertGreater(cs.file_count, 0)  # the files are the plain difference between the two tips
        self.assertEqual(sorted(cs.paths), self.names(self.web, f"main..{head_ref}"))

    def test_a_merge_base_that_cannot_be_read_is_also_unavailable_and_blocking(self):
        import shutil
        script = self.tmp / "slowgit"
        # merge-base fails the way a damaged or interrupted read does; every other call is real git.
        # (A failure, not a sleep with a short timeout: a short timeout also cuts the diff on a slow CI runner.)
        script.write_text('#!/bin/sh\nfor a in "$@"; do case "$a" in merge-base) echo "fatal: simulated read failure" >&2; exit 128;; esac; done\n'
                          f'exec "{shutil.which("git")}" "$@"\n')
        script.chmod(0o755)
        warm = self.make_git()
        for ref in ("main", "scenario/s02-hub-cutoff"):
            warm.pin(self.web_url, ref)  # the commits are in the cache: the failing git is asked only for the merge base
        git = self.make_git(git=str(script))
        cs = resolve(self.workspace(), "acme/web", compare="main...scenario/s02-hub-cutoff", git=git)
        self.assertIsNone(cs.pre_change_sha)
        kinds = sorted(k for k, _ in self.kinds(cs.incompleteness))
        self.assertEqual(kinds, ["pre_change_unavailable", "read_unreadable"])
        self.assertTrue(all(blocking for _, blocking in self.kinds(cs.incompleteness)))


class AsOf(ChangeCase):
    """`as_of` pins every ref given by name as of a time (a full sha is already a pin and is left as it is)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.shared = Path(tempfile.mkdtemp(prefix="reachmap-asof-"))  # one git cache for the class: the commits are fetched once
        cls.addClassCleanup(sg.remove_tree, cls.shared)

    def make_git(self, **kw):
        kw.setdefault("cache", self.shared / "cache")
        return super().make_git(**kw)

    def committer_time(self, ref):
        return int(build.run_git(self.web, "log", "-1", "--format=%ct", ref).stdout.strip())

    def test_a_time_after_every_commit_changes_nothing(self):
        ws, git = self.workspace(), self.make_git()
        plain = resolve(ws, "acme/web", compare="main...scenario/s02-hub-cutoff", git=git)
        late = resolve(ws, "acme/web", compare="main...scenario/s02-hub-cutoff", git=git, as_of="2100-01-01T00:00:00Z")
        self.assertEqual((late.base_sha, late.head_sha, late.pre_change_sha), (plain.base_sha, plain.head_sha, plain.pre_change_sha))
        self.assertEqual(late.paths, plain.paths)

    def test_a_ref_is_read_as_of_the_time_and_a_full_sha_is_not(self):
        ws, git = self.workspace(), self.make_git()
        main_tip, before = sg.rev(self.web, "main"), sg.rev(self.web, "main~1")
        when = self.committer_time("main") - 1  # one second before the tip of main
        iso = datetime.fromtimestamp(when, timezone.utc).isoformat()
        self.assertNotEqual(main_tip, before)
        as_of = resolve(ws, "acme/web", range=f"{before}..main", git=git, as_of=iso)
        self.assertEqual(as_of.base_sha, before)  # a full sha is a pin already
        self.assertEqual(as_of.head_sha, before)  # main as of one second before its tip is the commit before the tip
        self.assertEqual(as_of.paths, ())

    def test_a_time_before_the_first_commit_is_refused(self):
        with self.assertRaises(ChangeSetRefused) as caught:
            resolve(self.workspace(), "acme/web", compare="main...scenario/s02-hub-cutoff", git=self.make_git(), as_of="2000-01-01T00:00:00Z")
        self.assertIn("main", str(caught.exception))

    def test_a_bad_time_is_a_value_error(self):
        with self.assertRaises(ValueError):
            resolve(self.workspace(), "acme/web", compare="main...scenario/s02-hub-cutoff", git=self.make_git(), as_of="yesterday")


class Range(ChangeCase):
    def test_range_uses_two_dots_and_the_start_is_the_pre_change_version(self):
        git = self.make_git()
        cs = resolve(self.workspace(), "acme/web", range="release/2.0..main", git=git)
        start, end = sg.rev(self.web, "release/2.0"), sg.rev(self.web, "main")
        self.assertEqual((cs.kind, cs.base_sha, cs.head_sha, cs.pre_change_sha), ("range", start, end, start))
        self.assertEqual(sorted(cs.paths), self.names(self.web, "release/2.0..main"))
        self.assertEqual(cs.incompleteness, ())
        self.assertIn("range start", cs.how["pre_change"])

    def test_range_of_tags_on_the_contracts_repo(self):
        contracts = self.repos["acme/contracts"]
        ws = self.workspace(remote=sg.remote_url(contracts), name="acme/contracts")
        cs = resolve(ws, "acme/contracts", range="v1.9.0..v1.10.0", git=self.make_git())
        self.assertEqual(cs.base_sha, sg.rev(contracts, "v1.9.0"))
        self.assertEqual(cs.head_sha, sg.rev(contracts, "v1.10.0"))
        self.assertEqual(sorted(cs.paths), self.names(contracts, "v1.9.0..v1.10.0"))

    def test_range_rejects_three_dots(self):
        with self.assertRaises(ValueError):
            resolve(self.workspace(), "acme/web", range="a...b", git=self.make_git())


class PullRequests(ChangeCase):
    def github(self, route_overrides=None, **routes):
        transport = FakeTransport(routes)
        return GitHub(transport=transport), transport

    def test_open_pull_request_is_pinned_to_the_hosts_base_and_head(self):
        base, head = sg.rev(self.web, "main"), sg.rev(self.web, "scenario/s02-hub-cutoff")
        count = len(self.names(self.web, f"{base}...{head}"))
        subs = {B: base, H: head}
        host, transport = self.github(**{f"{GH}/repos/acme/web/pulls/12": patched("github/pr_open.json", subs, changed_files=count)})
        git = self.make_git()
        cs = resolve(self.workspace("github"), "acme/web", pr=12, git=git, host=host)
        self.assertEqual((cs.kind, cs.base_sha, cs.head_sha), ("pr", base, head))
        self.assertEqual(cs.pre_change_sha, base)  # the merge base of the open pull request
        self.assertEqual(sorted(cs.paths), self.names(self.web, f"{base}...{head}"))
        self.assertEqual(cs.change_request.number, 12)
        self.assertEqual(cs.change_request.title, "Filter invoices by status")
        self.assertEqual(cs.file_count, count)
        self.assertEqual(cs.incompleteness, ())
        self.assertIn("host", cs.how["head"])

    def test_merged_pull_request_uses_the_merge_commit_and_its_first_parent(self):
        merge = sg.rev(self.web, "main")
        parent = sg.rev(self.web, "main~1")
        other = sg.rev(self.web, "release/2.0")
        count = len(self.names(self.web, f"{parent}..{merge}"))
        subs = {M: merge, P: parent, Q: other}
        host, _ = self.github(**{f"{GH}/repos/acme/web/pulls/7": patched("github/pr_merged.json", subs, changed_files=count, commits=1)})
        cs = resolve(self.workspace("github"), "acme/web", pr="#7", git=self.make_git(), host=host)
        self.assertEqual((cs.head_sha, cs.pre_change_sha, cs.base_sha), (merge, parent, parent))
        self.assertEqual(cs.how["pre_change"], "first parent of the merge commit")
        self.assertEqual(sorted(cs.paths), self.names(self.web, f"{parent}..{merge}"))
        self.assertEqual(cs.change_request.state, "merged")

    def test_gitlab_merge_request_by_bang_number(self):
        api = self.repos["acme/api"]
        merge, parent, other = sg.rev(api, "main"), sg.rev(api, "main~1"), sg.rev(api, "release/1.x")
        count = len(self.names(api, f"{parent}..{merge}"))
        subs = {M: merge, P: parent, Q: other}
        url = f"{GL}/projects/acme%2Fapi/merge_requests/7"
        host = GitLab(transport=FakeTransport({url: patched("gitlab/mr_merged.json", subs, changes_count=str(count)),
                                               f"{url}/commits?per_page=100": "gitlab/mr_8_commits.json"}))
        ws = self.workspace("gitlab", remote=sg.remote_url(api), name="acme/api")
        cs = resolve(ws, "acme/api", pr="!7", git=self.make_git(), host=host)
        self.assertEqual((cs.kind, cs.head_sha, cs.pre_change_sha), ("pr", merge, parent))
        self.assertEqual(cs.change_request.number, 7)

    def test_trap06_404_is_no_such_change_request(self):
        git = self.make_git()
        host, _ = self.github(**{f"{GH}/repos/acme/web/pulls/999": "github/pr_not_found.json"})
        with self.assertRaises(NoSuchChangeRequest) as caught:
            resolve(self.workspace("github"), "acme/web", pr=999, git=git, host=host)
        self.assertIn("no such change request", str(caught.exception))
        self.assertIn("acme/web#999", str(caught.exception))
        self.assertEqual(git.incompleteness, [])

    def test_trap06_an_unreadable_host_answer_is_refused_with_a_record_never_an_empty_change(self):
        for fixture, expect in (("github/rate_limited.json", "rate limited"), ("github/server_error.json", "HTTP 500"),
                                ("github/malformed.json", "not valid JSON")):
            with self.subTest(fixture):
                analysis = Analysis(change={"kind": "pr"}, ref="a" * 40)
                git = self.make_git(record_to=analysis)
                host = GitHub(transport=FakeTransport({f"{GH}/repos/acme/web/pulls/12": fixture}), record_to=analysis)
                with self.assertRaises(ReadFailed) as caught:
                    resolve(self.workspace("github"), "acme/web", pr=12, git=git, host=host)
                self.assertIn(expect, caught.exception.detail)
                self.assertEqual(self.kinds(analysis.incompleteness), [("read_unreadable", True)])

    def test_trap06_timeout_from_the_host_is_unreadable(self):
        analysis = Analysis(change={"kind": "pr"}, ref="a" * 40)
        host = GitHub(transport=FakeTransport({f"{GH}/repos/acme/web/pulls/12": TimeoutError()}), record_to=analysis)
        with self.assertRaises(ReadFailed):
            resolve(self.workspace("github"), "acme/web", pr=12, git=self.make_git(record_to=analysis), host=host)
        self.assertEqual(self.kinds(analysis.incompleteness), [("read_unreadable", True)])

    def test_a_host_less_repository_has_no_change_requests(self):
        with self.assertRaises(ChangeSetRefused) as caught:
            resolve(self.workspace("git"), "acme/web", pr=12, git=self.make_git())
        self.assertIn("compare", str(caught.exception))

    def test_a_plain_remote_resolves_with_the_default_host(self):
        cs = resolve(self.workspace("git"), "acme/web", compare="main...scenario/s02-hub-cutoff", git=self.make_git())
        self.assertEqual(cs.kind, "compare")

    def test_open_pull_request_without_pre_change_version(self):
        base, head = sg.rev(self.web, "main"), sg.rev(self.web, "scenario/s16-no-merge-base")
        count = len(self.names(self.web, f"{base}..{head}"))
        subs = {B: base, H: head}
        host, _ = self.github(**{f"{GH}/repos/acme/web/pulls/12": patched("github/pr_open.json", subs, changed_files=count)})
        cs = resolve(self.workspace("github"), "acme/web", pr=12, git=self.make_git(), host=host)
        self.assertIsNone(cs.pre_change_sha)
        self.assertEqual(self.kinds(cs.incompleteness), [("pre_change_unavailable", True)])
        self.assertEqual(cs.incompleteness[0].subject, "feature/invoice-filter")


class FileCountCheck(ChangeCase):
    """Trap 7: the host's count is paginated in full, and the local diff must match it."""

    def big_repo(self, count):
        files = {f"src/generated/client{i:03d}.ts": f"export const n = {i};\n" for i in range(count)}
        return sg.make_repo(self.tmp, f"big{count}", [{"files": {"README.md": "x\n"}}, {"files": files}])

    def github_301(self, base, head):
        subs = {B: base, H: head}
        routes = {f"{GH}/repos/acme/web/pulls/301": "github/pr_301_no_count.json",
                  f"{GH}/repos/acme/web/pulls/301/files?per_page=100": "github/pr_301_files_page01.json"}
        for n in range(2, 12):
            routes[f"{GH}/repos/acme/web/pulls/301/files?per_page=100&page={n}"] = f"github/pr_301_files_page{n:02d}.json"
        return GitHub(transport=FakeTransport(routes, subs))

    def test_trap07_301_files_match_the_paginated_host_count(self):
        repo = self.big_repo(301)
        base, head = sg.rev(repo, "HEAD~1"), sg.rev(repo, "HEAD")
        ws = self.workspace("github", remote=sg.remote_url(repo))
        cs = resolve(ws, "acme/web", pr=301, git=self.make_git(), host=self.github_301(base, head))
        self.assertEqual(cs.file_count, 301)
        self.assertEqual(cs.change_request.file_count, 301)

    def test_trap07_a_local_diff_that_disagrees_with_the_host_count_refuses(self):
        repo = self.big_repo(300)
        base, head = sg.rev(repo, "HEAD~1"), sg.rev(repo, "HEAD")
        ws = self.workspace("github", remote=sg.remote_url(repo))
        analysis = Analysis(change={"kind": "pr"}, ref="a" * 40)
        git = self.make_git(record_to=analysis)
        with self.assertRaises(ChangeSetRefused) as caught:
            resolve(ws, "acme/web", pr=301, git=git, host=self.github_301(base, head))
        message = str(caught.exception)
        self.assertIn("300", message)
        self.assertIn("301", message)
        self.assertEqual(self.kinds(analysis.incompleteness), [("diff_count_mismatch", True)])
        self.assertEqual(self.kinds(caught.exception.records), [("diff_count_mismatch", True)])
        self.assertEqual(analysis.incompleteness[0].subject, "acme/web#301")

    def test_trap07_bitbucket_diffstat_is_followed_through_next_links(self):
        repo = self.big_repo(301)
        base, head = sg.rev(repo, "HEAD~1"), sg.rev(repo, "HEAD")
        subs = {B: base, H: head}
        subs.update({B[:12]: base[:12], H[:12]: head[:12]})
        pr = f"{BB}/repositories/acme/web/pullrequests/301"
        commit = f"{BB}/repositories/acme/web/commit"
        routes = {
            pr: "bitbucket/pr_301.json",
            f"{pr}/diffstat?pagelen=100": "bitbucket/diffstat_301_page1.json",
            f"{pr}/diffstat?pagelen=100&page=2": "bitbucket/diffstat_301_page2.json",
            f"{pr}/diffstat?pagelen=100&page=3": "bitbucket/diffstat_301_page3.json",
            f"{pr}/diffstat?pagelen=100&page=4": "bitbucket/diffstat_301_page4.json",
            f"{commit}/{head[:12]}": f"bitbucket/commit_{H[:12]}.json",
            f"{commit}/{base[:12]}": f"bitbucket/commit_{B[:12]}.json",
        }
        host = Bitbucket(transport=FakeTransport(routes, subs))
        ws = self.workspace("bitbucket", remote=sg.remote_url(repo))
        cs = resolve(ws, "acme/web", pr=301, git=self.make_git(), host=host)
        self.assertEqual((cs.file_count, cs.base_sha, cs.head_sha), (301, base, head))

        small = self.big_repo(300)
        sbase, shead = sg.rev(small, "HEAD~1"), sg.rev(small, "HEAD")
        subs = {B: sbase, H: shead, B[:12]: sbase[:12], H[:12]: shead[:12]}
        routes[f"{commit}/{shead[:12]}"] = f"bitbucket/commit_{H[:12]}.json"
        routes[f"{commit}/{sbase[:12]}"] = f"bitbucket/commit_{B[:12]}.json"
        host = Bitbucket(transport=FakeTransport(routes, subs))
        with self.assertRaises(ChangeSetRefused):
            resolve(self.workspace("bitbucket", remote=sg.remote_url(small)), "acme/web", pr=301, git=self.make_git(), host=host)


class MergeStrategies(ChangeCase):
    """A merged pull request is read at the commit it became and the commit before it. What that
    is depends on how it was merged: a rebase merge puts every commit of the request on the base
    branch, so the version before the change is the tip as it was before the first of them."""

    PR_FILES = {"a.txt", "b.txt", "c.txt"}

    def base_history(self):
        commits = [{"files": {"base.txt": "base\n", "a.txt": "a0\n", "b.txt": "b0\n", "c.txt": "c0\n"}, "message": "b0"}]
        commits += [{"files": {"a.txt": "PR a\n"}, "branch": "feature", "from": "main", "message": "f1"},
                    {"files": {"b.txt": "PR b\n"}, "branch": "feature", "message": "f2"},
                    {"files": {"c.txt": "PR c\n"}, "branch": "feature", "message": "f3"}]
        commits += [{"files": {f"other{i}.txt": f"o{i}\n"}, "message": f"b{i}"} for i in (1, 2, 3)]
        return commits

    def repo(self, strategy):
        """(url, shas): b0, b3 (the base tip before the merge), head (the request's own tip), merge."""
        commits = self.base_history()
        if strategy == "squash":
            commits.append({"files": {"a.txt": "PR a\n", "b.txt": "PR b\n", "c.txt": "PR c\n"}, "message": "Add the three changes (#7)"})
        elif strategy == "rebase":
            commits += [{"files": {"a.txt": "PR a\n"}, "message": "f1"}, {"files": {"b.txt": "PR b\n"}, "message": "f2"},
                        {"files": {"c.txt": "PR c\n"}, "message": "f3"}]
        elif strategy == "ambiguous":  # two commits that touch the same files: both readings fit the file list
            commits += [{"files": {"a.txt": "x1\n", "b.txt": "x1\n", "c.txt": "x1\n"}, "message": "s1"},
                        {"files": {"a.txt": "x2\n", "b.txt": "x2\n", "c.txt": "x2\n"}, "message": "s2"}]
        repo = sg.make_repo(self.tmp, f"m-{strategy}", commits)
        if strategy == "merge":
            sg.git_raw(repo, "merge", "--no-ff", "-q", "-m", "Merge pull request #7", "feature")
        merge = sg.rev(repo, "main")
        b3 = sg.rev(repo, "feature") and sg.git_raw(repo, "rev-list", "--first-parent", "-n", "1", "--grep=^b3$", "main")
        return sg.remote_url(repo), {"b0": sg.git_raw(repo, "rev-list", "--max-parents=0", "main"), "b3": b3,
                                     "head": sg.rev(repo, "feature"), "merge": merge, "repo": repo}

    def host_and_workspace(self, kind, url, shas, *, files, commits, base=None):
        base = base or shas["b0"]
        merge, head = shas["merge"], shas["head"]
        if kind == "github":
            route = patched("github/pr_merged.json", {M: merge, P: base, Q: head}, changed_files=files, commits=commits)
            host = GitHub(transport=FakeTransport({f"{GH}/repos/acme/web/pulls/7": route}))
            return host, self.workspace("github", remote=url), 7
        if kind == "gitlab":
            mr = f"{GL}/projects/acme%2Fapi/merge_requests/7"
            page = (200, {"X-Next-Page": ""}, json.dumps([{"id": f"{i:040x}"} for i in range(1, commits + 1)]).encode())
            routes = {mr: patched("gitlab/mr_merged.json", {M: merge, P: base, Q: head}, changes_count=str(files)),
                      f"{mr}/commits?per_page=100": page}
            return GitLab(transport=FakeTransport(routes)), self.workspace("gitlab", remote=url, name="acme/api"), 7
        subs = {M: merge, P: base, Q: head, M[:12]: merge[:12], P[:12]: base[:12], Q[:12]: head[:12]}
        pr, commit = f"{BB}/repositories/acme/web/pullrequests/8", f"{BB}/repositories/acme/web/commit"
        stat = {"pagelen": 100, "values": [{"type": "diffstat", "status": "modified", "new": {"path": f"f{i}"}} for i in range(files)]}
        listing = {"pagelen": 100, "values": [{"type": "commit", "hash": f"{i:040x}"} for i in range(1, commits + 1)]}
        routes = {pr: "bitbucket/pr_merged.json", f"{pr}/diffstat?pagelen=100": (200, {}, json.dumps(stat).encode()),
                  f"{pr}/commits?pagelen=100": (200, {}, json.dumps(listing).encode()),
                  f"{commit}/{merge[:12]}": f"bitbucket/commit_{M[:12]}.json", f"{commit}/{base[:12]}": f"bitbucket/commit_{P[:12]}.json",
                  f"{commit}/{head[:12]}": f"bitbucket/commit_{Q[:12]}.json"}
        return Bitbucket(transport=FakeTransport(routes, subs)), self.workspace("bitbucket", remote=url), 8

    KINDS = ("github", "gitlab", "bitbucket")

    def test_squash_merge_keeps_the_first_parent(self):
        url, shas = self.repo("squash")
        for kind in self.KINDS:
            with self.subTest(kind):
                host, ws, number = self.host_and_workspace(kind, url, shas, files=3, commits=3)
                cs = resolve(ws, next(iter(ws.repos)), pr=number, git=self.make_git(), host=host)
                self.assertEqual((cs.head_sha, cs.pre_change_sha, cs.base_sha), (shas["merge"], shas["b3"], shas["b3"]))
                self.assertEqual(set(cs.paths), self.PR_FILES)
                self.assertEqual(cs.incompleteness, ())
                self.assertIn("first parent", cs.how["pre_change"])

    def test_rebase_merge_goes_back_over_every_commit_of_the_request(self):
        url, shas = self.repo("rebase")
        merge_parent = sg.git_raw(shas["repo"], "rev-parse", f"{shas['merge']}~1")
        self.assertNotEqual(merge_parent, shas["b3"])  # the first parent is the request's second commit, not the base tip
        for kind in self.KINDS:
            with self.subTest(kind):
                host, ws, number = self.host_and_workspace(kind, url, shas, files=3, commits=3)
                cs = resolve(ws, next(iter(ws.repos)), pr=number, git=self.make_git(), host=host)
                self.assertEqual((cs.head_sha, cs.pre_change_sha, cs.base_sha), (shas["merge"], shas["b3"], shas["b3"]))
                self.assertEqual(set(cs.paths), self.PR_FILES)  # the changes of all three commits, not the last one only
                self.assertEqual(cs.incompleteness, ())
                self.assertIn("3 commits before the merge commit", cs.how["pre_change"])

    def test_true_merge_commit_keeps_the_first_parent_whatever_the_commit_count(self):
        url, shas = self.repo("merge")
        parents = sg.git_raw(shas["repo"], "rev-list", "--parents", "-n", "1", shas["merge"]).split()
        self.assertEqual(len(parents), 3)
        for kind in self.KINDS:
            with self.subTest(kind):
                host, ws, number = self.host_and_workspace(kind, url, shas, files=3, commits=3)
                cs = resolve(ws, next(iter(ws.repos)), pr=number, git=self.make_git(), host=host)
                self.assertEqual((cs.head_sha, cs.pre_change_sha), (shas["merge"], shas["b3"]))
                self.assertEqual(set(cs.paths), self.PR_FILES)
                self.assertEqual(cs.how["pre_change"], "first parent of the merge commit")

    def test_one_commit_request_needs_no_guessing(self):
        repo = sg.make_repo(self.tmp, "one", [{"files": {"a.txt": "1\n", "b.txt": "1\n"}}, {"files": {"a.txt": "2\n"}}, {"files": {"b.txt": "2\n"}}])
        shas = {"b0": sg.rev(repo, "HEAD~2"), "b3": sg.rev(repo, "HEAD~1"), "head": sg.rev(repo, "HEAD"), "merge": sg.rev(repo, "HEAD")}
        host, ws, number = self.host_and_workspace("github", sg.remote_url(repo), shas, files=1, commits=1)
        cs = resolve(ws, "acme/web", pr=number, git=self.make_git(), host=host)
        self.assertEqual(cs.pre_change_sha, shas["b3"])
        self.assertEqual(cs.paths, ("b.txt",))

    def test_fast_forward_merge_is_read_like_a_rebase_merge(self):
        repo = sg.make_repo(self.tmp, "ff", [
            {"files": {"base.txt": "b\n", "a.txt": "a0\n", "b.txt": "b0\n", "c.txt": "c0\n"}},
            {"files": {"a.txt": "PR a\n"}}, {"files": {"b.txt": "PR b\n"}}, {"files": {"c.txt": "PR c\n"}},
        ])
        b0 = sg.rev(repo, "HEAD~3")
        shas = {"b0": b0, "b3": b0, "head": sg.rev(repo, "HEAD"), "merge": sg.rev(repo, "HEAD")}
        host, ws, number = self.host_and_workspace("github", sg.remote_url(repo), shas, files=3, commits=3)
        cs = resolve(ws, "acme/web", pr=number, git=self.make_git(), host=host)
        self.assertEqual((cs.pre_change_sha, set(cs.paths)), (b0, self.PR_FILES))

    def test_a_commit_count_that_fits_neither_reading_refuses_and_says_why(self):
        url, shas = self.repo("rebase")
        for kind in self.KINDS:
            with self.subTest(kind):
                analysis = Analysis(change={"kind": "pr"}, ref="a" * 40)
                host, ws, number = self.host_and_workspace(kind, url, shas, files=3, commits=2)  # two commits do not make three files
                git = self.make_git(record_to=analysis)
                with self.assertRaises(ChangeSetRefused) as caught:
                    resolve(ws, next(iter(ws.repos)), pr=number, git=git, host=host)
                self.assertEqual(self.kinds(caught.exception.records), [("pre_change_unavailable", True)])
                self.assertEqual(self.kinds(analysis.incompleteness), [("pre_change_unavailable", True)])
                self.assertIn("neither", str(caught.exception))

    def test_two_readings_that_both_fit_refuse_rather_than_guess(self):
        url, shas = self.repo("ambiguous")
        host, ws, number = self.host_and_workspace("github", url, shas, files=3, commits=2)
        git = self.make_git()
        with self.assertRaises(ChangeSetRefused) as caught:
            resolve(ws, "acme/web", pr=number, git=git, host=host)
        self.assertIn("both", str(caught.exception))
        self.assertEqual(self.kinds(git.incompleteness), [("pre_change_unavailable", True)])

    def test_a_request_without_a_commit_count_cannot_be_pinned(self):
        url, shas = self.repo("rebase")
        status, headers, body = patched("github/pr_merged.json", {M: shas["merge"], P: shas["b0"], Q: shas["head"]}, changed_files=3)
        doc = json.loads(body)
        doc.pop("commits")
        host = GitHub(transport=FakeTransport({f"{GH}/repos/acme/web/pulls/7": (status, headers, json.dumps(doc).encode())}))
        git = self.make_git()
        with self.assertRaises(ChangeSetRefused):
            resolve(self.workspace("github", remote=url), "acme/web", pr=7, git=git, host=host)
        self.assertEqual(self.kinds(git.incompleteness), [("pre_change_unavailable", True)])

    def test_the_hosts_base_may_differ_from_the_first_parent(self):
        url, shas = self.repo("squash")
        other = sg.git_raw(shas["repo"], "rev-parse", f"{shas['b3']}~1")  # an older base tip than the merge's parent
        host, ws, number = self.host_and_workspace("github", url, shas, files=3, commits=3, base=other)
        cs = resolve(ws, "acme/web", pr=number, git=self.make_git(), host=host)
        self.assertEqual((cs.base_sha, cs.pre_change_sha), (shas["b3"], shas["b3"]))  # the first parent, not the host's base
        self.assertEqual(cs.change_request.base_sha, other)

    def test_a_merged_request_whose_parents_cannot_be_read_records_the_missing_pre_change_version(self):
        import shutil
        url, shas = self.repo("squash")
        script = self.tmp / "noparents"
        script.write_text('#!/bin/sh\nfor a in "$@"; do case "$a" in --parents) echo "fatal: no" >&2; exit 128;; esac; done\n'
                          f'exec "{shutil.which("git")}" "$@"\n')
        script.chmod(0o755)
        host, ws, number = self.host_and_workspace("github", url, shas, files=3, commits=3)
        analysis = Analysis(change={"kind": "pr"}, ref="a" * 40)
        git = self.make_git(git=str(script), record_to=analysis)
        try:
            resolve(ws, "acme/web", pr=number, git=git, host=host)
        except ChangeSetRefused:
            pass  # the count check may refuse what it cannot verify; what matters is the record
        kinds = sorted(k for k, _ in self.kinds(analysis.incompleteness))
        self.assertIn("pre_change_unavailable", kinds)
        self.assertIn("read_unreadable", kinds)

    def test_a_pinned_head_that_cannot_be_fetched_falls_back_to_the_file_count_and_records_nothing_for_it(self):
        url, shas = self.repo("rebase")
        host, ws, number = self.host_and_workspace("github", url, shas, files=3, commits=3)
        status, headers, body = patched("github/pr_merged.json", {M: shas["merge"], P: shas["b0"]}, changed_files=3, commits=3)
        body = body.replace(b"5" * 40, b"7" * 40)  # the request's own head is a commit the remote does not have
        host = GitHub(transport=FakeTransport({f"{GH}/repos/acme/web/pulls/7": (status, headers, body)}))
        git = self.make_git()
        cs = resolve(ws, "acme/web", pr=7, git=git, host=host)
        self.assertEqual((cs.pre_change_sha, set(cs.paths)), (shas["b3"], self.PR_FILES))
        self.assertEqual(cs.incompleteness, ())  # the optional check could not run; that is not a lost read


class SquashTitles(ChangeCase):
    def test_title_follows_the_pull_request_number_in_a_squash_subject(self):
        repo = sg.make_repo(self.tmp, "squash", [
            {"files": {"a": "1\n"}, "message": "Fix settlement rounding (#7)"},
            {"files": {"a": "2\n"}, "message": "No number here"},
            {"files": {"a": "3\n"}, "message": "Mentions (#8) in the middle of a subject"},
        ])
        remote = sg.remote_url(repo)
        git = self.make_git()
        host = GitHub(transport=FakeTransport({f"{GH}/repos/acme/web/pulls/7": "github/pr_merged.json"}), record_to=git.target)
        found = title_for_commit(git, host, remote, "acme/web", sg.rev(repo, "HEAD~2"))
        self.assertEqual(found.text, "Fix settlement rounding (#7)")  # the title of pull request 7
        self.assertEqual(title_for_commit(git, host, remote, "acme/web", sg.rev(repo, "HEAD~1")).state, Read.ABSENT)
        self.assertEqual(title_for_commit(git, host, remote, "acme/web", sg.rev(repo, "HEAD")).state, Read.ABSENT)

    def test_a_pull_request_the_host_does_not_have_is_absent_and_a_failure_is_unreadable(self):
        repo = sg.make_repo(self.tmp, "squash2", [{"files": {"a": "1\n"}, "message": "Do it (#9)"}])
        remote, sha = sg.remote_url(repo), sg.rev(repo, "HEAD")
        git = self.make_git()
        missing = GitHub(transport=FakeTransport({f"{GH}/repos/acme/web/pulls/9": "github/pr_not_found.json"}), record_to=git.target)
        self.assertEqual(title_for_commit(git, missing, remote, "acme/web", sha).state, Read.ABSENT)
        self.assertEqual(git.incompleteness, [])
        down = GitHub(transport=FakeTransport({f"{GH}/repos/acme/web/pulls/9": "github/server_error.json"}), record_to=git.target)
        self.assertEqual(title_for_commit(git, down, remote, "acme/web", sha).state, Read.UNREADABLE)
        self.assertEqual(self.kinds(git.incompleteness), [("read_unreadable", True)])
        gitlab_repo = sg.make_repo(self.tmp, "squash3", [{"files": {"a": "1\n"}, "message": "Do it (!12)"}])
        lab = GitLab(transport=FakeTransport({f"{GL}/projects/acme%2Fapi/merge_requests/12": "gitlab/mr_opened.json"}))
        self.assertEqual(
            title_for_commit(git, lab, sg.remote_url(gitlab_repo), "acme/api", sg.rev(gitlab_repo, "HEAD")).text,
            "Add the audit log filter",
        )
        plain = get_host("git")
        self.assertEqual(title_for_commit(git, plain, remote, "acme/web", sha).state, Read.ABSENT)


if __name__ == "__main__":
    import unittest
    unittest.main()
