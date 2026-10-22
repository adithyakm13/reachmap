"""The host layer (M4): GitHub, GitLab, Bitbucket Cloud and the host-less mode, against the
synthetic recorded responses in tests/fixtures/hosts. No transport is ever built: the default
ones are patched to raise, and a test asserts that."""

import base64
import email
import io
import json
import logging
import os
import subprocess
import tempfile
import unittest
import urllib.error
import urllib.request
import urllib.response
from pathlib import Path
from unittest import mock

from reachmap import hosts, redact
from reachmap.errors import ModelError, UnreadableError
from reachmap.hosts import ChangeRequest, CliTransport, HostUnsupported, TransportError, UrllibTransport, get_host, parse_number
from reachmap.hosts.bitbucket import Bitbucket
from reachmap.hosts.github import GitHub
from reachmap.hosts.gitlab import GitLab
from reachmap.model import Analysis, Read
from tests.support_hosts import B, H, M, P, Q, FakeTransport, forbid_default_transports, load

GH = "https://api.github.com"
GL = "https://gitlab.com/api/v4"
BB = "https://api.bitbucket.org/2.0"
SECRET = "tok_LIVE_0123456789abcdefXYZ"


class HostCase(unittest.TestCase):
    def setUp(self):
        for patcher in forbid_default_transports():
            self.addCleanup(patcher.stop)
        redact.forget_secrets()
        self.addCleanup(redact.forget_secrets)

    def kinds(self, host):
        return [(r.kind, r.blocks_selection) for r in host.incompleteness]


class NoNetwork(HostCase):
    def test_the_suite_wide_guard_is_on_and_stops_a_default_transport_before_it_is_built(self):
        self.assertTrue(os.environ.get(hosts.FORBID_DEFAULT_TRANSPORT_ENV))  # set in tests/__init__.py for every test
        with mock.patch("reachmap.hosts.UrllibTransport.default_transport", create=True):
            for kind in ("github", "gitlab", "bitbucket"):
                with self.assertRaises(hosts.DefaultTransportForbidden):
                    get_host(kind)._transport()

    def test_default_transports_cannot_be_built_in_the_suite(self):
        with self.assertRaises(AssertionError):
            UrllibTransport()
        with self.assertRaises(AssertionError):
            CliTransport("gh", [GH])

    def test_a_host_without_a_transport_fails_the_test_instead_of_reaching_out(self):
        for kind in ("github", "gitlab", "bitbucket"):
            host = get_host(kind)
            with self.assertRaises(AssertionError):
                host.change_request("acme/web", 1)

    def test_constructing_a_host_builds_no_transport(self):
        for kind in ("github", "gitlab", "bitbucket", "git"):
            get_host(kind)  # would raise if it built one


# ------------------------------------------------------------------------ ChangeRequest


class ChangeRequestTests(HostCase):
    def make(self, **kw):
        fields = dict(number=1, title="t", body="", state="open", base_ref="main", head_ref="f", base_sha=B, head_sha=H,
                      merge_sha=None, file_count=2, url="https://example.invalid/pr/1")
        fields.update(kw)
        return ChangeRequest(**fields)

    def test_round_trip_and_validation(self):
        cr = self.make(state="merged", merge_sha=M)
        self.assertEqual(ChangeRequest.from_json(cr.to_json()), cr)
        self.assertEqual(ChangeRequest.from_read(Read_found(cr)), cr)
        for bad in (dict(number=0), dict(state="draft"), dict(base_sha="abc"), dict(merge_sha=M), dict(file_count=-1),
                    dict(state="merged", merge_sha="xyz")):
            with self.assertRaises(ModelError):
                self.make(**bad)

    def test_from_read_refuses_absent_and_unreadable(self):
        from reachmap.model import ReadResult
        with self.assertRaises(ModelError):
            ChangeRequest.from_read(ReadResult.absent())
        with self.assertRaises(UnreadableError):
            ChangeRequest.from_read(ReadResult.unreadable("down"))

    def test_parse_number(self):
        self.assertEqual([parse_number(x) for x in (12, "12", "#12", "!12", " 12 ")], [12] * 5)
        for bad in ("abc", "", 0, "-3", True, "1.5"):
            with self.assertRaises(ValueError):
                parse_number(bad)


def Read_found(cr):
    from reachmap.model import ReadResult
    return ReadResult.found(cr.to_json())


# ------------------------------------------------------------------------ GitHub


