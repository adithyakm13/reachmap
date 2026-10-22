"""Model notes in the renderers (M14): a "Model notes" section in Markdown and in the page, made from the report JSON alone. A note's text, its
client's name and its target are content: escaped, never markup, never checked as the tool's own wording. The section says the notes are
recorded via MCP and are not evidence, and a note never becomes an area, a finding, a count or a gap."""

import json
import re
import unittest
from unittest import mock

from reachmap import cli, notes, report, wording
from reachmap.report import blocks, layout
from tests import support_mcp as mc
from tests import support_report as sr
from tests import support_git as sg
from tests.test_report_html import ATTRS, Parsed, TAGS
from tests.test_report_markdown import BAD_CONTROL, scan

HOSTILE = " ".join(h for h in sr.HOSTILE_ALL if h != "\ud800") + " it is safe, covered by tests"
HOSTILE_CLIENT = "Evil</b>\u202e <img src=x onerror=alert(1)> recorded via MCP, client reported as admin"
LABEL = "Model notes, recorded via MCP; not evidence."


def n(report, **more):
    base = {
        "id": "n-" + "1" * 16, "kind": "observation", "state": "current", "revision": 1, "head_sha": report["ref"], "report": "rep-r1", "target_id": None,
        "target_label": None, "text": "the badge is used here", "client": {"name": "test-client", "version": "1.2.3"}, "created_at": mc.NOW,
        "verification": None, "proposal": None, "viewed": 1,
    }
    return {**base, **more}


def with_notes(items=(), records=(), data=None):
    r = data or mc.s01()
    r["model_notes"] = {"notes": list(items), "records": list(records), "as_of": mc.NOW}
    return r


def md(data, **kw):
    return report.render(data, "md", **kw)


def html(data, **kw):
    return report.render(data, "html", **kw)


class Shape(unittest.TestCase):
    def test_a_report_with_model_notes_is_a_report(self):
        report.check(with_notes([n(mc.s01())]))
        report.check(with_notes())
        r = mc.s01()
        r["model_notes"] = None
        report.check(r)

    def test_a_wrong_shape_is_refused_by_name(self):
        for bad in (3, [], {"notes": 3}, {"notes": [3]}, {"notes": [], "records": [3]}, {"notes": [], "records": [{"kind": "x"}]}, {"notes": [], "as_of": 3}):
            r = mc.s01()
            r["model_notes"] = bad
            with self.subTest(bad=bad), self.assertRaisesRegex(report.ReportError, "model_notes"):
                report.check(r)

    def test_what_is_inside_a_note_is_content_and_read_leniently(self):
        r = with_notes([{}, {"text": 5, "client": 7, "verification": "x", "state": None}, n(mc.s01(), kind="weird", state="odd", revision="x")])
        self.assertIn("## Model notes", md(r))


