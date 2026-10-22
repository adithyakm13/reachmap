"""The report side of the runner API and its static files, without running an analysis: the list, the JSON and the page, the
decision endpoint's refusals, path traversal in report names and in static files, the minimal page."""

from __future__ import annotations

import json
import os
import shutil
import unittest
from pathlib import Path

from reachmap.report import assets, html
from reachmap.runner import server
from tests import support_report as sr
from tests.support_runner import Live

MARK = "SECRET-OUTSIDE-9f3a"


class Reports(unittest.TestCase):
    def setUp(self) -> None:
        self.live = Live()
        self.live.__enter__()
        self.addCleanup(self.live.__exit__, None, None, None)
        self.reports = self.live.reports
        self.reports.mkdir(parents=True, exist_ok=True)
        for name in ("complete", "failed", "timed_out"):
            shutil.copy(sr.FIXTURES / f"{name}.json", self.reports / f"{name}-r1.json")
        (self.live.root / "secret.json").write_text(json.dumps({"mark": MARK}), encoding="utf-8")

    def test_the_list_has_name_status_revision_key_and_created(self) -> None:
        r = self.live.get("/api/reports")
        self.assertEqual(r.status, 200)
        listing = r.json()["reports"]
        self.assertEqual(sorted(x["name"] for x in listing), ["complete-r1", "failed-r1", "timed_out-r1"])
        by_name = {x["name"]: x for x in listing}
        self.assertEqual(by_name["failed-r1"]["status"], "failed")
        self.assertEqual(by_name["timed_out-r1"]["status"], "timed_out")
        self.assertEqual(by_name["complete-r1"]["status"], "completed")
        for item in listing:
            self.assertEqual(set(item), {"name", "key", "revision", "status", "created", "head_sha", "areas", "blocking_records"})
        # the list of the workspace shows the head commit and the counts of a run that completed
        done = by_name["complete-r1"]
        self.assertEqual(done["head_sha"], sr.fixture("complete")["change"]["head_sha"])
        self.assertEqual((done["areas"], done["blocking_records"]), (3, 1))
        # a run that did not complete has no count: the list says nothing of it (a partial list is never countable)
        for name in ("failed-r1", "timed_out-r1"):
            self.assertEqual((by_name[name]["areas"], by_name[name]["blocking_records"]), (None, None), name)

    def test_the_counts_of_the_list_are_integers_or_nothing(self) -> None:
        odd = sr.fixture("complete")
        odd["counts"] = {"areas": "3", "blocking_records": True}
        odd["change"]["head_sha"] = ["x"]
        (self.reports / "odd-r1.json").write_text(json.dumps(odd), encoding="utf-8")
        item = {x["name"]: x for x in self.live.get("/api/reports").json()["reports"]}["odd-r1"]
        self.assertEqual((item["areas"], item["blocking_records"], item["head_sha"]), (None, None, None))

    def test_a_file_that_is_not_a_report_is_left_out_of_the_list(self) -> None:
        (self.reports / "junk.json").write_text("not json", encoding="utf-8")
        (self.reports / "notes.txt").write_text("x", encoding="utf-8")
        (self.reports / ".hidden-r1.json").write_text((sr.FIXTURES / "complete.json").read_text(encoding="utf-8"), encoding="utf-8")
        names = sorted(x["name"] for x in self.live.get("/api/reports").json()["reports"])
        self.assertEqual(names, ["complete-r1", "failed-r1", "timed_out-r1"])

    def test_the_report_json_and_the_page(self) -> None:
        r = self.live.get("/api/reports/complete-r1")
        self.assertEqual(r.status, 200)
        self.assertEqual(r.json()["status"], "completed")
        page = self.live.get("/api/reports/complete-r1.html")
        self.assertEqual(page.status, 200)
        self.assertEqual(page.headers["content-type"], "text/html; charset=utf-8")
        text = page.body.decode("utf-8")
        self.assertTrue(text.lstrip().lower().startswith("<!doctype html"))
        self.assertEqual(page.headers["content-security-policy"], server.report_policy())
        meta = html.policy(assets.read("report.js"))
        self.assertIn(f'content="{meta}"', text)  # the page's own meta tag is the M10 policy; the header is that and the ancestors rule
        self.assertTrue(server.report_policy().startswith(meta))
        for name in ("failed-r1", "timed_out-r1"):
            self.assertEqual(self.live.get(f"/api/reports/{name}.html").status, 200)

    def test_an_unknown_report_is_404_and_a_broken_one_is_not_served(self) -> None:
        (self.reports / "broken-r1.json").write_text("{not json", encoding="utf-8")
        for name in ("nope", "broken-r1", "complete-r1.json", "complete"):
            for suffix in ("", ".html"):
                r = self.live.get(f"/api/reports/{name}{suffix}")
                self.assertEqual(r.status, 404, name + suffix)
                self.assertEqual(r.json(), {"error": "not_found"})

    def test_traversal_in_a_report_name(self) -> None:
        for path in ("/api/reports/..%2fsecret", "/api/reports/%2e%2e%2fsecret", "/api/reports/../secret", "/api/reports/..%2f..%2fsecret.json",
                     "/api/reports/%2e%2e/secret", "/api/reports/..\\secret", "/api/reports/..%5csecret", "/api/reports/%2fetc%2fpasswd", "/api/reports//etc/passwd",
                     "/api/reports/%00", "/api/reports/complete-r1%00", "/api/reports/%252e%252e%252fsecret", "/api/reports/.reachmap", "/api/reports/.reachmap%2flock",
                     "/api/reports/..%2fsecret.html", "/api/reports/." + "/" * 3 + "secret", "/api/reports/" + "a" * 5000):
            with self.subTest(path=path):
                r = self.live.get(path)
                self.assertIn(r.status, (400, 404))
                self.assertNotIn(MARK.encode(), r.body)
                self.assertNotIn(b"root:", r.body)

    @unittest.skipUnless(hasattr(os, "symlink"), "no symlinks")
    def test_a_report_that_is_a_link_is_not_followed(self) -> None:
        outside = self.live.root / "secret.json"
        shutil.copy(sr.FIXTURES / "complete.json", outside)
        outside.write_text(outside.read_text(encoding="utf-8").replace('"status"', f'"{MARK}": 1, "status"', 1), encoding="utf-8")
        os.symlink(outside, self.reports / "link-r1.json")
        for path in ("/api/reports/link-r1", "/api/reports/link-r1.html"):
            r = self.live.get(path)
            self.assertEqual(r.status, 404)
            self.assertNotIn(MARK.encode(), r.body)
        self.assertNotIn("link-r1", [x["name"] for x in self.live.get("/api/reports").json()["reports"]])

    def test_the_report_responses_carry_the_common_headers_and_forbid_framing(self) -> None:
        for path in ("/api/reports", "/api/reports/complete-r1", "/api/reports/complete-r1.html", "/api/reports/nope"):
            r = self.live.get(path)
            for name, value in {"x-content-type-options": "nosniff", "cache-control": "no-store", "referrer-policy": "no-referrer"}.items():
                self.assertEqual(r.headers.get(name), value, f"{path}: {name}")
        page = self.live.get("/api/reports/complete-r1.html")
        self.assertIn("frame-ancestors 'none'", page.headers["content-security-policy"])

    def test_names_are_matched_exactly_not_by_the_case_insensitivity_of_a_file_system(self) -> None:
        for path in ("/api/reports/COMPLETE-R1", "/api/reports/Complete-r1", "/api/reports/COMPLETE-R1.html"):
            self.assertEqual(self.live.get(path).status, 404, path)
        self.assertEqual(self.live.get("/api/reports/complete-r1").status, 200)
        self.assertNotIn("COMPLETE-R1", [x["name"] for x in self.live.get("/api/reports").json()["reports"]])

    @unittest.skipUnless(hasattr(os, "link"), "no hard links")
    def test_a_report_with_a_second_hard_link_is_not_served(self) -> None:
        outside = self.live.root / "elsewhere.json"
        shutil.copy(sr.FIXTURES / "complete.json", outside)
        os.link(outside, self.reports / "linked-r1.json")
        self.assertEqual(self.live.get("/api/reports/linked-r1").status, 404)
        self.assertEqual(self.live.get("/api/reports/linked-r1.html").status, 404)
        self.assertNotIn("linked-r1", [x["name"] for x in self.live.get("/api/reports").json()["reports"]])

    def test_decisions_on_a_report_without_review_data_or_a_run_that_did_not_complete(self) -> None:
        good = {"target": "a-" + "1" * 16, "verdict": "test", "author": "Quinn"}
        r = self.live.post("/api/reports/timed_out-r1/decisions", good)
        self.assertEqual((r.status, r.json()), (409, {"error": "not_completed"}))
        r = self.live.post("/api/reports/failed-r1/decisions", good)
        self.assertEqual((r.status, r.json()), (409, {"error": "not_completed"}))
        r = self.live.post("/api/reports/nope/decisions", good)
        self.assertEqual((r.status, r.json()), (404, {"error": "not_found"}))
        self.assertFalse((self.reports / ".reachmap" / "decisions").exists())