class GitHubTests(HostCase):
    def host(self, routes, **kw):
        transport = FakeTransport(routes, {"9" * 40: "9" * 40})
        return GitHub(transport=transport, **kw), transport

    def test_open_pull_request(self):
        host, transport = self.host({f"{GH}/repos/acme/web/pulls/12": "github/pr_open.json"})
        res = host.change_request("acme/web", 12)
        cr = ChangeRequest.from_read(res)
        self.assertEqual(
            (cr.number, cr.title, cr.state, cr.base_ref, cr.head_ref, cr.base_sha, cr.head_sha, cr.file_count),
            (12, "Filter invoices by status", "open", "main", "feature/invoice-filter", B, H, 3),
        )
        self.assertIsNone(cr.merge_sha)  # GitHub's merge_commit_sha on an open request is a test merge: not a commit on main
        self.assertIn("Fixes the empty state", cr.body)
        self.assertEqual(cr.url, "https://github.com/acme/web/pull/12")
        self.assertEqual(transport.urls, [f"{GH}/repos/acme/web/pulls/12"])
        method, _, headers = transport.requests[0]
        self.assertEqual(method, "GET")
        self.assertEqual(headers["Accept"], "application/vnd.github+json")
        self.assertNotIn("Authorization", headers)
        self.assertEqual(host.incompleteness, [])

    def test_merged_pull_request_has_its_merge_commit(self):
        host, _ = self.host({f"{GH}/repos/acme/web/pulls/7": "github/pr_merged.json"})
        cr = ChangeRequest.from_read(host.change_request("acme/web", 7))
        self.assertEqual((cr.state, cr.merge_sha, cr.base_sha, cr.head_sha, cr.file_count), ("merged", M, P, Q, 4))
        self.assertEqual(host.title_of("acme/web", 7).text, "Fix settlement rounding (#7)")

    def test_trap06_404_is_absent_and_nothing_is_recorded(self):
        host, _ = self.host({f"{GH}/repos/acme/web/pulls/999": "github/pr_not_found.json"})
        res = host.change_request("acme/web", 999)
        self.assertEqual(res.state, Read.ABSENT)
        self.assertEqual(host.incompleteness, [])
        self.assertEqual(host.title_of("acme/web", 999).state, Read.ABSENT)

    def test_trap06_every_other_failure_is_unreadable_and_recorded(self):
        url = f"{GH}/repos/acme/web/pulls/12"
        cases = {
            "rate limit": ("github/rate_limited.json", "rate limited"),
            "bad credentials": ("github/bad_credentials.json", "HTTP 401"),
            "server error": ("github/server_error.json", "HTTP 500"),
            "unavailable": ("github/unavailable.json", "HTTP 503"),
            "malformed json": ("github/malformed.json", "not valid JSON"),
            "wrong shape": ("github/wrong_shape.json", "unexpected response"),
            "timeout": (TimeoutError("slow"), "did not answer in time"),
            "connection": (ConnectionRefusedError("refused"), "the request failed"),
            "transport": (TransportError("gh: To get started with GitHub CLI, please run: gh auth login"), "gh auth login"),
        }
        for name, (route, expect) in cases.items():
            with self.subTest(name):
                host, _ = self.host({url: route})
                res = host.change_request("acme/web", 12)
                self.assertEqual(res.state, Read.UNREADABLE)
                self.assertIn(expect, res.detail)
                self.assertEqual(self.kinds(host), [("read_unreadable", True)])
                self.assertIn("acme/web", host.incompleteness[0].subject)
                with self.assertRaises(UnreadableError):
                    res.text_or_none()
                title = host.title_of("acme/web", 12)
                self.assertEqual(title.state, Read.UNREADABLE)

    def test_forbidden_without_rate_limit_headers_is_unreadable_not_absent(self):
        host, _ = self.host({f"{GH}/repos/acme/web/pulls/12": (403, {}, b'{"message":"Resource not accessible by integration"}')})
        res = host.change_request("acme/web", 12)
        self.assertEqual(res.state, Read.UNREADABLE)
        self.assertIn("HTTP 403", res.detail)

    def test_trap07_file_count_falls_back_to_the_paginated_list(self):
        routes = {f"{GH}/repos/acme/web/pulls/301": "github/pr_301_no_count.json",
                  f"{GH}/repos/acme/web/pulls/301/files?per_page=100": "github/pr_301_files_page01.json"}
        for n in range(2, 12):
            routes[f"{GH}/repos/acme/web/pulls/301/files?per_page=100&page={n}"] = f"github/pr_301_files_page{n:02d}.json"
        host, transport = self.host(routes)
        cr = ChangeRequest.from_read(host.change_request("acme/web", 301))
        self.assertEqual(cr.file_count, 301)  # eleven pages of 30, the last holding one file
        self.assertEqual(len(transport.requests), 12)
        self.assertEqual(host.incompleteness, [])
        self.assertTrue(all(h.get("Accept", "").startswith("application/vnd.github") for _, _, h in transport.requests))

    def test_trap07_the_pull_request_count_is_used_when_there_is_one(self):
        host, transport = self.host({f"{GH}/repos/acme/web/pulls/301": "github/pr_301_with_count.json"})
        self.assertEqual(ChangeRequest.from_read(host.change_request("acme/web", 301)).file_count, 301)
        self.assertEqual(len(transport.requests), 1)

    def test_a_page_that_fails_makes_the_whole_count_unreadable_never_a_partial_count(self):
        routes = {f"{GH}/repos/acme/web/pulls/301": "github/pr_301_no_count.json",
                  f"{GH}/repos/acme/web/pulls/301/files?per_page=100": "github/pr_301_files_page01.json"}
        for n in range(2, 5):
            routes[f"{GH}/repos/acme/web/pulls/301/files?per_page=100&page={n}"] = f"github/pr_301_files_page{n:02d}.json"
        routes[f"{GH}/repos/acme/web/pulls/301/files?per_page=100&page=5"] = "github/server_error.json"
        host, _ = self.host(routes)
        res = host.change_request("acme/web", 301)
        self.assertEqual(res.state, Read.UNREADABLE)
        self.assertIn("HTTP 500", res.detail)
        self.assertEqual(self.kinds(host), [("read_unreadable", True)])

    def test_pagination_refuses_loops_and_other_hosts(self):
        first = f"{GH}/repos/acme/web/pulls/301/files?per_page=100"
        page = lambda nxt: (200, {"Link": f'<{nxt}>; rel="next"'}, b"[]")
        for name, nxt in (("loop", first), ("foreign", "https://evil.example.invalid/steal?page=2")):
            with self.subTest(name):
                host, transport = self.host({f"{GH}/repos/acme/web/pulls/301": "github/pr_301_no_count.json", first: page(nxt)})
                res = host.change_request("acme/web", 301)
                self.assertEqual(res.state, Read.UNREADABLE)
                self.assertEqual(len(transport.requests), 2)  # the foreign link was never requested

    def test_trap09_branch_name_with_a_slash_is_encoded_in_the_api_path(self):
        host, transport = self.host({f"{GH}/repos/acme/web/branches/release%2F1.x": "github/branch_release_1x.json"})
        res = host.branch_head("acme/web", "release/1.x")
        self.assertEqual(res.text, B)
        self.assertEqual(transport.urls, [f"{GH}/repos/acme/web/branches/release%2F1.x"])
        self.assertNotIn("release/1.x", transport.urls[0])
        gone, _ = self.host({f"{GH}/repos/acme/web/branches/nope": "github/branch_not_found.json"})
        self.assertEqual(gone.branch_head("acme/web", "nope").state, Read.ABSENT)
        self.assertEqual(gone.incompleteness, [])

    def test_remote_and_repo_names(self):
        host = GitHub(transport=FakeTransport({}))
        self.assertEqual(host.remote("acme/web"), "https://github.com/acme/web.git")
        for bad in ("web", "a/b/c", "", "acme/"):
            with self.assertRaises(ValueError):
                host.remote(bad)
        with self.assertRaises(ValueError):
            host.change_request("acme/web/extra", 1)

    def test_state_of_a_closed_unmerged_request(self):
        status, headers, body = load("github/pr_open.json")
        doc = json.loads(body)
        doc.update(state="closed", closed_at="2023-11-16T00:00:00Z")
        host, _ = self.host({f"{GH}/repos/acme/web/pulls/12": (200, headers, json.dumps(doc).encode())})
        cr = ChangeRequest.from_read(host.change_request("acme/web", 12))
        self.assertEqual((cr.state, cr.merge_sha), ("closed", None))

    def test_a_merged_request_without_a_merge_commit_is_unreadable(self):
        status, headers, body = load("github/pr_merged.json")
        doc = json.loads(body)
        doc["merge_commit_sha"] = None
        host, _ = self.host({f"{GH}/repos/acme/web/pulls/7": (200, headers, json.dumps(doc).encode())})
        self.assertEqual(host.change_request("acme/web", 7).state, Read.UNREADABLE)

    def test_response_for_another_number_is_not_accepted(self):
        host, _ = self.host({f"{GH}/repos/acme/web/pulls/13": "github/pr_open.json"})  # the file is for #12
        self.assertEqual(host.change_request("acme/web", 13).state, Read.UNREADABLE)