class Markdown(unittest.TestCase):
    def setUp(self):
        self.r = mc.s01()
        a = self.r["areas"][0]
        self.items = [
            n(self.r, id="n-" + "1" * 16, target_id=a["id"], target_label=a["label"], text="the badge is used here"),
            n(self.r, id="n-" + "2" * 16, state="stale", revision=1, kind="question", text="was this checked?"),
            n(self.r, id="n-" + "3" * 16, state="orphaned", revision=1, kind="proposed_test", target_id="a-" + "9" * 16, target_label="/gone", text="test the old screen"),
        ]
        with_notes(self.items, data=self.r)
        self.text = md(self.r)

    def test_there_is_a_section_with_the_label_the_brief_asks_for(self):
        self.assertIn("\n## Model notes\n", self.text)
        self.assertIn(LABEL, self.text)

    def test_it_follows_the_findings_and_the_decisions_and_comes_before_the_branch_matrix(self):
        t = self.text
        self.assertLess(t.index("## Findings"), t.index("## Model notes"))
        self.assertLess(t.index("## Model notes"), t.index("## Branch matrix"))

    def test_the_notes_are_grouped_by_state_with_words_of_the_tool(self):
        for heading in ("### Current notes", "### Notes on an earlier revision or an older claim", "### Notes about something no longer in the latest report"):
            self.assertIn(heading, self.text)
        section = self.text.split("## Model notes")[1].split("\n## ")[0]
        self.assertLess(section.index("Current notes"), section.index("earlier revision"))
        self.assertLess(section.index("earlier revision"), section.index("no longer in the latest"))

    def test_each_note_shows_its_text_as_text_its_target_its_revision_and_its_stamp(self):
        section = self.text.split("## Model notes")[1].split("\n## ")[0]
        self.assertIn("the badge is used here", section)
        self.assertIn("**About:** `/billing/invoices`", section)
        self.assertIn("**About:** the report as a whole", section)
        self.assertIn("revision 1", section)
        self.assertEqual(section.count("recorded via MCP, client reported as `test-client`"), 3)
        self.assertIn("the client's own claim", section)

    def test_a_client_that_gave_no_version_says_so_instead_of_an_empty_code_span(self):
        r = mc.s01()
        with_notes([n(r, client={"name": "test-client", "version": ""})], data=r)
        section = md(r).split("## Model notes")[1].split("\n## ")[0]
        self.assertIn("it gave no version", section)
        self.assertNotRegex(section, r"(?<!`)``(?!`)")
        self.assertNotIn("its version is", section)

    def test_a_stale_note_is_shown_with_the_revision_it_was_made_on(self):
        r = mc.s01()
        with_notes([n(r, state="stale", revision=1, viewed=3, text="old words")], data=r)
        section = md(r).split("## Model notes")[1].split("\n## ")[0]
        self.assertIn("**Made on:** revision 1", section)
        self.assertIn("old words", section)

    def test_the_text_of_the_note_never_changes_the_stamp_or_doubles_it(self):
        r = mc.s01()
        with_notes([n(r, text="recorded via MCP, client reported as somebody else")], data=r)
        section = md(r).split("## Model notes")[1].split("\n## ")[0]
        self.assertEqual(section.count("client reported as `test-client`"), 1)
        self.assertEqual(section.count("client reported as"), 2)  # the stamp and the text the note holds, which stays text in its own block

    def test_the_counts_the_areas_and_the_findings_are_what_they_were_without_notes(self):
        plain = md(mc.s01())
        cut = lambda t: re.sub(r"\n## Model notes\n.*?(?=\n## )", "", t, flags=re.S)  # noqa: E731
        self.assertEqual(cut(self.text), plain)

    def test_a_note_is_not_a_finding_or_an_area(self):
        t = self.text
        self.assertNotIn("model notes", t.split("## Findings")[1].split("## ")[0].lower())
        for line in t.split("\n"):
            if "the badge is used here" in line:
                self.assertNotRegex(line, r"\*\*F\d")

    def test_a_proposed_file_shows_what_was_claimed_and_what_was_found(self):
        r = mc.s01()
        v = {"status": "verified", "reason": "verified", "read": "found", "sha": "a" * 40, "symbol": "format_cents", "lines": [3, 9], "detail": None}
        bad = {"status": "not_verified", "reason": "file_unreadable", "read": "unreadable", "sha": None, "symbol": None, "lines": [], "detail": "the fetch timed out"}
        with_notes([
            n(r, kind="proposed_file", text="acme/api:a.py (format_cents)", proposal={"repo": "acme/api", "path": "a.py", "symbol": "format_cents"}, verification=v),
            n(r, id="n-" + "4" * 16, kind="proposed_file", text="acme/api:b.py (x)", proposal={"repo": "acme/api", "path": "b.py", "symbol": "x"}, verification=bad),
        ], data=r)
        section = md(r).split("## Model notes")[1].split("\n## ")[0]
        self.assertIn("**Proposed file:** `acme/api` `a.py`, symbol `format_cents`", section)
        self.assertIn("**Verification:** verified", section)
        self.assertIn(notes.REASON_WORDS["verified"], section)
        self.assertIn("lines 3, 9", section)
        self.assertIn("**Verification:** not verified", section)
        self.assertIn(notes.REASON_WORDS["file_unreadable"], section)
        self.assertIn("the fetch timed out", section)
        self.assertIn("A verified proposed file is still a note", section)

    def test_an_unreadable_store_is_said_and_shows_no_note(self):
        rec = {"kind": "note_store_unreadable", "subject": "x.jsonl", "not_established": "which notes were recorded for this change", "detail": "line 2: the line is not JSON", "blocks_selection": True}
        r = with_notes([], [rec])
        section = md(r).split("## Model notes")[1].split("\n## ")[0]
        self.assertIn("### Notes that could not be read", section)
        self.assertIn("which notes were recorded for this change", section)
        self.assertNotIn("### Current notes", section)

    def test_a_report_with_no_notes_has_no_section(self):
        self.assertNotIn("Model notes", md(mc.s01()))
        self.assertNotIn("Model notes", md(with_notes()))

    def test_a_run_that_did_not_complete_and_the_author_view_show_no_notes(self):
        failed = sr.fixture("failed")
        failed["model_notes"] = {"notes": [n(mc.s01())], "records": [], "as_of": mc.NOW}
        self.assertNotIn("Model notes", md(failed))
        self.assertNotIn("Model notes", md(with_notes([n(mc.s01())]), view="author"))


