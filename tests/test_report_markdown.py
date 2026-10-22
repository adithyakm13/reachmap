"""The Markdown report (M10): readable as plain text, and report content stays inert in it. Content is in code spans and fenced
blocks (the fence longer than any backtick run in what it holds) or escaped; raw HTML, link syntax, autolinks and table
cells cannot be made from it. Golden files for s11 and s13 pin the format, so a change of format is a reviewed change."""

import os
import re
import unittest

from reachmap import report
from tests import support_report as sr

ASCII_PUNCT = set("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")
BAD_CONTROL = re.compile("[\u0000-\u0008\u000b-\u001f\u007f-\u009f\u200b-\u200f\u2028-\u202e\u2060-\u2064\u2066-\u2069\ufeff\ud800-\udfff]")


def md(data, **kw):
    return report.render(data, "md", **kw)


def scan(text):
    """Walk Markdown the way a CommonMark reader would for what matters here. Yields (kind, value): ("fence", (open_len, lines)) for
    every fenced block, ("code", text) for every code span, ("text", text) for what is left of a line (escaped pairs removed) and
    ("table", cells) for a table row. A backtick run outside code that closes nothing is a violation: kind "stray"."""
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^(\s*)(`{3,})([^`]*)$", line)  # an info string cannot hold a backtick: a line that starts with a code span is not a fence
        if m:
            fence, indent = len(m.group(2)), len(m.group(1))
            body = []
            i += 1
            closed = False
            while i < len(lines):
                c = re.match(r"^(\s*)(`+)\s*$", lines[i])
                if c and len(c.group(2)) >= fence and len(c.group(1)) <= indent + 3:
                    closed = True
                    break
                body.append(lines[i])
                i += 1
            yield "fence", (fence, body, closed, m.group(3))
            i += 1
            continue
        stripped = line.lstrip()
        if stripped.startswith("|"):
            cells, cur, j = [], "", 1
            s = stripped
            while j < len(s):
                if s[j] == "\\" and j + 1 < len(s) and s[j + 1] == "|":
                    cur += "|"
                    j += 2
                    continue
                if s[j] == "|":
                    cells.append(cur)
                    cur = ""
                else:
                    cur += s[j]
                j += 1
            if cur.strip():
                cells.append(cur)
            yield "table", [c.strip() for c in cells]
            i += 1
            continue
        out, j = "", 0
        while j < len(line):
            ch = line[j]
            if ch == "\\" and j + 1 < len(line) and line[j + 1] in ASCII_PUNCT:
                j += 2
                out += "\u0001"  # an escaped character: harmless
                continue
            if ch == "`":
                k = j
                while k < len(line) and line[k] == "`":
                    k += 1
                run = k - j
                close = re.search(r"(?<!`)`{%d}(?!`)" % run, line[k:])
                if close is None:
                    yield "stray", line[max(0, j - 40):k + 40]
                    j = k
                    continue
                yield "code", line[k:k + close.start()]
                j = k + close.end()
                continue
            out += ch
            j += 1
        yield "text", out
        i += 1