# ------------------------------------------------------------------------ GitLab


class GitLabTests(HostCase):
    def host(self, routes, **kw):
        transport = FakeTransport(routes)
        return GitLab(transport=transport, **kw), transport

    def test_merge_request_is_per_project_and_the_project_path_is_encoded(self):
        host, transport = self.host({f"{GL}/projects/acme%2Fapi/merge_requests/12": "gitlab/mr_opened.json"})
        cr = ChangeRequest.from_read(host.change_request("acme/api", "!12"))
        self.assertEqual(transport.urls, [f"{GL}/projects/acme%2Fapi/merge_requests/12"])
        self.assertEqual(
            (cr.number, cr.title, cr.state, cr.base_ref, cr.head_ref, cr.base_sha, cr.head_sha, cr.merge_sha, cr.file_count),
            (12, "Add the audit log filter", "open", "main", "feature/audit-filter", B, H, None, 3),
        )
        self.assertEqual(cr.url, "https://gitlab.com/acme/api/-/merge_requests/12")

    def test_merged_and_squashed_merge_requests(self):
        host, _ = self.host({f"{GL}/projects/acme%2Fapi/merge_requests/7": "gitlab/mr_merged.json",
                             f"{GL}/projects/acme%2Fapi/merge_requests/7/commits?per_page=100": "gitlab/mr_7_commits.json",
                             f"{GL}/projects/acme%2Fapi/merge_requests/8": "gitlab/mr_squashed.json",
                             f"{GL}/projects/acme%2Fapi/merge_requests/8/commits?per_page=100": "gitlab/mr_8_commits.json"})
        merged = ChangeRequest.from_read(host.change_request("acme/api", 7))
        self.assertEqual((merged.state, merged.merge_sha, merged.base_sha, merged.head_sha), ("merged", M, P, Q))
        squashed = ChangeRequest.from_read(host.change_request("acme/api", 8))
        self.assertEqual((squashed.state, squashed.merge_sha), ("merged", M))  # the squash commit when there is no merge commit

    def test_nested_groups_are_one_encoded_segment(self):
        host, transport = self.host({f"{GL}/projects/acme%2Fplatform%2Fapi/merge_requests/12": "gitlab/mr_opened.json"})
        self.assertEqual(host.change_request("acme/platform/api", 12).state, Read.FOUND)
        self.assertEqual(transport.urls, [f"{GL}/projects/acme%2Fplatform%2Fapi/merge_requests/12"])
        self.assertEqual(host.remote("acme/platform/api"), "https://gitlab.com/acme/platform/api.git")

    def test_trap06_404_absent_and_the_rest_unreadable(self):
        url = f"{GL}/projects/acme%2Fapi/merge_requests/12"
        host, _ = self.host({url: "gitlab/mr_not_found.json"})
        self.assertEqual(host.change_request("acme/api", 12).state, Read.ABSENT)
        self.assertEqual(host.incompleteness, [])
        cases = {
            "rate limited": ("gitlab/rate_limited.json", "rate limited"),
            "forbidden": ("gitlab/forbidden.json", "HTTP 403"),
            "unauthorized": ("gitlab/unauthorized.json", "HTTP 401"),
            "server error": ("gitlab/server_error.json", "HTTP 500"),
            "malformed": ("gitlab/malformed.json", "not valid JSON"),
            "no diff refs": ("gitlab/mr_no_diff_refs.json", "diff_refs"),
            "timeout": (TimeoutError(), "did not answer in time"),
        }
        for name, (route, expect) in cases.items():
            with self.subTest(name):
                host, _ = self.host({url.replace("/12", "/13") if name == "no diff refs" else url: route})
                res = host.change_request("acme/api", 13 if name == "no diff refs" else 12)
                self.assertEqual(res.state, Read.UNREADABLE)
                self.assertIn(expect, res.detail)
                self.assertEqual(self.kinds(host), [("read_unreadable", True)])
                self.assertIn("merge request !", host.incompleteness[0].subject)

    def test_trap07_changes_count_of_1000_plus_is_counted_page_by_page(self):
        base = f"{GL}/projects/acme%2Fapi/merge_requests/1001"
        routes = {base: "gitlab/mr_many_changes.json", f"{base}/diffs?per_page=100": "gitlab/mr_1001_diffs_page01.json"}
        for n in range(2, 12):
            routes[f"{base}/diffs?per_page=100&page={n}"] = f"gitlab/mr_1001_diffs_page{n:02d}.json"
        host, transport = self.host(routes)
        cr = ChangeRequest.from_read(host.change_request("acme/api", 1001))
        self.assertEqual(cr.file_count, 1001)
        self.assertEqual(len(transport.requests), 12)

    def test_trap09_branch_slash_is_encoded(self):
        host, transport = self.host({f"{GL}/projects/acme%2Fapi/repository/branches/release%2F1.x": "gitlab/branch_release_1x.json"})
        self.assertEqual(host.branch_head("acme/api", "release/1.x").text, B)
        self.assertEqual(transport.urls, [f"{GL}/projects/acme%2Fapi/repository/branches/release%2F1.x"])
        gone, _ = self.host({f"{GL}/projects/acme%2Fapi/repository/branches/nope": "gitlab/branch_not_found.json"})
        self.assertEqual(gone.branch_head("acme/api", "nope").state, Read.ABSENT)

    def test_token_goes_only_in_the_authorization_header(self):
        host, transport = self.host({f"{GL}/projects/acme%2Fapi/merge_requests/12": "gitlab/mr_opened.json"},
                                    env={"GITLAB_TOKEN": SECRET})
        host.change_request("acme/api", 12)
        (_, url, headers), = transport.requests
        self.assertEqual(headers["Authorization"], f"Bearer {SECRET}")
        self.assertNotIn(SECRET, url)
        self.assertNotIn("PRIVATE-TOKEN", headers)

    def test_a_named_token_variable_and_a_cli_login(self):
        host, transport = self.host({f"{GL}/projects/acme%2Fapi/merge_requests/12": "gitlab/mr_opened.json"},
                                    env={"MY_GL": "abcdefgh12345"}, token_env="MY_GL")
        host.change_request("acme/api", 12)
        self.assertEqual(transport.requests[0][2]["Authorization"], "Bearer abcdefgh12345")
        cli = FakeTransport({f"{GL}/projects/acme%2Fapi/merge_requests/12": "gitlab/mr_opened.json"}, cli_login=True)
        GitLab(transport=cli, env={"GITLAB_TOKEN": SECRET}).change_request("acme/api", 12)
        self.assertNotIn("Authorization", cli.requests[0][2])  # glab logs in by itself


