"""The single-file HTML report (M10): one page, no URL, no network, and report content that cannot become markup.

The page is parsed with the standard library's `html.parser` and what comes out is what is asserted: the elements and attributes
are the template's and never the content's, the page holds exactly one JSON script and one inline script and neither can be closed
early, the JSON parses back to the report, and the Content Security Policy names the hash of the script."""

import base64
import hashlib
import json
import re
import shutil
import subprocess
import unittest
from html.parser import HTMLParser
from importlib import resources

from reachmap import report, wording
from tests import support_report as sr

TAGS = frozenset({
    "html", "head", "meta", "title", "style", "body", "main", "header", "section", "h1", "h2", "h3", "h4", "h5", "h6", "p", "ul", "ol", "li", "details",
    "summary", "table", "thead", "tbody", "tr", "th", "td", "pre", "code", "strong", "em", "span", "div", "script", "caption", "link",
})
VOID = {"meta", "br", "hr", "img", "input", "link"}
ATTRS = frozenset({"lang", "charset", "http-equiv", "content", "name", "class", "id", "type", "scope", "open", "data-a", "data-f", "data-s", "rel", "href"})
ICON = ("link", (("rel", "icon"), ("href", "data:,")))  # the one link of the page: an empty data URL, so that a browser does not ask for /favicon.ico
SAFE_VALUE = re.compile(r"^[a-z0-9_ -]*$")
URL = re.compile(r"https?:|(?<![:\w])//|\burl\(|javascript:", re.I)  # `\burl\(` is CSS: createObjectURL( is the page making a download link (M11)
_Q = "[\"'`]"  # a quote of any kind
_DANGEROUS = "innerHTML|outerHTML|insertAdjacentHTML|srcdoc|href|src|action|formAction"
FORBIDDEN_JS = re.compile("|".join([
    "innerHTML", "outerHTML", "insertAdjacentHTML", r"document\s*\.\s*write", r"\beval\b", r"(?-i:\bFunction\b)",
    rf"setTimeout\s*\(\s*{_Q}", rf"setInterval\s*\(\s*{_Q}",
    rf"\.\s*({_DANGEROUS})\s*=", rf"setAttribute\s*\(\s*{_Q}({_DANGEROUS}|on\w+|style)",
    rf"\[\s*{_Q}\s*({_DANGEROUS})",  # bracket access: el["innerHTML"]
    r"\bfetch\s*\(", "XMLHttpRequest", "WebSocket", "sendBeacon", r"\bimport\s*\(", "importScripts", r"\.open\s*\(", r"location\s*[.=]",
    "createContextualFragment", "DOMParser", "setHTMLUnsafe", "parseFromString", r"\bsrcdoc\b",
    rf"{_Q}\s*\+\s*{_Q}",  # a name put together from two literals
    rf"createElement\s*\(\s*{_Q}(script|iframe|object|embed|link|img|a|form){_Q}",
]), re.I)


# The one link the script makes (M11): the download of the decisions file, an anchor made for the click, pointing at a Blob the script made.
# These two lines are the only places `createElement("a")` and `.href =` may appear, each exactly once (tests/test_review_js.py pins the rest).
EXPORT_LINK_LINES = ('var link = env.doc.createElement("a");', "link.href = env.URL.createObjectURL(blob);")