class Static(unittest.TestCase):
    def make(self) -> tuple[Path, Path]:
        root = Path(self._tmp)
        static = root / "static"
        (static / "nested").mkdir(parents=True)
        (static / "index.html").write_text("<!doctype html><title>workspace</title><script src=\"/app.js\"></script>", encoding="utf-8")
        (static / "app.js").write_text("// app", encoding="utf-8")
        (static / "nested" / "a.css").write_text("a{}", encoding="utf-8")
        (static / "data.bin").write_bytes(b"\x00\x01")
        (static / ".hidden.js").write_text("// hidden", encoding="utf-8")
        (root / "secret.txt").write_text(MARK, encoding="utf-8")
        (root / "outside").mkdir()
        (root / "outside" / "x.js").write_text(MARK, encoding="utf-8")
        if hasattr(os, "symlink"):
            os.symlink(root / "secret.txt", static / "evil.js")
            os.symlink(root / "outside", static / "linkdir")
            os.symlink(static / "app.js", static / "inside.js")  # even a link to a file inside is not followed
        return static, root

    def setUp(self) -> None:
        import tempfile

        holder = tempfile.TemporaryDirectory(prefix="reachmap-static-")
        self.addCleanup(holder.cleanup)
        self._tmp = holder.name
        self.static, self.root = self.make()
        self.live = Live(static_dir=self.static)
        self.live.__enter__()
        self.addCleanup(self.live.__exit__, None, None, None)

    def test_the_workspace_files_are_served_when_present(self) -> None:
        r = self.live.get("/", token=False)
        self.assertEqual(r.status, 200)
        self.assertIn(b"<title>workspace</title>", r.body)
        self.assertEqual(self.live.get("/index.html", token=False).status, 200)
        js = self.live.get("/app.js", token=False)
        self.assertEqual((js.status, js.body), (200, b"// app"))
        self.assertTrue(js.headers["content-type"].startswith("text/javascript"))
        self.assertEqual(js.headers["x-content-type-options"], "nosniff")
        css = self.live.get("/nested/a.css", token=False)
        self.assertEqual(css.status, 200)
        self.assertTrue(css.headers["content-type"].startswith("text/css"))
        self.assertIn("connect-src 'self'", r.headers["content-security-policy"])

    def test_traversal_is_impossible(self) -> None:
        paths = [
            "/..%2fsecret.txt", "/%2e%2e%2fsecret.txt", "/%2e%2e/secret.txt", "/../secret.txt", "//secret.txt", "/nested/..%2f..%2fsecret.txt",
            "/nested/../../secret.txt", "/nested/%2e%2e/%2e%2e/secret.txt", "/%5c..%5csecret.txt", "/..%5csecret.txt", "/etc/passwd", "/%2fetc%2fpasswd",
            "//etc/passwd", "/%00", "/app.js%00.txt", "/%252e%252e%252fsecret.txt", "/nested/..", "/nested/../app.js", "/./app.js", "/nested/./a.css",
            "/.hidden.js", "/nested//a.css", "/..", "/%2e%2e", "/app.js/", "/app.js/..", f"/{self.root.name}/secret.txt", "/linkdir/x.js", "/evil.js",
            "/inside.js", "/linkdir", "/data.bin", "/\\app.js", "/..;/secret.txt", "/%c0%ae%c0%ae/secret.txt", "/nested/a.css/../../../secret.txt",
        ]
        for path in paths:
            with self.subTest(path=path):
                r = self.live.get(path, token=False)
                self.assertIn(r.status, (400, 404))
                self.assertNotIn(MARK.encode(), r.body)
                self.assertNotIn(b"root:", r.body)
                self.assertNotIn(b"// app", r.body)
                self.assertNotIn(b"// hidden", r.body)

    def test_the_api_namespace_is_never_static(self) -> None:
        (self.static / "api").mkdir()
        (self.static / "api" / "health").write_text("static-health", encoding="utf-8")
        r = self.live.get("/api/health")
        self.assertEqual(r.json(), {"ok": True})
        self.assertEqual(self.live.get("/api/nothing", token=False).status, 401)

    @unittest.skipUnless(hasattr(os, "link"), "no hard links")
    def test_a_file_with_a_second_hard_link_is_not_served(self) -> None:
        os.link(self.root / "secret.txt", self.static / "hard.txt")
        (self.static / "hard.js").write_text("// hard", encoding="utf-8")
        os.link(self.static / "hard.js", self.root / "hard-copy.js")
        for path in ("/hard.txt", "/hard.js"):
            r = self.live.get(path, token=False)
            self.assertEqual(r.status, 404, path)
            self.assertNotIn(MARK.encode(), r.body)

    def test_names_are_matched_exactly(self) -> None:
        for path in ("/APP.JS", "/App.js", "/NESTED/a.css", "/Index.html"):
            self.assertEqual(self.live.get(path, token=False).status, 404, path)

    def test_a_client_route_gets_the_index_page_so_that_a_reload_works(self) -> None:
        index = self.live.get("/index.html", token=False)
        for path in ("/reports/some-report-r1", "/reports/some-report-r1/areas/a-0123456789abcdef", "/reports/x/findings/f-0123456789abcdef", "/jobs",
                     "/reports", "/reports/a.b-r2"):
            with self.subTest(path=path):
                r = self.live.get(path, token=False)
                self.assertEqual(r.status, 200)
                self.assertEqual(r.body, index.body)
                self.assertTrue(r.headers["content-type"].startswith("text/html"))
                self.assertIn("connect-src 'self'", r.headers["content-security-policy"])
                self.assertEqual(r.headers["cache-control"], "no-store")

    def test_only_the_routes_of_the_workspace_fall_back_to_the_index_page(self) -> None:
        for path in ("/nothing", "/assets/missing.js", "/etc/passwd", "/reports/../secret.txt", "/reports/%2e%2e%2fsecret.txt", "/reports//x", "/reports/x/",
                     "/jobs/..", "/api-not", "/Reports/x", "/reports%2fx", "/reports/\\x", "/reports/x%00"):
            with self.subTest(path=path):
                r = self.live.get(path, token=False)
                self.assertIn(r.status, (400, 404))
                self.assertNotIn(b"<title>workspace</title>", r.body)
                self.assertNotIn(MARK.encode(), r.body)

    def test_the_api_never_falls_back_to_the_index_page(self) -> None:
        r = self.live.get("/api/reports/nothing-here", token=True)
        self.assertEqual(r.status, 404)
        self.assertEqual(r.json(), {"error": "not_found"})

    def test_a_client_route_is_not_a_file_for_a_method_that_is_not_get(self) -> None:
        for method in ("POST", "DELETE", "PUT", "HEAD"):
            r = self.live.request(method, "/reports/x", body=b"{}" if method != "HEAD" else None)
            self.assertIn(r.status, (401, 403, 404, 405), method)
            self.assertNotIn(b"<title>workspace</title>", r.body)

    def test_only_get_serves_files(self) -> None:
        for method in ("POST", "DELETE", "PUT"):
            r = self.live.request(method, "/app.js", body=b"{}")
            self.assertIn(r.status, (401, 403, 404, 405))
            self.assertNotEqual(r.body, b"// app")