# ------------------------------------------------------------------------ Bitbucket


class BitbucketTests(HostCase):
    def routes(self):
        pr = f"{BB}/repositories/acme/web/pullrequests"
        commit = f"{BB}/repositories/acme/web/commit"
        return {
            f"{pr}/21": "bitbucket/pr_open.json",
            f"{pr}/21/diffstat?pagelen=100": "bitbucket/diffstat_21.json",
            f"{commit}/{H[:12]}": f"bitbucket/commit_{H[:12]}.json",
            f"{commit}/{B[:12]}": f"bitbucket/commit_{B[:12]}.json",
            f"{pr}/8": "bitbucket/pr_merged.json",
            f"{pr}/8/diffstat?pagelen=100": "bitbucket/diffstat_8.json",
            f"{pr}/8/commits?pagelen=100": "bitbucket/pr_8_commits.json",
            f"{commit}/{M[:12]}": f"bitbucket/commit_{M[:12]}.json",
            f"{commit}/{Q[:12]}": f"bitbucket/commit_{Q[:12]}.json",
            f"{commit}/{P[:12]}": f"bitbucket/commit_{P[:12]}.json",
            f"{pr}/301": "bitbucket/pr_301.json",
            f"{pr}/301/diffstat?pagelen=100": "bitbucket/diffstat_301_page1.json",
            f"{pr}/301/diffstat?pagelen=100&page=2": "bitbucket/diffstat_301_page2.json",
            f"{pr}/301/diffstat?pagelen=100&page=3": "bitbucket/diffstat_301_page3.json",
            f"{pr}/301/diffstat?pagelen=100&page=4": "bitbucket/diffstat_301_page4.json",
        }

    def host(self, routes=None, **kw):
        transport = FakeTransport(self.routes() if routes is None else routes)
        return Bitbucket(transport=transport, **kw), transport

    def test_open_pull_request_expands_the_short_hashes(self):
        host, transport = self.host()
        cr = ChangeRequest.from_read(host.change_request("acme/web", 21))
        self.assertEqual(
            (cr.number, cr.title, cr.state, cr.base_ref, cr.head_ref, cr.base_sha, cr.head_sha, cr.merge_sha, cr.file_count),
            (21, "Add the export button", "open", "main", "feature/export", B, H, None, 3),
        )
        self.assertEqual(cr.url, "https://bitbucket.org/acme/web/pull-requests/21")
        self.assertIn(f"{BB}/repositories/acme/web/commit/{H[:12]}", transport.urls)  # full hashes came from the commit endpoint
        self.assertEqual(host.incompleteness, [])

    def test_merged_pull_request(self):
        host, _ = self.host()
        cr = ChangeRequest.from_read(host.change_request("acme/web", 8))
        self.assertEqual((cr.state, cr.merge_sha, cr.base_sha, cr.head_sha, cr.file_count), ("merged", M, P, Q, 4))

    def test_trap07_diffstat_is_followed_through_every_next_link(self):
        host, transport = self.host()
        cr = ChangeRequest.from_read(host.change_request("acme/web", 301))
        self.assertEqual(cr.file_count, 301)  # pages of 100, 100, 100 and 1
        stat_urls = [u for u in transport.urls if "diffstat" in u]
        self.assertEqual(len(stat_urls), 4)

    def test_a_missing_diffstat_page_is_never_a_smaller_count(self):
        routes = self.routes()
        routes[f"{BB}/repositories/acme/web/pullrequests/301/diffstat?pagelen=100&page=3"] = "bitbucket/rate_limited.json"
        host, _ = self.host(routes)
        res = host.change_request("acme/web", 301)
        self.assertEqual(res.state, Read.UNREADABLE)
        self.assertIn("rate limited", res.detail)
        self.assertEqual(self.kinds(host), [("read_unreadable", True)])

    def test_trap06_404_is_absent_and_the_rest_are_unreadable(self):
        pr = f"{BB}/repositories/acme/web/pullrequests/999"
        host, _ = self.host({pr: "bitbucket/pr_not_found.json"})
        self.assertEqual(host.change_request("acme/web", 999).state, Read.ABSENT)
        self.assertEqual(host.incompleteness, [])
        cases = {
            "rate limited": ("bitbucket/rate_limited.json", "rate limited"),
            "unauthorized": ("bitbucket/unauthorized.json", "HTTP 401"),
            "bad gateway": ("bitbucket/server_error.json", "HTTP 502"),
            "malformed": ("bitbucket/malformed.json", "not valid JSON"),
            "timeout": (TimeoutError(), "did not answer in time"),
        }
        for name, (route, expect) in cases.items():
            with self.subTest(name):
                host, _ = self.host({pr: route})
                res = host.change_request("acme/web", 999)
                self.assertEqual(res.state, Read.UNREADABLE)
                self.assertIn(expect, res.detail)
                self.assertEqual(self.kinds(host), [("read_unreadable", True)])

    def test_a_commit_the_host_cannot_expand_makes_the_request_unreadable(self):
        routes = self.routes()
        routes[f"{BB}/repositories/acme/web/commit/{H[:12]}"] = "bitbucket/server_error.json"
        host, _ = self.host(routes)
        self.assertEqual(host.change_request("acme/web", 21).state, Read.UNREADABLE)

    def test_trap09_branch_slash_is_encoded(self):
        host, transport = self.host({f"{BB}/repositories/acme/web/refs/branches/release%2F1.x": "bitbucket/branch_release_1x.json"})
        self.assertEqual(host.branch_head("acme/web", "release/1.x").text, B)
        self.assertEqual(transport.urls, [f"{BB}/repositories/acme/web/refs/branches/release%2F1.x"])
        gone, _ = self.host({f"{BB}/repositories/acme/web/refs/branches/nope": "bitbucket/branch_not_found.json"})
        self.assertEqual(gone.branch_head("acme/web", "nope").state, Read.ABSENT)

    def test_authorization_header_forms(self):
        host, transport = self.host(env={"BITBUCKET_TOKEN": SECRET})
        host.change_request("acme/web", 21)
        self.assertTrue(all(h["Authorization"] == f"Bearer {SECRET}" for _, _, h in transport.requests))
        pair = "devone:app-password-123456"
        host, transport = self.host(env={"BITBUCKET_TOKEN": pair})
        host.change_request("acme/web", 21)
        expected = "Basic " + base64.b64encode(pair.encode()).decode()
        self.assertTrue(all(h["Authorization"] == expected for _, _, h in transport.requests))
        self.assertTrue(all(pair not in u for u in transport.urls))