class Parsed(HTMLParser):
    """Everything the page holds, in the order met: the tags and attributes, the text per kind, and whether the tags nest."""

    def __init__(self, page):
        super().__init__(convert_charrefs=True)
        self.tags, self.stack, self.errors = [], [], []
        self.scripts, self.styles, self.titles, self.text, self.comments, self.headings = [], [], [], [], [], []
        self._in = None
        self.feed(page)
        self.close()

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, tuple(attrs)))
        if tag in VOID:
            return
        self.stack.append(tag)
        self._in = tag if tag in ("script", "style", "title") else self._in
        if tag == "script":
            self.scripts.append({"attrs": dict(attrs), "text": ""})
        if tag == "style":
            self.styles.append("")
        if tag == "title":
            self.titles.append("")
        if re.fullmatch(r"h[1-6]", tag):
            self.headings.append(int(tag[1]))

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"</{tag}> closes {self.stack[-1] if self.stack else 'nothing'}")
            return
        self.stack.pop()
        if tag in ("script", "style", "title"):
            self._in = None

    def handle_data(self, data):
        if self._in == "script":
            self.scripts[-1]["text"] += data
        elif self._in == "style":
            self.styles[-1] += data
        elif self._in == "title":
            self.titles[-1] += data
        else:
            self.text.append(data)

    def handle_comment(self, data):
        self.comments.append(data)

    def unknown_decl(self, data):
        self.comments.append(data)

    def handle_decl(self, decl):
        self.comments.append(decl)

    def handle_pi(self, data):
        self.comments.append(data)

    @property
    def json_scripts(self):
        return [s for s in self.scripts if s["attrs"].get("type") == "application/json"]

    @property
    def code_scripts(self):
        return [s for s in self.scripts if "type" not in s["attrs"]]


def html(data, **kw):
    return report.render(data, "html", **kw)