class StyleNonce(unittest.TestCase):
    """The index page of the packaged workspace carries a nonce of its own in each response, in the page and in the policy; nothing else does."""

    PLACEHOLDER = "__REACHMAP_STYLE_NONCE__"

    def setUp(self) -> None:
        import tempfile

        holder = tempfile.TemporaryDirectory(prefix="reachmap-nonce-")
        self.addCleanup(holder.cleanup)
        static = Path(holder.name) / "static"
        static.mkdir()
        (static / "index.html").write_text(f'<!doctype html><meta name="reachmap-style-nonce" content="{self.PLACEHOLDER}"><title>workspace</title>', encoding="utf-8")
        (static / "app.js").write_text(f'// {self.PLACEHOLDER}', encoding="utf-8")
        (static / "plain.html").write_text("<!doctype html><title>plain</title>", encoding="utf-8")
        self.live = Live(static_dir=static)
        self.live.__enter__()
        self.addCleanup(self.live.__exit__, None, None, None)

    def nonce_of(self, r) -> str:  # type: ignore[no-untyped-def]
        import re

        found = re.findall(r'content="([A-Za-z0-9_-]+)"', r.body.decode("utf-8"))
        self.assertEqual(len(found), 1, r.body)
        return found[0]

    def test_the_page_and_the_policy_carry_the_same_fresh_nonce(self) -> None:
        seen = set()
        for path in ("/", "/", "/index.html", "/reports/some-report-r1", "/jobs"):
            r = self.live.get(path, token=False)
            self.assertEqual(r.status, 200, path)
            nonce = self.nonce_of(r)
            self.assertRegex(nonce, r"^[A-Za-z0-9_-]{24}$")
            self.assertNotIn(self.PLACEHOLDER, r.body.decode("utf-8"))
            csp = r.headers["content-security-policy"]
            self.assertIn(f"style-src 'self' 'nonce-{nonce}';", csp)
            seen.add(nonce)
        self.assertEqual(len(seen), 5, "a nonce is never used twice")

    def test_the_policy_with_a_nonce_is_the_workspace_policy_and_nothing_looser(self) -> None:
        r = self.live.get("/", token=False)
        csp = r.headers["content-security-policy"]
        nonce = self.nonce_of(r)
        self.assertEqual(csp, server.workspace_policy(nonce))
        self.assertEqual(csp.replace(f" 'nonce-{nonce}'", ""), server.workspace_policy())
        for loose in ("unsafe-inline", "unsafe-eval", "data:*", "*", "http:", "https:"):
            self.assertNotIn(loose, csp.replace("img-src 'self' data:", ""))
        self.assertIn("script-src 'self';", csp)
        self.assertIn("connect-src 'self'", csp)
        self.assertEqual(csp.count("nonce-"), 1, "a nonce for styles only: scripts have none")

    def test_other_files_have_no_nonce_and_are_not_rewritten(self) -> None:
        js = self.live.get("/app.js", token=False)
        self.assertEqual(js.body.decode("utf-8"), f"// {self.PLACEHOLDER}")
        self.assertEqual(js.headers["content-security-policy"], server.workspace_policy())
        plain = self.live.get("/plain.html", token=False)
        self.assertEqual(plain.headers["content-security-policy"], server.workspace_policy())
        self.assertEqual(plain.body, b"<!doctype html><title>plain</title>")

    def test_the_policy_without_a_nonce_is_the_one_of_before(self) -> None:
        self.assertIn("style-src 'self';", server.workspace_policy())
        self.assertNotIn("nonce", server.workspace_policy())

    def test_the_nonce_is_never_in_an_api_answer_or_a_log_line(self) -> None:
        r = self.live.get("/", token=False)
        nonce = self.nonce_of(r)
        for path in ("/api/health", "/api/reports"):
            answer = self.live.get(path)
            self.assertNotIn(nonce.encode(), answer.body)
            self.assertNotIn("nonce", str(answer.headers).lower())
        self.assertFalse([line for line in self.live.logs if nonce in line])

    def test_the_minimal_page_is_served_as_before_without_a_nonce(self) -> None:
        with Live() as live:
            r = live.get("/", token=False)
            self.assertEqual(r.headers["content-security-policy"], server.workspace_policy())