# ------------------------------------------------------------------------ plain, registry, tokens


class PlainAndRegistry(HostCase):
    def test_plain_host_has_no_change_requests(self):
        host = get_host("git", remotes={"acme/web": "file:///x"})
        self.assertEqual(host.kind, "git")
        self.assertEqual(host.remote("acme/web"), "file:///x")
        with self.assertRaises(HostUnsupported):
            host.remote("acme/other")
        for call in (lambda: host.change_request("acme/web", 1), lambda: host.title_of("acme/web", 1), lambda: host.branch_head("acme/web", "main")):
            with self.assertRaises(HostUnsupported):
                call()

    def test_registry(self):
        self.assertEqual(set(hosts.hosts()), {"github", "gitlab", "bitbucket", "git"})
        self.assertIsInstance(get_host("gitlab"), GitLab)
        with self.assertRaises(ValueError) as caught:
            get_host("azure")
        self.assertIn("github", str(caught.exception))


class Trap10Tokens(HostCase):
    """The host half of trap 10: a fake token value in the environment never leaves in a URL,
    an exception, a log record, the cache directory or a report."""

    def test_trap10_token_is_only_in_the_authorization_header(self):
        records = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        handler = Capture(level=logging.DEBUG)
        root = logging.getLogger()
        old = root.level
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        self.addCleanup(root.removeHandler, handler)
        self.addCleanup(root.setLevel, old)

        analysis = Analysis(change={"kind": "pr"}, ref="a" * 40)
        outputs = []
        all_requests = []
        env = {"GITLAB_TOKEN": SECRET, "BITBUCKET_TOKEN": SECRET, "GITHUB_TEST_TOKEN": SECRET}
        failing = [
            OSError(f"connection to https://x:{SECRET}@gitlab.com failed, header Authorization: Bearer {SECRET}"),
            TransportError(f"glab said {SECRET}"),
            TimeoutError(SECRET),
            ValueError(f"bad url token={SECRET}"),
        ]
        for kind, repo, cls, extra in (
            ("gitlab", "acme/api", GitLab, {}), ("bitbucket", "acme/web", Bitbucket, {}),
            ("github", "acme/web", GitHub, {"token_env": "GITHUB_TEST_TOKEN"}),
        ):
            for exc in failing:
                transport = FakeTransport({}, cli_login=False)
                transport.routes = {}

                def boom(method, url, headers=None, _t=transport, _e=exc):
                    _t.requests.append((method, url, dict(headers or {})))
                    raise _e

                host = cls(transport=boom, env=env, record_to=analysis, **extra)
                res = host.change_request(repo, 12)
                self.assertEqual(res.state, Read.UNREADABLE)
                outputs.append(res.detail)
                all_requests.append(transport.requests)
            # successful requests too
            fine = FakeTransport({f"{GL}/projects/acme%2Fapi/merge_requests/12": "gitlab/mr_opened.json"}) if kind == "gitlab" else None
            if fine is not None:
                cls(transport=fine, env=env, record_to=analysis).change_request(repo, 12)
                all_requests.append(fine.requests)

        outputs.append(json.dumps(analysis.to_dict()) if analysis.status == "completed" else "")
        outputs.extend(r.note() for r in analysis.incompleteness)
        outputs.extend(json.dumps(r.to_dict()) for r in analysis.incompleteness)
        for text in outputs:
            self.assertNotIn(SECRET, text)
        for requests in all_requests:
            for method, url, headers in requests:
                self.assertNotIn(SECRET, url)
                for name, value in headers.items():
                    if name != "Authorization":
                        self.assertNotIn(SECRET, value)
        self.assertNotIn(SECRET, "\n".join(records))
        sent = [h for reqs in all_requests for _, _, h in reqs if "Authorization" in h]
        self.assertTrue(sent)  # the token was used, and only there