class Structure(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reports = {
            "hostile fixture": sr.fixture("hostile"), "real hostile run": sr.hostile_run(), "complete": sr.fixture("complete"),
            "failed": sr.fixture("failed"), "timed out": sr.fixture("timed_out"), "s13": sr.real(*sr.S13),
        }
        cls.pages = {k: html(v) for k, v in cls.reports.items()}
        cls.parsed = {k: Parsed(v) for k, v in cls.pages.items()}

    def test_one_json_script_and_one_inline_script_and_nothing_else_runs(self):
        for name, p in self.parsed.items():
            with self.subTest(name):
                self.assertEqual(len(p.json_scripts), 1)
                self.assertEqual(len(p.code_scripts), 1)
                self.assertEqual(len(p.scripts), 2)
                self.assertEqual(p.json_scripts[0]["attrs"], {"type": "application/json", "id": "report-data"})

    def test_neither_script_body_can_be_closed_early_or_hide_a_comment(self):
        for name, p in self.parsed.items():
            for s in p.scripts:
                with self.subTest(name):
                    self.assertNotRegex(s["text"], r"(?i)</script")
                    self.assertNotIn("<!--", s["text"])
                    self.assertNotIn("-->", s["text"])
                    self.assertNotIn("]]>", s["text"].replace("\\u003e", ""))

    def test_the_page_holds_no_comment_declaration_or_processing_instruction_but_its_doctype(self):
        for name, p in self.parsed.items():
            self.assertEqual([c for c in p.comments if c.lower() != "doctype html"], [], name)

    def test_the_embedded_json_parses_back_to_exactly_the_report(self):
        for name, p in self.parsed.items():
            with self.subTest(name):
                self.assertEqual(json.loads(p.json_scripts[0]["text"]), self.reports[name])

    def test_the_embedded_json_has_no_raw_angle_bracket_ampersand_or_line_separator(self):
        for name, p in self.parsed.items():
            text = p.json_scripts[0]["text"]
            with self.subTest(name):
                for ch in "<>&\u2028\u2029":
                    self.assertNotIn(ch, text)
                self.assertTrue(text.isascii())
        self.assertIn("\\u003c", self.parsed["hostile fixture"].json_scripts[0]["text"])
        self.assertIn("\\u2028", self.parsed["hostile fixture"].json_scripts[0]["text"])

    def test_only_the_templates_elements_and_attributes_appear_and_their_values_are_never_content(self):
        for name, p in self.parsed.items():
            for tag, attrs in p.tags:
                with self.subTest(name, tag=tag):
                    self.assertIn(tag, TAGS)
                    for key, value in attrs:
                        self.assertIn(key, ATTRS)
                        if tag == "meta" or (tag, key, value) == ("script", "type", "application/json") or (tag, key, value) in (("link", "rel", "icon"), ("link", "href", "data:,")):
                            continue
                        self.assertRegex(value or "", SAFE_VALUE)

    def test_the_tags_nest_and_every_one_is_closed(self):
        for name, p in self.parsed.items():
            with self.subTest(name):
                self.assertEqual(p.errors, [])
                self.assertEqual(p.stack, [])

    def test_the_hostile_strings_add_text_and_no_element_or_attribute_name(self):
        """The same report with the hostile strings removed has the same kinds of element and attribute."""
        a = Parsed(html(sr.fixture("hostile")))
        b = Parsed(html(sr.fixture("complete")))
        self.assertEqual({t for t, _ in a.tags}, {t for t, _ in b.tags})
        self.assertEqual({k for _, attrs in a.tags for k, _ in attrs}, {k for _, attrs in b.tags for k, _ in attrs})
        self.assertEqual([t for t in a.headings], [t for t in b.headings])

    def test_the_hostile_strings_are_in_the_page_as_text_only(self):
        p = self.parsed["real hostile run"]
        text = "".join(p.text)
        self.assertIn("<img src=x onerror=alert(1)>", text)
        self.assertIn("</script><script>alert(1)</script>", text)
        self.assertIn("[x](javascript:alert(1))", text)
        self.assertIn("<details open>", text)
        # in the markup they are only escaped: the parser met none of them as an element
        self.assertNotIn("img", [t for t, _ in p.tags])
        self.assertNotIn("svg", [t for t, _ in p.tags])
        self.assertEqual(sum(1 for t, _ in p.tags if t == "script"), 2)
        self.assertNotIn("<img src=x", re.sub(r"(?s)<script.*?</script>", "", self.pages["real hostile run"]))

    def test_no_attribute_is_an_event_handler_a_link_a_source_or_a_style(self):
        for name, p in self.parsed.items():
            for _, attrs in p.tags:
                for key, _ in attrs:
                    self.assertFalse(key.startswith("on") or key in ("src", "srcdoc", "style", "action", "formaction"), (name, key))
        for name, p in self.parsed.items():
            self.assertEqual([(t, a) for t, a in p.tags if any(k == "href" for k, _ in a)], [ICON], name)  # no link but the icon, whose target is data:,

    def test_a_lone_surrogate_a_control_character_and_a_bidi_override_do_not_reach_the_page_raw(self):
        page = self.pages["hostile fixture"]
        page.encode("utf-8")
        text = "".join(self.parsed["hostile fixture"].text)
        self.assertNotRegex(text, "[\u0000-\u0008\u000b-\u001f\u202a-\u202e\u2028\u2029\u2066-\u2069\ud800-\udfff]")
        self.assertIn("\\u202e", text)

    def test_the_page_is_deterministic(self):
        self.assertEqual(html(self.reports["complete"]), html(self.reports["complete"]))


class Policy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = html(sr.hostile_run())
        cls.p = Parsed(cls.page)

    def csp(self):
        (meta,) = [dict(a) for t, a in self.p.tags if t == "meta" and dict(a).get("http-equiv", "").lower() == "content-security-policy"]
        return meta["content"]

    def test_the_policy_is_the_briefs_with_the_hash_of_the_inline_script(self):
        (code,) = self.p.code_scripts
        digest = base64.b64encode(hashlib.sha256(code["text"].encode("utf-8")).digest()).decode()
        self.assertEqual(
            self.csp(),
            f"default-src 'none'; style-src 'unsafe-inline'; script-src 'sha256-{digest}'; img-src data:; base-uri 'none'; form-action 'none'")

    def test_no_connect_src_frame_or_other_source_is_named_so_the_page_cannot_call_out(self):
        csp = self.csp()
        self.assertNotIn("connect-src", csp)
        self.assertIn("default-src 'none'", csp)
        self.assertNotRegex(csp, r"https?:|\*")
        self.assertNotIn("'unsafe-eval'", csp)
        self.assertNotIn("script-src 'unsafe-inline'", csp)

    def test_the_hash_follows_the_script(self):
        other = html(sr.fixture("complete"))
        p = Parsed(other)
        (code,) = p.code_scripts
        digest = base64.b64encode(hashlib.sha256(code["text"].encode("utf-8")).digest()).decode()
        self.assertIn(f"'sha256-{digest}'", other)

    def test_there_is_no_url_in_the_template_the_style_the_script_or_any_attribute(self):
        for name, data in (("hostile", sr.hostile_run()), ("complete", sr.fixture("complete")), ("failed", sr.fixture("failed"))):
            p = Parsed(html(data))
            with self.subTest(name):
                for css in p.styles:
                    self.assertIsNone(URL.search(css))
                for code in p.code_scripts:
                    self.assertIsNone(URL.search(code["text"]))
                for _, attrs in p.tags:
                    for key, value in attrs:
                        self.assertIsNone(URL.search(value or ""), (key, value))
                self.assertIsNone(re.search(r"@import|@font-face|<img|<iframe|<object|<embed|<base|<form|<a\s", html(data).replace("&lt;", "").replace('<link rel="icon" href="data:,">', "")))

    def test_a_benign_report_has_no_url_anywhere_in_the_page_but_the_embedded_json_strings(self):
        data = sr.fixture("complete")
        page = html(data)
        stripped = re.sub(r"(?s)<script type=\"application/json\".*?</script>", "", page)
        self.assertIsNone(URL.search(stripped), URL.search(stripped))

    def test_the_title_is_text_and_names_the_report(self):
        (title,) = self.p.titles
        self.assertTrue(title.startswith("Reachmap report"))
        self.assertIn("acme/solo", title)


class Assets(unittest.TestCase):
    def asset(self, name):
        return resources.files("reachmap.report").joinpath("assets", name).read_text(encoding="utf-8")

    def test_the_three_assets_exist_and_are_inlined_into_the_page(self):
        page = html(sr.fixture("complete"))
        for name in ("report.css", "report.js"):
            self.assertIn(self.asset(name).strip(), page)
        self.assertTrue(page.lower().startswith("<!doctype html>"))
        self.assertTrue(self.asset("report.html").strip().startswith("<!doctype html>"))

    def test_the_script_never_builds_markup_from_text_or_sets_a_link_a_source_or_calls_out(self):
        js = self.asset("report.js")
        for line in EXPORT_LINK_LINES:
            self.assertEqual(js.count(line), 1, line)
            js = js.replace(line, "")
        self.assertIsNone(FORBIDDEN_JS.search(js), FORBIDDEN_JS.search(js))
        self.assertIn("JSON.parse", js)
        self.assertIn("textContent", js)
        self.assertIn("createElement", js)

    def test_the_template_the_style_and_the_script_hold_no_url_and_no_comment(self):
        for name in ("report.html", "report.css", "report.js"):
            text = self.asset(name)
            with self.subTest(name):
                self.assertIsNone(URL.search(text), URL.search(text))
                self.assertNotIn("<!--", text)
                self.assertNotIn("@import", text)
                self.assertNotIn("url(", text)

    def test_the_theme_is_kept_in_local_storage_only_inside_try_and_the_page_works_when_it_throws(self):
        js = self.asset("report.js")
        uses = [m.start() for m in re.finditer(r"localStorage", js)]
        self.assertTrue(uses)
        for pos in uses:
            before = js[:pos]
            self.assertGreater(before.rfind("try {"), before.rfind("}"), "localStorage used outside a try block")
        self.assertIn("catch", js)

    def test_the_script_is_valid_javascript(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed; the script is checked by eye and by the patterns above")
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "report.js")
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.asset("report.js"))
            done = subprocess.run([node, "--check", path], capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)

    def test_the_style_has_a_light_default_a_dark_theme_a_print_rule_and_a_visible_focus(self):
        css = self.asset("report.css")
        self.assertIn("@media print", css)
        self.assertIn(":focus-visible", css)
        self.assertIn('[data-theme="dark"]', css)
        self.assertIn("system-ui", css)
        self.assertIn("ui-monospace", css)
        self.assertNotRegex(css, r"@font-face|woff|ttf|otf")

    def test_everything_the_page_says_passes_the_wording_check(self):
        for name in ("report.html", "report.css", "report.js"):
            self.assertEqual(wording.check(self.asset(name)), [], name)

    def test_the_package_ships_its_assets(self):
        import tomllib
        from pathlib import Path
        meta = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
        data = meta["tool"]["setuptools"]["package-data"]
        self.assertIn("assets/*", data["reachmap.report"])


