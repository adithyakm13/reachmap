"""M13: the frontend and the runner. Nothing here builds the frontend (that is `npm run build`, run by CI and by the release).

- When `frontend/dist/` exists: it is one page with hashed scripts and style sheets (the routes and the graph libraries are chunks) that load
  nothing from another host, and the runner serves them with its policy (`connect-src 'self'`, no inline script, the style nonce of the index
  page and no other inline style), a client route answers with the index page and a missing file does not.
- Always: the first segments of the routes of the workspace are the ones the runner falls back to the index page for; the report fixtures of
  the frontend (`frontend/fixtures/`) are the M10 fixtures and what the analyser makes of s11 and s13 today, and the order file is what the
  Python view model makes of each (so a change of the analyser or of the view model that the TypeScript view model has not followed fails here).
"""

from __future__ import annotations

import importlib.util
import json
import re
import unittest
from pathlib import Path

from reachmap.runner import server
from tests import support_report as sr
from tests.support_runner import Live

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
DIST = FRONTEND / "dist"
FIXTURES = FRONTEND / "fixtures"
# Strings of React, React DOM, React Router, Mantine and React Flow that are addresses and are never requested: the namespaces of SVG, MathML and
# XLink, the address in the text of a minified production error, the address in a message of the router, the base the router parses a path
# against, the address in a warning of Mantine about colours, and the addresses of React Flow's attribution link (not drawn: the page hides it)
# and of its error messages.
LIBRARY_HOSTS = {"www.w3.org", "react.dev", "reactrouter.com", "localhost", "mantine.dev", "reactflow.dev"}
PLACEHOLDER = "__REACHMAP_STYLE_NONCE__"
ASSET = r"^assets/[A-Za-z0-9_-]+-[A-Za-z0-9_-]{8}\.(js|css)$"
NOTICES = "THIRD_PARTY_LICENSES.txt"  # written next to the page by the postbuild step (frontend/scripts/third-party-licenses.mjs)
NOTE_FIXTURES = ("notes", "notes_r1", "notes_hostile", "notes_unreadable")