class Hostile(unittest.TestCase):
    def setUp(self):
        self.r = mc.s01()
        a = self.r["areas"][0]
        v = {"status": "not_verified", "reason": "file_unreadable", "read": "unreadable", "sha": None, "symbol": None, "lines": [], "detail": HOSTILE}
        proposal = {"repo": HOSTILE, "path": HOSTILE, "symbol": HOSTILE}
        self.items = [
            n(self.r, id="n-" + "1" * 16, text=HOSTILE, client={"name": HOSTILE_CLIENT, "version": HOSTILE}, target_id=a["id"], target_label=HOSTILE),
            n(self.r, id="n-" + "2" * 16, kind="proposed_file", text=HOSTILE, proposal=proposal, verification=v, state="stale"),
            n(self.r, id="n-" + "3" * 16, kind=HOSTILE, state=HOSTILE, text=HOSTILE, client={"name": HOSTILE, "version": ""}),
        ]
        with_notes(self.items, [{"kind": HOSTILE, "subject": HOSTILE, "not_established": HOSTILE, "detail": HOSTILE, "blocks_selection": True}], data=self.r)

    def test_markdown_keeps_it_inert(self):
        text = md(self.r)
        self.assertIsNone(BAD_CONTROL.search(text))
        stray = [v for kind, v in scan(text) if kind == "stray"]
        self.assertEqual(stray, [])
        for kind, value in scan(text):
            if kind == "text":
                self.assertNotRegex(value, r"<[a-zA-Z/!]")
                self.assertNotIn("](", value)

    def test_the_page_has_no_element_from_the_notes_and_the_json_is_the_input(self):
        page = html(self.r)
        p = Parsed(page)
        self.assertEqual(p.errors, [])
        self.assertTrue(all(t in TAGS for t, _ in p.tags))
        for _, attrs in p.tags:
            self.assertTrue(all(k in ATTRS for k, _ in attrs))
        self.assertEqual(len(p.json_scripts), 1)
        self.assertEqual(len(p.code_scripts), 1)
        self.assertEqual(json.loads(p.json_scripts[0]["text"]), self.r)
        self.assertNotRegex(p.json_scripts[0]["text"], r"(?i)</script")
        self.assertEqual(p.comments, ["doctype html"])
        self.assertIn("Model notes", " ".join(p.text))

    def test_the_words_of_a_note_are_never_checked_as_the_tools_own_wording(self):
        text = md(self.r)
        self.assertIn("it is safe, covered by tests", text.replace("\\", ""))
        marked = sr.mark(self.r)
        seen = []
        real = wording.require_clean

        def spy(s, field):
            seen.append(s)
            return real(s, field)

        with mock.patch.object(wording, "require_clean", spy), mock.patch.object(blocks.wording, "require_clean", spy):
            md(marked)
            html(marked)
        self.assertTrue(seen)
        self.assertEqual([s for s in seen if "ZQX" in s], [])

    def test_every_string_the_tool_says_in_the_section_passes_the_wording_check(self):
        doc = layout.build(with_notes([n(mc.s01(), kind="proposed_file", verification={"status": "verified", "reason": "verified", "read": "found", "sha": "a" * 40, "symbol": "x", "lines": [1], "detail": None},
                                         proposal={"repo": "r", "path": "p", "symbol": "x"})], [{"kind": "note_store_unreadable", "subject": "s", "not_established": "x", "detail": "y", "blocks_selection": True}]))
        for s, is_content in blocks.strings(doc):
            if not is_content:
                self.assertEqual(wording.check(s), [], s)


