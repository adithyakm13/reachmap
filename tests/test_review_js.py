"""The decision controls of the HTML report (M11): the page records a verdict with a reason and an author name per area and finding, keeps
them in memory (and in `localStorage` as a convenience, inside try), and exports a JSON file with the report's own fingerprints. It builds
DOM with `createElement` and `textContent` only and never sends anything anywhere.

The behaviour is run in node against a small fake DOM (`tests/js/review_harness.js`) when node is installed; the static checks on the
script do not need it."""

import io
import json
import re
import shutil
import subprocess
import unittest
from importlib import resources
from pathlib import Path

from reachmap import cli, report, wording
from reachmap.review import exchange, revisions, store
from tests import support_review as rv
from tests.test_report_html import EXPORT_LINK_LINES as ALLOWED_LINK_LINES
from tests.test_report_html import FORBIDDEN_JS, URL, Parsed

HARNESS = Path(__file__).resolve().parent / "js" / "review_harness.js"
ASSET = resources.files("reachmap.report").joinpath("assets", "report.js")
NODE = shutil.which("node")
HOSTILE = "</script><script>alert(1)</script> <!-- <img src=x onerror=alert(1)> \"><svg onload=alert(1)> javascript:alert(1) ]]> [x](javascript:alert(1)) <details open> \u202e \u2028"


def script() -> str:
    return ASSET.read_text(encoding="utf-8")


class Static(unittest.TestCase):
    def test_the_only_link_the_script_makes_is_the_download_of_a_blob_the_script_made_and_it_is_made_once(self):
        js = script()
        for line in ALLOWED_LINK_LINES:
            self.assertEqual(js.count(line), 1, line)
        self.assertEqual(len(re.findall(r"createElement\(\s*[\"']a[\"']", js)), 1)
        self.assertEqual(len(re.findall(r"\.href\s*=", js)), 1)
        rest = js
        for line in ALLOWED_LINK_LINES:
            rest = rest.replace(line, "")
        self.assertIsNone(FORBIDDEN_JS.search(rest), FORBIDDEN_JS.search(rest))

    def test_the_script_has_no_url_no_comment_marker_and_no_banned_wording(self):
        js = script()
        self.assertIsNone(URL.search(js), URL.search(js))
        self.assertEqual(wording.check(js), [])
        self.assertNotIn("<!--", js)

    def test_the_decision_code_builds_dom_with_text_content_and_never_markup(self):
        js = script()
        decide = js[js.index("var review = "):js.index("(function () {\n  /* The page works without this script")] if "var review = " in js else ""
        self.assertTrue(decide, "the decision code is the `review` part of the script")
        self.assertIn("textContent", decide)
        self.assertIn("createElement", decide)
        self.assertIn("createTextNode", decide)
        for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "DOMParser", "createContextualFragment"):
            self.assertNotIn(banned, decide)

    def test_local_storage_is_only_used_inside_try_and_the_page_works_when_it_throws(self):
        js = script()
        uses = [m.start() for m in re.finditer(r"localStorage", js)]
        self.assertGreaterEqual(len(uses), 3)  # the theme, and the decisions
        for pos in uses:
            before = js[:pos]
            self.assertGreater(before.rfind("try {"), before.rfind("}"), "localStorage used outside a try block")

    def test_the_policy_of_a_page_with_decisions_has_no_connect_src_and_names_the_hash_of_the_one_inline_script(self):
        page = report.render(rv.decided(), "html")
        csp = re.search(r'Content-Security-Policy" content="([^"]*)"', page).group(1)
        self.assertNotIn("connect-src", csp)
        self.assertEqual(len(Parsed(page).code_scripts), 1)
        self.assertIn(script().strip(), page)
        self.assertEqual(csp.count("script-src"), 1)

    def test_the_script_in_the_page_is_valid_javascript(self):
        if NODE is None:
            self.skipTest("node is not installed; the script is checked by the patterns above")
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "page.js"
            path.write_text(Parsed(report.render(rv.decided(), "html")).code_scripts[0]["text"], encoding="utf-8")
            done = subprocess.run([NODE, "--check", str(path)], capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)

    def test_everything_the_controls_say_is_in_the_scripts_words_and_a_skip_says_risk_accepted_not_tested(self):
        js = script()
        from reachmap.model import verdict_text

        for verdict in ("test", "sample", "skip", "accept", "reject"):
            self.assertIn(verdict_text(verdict), js)  # the words of the model, not another wording
        self.assertIn("risk accepted, not tested", js)
        self.assertIn("Export decisions", js)
        self.assertIn("Record decision", js)