def make_reports_module():
    spec = importlib.util.spec_from_file_location("frontend_make_reports", FIXTURES / "make_reports.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless((DIST / "index.html").is_file(), "frontend/dist/ is not built (npm run build)")
class BuiltFrontend(unittest.TestCase):
    live: Live

    @classmethod
    def setUpClass(cls) -> None:
        cls.live = Live(static_dir=DIST)
        cls.live.__enter__()
        cls.addClassCleanup(cls.live.__exit__, None, None, None)
        cls.files = {p.relative_to(DIST).as_posix(): p for p in DIST.rglob("*") if p.is_file()}

    def test_it_is_one_page_with_hashed_scripts_and_style_sheets_split_by_route(self) -> None:
        self.assertEqual(sorted(n for n in self.files if n.endswith(".html")), ["index.html"])
        others = [n for n in self.files if not n.endswith(".html") and n != NOTICES]
        self.assertTrue(others)
        for name in others:
            self.assertRegex(name, ASSET)
        self.assertEqual([n for n in self.files if n.endswith(".map")], [])
        html = self.files["index.html"].read_text(encoding="utf-8")
        entries = re.findall(r'<script type="module"[^>]*\bsrc="(/assets/[^"]+)"', html)
        self.assertEqual(len(entries), 1, "one entry script: the routes and the graph libraries are chunks it loads when they are needed")
        self.assertTrue(any("Graphs" in n for n in others), "the graph libraries are a chunk of their own")
        # the entry script does not hold the graph libraries: a page with no graph never loads them
        entry = (DIST / entries[0].lstrip("/")).read_text(encoding="utf-8")
        self.assertNotIn("react-flow__", entry)

    def test_the_build_holds_the_third_party_notices_and_the_runner_serves_them_as_plain_text(self) -> None:
        self.assertIn(NOTICES, self.files)
        text = self.files[NOTICES].read_text(encoding="utf-8")
        lock = json.loads((FRONTEND / "package-lock.json").read_text(encoding="utf-8"))
        for key, entry in lock["packages"].items():
            if key and not entry.get("dev") and (FRONTEND / key).is_dir():
                self.assertIn(f"{key.rsplit('node_modules/', 1)[1]} {entry['version']}\n", text, key)
        r = self.live.get("/" + NOTICES, token=False)
        self.assertEqual(r.status, 200)
        self.assertEqual(r.headers["content-type"], "text/plain; charset=utf-8")
        self.assertEqual(r.headers["x-content-type-options"], "nosniff")
        self.assertEqual(r.body, self.files[NOTICES].read_bytes())

    def test_the_page_loads_only_its_own_files_and_has_nothing_inline(self) -> None:
        html = self.files["index.html"].read_text(encoding="utf-8")
        for value in re.findall(r'\b(?:src|href|action)="([^"]*)"', html):
            self.assertTrue(value == "data:," or re.fullmatch(r"/assets/[A-Za-z0-9_.-]+", value), value)
        for attrs, body in re.findall(r"<script\b([^>]*)>(.*?)</script>", html, flags=re.S):
            self.assertIn('src="/assets/', attrs)
            self.assertEqual(body.strip(), "")
        self.assertNotRegex(html, r"<style\b|\sstyle=|\son[a-z]+=")
        self.assertNotRegex(html, r"(?i)https?:|//[a-z]")
        # the one place for the nonce the runner writes into each response of the index page
        self.assertEqual(html.count(PLACEHOLDER), 1)
        self.assertIn(f'<meta name="reachmap-style-nonce" content="{PLACEHOLDER}"', html)

    def test_no_external_url_in_the_built_files(self) -> None:
        for name, path in self.files.items():
            text = path.read_text(encoding="utf-8")
            with self.subTest(file=name):
                if name.endswith(".css"):
                    self.assertNotRegex(text, r"(?i)@import|url\s*\(|https?:|//[a-z]")
                if name.endswith(".js"):
                    hosts = set(re.findall(r"\bhttps?://([A-Za-z0-9.-]+)", text))
                    self.assertLessEqual(hosts, LIBRARY_HOSTS)
                    self.assertNotRegex(text, r"\bwss?://")

    def test_the_runner_serves_the_files_with_its_policy(self) -> None:
        for name, path in self.files.items():
            with self.subTest(file=name):
                r = self.live.get("/" + name, token=False)
                self.assertEqual(r.status, 200)
                csp = r.headers["content-security-policy"]
                self.assertIn("connect-src 'self'", csp)
                self.assertIn("script-src 'self'", csp)
                self.assertNotIn("unsafe-inline", csp)
                self.assertNotIn("unsafe-eval", csp)
                self.assertEqual(r.headers["x-content-type-options"], "nosniff")
                if name == "index.html":
                    nonce = re.search(r'content="([A-Za-z0-9_-]{24})"', r.body.decode("utf-8"))
                    self.assertIsNotNone(nonce)
                    self.assertEqual(r.body, path.read_bytes().replace(PLACEHOLDER.encode(), nonce.group(1).encode()))
                    self.assertIn(f"style-src 'self' 'nonce-{nonce.group(1)}';", csp)
                else:
                    self.assertEqual(r.body, path.read_bytes())
                    self.assertIn("style-src 'self';", csp)
        js = next(n for n in self.files if n.endswith(".js"))
        self.assertTrue(self.live.get("/" + js, token=False).headers["content-type"].startswith("text/javascript"))

    def test_a_route_of_the_workspace_answers_with_the_page_and_a_missing_file_does_not(self) -> None:
        index = self.files["index.html"].read_bytes()
        for path in ("/", "/jobs", "/reports/anything-r1", "/reports/anything-r1/areas/a-0123456789abcdef", "/reports/anything-r1/findings"):
            r = self.live.get(path, token=False)
            nonce = re.search(rb'content="([A-Za-z0-9_-]{24})"', r.body)
            self.assertIsNotNone(nonce, path)
            self.assertEqual((r.status, r.body), (200, index.replace(PLACEHOLDER.encode(), nonce.group(1))), path)
        for path in ("/assets/missing.js", "/missing", "/reports/x/%2e%2e/%2e%2e/etc/passwd"):
            self.assertEqual(self.live.get(path, token=False).status, 404, path)


class RoutesAndFixtures(unittest.TestCase):
    def test_the_routes_of_the_workspace_are_the_ones_the_runner_answers_with_the_page(self) -> None:
        text = (FRONTEND / "src" / "routes.tsx").read_text(encoding="utf-8")
        firsts = {p.split("/")[0] for p in re.findall(r'path:\s*"([^"]+)"', text)} - {"*"}
        self.assertEqual(firsts, set(server.CLIENT_ROUTES))

    def test_the_m10_fixtures_are_copied_as_they_are(self) -> None:
        for name in ("complete", "hostile", "failed", "timed_out"):
            with self.subTest(name=name):
                mine = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
                theirs = json.loads((sr.FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
                self.assertEqual(mine, theirs)

    def test_the_scenario_reports_are_what_the_analyser_makes_today(self) -> None:
        mod = make_reports_module()
        for name, args in (("s13", sr.S13), ("s11", sr.S11)):
            with self.subTest(name=name):
                made = mod.reproducible(sr.real(*args))
                have = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
                self.assertEqual(made, have, "regenerate: python3.12 frontend/fixtures/make_reports.py")

    def test_the_unordered_fixture_is_made_from_s13_and_really_is_out_of_order(self) -> None:
        mod = make_reports_module()
        made = mod.unordered(json.loads((FIXTURES / "s13.json").read_text(encoding="utf-8")))
        have = json.loads((FIXTURES / "unordered.json").read_text(encoding="utf-8"))
        self.assertEqual(made, have, "regenerate: python3.12 frontend/fixtures/make_reports.py")
        order = mod.order_of(have)
        in_report = [a["label"] for a in have["areas"]]
        shown = order["mustTest"] + order["sample"] + order["candidates"]
        self.assertEqual(sorted(shown), sorted(in_report))
        self.assertNotEqual(shown, in_report)

    def test_the_order_file_is_what_the_python_view_model_makes_of_each_report(self) -> None:
        mod = make_reports_module()
        have = json.loads((FIXTURES / "order.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(have), sorted(["complete", "failed", "hostile", "s11", "s13", "timed_out", "unordered", *mod.SOURCE_STATES, *NOTE_FIXTURES]))
        for name, expected in have.items():
            with self.subTest(name=name):
                report = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
                self.assertEqual(mod.order_of(report), expected, "regenerate: python3.12 frontend/fixtures/make_reports.py")


class ModelNoteFixtures(unittest.TestCase):
    """M14b: the report fixtures with model notes are made by the notes module (not written by hand), the page's words are the Python ones, and a
    note changes no count."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = make_reports_module()
        cls.source = json.loads((FIXTURES / "unordered.json").read_text(encoding="utf-8"))
        cls.have = {n: json.loads((FIXTURES / f"{n}.json").read_text(encoding="utf-8")) for n in NOTE_FIXTURES}

    def test_the_notes_fixtures_are_what_the_notes_module_makes_today(self) -> None:
        made = self.mod.notes_reports(self.source)
        self.assertEqual(sorted(made), sorted(NOTE_FIXTURES))
        for name in NOTE_FIXTURES:
            with self.subTest(name=name):
                self.assertEqual(made[name], self.have[name], "regenerate: python3.12 frontend/fixtures/make_reports.py")

    def test_the_notes_are_in_each_state_and_of_each_kind_and_the_store_made_them(self) -> None:
        items = self.have["notes"]["model_notes"]["notes"]
        self.assertEqual({n["state"] for n in items}, {"current", "stale", "orphaned"})
        self.assertEqual({n["kind"] for n in items}, {"observation", "question", "proposed_test", "proposed_file"})
        self.assertEqual({n["verification"]["status"] for n in items if n["verification"]}, {"verified", "not_verified"})
        self.assertTrue(all(n["revision"] == 1 for n in items if n["state"] != "current"))
        self.assertEqual(self.have["notes_unreadable"]["model_notes"]["notes"], [])
        self.assertEqual([r["kind"] for r in self.have["notes_unreadable"]["model_notes"]["records"]], ["note_store_unreadable"])

    def test_the_hostile_fixture_has_markup_bidi_fence_looking_text_and_a_stamp_looking_text_in_notes(self) -> None:
        text = json.dumps(self.have["notes_hostile"]["model_notes"], ensure_ascii=True)
        for piece in ("<img src=x onerror=alert(1)>", "</script><script>alert(1)</script>", "\\u202e", "<<<END UNTRUSTED REPORT CONTENT", "Stamp: recorded via MCP, client reported as the-framework"):
            self.assertIn(piece, text)

    def test_a_stamp_looking_note_has_a_real_client_that_its_text_does_not_name_and_one_has_no_client_name(self) -> None:
        items = self.have["notes_hostile"]["model_notes"]["notes"]
        look = [n for n in items if str(n["text"]).startswith("recorded via MCP, client reported as admin") and n["client"]["name"]]
        self.assertEqual(len(look), 1)
        self.assertEqual(look[0]["client"]["name"], "claude-desktop")
        self.assertNotIn("claude-desktop", look[0]["text"])
        blank = [n for n in items if isinstance(n.get("client"), dict) and n["client"].get("name") == "" and "client reported as admin" in str(n.get("text"))]
        self.assertEqual(len(blank), 1)

    def test_the_first_report_viewed_while_its_rerun_exists_has_notes_made_on_the_rerun(self) -> None:
        items = self.have["notes_r1"]["model_notes"]["notes"]
        self.assertEqual({n["viewed"] for n in items}, {1})
        self.assertEqual({n["revision"] for n in items}, {1, 2})
        self.assertEqual(self.have["notes_r1"]["review"]["revision"], 1)
        self.assertEqual({n["state"] for n in items if n["revision"] == 2}, {"current"})

    def test_every_fixture_with_notes_is_a_report_and_a_note_changes_no_count(self) -> None:
        from reachmap import report

        for name in NOTE_FIXTURES:
            with self.subTest(name=name):
                data = self.have[name]
                report.check(data)
                bare = {k: v for k, v in data.items() if k != "model_notes"}
                with_notes, without = self.mod.model.build(data), self.mod.model.build(bare)
                self.assertIsNotNone(with_notes.model_notes)
                self.assertIsNone(without.model_notes)
                self.assertEqual(with_notes.header.counts, without.header.counts)
                self.assertEqual([a.label for a in with_notes.must_test], [a.label for a in without.must_test])
                self.assertEqual(with_notes.records, without.records)

    def test_the_words_file_is_what_the_python_tables_say(self) -> None:
        have = json.loads((FIXTURES / "note_words.json").read_text(encoding="utf-8"))
        self.assertEqual(self.mod.note_words(), have, "regenerate: python3.12 frontend/fixtures/make_reports.py")

    def test_the_label_is_the_one_the_brief_asks_for(self) -> None:
        self.assertEqual(self.mod.note_words()["label"], "Model notes, recorded via MCP; not evidence.")


if __name__ == "__main__":
    unittest.main()