class Minimal(unittest.TestCase):
    def test_without_workspace_files_a_minimal_page_is_served(self) -> None:
        with Live() as live:
            r = live.get("/", token=False)
            self.assertEqual(r.status, 200)
            text = r.body.decode("utf-8")
            self.assertIn('src="/minimal.js"', text)
            self.assertNotIn("<script>", text.replace("<script src", "<script-src"))  # no inline script: the policy has none
            js = live.get("/minimal.js", token=False)
            self.assertEqual(js.status, 200)
            self.assertIn(b"textContent", js.body)
            self.assertNotIn(b"innerHTML", js.body)
            self.assertNotIn(b"document.write", js.body)
            # the token stays in memory: it is taken out of the address at once, never stored, and no page is opened with it
            self.assertIn(b"history.replaceState(null", js.body)
            for banned in (b"localStorage", b"sessionStorage", b"document.cookie", b"window.open", b"location.hash +", b"eval("):
                self.assertNotIn(banned, js.body, banned)
            self.assertIn(b"revokeObjectURL", js.body)  # a report is offered as a download of a blob that is revoked after use
            self.assertIn(b"download", js.body)
            csp = r.headers["content-security-policy"]
            self.assertIn("script-src 'self'", csp)
            self.assertNotIn("unsafe-inline", csp.split("script-src")[1].split(";")[0])

    def test_the_minimal_page_has_no_client_routes(self) -> None:
        with Live() as live:
            for path in ("/reports/x", "/jobs"):
                self.assertEqual(live.get(path, token=False).status, 404, path)

    def test_the_page_does_not_list_reports_without_the_token(self) -> None:
        with Live() as live:
            live.reports.mkdir(parents=True, exist_ok=True)
            shutil.copy(sr.FIXTURES / "complete.json", live.reports / "complete-r1.json")
            r = live.get("/", token=False)
            self.assertNotIn(b"complete-r1", r.body)  # the list comes from the API with the token the page reads from the fragment


if __name__ == "__main__":
    unittest.main()