@unittest.skipIf(NODE is None, "node is not installed")
class Behaviour(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = rv.s01()

    def go(self, steps=(), storage=None, data=None, clock=None):
        payload = {"report": data if data is not None else self.report, "steps": list(steps), "storage": storage or {}}
        if clock is not None:
            payload["clock"] = clock
        done = subprocess.run([NODE, str(HARNESS), str(ASSET)], input=json.dumps(payload), capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def record(self, kind, index, verdict, reason="", author="Quinn"):
        return [
            {"op": "set", "kind": kind, "index": index, "field": "verdict", "value": verdict},
            {"op": "set", "kind": kind, "index": index, "field": "reason", "value": reason},
            {"op": "set", "kind": kind, "index": index, "field": "author", "value": author},
            {"op": "click", "kind": kind, "index": index, "button": "Record decision"},
            {"op": "read", "kind": kind, "index": index},
        ]

    def export(self, steps, **kw):
        out = self.go([*steps, {"op": "bar", "button": "Export decisions"}], **kw)
        self.assertEqual(out["errors"], [])
        self.assertEqual(len(out["downloads"]), 1)
        return out, json.loads(out["downloads"][0]["text"])

    # -------------------------------------------------------------------------------- the controls

    def test_every_area_and_finding_gets_controls_and_the_bar_has_an_export_button(self):
        out = self.go()
        self.assertEqual(out["hasControls"]["a"], [True] * len(self.report["areas"]))
        self.assertEqual(out["hasControls"]["f"], [True] * len(self.report["findings"]))
        self.assertIn("Export decisions", out["bar"])

    def test_the_verdicts_offered_are_those_of_the_kind_of_target(self):
        out = self.go([{"op": "options", "kind": "a", "index": 0}, {"op": "options", "kind": "f", "index": 0}])
        self.assertEqual(out["status"]["a0-options"], ["", "test", "sample", "skip"])
        self.assertEqual(out["status"]["f0-options"], ["", "accept", "reject"])

    def test_a_report_without_review_data_gets_no_control_and_no_export_button(self):
        data = {k: v for k, v in self.report.items() if k != "review"}
        out = self.go(data=data)
        self.assertEqual(out["hasControls"]["a"], [False] * len(data["areas"]))
        self.assertNotIn("Export decisions", out["bar"])

    def test_a_decision_recorded_says_so_in_words_and_a_skip_reads_as_risk_accepted_not_tested(self):
        out = self.go(self.record("a", 0, "skip", "retired", "Quinn"))
        self.assertEqual(out["errors"], [])
        self.assertEqual(out["status"]["a0"], 'Recorded: risk accepted, not tested (reason from Quinn: "retired").')
        out = self.go(self.record("a", 1, "test", "", "Sam"))
        self.assertEqual(out["status"]["a1"], "Recorded: marked to test (by Sam).")
        out = self.go(self.record("f", 0, "reject", "wrong service", "Sam"))
        self.assertEqual(out["status"]["f0"], 'Recorded: finding rejected by the reviewer (reason from Sam: "wrong service").')

    def test_what_cannot_be_recorded_says_why_and_records_nothing(self):
        for kind, verdict, reason, author, word in (
            ("a", "", "", "Quinn", "verdict"), ("a", "skip", "", "Quinn", "reason"), ("f", "reject", "   ", "Quinn", "reason"), ("a", "test", "", "", "name"),
            ("a", "test", "", "  ", "name"), ("a", "skip", "x" * 2001, "Quinn", "2000"), ("a", "test", "", "n" * 101, "100"),
        ):
            with self.subTest(verdict=verdict, reason=reason[:5], author=author[:5]):
                steps = self.record(kind, 0, verdict, reason, author)
                out, doc = self.export(steps)
                self.assertNotIn("Recorded", out["status"]["a0" if kind == "a" else "f0"])
                self.assertIn(word, out["status"]["a0" if kind == "a" else "f0"])
                self.assertEqual(doc["decisions"], [])

    def test_a_later_decision_for_the_same_target_replaces_the_one_in_the_page(self):
        steps = self.record("a", 0, "skip", "first", "Q") + self.record("a", 0, "sample", "", "Q")
        out, doc = self.export(steps)
        self.assertEqual([d["verdict"] for d in doc["decisions"]], ["sample"])

    # -------------------------------------------------------------------------------- the export

    def test_the_export_is_the_documented_file_with_the_reports_key_head_revision_and_fingerprints(self):
        steps = self.record("a", 0, "skip", "retired", "Quinn") + self.record("f", 1, "accept", "", "Quinn")
        out, doc = self.export(steps, clock=1767323045000)
        self.assertEqual(list(doc), ["format", "version", "report", "exported_at", "decisions"])
        self.assertEqual((doc["format"], doc["version"]), ("reachmap-decisions", 1))
        self.assertEqual(doc["report"], {"key": {k: self.report["review"]["key"][k] for k in ("workspace", "repo", "change")}, "head_sha": self.report["ref"], "revision": self.report["review"]["revision"]})
        self.assertRegex(doc["exported_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")
        a, f = doc["decisions"]
        self.assertEqual(list(a), ["target_id", "verdict", "reason", "author", "fingerprint", "created_at"])
        self.assertEqual((a["target_id"], a["verdict"], a["reason"], a["author"]), (self.report["areas"][0]["id"], "skip", "retired", "Quinn"))
        self.assertEqual(a["fingerprint"], self.report["areas"][0]["fingerprint"])  # the report's, not computed by the page
        self.assertEqual(f["fingerprint"], self.report["findings"][1]["fingerprint"])
        self.assertEqual((f["target_id"], f["verdict"]), (self.report["findings"][1]["id"], "accept"))
        self.assertRegex(a["created_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")
        self.assertLess(a["created_at"], f["created_at"])

    def test_the_export_is_a_file_download_of_a_blob_named_for_the_revision_and_nothing_is_sent_anywhere(self):
        out, _ = self.export(self.record("a", 0, "test"))
        (d,) = out["downloads"]
        self.assertEqual(d["name"], f"reachmap-decisions-r{self.report['revision']}.json")
        self.assertEqual(d["type"], "application/json")
        self.assertTrue(d["href"].startswith("blob:"))
        self.assertEqual(out["revoked"], [d["href"]])
        self.assertEqual(out["calls"], {"network": 0, "innerHTML": 0})
        self.assertEqual(out["anchorsInDocument"], 0)  # the link is made for the click and is gone after it

    def test_with_nothing_decided_the_export_says_so_and_still_writes_a_valid_empty_file(self):
        out, doc = self.export([])
        self.assertEqual(doc["decisions"], [])
        exchange.parse(json.dumps(doc))

    def test_the_file_the_page_writes_is_read_by_the_importer_and_the_fingerprint_survives(self):
        steps = self.record("a", 0, "skip", "retired", "Quinn") + self.record("a", 2, "test", "", "Sam") + self.record("f", 0, "reject", "no", "Sam")
        _, doc = self.export(steps)
        got = exchange.parse(json.dumps(doc))
        self.assertEqual(got.revision, self.report["review"]["revision"])
        items = got.to_stored()
        self.assertEqual([i.decision.channel for i in items], ["html-import"] * 3)
        self.assertEqual(items[0].decision.fingerprint, self.report["areas"][0]["fingerprint"])
        got.require_key(store.make_key(*(self.report["review"]["key"][k] for k in ("workspace", "repo", "change"))))

    def test_the_whole_round_trip_page_export_then_import_through_the_command_keeps_the_exported_fingerprint(self):
        import tempfile

        steps = self.record("a", 0, "skip", "retired", "Quinn") + self.record("f", 0, "accept", "", "Quinn")
        _, doc = self.export(steps)
        with tempfile.TemporaryDirectory() as d:
            base = Path(d) / "reports"
            base.mkdir()
            path = base / "r.json"
            path.write_text(json.dumps(self.report), encoding="utf-8")
            exported = Path(d) / "decisions.json"
            exported.write_text(json.dumps(doc), encoding="utf-8")
            key = store.make_key(*(self.report["review"]["key"][k] for k in ("workspace", "repo", "change")))
            self.assertEqual(cli.main(["decide", "--import", str(exported), str(path)], stdout=io.StringIO(), stderr=io.StringIO()), 2)  # no record of that revision here
            revisions.record(base, key, revisions.Rev(self.report["review"]["revision"], path.name, self.report["ref"], "2026-01-02T03:04:05Z"))
            code = cli.main(["decide", "--import", str(exported), str(path)], stdout=io.StringIO(), stderr=io.StringIO())
            self.assertEqual(code, 0)
            items = store.read(base, key).items
            self.assertEqual([i.decision.fingerprint for i in items], [self.report["areas"][0]["fingerprint"], self.report["findings"][0]["fingerprint"]])
            out = io.StringIO()
            cli.main(["report", str(path)], stdout=out, stderr=io.StringIO())
            self.assertIn('risk accepted, not tested (reason from Quinn: "retired")', out.getvalue())

    # -------------------------------------------------------------------------------- hostile text

    def test_a_hostile_reason_and_author_are_text_in_the_export_and_in_the_page_nothing_else(self):
        steps = self.record("a", 0, "skip", HOSTILE, HOSTILE[:90]) + self.record("a", 0, "skip", HOSTILE, "x" * 5)
        out, doc = self.export(steps)
        self.assertEqual(out["errors"], [])
        self.assertEqual(doc["decisions"][0]["reason"], HOSTILE)
        self.assertEqual(out["calls"]["innerHTML"], 0)
        self.assertTrue(set(out["tags"]) <= {"DIV", "LABEL", "SELECT", "OPTION", "INPUT", "BUTTON", "SPAN", "A", "P"}, out["tags"])
        self.assertIn(json.dumps(HOSTILE, ensure_ascii=False), out["status"]["a0"])  # as text of a span; the fake DOM would have raised on innerHTML

    def test_the_status_line_holds_the_reason_as_text_even_with_markup_in_it(self):
        out = self.go(self.record("a", 0, "skip", "<b>bold</b>", "<i>me</i>"))
        self.assertEqual(out["status"]["a0"], 'Recorded: risk accepted, not tested (reason from <i>me</i>: "<b>bold</b>").')
        self.assertNotIn("B", out["tags"])
        self.assertNotIn("I", out["tags"])

    def test_the_exported_file_with_a_hostile_reason_is_imported_and_rendered_inert(self):
        _, doc = self.export(self.record("a", 0, "skip", HOSTILE.replace("\u2028", "").replace("\u202e", ""), "Quinn"))
        got = exchange.parse(json.dumps(doc))
        data = rv.s01()
        item = got.to_stored()[0]
        entry = rv.entry(data, "area", 0, "skip", item.decision.reason, item.decision.author, "html-import")
        data["review"]["decisions"] = {"current": [entry], "stale": [], "orphaned": [], "undecided": {"areas": [], "findings": []}, "records": []}
        page = report.render(data, "html")
        parsed = Parsed(page)
        self.assertEqual(len(parsed.code_scripts), 1)
        self.assertEqual(len(parsed.json_scripts), 1)
        self.assertNotIn("<img src=x", re.sub(r"(?s)<script.*?</script>", "", page))
        text = report.render(data, "md")
        self.assertNotIn("<script>", text)

    # -------------------------------------------------------------------------------- storage

    def test_decisions_are_kept_in_local_storage_and_come_back_when_the_page_is_opened_again(self):
        first = self.go(self.record("a", 0, "skip", "retired", "Quinn"))
        self.assertEqual(len(first["storage"]), 1)
        (key,) = first["storage"]
        self.assertNotIn(key, ("reachmap-theme",))
        self.assertIn(self.report["review"]["key"]["slug"], key)
        self.assertIn(self.report["ref"], key)  # a page of another commit does not read these decisions
        out, doc = self.export([], storage={"initial": first["storage"]})
        self.assertEqual([d["verdict"] for d in doc["decisions"]], ["skip"])
        out = self.go([{"op": "read", "kind": "a", "index": 0}], storage={"initial": first["storage"]})
        self.assertIn("risk accepted, not tested", out["status"]["a0"])

    def test_a_page_whose_storage_throws_still_records_and_exports(self):
        out, doc = self.export(self.record("a", 0, "test", "", "Quinn"), storage={"throws": True})
        self.assertEqual(out["errors"], [])
        self.assertEqual([d["verdict"] for d in doc["decisions"]], ["test"])
        self.assertEqual(out["storage"], {})

    def test_stored_data_that_is_not_what_the_page_wrote_is_ignored(self):
        key = next(iter(self.go(self.record("a", 0, "test"))["storage"]))
        t = self.report["areas"][0]
        good = {"target_id": t["id"], "verdict": "test", "reason": "", "author": "Q", "fingerprint": t["fingerprint"], "created_at": "2026-01-02T03:04:05.000Z"}
        for name, value in (
            ("not json", "{{{"), ("not a list", json.dumps({"a": 1})), ("wrong fingerprint", json.dumps([{**good, "fingerprint": "f" * 64}])),
            ("unknown target", json.dumps([{**good, "target_id": "a-" + "0" * 16}])), ("verdict of the other kind", json.dumps([{**good, "verdict": "accept"}])),
            ("skip without a reason", json.dumps([{**good, "verdict": "skip"}])), ("proto", '[{"target_id": "__proto__", "verdict": "test"}]'),
            ("extra key", json.dumps([{**good, "channel": "runner"}])), ("numbers", json.dumps([{**good, "reason": 5}])),
        ):
            with self.subTest(name):
                out, doc = self.export([], storage={"initial": {key: value}})
                self.assertEqual(doc["decisions"], [], name)

    def test_the_storage_of_one_page_is_not_read_by_a_page_of_another_revision(self):
        first = self.go(self.record("a", 0, "test"))
        other = dict(self.report, ref="b" * 40, revision=2)
        out, doc = self.export([], storage={"initial": first["storage"]}, data=other)
        self.assertEqual(doc["decisions"], [])


if __name__ == "__main__":
    unittest.main()