# ------------------------------------------------------------------------ default transports (no network)


class CliTransportTests(unittest.TestCase):
    def test_parse_status_headers_and_body(self):
        out = b'HTTP/2.0 404 Not Found\r\nContent-Type: application/json\r\nX-GitHub-Request-Id: ab12\r\n\r\n{"message":"Not Found"}'
        status, headers, body = CliTransport.parse(out, "gh: Not Found (HTTP 404)", 1)
        self.assertEqual((status, headers["Content-Type"], body), (404, "application/json", b'{"message":"Not Found"}'))
        status, _, body = CliTransport.parse(b"HTTP/1.1 200 OK\nLink: <x>; rel=\"next\"\n\n[]", "", 0)
        self.assertEqual((status, body), (200, b"[]"))
        with self.assertRaises(TransportError) as caught:
            CliTransport.parse(b"", "To get started with GitHub CLI, please run:  gh auth login", 4)
        self.assertIn("gh auth login", str(caught.exception))

    def test_argv_never_carries_the_credential_and_the_endpoint_is_a_path(self):
        transport = CliTransport("gh", [GH], timeout=5)
        captured = {}

        def fake_run(argv, **kw):
            captured["argv"] = argv
            return subprocess.CompletedProcess(argv, 0, b"HTTP/2.0 200 OK\r\n\r\n[]", b"")

        with mock.patch("subprocess.run", fake_run):
            status, _, body = transport("GET", f"{GH}/repos/acme/web/branches/release%2F1.x?x=1",
                                        {"Accept": "application/json", "Authorization": f"Bearer {SECRET}"})
        self.assertEqual(status, 200)
        argv = captured["argv"]
        self.assertEqual(argv[:5], ["gh", "api", "-i", "-X", "GET"])
        self.assertEqual(argv[-1], "repos/acme/web/branches/release%2F1.x?x=1")
        self.assertNotIn(SECRET, " ".join(argv))
        self.assertNotIn("Authorization", " ".join(argv))
        with self.assertRaises(TransportError):
            transport("GET", "https://elsewhere.example.invalid/x")

    def test_timeout_and_missing_binary(self):
        transport = CliTransport("gh", [GH], timeout=1)
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("gh", 1)):
            with self.assertRaises(TimeoutError):
                transport("GET", f"{GH}/x")
        with mock.patch("subprocess.run", side_effect=FileNotFoundError("gh")):
            with self.assertRaises(TransportError):
                transport("GET", f"{GH}/x")


class UrllibTransportTests(unittest.TestCase):
    def test_http_error_status_is_a_response_and_a_timeout_is_a_timeout(self):
        transport = UrllibTransport(timeout=2)

        class Err(urllib.error.HTTPError):
            def __init__(self):
                super().__init__("https://x.invalid", 404, "Not Found", {"X-A": "1"}, None)

            def read(self, n=-1):
                return b'{"message":"nope"}'

        with mock.patch.object(transport._opener, "open", side_effect=Err()):
            status, headers, body = transport("GET", "https://x.invalid/a")
        self.assertEqual((status, headers.get("X-A"), body), (404, "1", b'{"message":"nope"}'))
        with mock.patch.object(transport._opener, "open", side_effect=urllib.error.URLError(TimeoutError("t"))):
            with self.assertRaises(TimeoutError):
                transport("GET", "https://x.invalid/a")
        with mock.patch.object(transport._opener, "open", side_effect=urllib.error.URLError(ConnectionRefusedError("refused"))):
            with self.assertRaises(TransportError):
                transport("GET", "https://x.invalid/a")


if __name__ == "__main__":
    unittest.main()


