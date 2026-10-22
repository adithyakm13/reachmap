"""The text a person types into a decision (the author's name, the reason) is checked by one rule for every channel: `reachmap decide`, the
decisions file the page exports, and the runner. An author's name has no control, invisible or direction-changing character; a reason may have a
newline and a tab and nothing else of those kinds. Markup, quotes and every visible character are allowed: report content is never trusted
by what it holds but by how it is shown."""

from __future__ import annotations

import unittest

from reachmap.review import formats

BAD_AUTHORS = [
    "Quinn\x1b", "Qu\x00inn", "Quinn\n", "Quinn\t", "Quinn\r", "Quinn\x7f", "Quinn\x85", "Quinn\u202e", "\u202aQuinn", "Quinn\u2066", "Quinn\u2069", "Quinn\u200b", "Quinn\u200d",
    "Quinn\u200e", "Quinn\u200f", "Quinn\u061c", "Quinn\ufeff", "Quinn\u2028", "Quinn\u2029", "Quinn\u00ad", "Quinn\ud800",
]
BAD_REASONS = ["x\x1b[31m", "x\x00", "x\r", "x\x7f", "x\x85", "x\u202e", "x\u202a", "x\u2066", "x\u2069", "x\u200e", "x\u200f", "x\u061c", "x\u2028", "x\u2029", "x\x0b", "x\x0c", "x\ud800"]
GOOD = ["Quinn", "Quinn O'Neil", "</b>Quinn <img src=x onerror=alert(1)>", "\u00c9mile", "Zo\u00eb", "\u5f20\u4f1f", "Quinn \U0001f600", "a" * 100, "it is safe, covered by tests"]


class Author(unittest.TestCase):
    def test_refused(self) -> None:
        for name in BAD_AUTHORS:
            with self.subTest(name=repr(name)):
                with self.assertRaises(formats.TextRefused) as ctx:
                    formats.check_author(name)
                self.assertNotIn(name.strip(), str(ctx.exception).replace("Quinn", "") if name.strip() != "Quinn" else "")
                self.assertEqual(str(ctx.exception), formats.AUTHOR_WORDS)

    def test_allowed(self) -> None:
        for name in GOOD:
            self.assertEqual(formats.check_author(name), name)


class Reason(unittest.TestCase):
    def test_refused(self) -> None:
        for text in BAD_REASONS:
            with self.subTest(text=repr(text)):
                with self.assertRaises(formats.TextRefused) as ctx:
                    formats.check_reason(text)
                self.assertEqual(str(ctx.exception), formats.REASON_WORDS)

    def test_a_newline_and_a_tab_are_allowed_and_so_is_every_visible_character(self) -> None:
        for text in ("", "line one\nline two\twith a tab", "</script><script>alert(1)</script> <!-- ]]> \"><svg onload=x> `````` [x](javascript:alert(1))", "\U0001f468\u200d\U0001f469 family joiner",
                     "\u00e9\u5f20\u4f1f", "it is safe"):
            self.assertEqual(formats.check_reason(text), text)

    def test_the_words_are_fixed_and_repeat_nothing(self) -> None:
        self.assertNotIn("\x1b", formats.AUTHOR_WORDS + formats.REASON_WORDS)
        for words in (formats.AUTHOR_WORDS, formats.REASON_WORDS):
            self.assertTrue(words.isascii())


if __name__ == "__main__":
    unittest.main()