class Accessibility(unittest.TestCase):
    def test_headings_come_in_order_without_a_skipped_level(self):
        for name in ("complete", "hostile", "failed"):
            p = Parsed(html(sr.fixture(name)))
            with self.subTest(name):
                self.assertEqual(p.headings[0], 1)
                self.assertEqual(p.headings.count(1), 1)
                for a, b in zip(p.headings, p.headings[1:]):
                    self.assertLessEqual(b, a + 1)

    def test_every_table_has_header_cells_with_a_scope_and_a_caption(self):
        p = Parsed(html(sr.fixture("complete")))
        tables = [i for i, (t, _) in enumerate(p.tags) if t == "table"]
        self.assertTrue(tables)
        self.assertGreaterEqual(len([1 for t, _ in p.tags if t == "caption"]), len(tables))
        ths = [dict(a) for t, a in p.tags if t == "th"]
        self.assertTrue(ths)
        self.assertTrue(all(a.get("scope") in ("col", "row") for a in ths))

    def test_the_page_names_its_language_and_the_viewport(self):
        page = html(sr.fixture("complete"))
        self.assertRegex(page, r'<html lang="en"')
        self.assertIn('name="viewport"', page)

    def test_the_script_makes_buttons_as_buttons_and_labels_its_controls(self):
        js = resources.files("reachmap.report").joinpath("assets", "report.js").read_text(encoding="utf-8")
        self.assertIn('createElement("button")', js)
        self.assertIn('createElement("label")', js)
        self.assertNotRegex(js, r'createElement\("div"\)[^;]*addEventListener\("click"')