class ReviewFixes(HostCase):
    def test_title_of_asks_once_and_only_needs_a_title(self):
        pr = f"{BB}/repositories/acme/web/pullrequests/21"
        transport = FakeTransport({pr: "bitbucket/pr_open.json"})  # no commit or diffstat route: asking for one fails the test
        self.assertEqual(Bitbucket(transport=transport).title_of("acme/web", 21).text, "Add the export button")
        self.assertEqual(transport.urls, [pr])
        gl = FakeTransport({f"{GL}/projects/acme%2Fapi/merge_requests/7": "gitlab/mr_merged.json"})
        self.assertEqual(GitLab(transport=gl).title_of("acme/api", 7).text, "Fix the rounding helper")
        self.assertEqual(len(gl.requests), 1)
        status, headers, body = load("github/pr_open.json")
        doc = json.loads(body)
        for key in ("head", "base", "merge_commit_sha"):
            doc.pop(key)  # a title is all it needs
        gh = FakeTransport({f"{GH}/repos/acme/web/pulls/12": (200, headers, json.dumps(doc).encode())})
        self.assertEqual(GitHub(transport=gh).title_of("acme/web", 12).text, "Filter invoices by status")
        for broken in ({"number": 12}, {"title": 5}):
            host = GitHub(transport=FakeTransport({f"{GH}/repos/acme/web/pulls/12": (200, headers, json.dumps(broken).encode())}))
            self.assertEqual(host.title_of("acme/web", 12).state, Read.UNREADABLE)

    def test_a_list_longer_than_the_page_limit_is_unreadable_not_cut(self):
        first = f"{GH}/repos/acme/web/pulls/301/files?per_page=100"
        routes = {f"{GH}/repos/acme/web/pulls/301": "github/pr_301_no_count.json"}
        url = first
        for n in range(2, 8):
            following = f"{GH}/repos/acme/web/pulls/301/files?per_page=100&page={n}"
            routes[url] = (200, {"Link": f'<{following}>; rel="next"'}, b'[{"filename": "f"}]')
            url = following
        routes[url] = (200, {}, b"[]")
        with mock.patch.object(hosts, "MAX_PAGES", 3):
            host = GitHub(transport=FakeTransport(routes))
            res = host.change_request("acme/web", 301)
        self.assertEqual(res.state, Read.UNREADABLE)
        self.assertIn("more than 3 pages", res.detail)
        with mock.patch.object(hosts, "MAX_PAGES", 20):  # the same list within the limit is read in full
            self.assertEqual(ChangeRequest.from_read(GitHub(transport=FakeTransport(routes)).change_request("acme/web", 301)).file_count, 6)

    def test_bitbucket_refuses_a_commit_that_does_not_start_with_the_short_hash(self):
        routes = BitbucketTests.routes(self)
        wrong = dict(load(f"bitbucket/commit_{H[:12]}.json")[1])
        status, headers, body = load(f"bitbucket/commit_{H[:12]}.json")
        doc = json.loads(body)
        doc["hash"] = "9" * 40  # a different commit than the 222222222222 that was asked for
        routes[f"{BB}/repositories/acme/web/commit/{H[:12]}"] = (200, headers, json.dumps(doc).encode())
        res = Bitbucket(transport=FakeTransport(routes)).change_request("acme/web", 21)
        self.assertEqual(res.state, Read.UNREADABLE)
        self.assertIn("does not match", res.detail)

    def test_a_token_is_never_sent_over_a_connection_that_is_not_https(self):
        transport = FakeTransport({"http://gitlab.internal/api/v4/projects/acme%2Fapi/merge_requests/1": "gitlab/mr_opened.json"})
        host = GitLab(transport=transport, env={"GITLAB_TOKEN": SECRET}, api_url="http://gitlab.internal/api/v4")
        res = host.change_request("acme/api", 1)
        self.assertEqual(res.state, Read.UNREADABLE)
        self.assertIn("not https", res.detail)
        self.assertEqual(transport.requests, [])
        self.assertNotIn(SECRET, res.detail)
        anonymous = GitLab(transport=transport, env={}, api_url="http://gitlab.internal/api/v4")  # nothing to leak: allowed
        self.assertEqual(anonymous.change_request("acme/api", 1).state, Read.UNREADABLE)  # (the iid in the fixture is 12)
        self.assertEqual(len(transport.requests), 1)

    def test_the_basic_header_and_its_base64_never_reach_a_record(self):
        pair = "devone:app-password-123456"
        b64 = base64.b64encode(pair.encode()).decode()
        analysis = Analysis(change={"kind": "pr"}, ref="a" * 40)

        def boom(method, url, headers=None):
            raise OSError(f"connection reset; request had Authorization: Basic {b64} and credentials {b64}")

        host = Bitbucket(transport=boom, env={"BITBUCKET_TOKEN": pair}, record_to=analysis)
        res = host.change_request("acme/web", 21)
        self.assertEqual(res.state, Read.UNREADABLE)
        for text in (res.detail, json.dumps([r.to_dict() for r in analysis.incompleteness])):
            self.assertNotIn(b64, text)
            self.assertNotIn(pair, text)

    def test_commit_counts_of_merged_requests(self):
        status, headers, body = load("github/pr_merged.json")
        gh = GitHub(transport=FakeTransport({f"{GH}/repos/acme/web/pulls/7": (status, headers, body)}))
        self.assertEqual(ChangeRequest.from_read(gh.change_request("acme/web", 7)).commit_count, 3)
        gl_routes = {f"{GL}/projects/acme%2Fapi/merge_requests/7": "gitlab/mr_merged.json",
                     f"{GL}/projects/acme%2Fapi/merge_requests/7/commits?per_page=100": "gitlab/mr_7_commits.json"}
        self.assertEqual(ChangeRequest.from_read(GitLab(transport=FakeTransport(gl_routes)).change_request("acme/api", 7)).commit_count, 3)
        bb = BitbucketTests.routes(self)
        self.assertEqual(ChangeRequest.from_read(Bitbucket(transport=FakeTransport(bb)).change_request("acme/web", 8)).commit_count, 3)
        # an open request is not asked: no commits route is needed
        opened = FakeTransport({f"{GL}/projects/acme%2Fapi/merge_requests/12": "gitlab/mr_opened.json"})
        self.assertIsNone(ChangeRequest.from_read(GitLab(transport=opened).change_request("acme/api", 12)).commit_count)

    def test_commit_lists_are_paginated_and_failures_are_unreadable(self):
        base = f"{GL}/projects/acme%2Fapi/merge_requests/7"
        first = f"{base}/commits?per_page=100"
        page = lambda n, nxt: (200, {"X-Next-Page": nxt}, json.dumps([{"id": f"{i:040x}"} for i in range(n)]).encode())
        routes = {base: "gitlab/mr_merged.json", first: page(100, "2"), f"{first}&page=2": page(100, "3"), f"{first}&page=3": page(7, "")}
        self.assertEqual(ChangeRequest.from_read(GitLab(transport=FakeTransport(routes)).change_request("acme/api", 7)).commit_count, 207)
        routes[f"{first}&page=3"] = "gitlab/server_error.json"
        host = GitLab(transport=FakeTransport(routes))
        self.assertEqual(host.change_request("acme/api", 7).state, Read.UNREADABLE)
        self.assertEqual(self.kinds(host), [("read_unreadable", True)])
        routes[first] = (200, {}, b"[]")  # a merged request with no commits is not believable
        self.assertEqual(GitLab(transport=FakeTransport(routes)).change_request("acme/api", 7).state, Read.UNREADABLE)

    def test_token_cache_walk_covers_a_cache_that_was_used(self):
        from reachmap.gitlayer import Git
        from tests import support_git as sg
        repos = sg.fixture_repos()
        with tempfile.TemporaryDirectory() as cache, mock.patch.dict(os.environ, {"GITLAB_TOKEN": SECRET, "BITBUCKET_TOKEN": SECRET}):
            git = Git(cache, env=sg.isolated_env(Path(cache) / "home"))
            url = sg.remote_url(repos["acme/web"])
            sha = git.pin(url, "main")
            git.file_at(url, "package.json", sha)
            git.checkout(url, sha)
            git.grep(git.checkout(url, sha), "import", fixed=True, result_cap=3)
            git.heads(f"https://x-access-token:{SECRET}@example.invalid/acme/web.git", ["*"])  # refused by git, cache created
            walked = 0
            for dirpath, dirnames, filenames in os.walk(cache):
                for name in (*dirnames, *filenames):
                    self.assertNotIn(SECRET, name)
                    path = Path(dirpath) / name
                    if path.is_file() and not path.is_symlink():
                        walked += 1
                        self.assertNotIn(SECRET.encode(), path.read_bytes(), str(path))
            self.assertGreater(walked, 20)  # a real cache: configs, refs, objects, a checkout and a manifest
            sg.remove_tree(cache)  # the checkout is read only