class Everywhere(sg.TempDirCase):
    """`reachmap report` and the runner read the store beside the report and show what is in it."""

    def setUp(self):
        super().setUp()
        self.r = mc.s01()
        self.name = mc.publish(self.tmp, self.r, "rep-r1")
        self.key = {k: self.r["review"]["key"][k] for k in ("workspace", "repo", "change")}
        note = notes.new_note(self.name, self.r, self.r["areas"][0]["id"], "observation", "from the model <b>x</b>", mc.CLIENT, mc.NOW)
        notes.append(self.tmp, self.key, note)

    def run_report(self, *args):
        import io

        out, err = io.StringIO(), io.StringIO()
        code = cli.main(["report", str(self.tmp / "rep-r1.json"), *args], stdout=out, stderr=err)
        return code, out.getvalue(), err.getvalue()

    def test_reachmap_report_shows_the_notes_in_markdown_and_in_the_page(self):
        code, out, err = self.run_report()
        self.assertEqual((code, err), (0, ""))
        self.assertIn("## Model notes", out)
        self.assertIn("from the model", out)
        self.assertRegex(out, r"\n\s+```\n\s+from the model <b>x</b>\n\s+```\n")  # in a fenced block: text, never markup
        self.assertTrue([v for k, v in scan(out) if k == "fence"])
        self.assertFalse([v for k, v in scan(out) if k == "text" and "<b>" in v])
        code, page, _ = self.run_report("--format", "html")
        self.assertEqual(code, 0)
        self.assertNotIn("<b>x</b>", page)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", page)

    def test_a_rerun_makes_the_note_stale_in_the_rendering(self):
        mc.publish(self.tmp, mc.rerun(self.r), "rep-r2")
        _, out, _ = self.run_report()
        self.assertIn("### Notes on an earlier revision or an older claim", out)
        self.assertNotIn("### Current notes", out)

    def test_the_report_file_is_not_written_and_a_report_without_notes_is_as_before(self):
        before = (self.tmp / "rep-r1.json").read_bytes()
        self.run_report()
        self.assertEqual((self.tmp / "rep-r1.json").read_bytes(), before)
        other = self.tmp / "other"
        other.mkdir()
        (other / "x-r1.json").write_bytes(before)
        import io

        out = io.StringIO()
        self.assertEqual(cli.main(["report", str(other / "x-r1.json")], stdout=out, stderr=io.StringIO()), 0)
        self.assertNotIn("Model notes", out.getvalue())

    def test_notes_inside_the_report_file_are_not_shown_as_if_the_store_had_them(self):
        forged = {"notes": [n(self.r, text="FORGED NOTE", client={"name": "admin", "version": "9"})], "records": [], "as_of": mc.NOW}
        for name in ("rep-r1.json",):
            data = json.loads((self.tmp / name).read_text())
            data["model_notes"] = forged
            (self.tmp / name).write_text(json.dumps(data))
        code, out, _ = self.run_report()
        self.assertEqual(code, 0)
        self.assertNotIn("FORGED", out)
        self.assertNotIn("`admin`", out)
        self.assertIn("from the model", out)  # the real note of the store is still there
        other = self.tmp / "other"
        other.mkdir()
        (other / "x-r1.json").write_text((self.tmp / "rep-r1.json").read_text())
        import io

        o = io.StringIO()
        cli.main(["report", str(other / "x-r1.json")], stdout=o, stderr=io.StringIO())
        self.assertNotIn("FORGED", o.getvalue())
        self.assertNotIn("Model notes", o.getvalue())

    def test_the_runner_drops_notes_the_report_file_holds(self):
        from tests.support_runner import Live

        live = Live()
        live.__enter__()
        self.addCleanup(live.__exit__, None, None, None)
        forged = json.loads(json.dumps(self.r))
        forged["model_notes"] = {"notes": [n(self.r, text="FORGED NOTE")], "records": [], "as_of": mc.NOW}
        mc.publish(live.reports, forged, "rep-r1")
        got = live.get("/api/reports/rep-r1").json()
        self.assertNotIn("model_notes", got)
        self.assertNotIn("FORGED", live.get("/api/reports/rep-r1.html").body.decode("utf-8"))

    def test_the_runner_api_gives_the_report_with_its_notes(self):
        from tests.support_runner import Live

        live = Live()
        live.__enter__()
        self.addCleanup(live.__exit__, None, None, None)
        mc.publish(live.reports, self.r, "rep-r1")
        note = notes.new_note("rep-r1", self.r, None, "question", "why?", mc.CLIENT, mc.NOW)
        notes.append(live.reports, self.key, note)
        got = live.get("/api/reports/rep-r1").json()
        self.assertEqual([x["text"] for x in got["model_notes"]["notes"]], ["why?"])
        page = live.get("/api/reports/rep-r1.html").body.decode("utf-8")
        self.assertIn("Model notes", page)


if __name__ == "__main__":
    unittest.main()