class Content(unittest.TestCase):
    def test_a_report_without_javascript_has_every_area_finding_and_record_in_the_page_text(self):
        data = sr.fixture("complete")
        p = Parsed(html(data))
        text = " ".join(p.text)
        for a in data["areas"]:
            self.assertIn(a["label"], text)
        for f in data["findings"]:
            self.assertIn(f["title"].replace("'", "'"), text)
        for r in data["incompleteness"]:
            self.assertIn(r["not_established"], text)

    def test_the_chains_are_collapsible_details_and_the_filters_have_indices_to_work_from(self):
        p = Parsed(html(sr.fixture("complete")))
        details = [dict(a) for t, a in p.tags if t == "details"]
        self.assertTrue(any("chains" in d.get("class", "") for d in details))
        self.assertTrue(all("open" not in d for d in details))  # folded by default, with or without the script
        self.assertTrue([a for t, a in p.tags if dict(a).get("data-a") is not None])
        self.assertTrue([a for t, a in p.tags if dict(a).get("data-f") is not None])

    def test_a_failed_run_shows_the_header_and_records_and_says_why_nothing_else_is_shown(self):
        p = Parsed(html(sr.fixture("failed")))
        text = " ".join(p.text)
        self.assertIn("No findings are shown because the run did not complete.", text)
        self.assertNotIn("Areas to test", text)

    def test_a_long_line_is_cut_in_the_page_but_whole_in_the_embedded_json(self):
        data = sr.with_long_line(sr.hostile_run(), 500_000)
        page = html(data)
        p = Parsed(page)
        self.assertEqual(json.loads(p.json_scripts[0]["text"]), data)
        visible = "".join(p.text)
        self.assertNotIn("X" * 5000, visible)
        self.assertRegex(visible, r"\[cut: \d+ more characters? in the report JSON\]")
        self.assertNotRegex(p.code_scripts[0]["text"], r"(?i)</script")

    def test_a_two_megabyte_line_still_gives_a_page_that_parses(self):
        data = sr.with_long_line(sr.hostile_run())
        p = Parsed(html(data))
        self.assertEqual(len(p.json_scripts), 1)
        self.assertEqual(len(p.code_scripts), 1)
        self.assertEqual(json.loads(p.json_scripts[0]["text"]), data)

    def test_the_theme_toggle_and_the_filters_are_the_scripts_so_the_static_page_has_no_control(self):
        p = Parsed(html(sr.fixture("complete")))
        self.assertNotIn("button", [t for t, _ in p.tags])
        self.assertNotIn("select", [t for t, _ in p.tags])