class UrllibRedirects(unittest.TestCase):
    """The real UrllibTransport (HostCase patches its constructor, so this is a plain TestCase);
    the network is replaced by a handler that answers every request with a redirect."""

    def setUp(self):
        redact.forget_secrets()
        self.addCleanup(redact.forget_secrets)

    def test_a_redirect_is_unreadable_never_absent_and_never_followed(self):
        class FakeHTTPS(urllib.request.HTTPSHandler):
            def __init__(self):
                super().__init__()
                self.seen = []

            def https_open(self, req):
                self.seen.append((req.full_url, dict(req.header_items())))
                headers = email.message_from_string("Location: https://other.example.invalid/steal\n\n")
                response = urllib.response.addinfourl(io.BytesIO(b""), headers, req.full_url, 302)
                response.msg = "Found"
                return response

        fake = FakeHTTPS()
        transport = UrllibTransport(2, handlers=[fake])
        host = GitLab(transport=transport, env={"GITLAB_TOKEN": SECRET}, api_url="https://gitlab.example.com/api/v4")
        res = host.change_request("acme/api", 1)
        self.assertEqual(res.state, Read.UNREADABLE)
        self.assertIn("redirect refused", res.detail)
        self.assertIn("other.example.invalid", res.detail)
        self.assertEqual(len(fake.seen), 1)  # the other host was never asked
        self.assertEqual(fake.seen[0][1]["Authorization"], f"Bearer {SECRET}")
        self.assertNotIn(SECRET, res.detail)
        self.assertEqual([(r.kind, r.blocks_selection) for r in host.incompleteness], [("read_unreadable", True)])


class CliHostnameTests(unittest.TestCase):
    def test_argv_names_the_host_for_an_enterprise_instance(self):
        transport = CliTransport("gh", ["https://ghe.example.com/api/v3"], timeout=5, hostname="ghe.example.com")
        captured = {}

        def fake_run(argv, **kw):
            captured["argv"] = argv
            return subprocess.CompletedProcess(argv, 0, b"HTTP/2.0 200 OK\r\n\r\n[]", b"")

        with mock.patch("subprocess.run", fake_run):
            transport("GET", "https://ghe.example.com/api/v3/repos/acme/web/pulls/12", {"Accept": "application/json"})
        self.assertEqual(captured["argv"][:6], ["gh", "api", "--hostname", "ghe.example.com", "-i", "-X"])
        self.assertEqual(captured["argv"][-1], "repos/acme/web/pulls/12")

    def test_default_transports_get_a_hostname_only_for_a_non_default_api(self):
        with mock.patch("reachmap.hosts.github.shutil.which", return_value="/x/gh"):
            default = GitHub().default_transport()
            enterprise = GitHub(api_url="https://ghe.example.com/api/v3").default_transport()
        self.assertIsNone(default.hostname)
        self.assertEqual(enterprise.hostname, "ghe.example.com")
        self.assertEqual(enterprise.endpoint("https://ghe.example.com/api/v3/repos/a/b/pulls/1"), "repos/a/b/pulls/1")
        with mock.patch("reachmap.hosts.gitlab.shutil.which", return_value="/x/glab"):
            self.assertIsNone(GitLab().default_transport().hostname)
            own = GitLab(api_url="https://gitlab.example.com/api/v4").default_transport()
        self.assertEqual(own.hostname, "gitlab.example.com")
        with mock.patch("reachmap.hosts.gitlab.shutil.which", return_value=None):
            self.assertIsInstance(GitLab().default_transport(), UrllibTransport)  # no glab: token and urllib