class Inert(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.docs = {
            "fixture": md(sr.fixture("hostile")), "real run": md(sr.hostile_run()), "real run, author": md(sr.hostile_run(), view="author"),
            "s11 made hostile": md(sr.hostile_copy(sr.real(*sr.S11))), "s13 made hostile": md(sr.hostile_copy(sr.real(*sr.S13))),
            "failed made hostile": md(sr.hostile_copy(sr.fixture("failed"))),
        }

    def test_outside_code_there_is_no_markup_an_angle_bracket_a_bracket_or_an_emphasis_mark(self):
        for name, text in self.docs.items():
            for kind, value in scan(text):
                if kind != "text":
                    continue
                value = re.sub(r"\*\*[^*]+\*\*", "", value)  # the tool's own strong labels
                with self.subTest(doc=name, text=value[:60]):
                    self.assertNotRegex(value, r"[<>\[\]*_~|]")
                    self.assertNotRegex(value, r"&(?!amp;|lt;|gt;)")

    def test_no_backtick_is_left_outside_a_code_span_or_a_fence(self):
        for name, text in self.docs.items():
            self.assertEqual([v for k, v in scan(text) if k == "stray"], [], name)

    def test_every_fence_is_longer_than_any_backtick_run_in_its_content_and_closes(self):
        for name, text in self.docs.items():
            for kind, value in scan(text):
                if kind != "fence":
                    continue
                fence, body, closed, info = value
                with self.subTest(doc=name):
                    self.assertTrue(closed)
                    self.assertEqual(info, "")
                    longest = max((len(r) for line in body for r in re.findall(r"`+", line)), default=0)
                    self.assertGreater(fence, longest)
                    self.assertGreaterEqual(fence, 3)

    def test_the_ten_backtick_run_is_in_a_fence_of_eleven_or_more_and_still_whole(self):
        text = self.docs["real run"]
        fences = [v for k, v in scan(text) if k == "fence" and any("`" * 10 in line for line in v[1])]
        self.assertTrue(fences)
        for fence, body, closed, _ in fences:
            self.assertGreaterEqual(fence, 11)
            self.assertTrue(any("`" * 10 in line for line in body))

    def test_a_code_span_never_ends_early_because_of_a_backtick_in_what_it_holds(self):
        data = sr.fixture("complete")
        data["areas"][0]["label"] = "GET /a`b``c```d"
        data["branches"][0]["branch"] = "`edge`"
        text = md(data)
        spans = [v for k, v in scan(text) if k == "code"]
        self.assertTrue(any("GET /a`b``c```d" in s for s in spans))
        self.assertTrue(any("`edge`" in s for s in spans))
        self.assertEqual([v for k, v in scan(text) if k == "stray"], [])

    def test_the_raw_html_of_content_is_text_in_code_and_escaped_elsewhere(self):
        for name, text in self.docs.items():
            plain = [v for k, v in scan(text) if k == "text"]
            self.assertFalse(any("<" in p or ">" in p for p in plain), name)
        text = self.docs["real run"]
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", text)  # a title: escaped
        self.assertIn("<img src=x onerror=alert(1)>", "".join(v for k, v in scan(text) if k == "code"))  # a route: in code

    def test_link_syntax_from_content_is_inert_everywhere(self):
        for name, text in self.docs.items():
            for kind, value in scan(text):
                if kind == "text":
                    self.assertNotRegex(value, r"\]\(", name)
                    self.assertNotRegex(value, r"https?:|www\.", name)  # no autolink can start in plain text

    def test_a_link_in_content_is_put_in_code_rather_than_left_to_be_autolinked(self):
        data = sr.fixture("complete")
        data["findings"][0]["title"] = "See https://example.invalid/x and www.example.invalid and a@b.invalid"
        text = md(data)
        self.assertTrue(any("https://example.invalid/x" in v for k, v in scan(text) if k == "code"))
        self.assertFalse(any("example.invalid" in v for k, v in scan(text) if k == "text"))

    def test_a_newline_in_content_cannot_start_a_block(self):
        data = sr.fixture("complete")
        data["findings"][0]["title"] = "first\n# heading\n- item\n```\n<script>"
        data["incompleteness"][0]["detail"] = "x\n\n## Findings\n\n> quote"
        text = md(data)
        self.assertEqual(re.findall(r"^#{1,6} .*$", text, re.M).count("# heading"), 0)
        self.assertEqual(len(re.findall(r"^## Findings$", text, re.M)), 1)
        self.assertNotRegex(text, r"(?m)^> quote")

    def test_leading_markup_characters_of_content_are_escaped(self):
        data = sr.fixture("complete")
        for lead in ("# a", "- a", "+ a", "1. a", "2) a", "> a", "=== a", "    code"):
            data["notes"] = [lead]
            text = md(data)
            with self.subTest(lead):
                line = text.split("**Notes (1)**")[1].split("\n")[2]
                self.assertTrue(line.startswith("- "), line)
                self.assertNotRegex(line[2:], r"^(#|-|\+|\d+[.)]|>|=|\s)")
                if lead[0].isdigit():
                    self.assertRegex(line[2:], r"^\d\\[.)]")  # the delimiter is escaped, not the digit

    def test_table_rows_keep_their_columns_whatever_a_cell_holds(self):
        data = sr.fixture("complete")
        data["branches"][0]["branch"] = "a|b|c`|`"
        text = md(data)
        rows = [v for k, v in scan(text) if k == "table"]
        matrix = [r for r in rows if len(r) == 5]
        self.assertGreaterEqual(len(matrix), 5)  # the header, the rule and four rows
        self.assertEqual({len(r) for r in matrix}, {5})
        self.assertTrue(any("a\\|b\\|c" in c or "a|b|c" in c for r in matrix for c in r))

    def test_control_bidi_and_zero_width_characters_are_shown_not_sent(self):
        for name, text in self.docs.items():
            self.assertIsNone(BAD_CONTROL.search(text), name)
        self.assertIn("\\u202e", self.docs["fixture"])
        self.assertIn("\\x1b", self.docs["fixture"])

    def test_text_is_valid_utf8(self):
        for text in self.docs.values():
            text.encode("utf-8")

    def test_a_two_megabyte_line_is_cut_and_the_cut_is_said(self):
        text = md(sr.with_long_line(sr.hostile_run()))
        self.assertLess(len(text), 200_000)
        self.assertRegex(text, r"\[cut: \d+ more characters? in the report JSON\]")
        self.assertNotIn("X" * 5000, text)
        self.assertIn("X" * 100, text)

    def test_a_real_line_of_two_megabytes_in_the_change_is_cut_too(self):
        text = md(sr.hostile_run(2_000_000))
        self.assertLess(len(text), 300_000)
        self.assertNotIn("A" * 5000, text)


class Format(unittest.TestCase):
    def test_the_same_report_gives_the_same_bytes_and_ends_with_one_newline(self):
        data = sr.fixture("complete")
        a, b = md(data), md(data)
        self.assertEqual(a, b)
        self.assertTrue(a.endswith("\n") and not a.endswith("\n\n"))

    def test_the_run_time_is_not_in_the_output_so_two_runs_of_a_change_read_alike(self):
        data = sr.fixture("complete")
        other = sr.fixture("complete")
        other["run"] = {"started_at": "2030-01-01T00:00:00Z", "elapsed_s": 99.0}
        self.assertEqual(md(data), md(other))

    def test_no_line_has_trailing_spaces_and_nothing_is_wider_than_the_prose_it_holds(self):
        for line in md(sr.fixture("complete")).splitlines():
            self.assertEqual(line, line.rstrip())

    def test_nothing_in_what_the_tool_says_has_a_dash_an_arrow_or_a_curly_quote(self):
        text = re.sub(r"`[^`\n]*`", "", md(sr.fixture("complete")))
        self.assertNotRegex(text, "[\u2014\u2013\u2192\u201c\u201d\u2018\u2019]|->")

    def test_the_format_names(self):
        data = sr.fixture("complete")
        self.assertEqual(report.render(data, "markdown"), md(data))
        with self.assertRaises(ValueError):
            report.render(data, "pdf")


class Golden(unittest.TestCase):
    """`REACHMAP_UPDATE_GOLDEN=1 python3.12 -m unittest tests.test_report_markdown` rewrites the files; the diff is then reviewed."""

    def check(self, name, args):
        text = md(sr.real(*args))
        path = sr.GOLDEN / f"{name}.md"
        if os.environ.get("REACHMAP_UPDATE_GOLDEN"):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
        self.assertTrue(path.exists(), f"{path} is missing; run with REACHMAP_UPDATE_GOLDEN=1 to write it")
        self.assertEqual(text, path.read_text(encoding="utf-8"), f"the Markdown of {name} changed: review it, then REACHMAP_UPDATE_GOLDEN=1")

    def test_s11_the_contract_field(self):
        self.check("s11", sr.S11)

    def test_s13_the_branch_matrix(self):
        self.check("s13", sr.S13)

    def test_the_goldens_say_what_the_runs_found(self):
        s13 = (sr.GOLDEN / "s13.md").read_text(encoding="utf-8")
        self.assertIn("branch-missing", s13)
        self.assertIn("pre_change", s13)
        s11 = (sr.GOLDEN / "s11.md").read_text(encoding="utf-8")
        self.assertIn("contract-tightened", s11)


class Details(unittest.TestCase):
    def test_a_heading_cannot_close_itself_with_a_trailing_hash(self):
        data = sr.fixture("complete")
        for tail in ("#", "##", " #", "x #", " # #", " #  "):
            data["findings"][0]["kind"] = "kind" + tail
            text = md(data)
            heads = [ln for ln in text.splitlines() if ln.startswith("### kind")]
            with self.subTest(tail):
                self.assertEqual(len(heads), 1)
                self.assertIsNone(re.search(r"(?<!\\)#\s*$", heads[0]), heads[0])  # no closing sequence: every trailing # has its backslash

    def test_dollar_signs_and_emoji_shortcodes_in_text_cannot_become_math_or_emoji(self):
        data = sr.fixture("complete")
        data["findings"][0]["title"] = "Costs $5 and $x$ then :smile: and :+1: or 12:30:45"
        text = md(data)
        line = [ln for ln in text.splitlines() if ln.startswith("- **F1**")][0]
        plain = [v for k, v in scan(line) if k == "text"]
        self.assertFalse(any("$" in p.replace("\\$", "") for p in plain), line)
        self.assertFalse(any(re.search(r":[a-z0-9_+-]+:", p) for p in plain), line)
        self.assertIn(":smile:", "".join(v for k, v in scan(line) if k == "code") + line)  # still readable: in code

    def test_the_whole_hostile_set_is_found_nowhere_as_markup_in_the_made_hostile_real_reports(self):
        for name, data in (("s11", sr.real(*sr.S11)), ("s13", sr.real(*sr.S13)), ("failed", sr.fixture("failed")), ("timed out", sr.fixture("timed_out"))):
            text = md(sr.hostile_copy(data))
            plain = [v for k, v in scan(text) if k == "text"]
            with self.subTest(name):
                self.assertFalse(any("<" in p or ">" in p for p in plain))
                self.assertEqual([v for k, v in scan(text) if k == "stray"], [])


if __name__ == "__main__":
    unittest.main()