class Usability(unittest.TestCase):
    def js(self):
        return resources.files("reachmap.report").joinpath("assets", "report.js").read_text(encoding="utf-8")

    def css(self):
        return resources.files("reachmap.report").joinpath("assets", "report.css").read_text(encoding="utf-8")

    def test_the_page_says_it_has_no_icon_to_fetch_so_a_server_is_never_asked_for_one(self):
        page = html(sr.fixture("complete"))
        self.assertIn('<link rel="icon" href="data:,">', page.split("<title>")[0])
        self.assertEqual(page.count("<link"), 1)
        self.assertIn("img-src data:", page)  # the policy that allows exactly this

    def test_a_table_row_with_a_natural_key_has_a_row_header(self):
        p = Parsed(html(sr.fixture("complete")))
        scopes = [dict(a).get("scope") for t, a in p.tags if t == "th"]
        self.assertIn("row", scopes)
        self.assertIn("col", scopes)
        counts = html(sr.fixture("complete")).split("<caption>Counts</caption>")[1].split("</table>")[0]
        self.assertIn('<tbody><tr><th scope="row">Areas to test</th><td>3</td></tr>', counts)
        matrix = html(sr.fixture("complete")).split("<caption>Branch matrix</caption>")[1].split("</table>")[0]
        self.assertRegex(matrix, r'<tr><th scope="row"><code>main</code></th><td>')

    def test_the_theme_button_has_a_fixed_label_and_says_its_state_with_aria_pressed_only(self):
        js = self.js()
        self.assertIn('aria-pressed', js)
        self.assertEqual(len(re.findall(r'textContent\s*=\s*"[^"]*theme', js)), 1)  # one label, set once
        self.assertNotIn("Use the light theme", js)
        self.assertNotRegex(js, r'textContent\s*=\s*theme')

    def test_the_print_style_forces_light_backgrounds_for_code_blocks_and_headers(self):
        css = self.css()
        printing = css.split("@media print")[1]
        for selector in ("pre", "th", "code"):
            self.assertRegex(printing, rf"(^|[\s,{{]){selector}\b[^{{}}]*\{{[^}}]*background:\s*#fff", selector)

    def test_every_filtered_list_follows_its_group_heading_so_that_the_script_can_hide_the_two_together(self):
        """The script hides the heading and the note of a group when a filter hides every item of its list: they are the siblings before it."""
        page = html(sr.fixture("complete"))
        self.assertEqual(len(re.findall(r"</h3>(?:<p>[^<]*</p>)?<ul><li data-a=", page)), 3)  # must test, sample, candidates
        self.assertIn('data-f="0"', page)
        self.assertIn("previousElementSibling", self.js())
        self.assertIn('"H3"', self.js())


if __name__ == "__main__":
    unittest.main()
