import json
import unittest
from pathlib import Path

from reachmap import wording
from reachmap.wording import WordingError, check, require_clean

PHRASES = (
    "covered by",
    "already covers",
    "no need to test",
    "nothing to test",
    "safe",
    "is safe",
    "safe to release",
    "fully covered",
    "no impact",
)


class WordingTest(unittest.TestCase):
    def test_banned_list_has_every_phrase_of_the_brief(self):
        for phrase in PHRASES:
            self.assertIn(phrase, wording.BANNED)

    def test_check_finds_each_banned_phrase(self):
        for phrase in PHRASES:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, check(f"The change is {phrase} here"))

    def test_check_is_case_insensitive(self):
        self.assertIn("safe", check("SAFE"))
        self.assertIn("covered by", check("Covered By a test"))
        self.assertIn("no need to test", check("No Need To Test this"))

    def test_check_tolerates_extra_whitespace_inside_a_phrase(self):
        self.assertIn("no need to test", check("no  need\tto test"))

    def test_safe_matches_on_word_boundaries_only(self):
        for text in ("unsafe", "safety", "safely", "safeguard", "unsafely"):
            with self.subTest(text=text):
                self.assertEqual(check(text), [])
        self.assertEqual(check("a safe place"), ["safe"])

    def test_a_hyphen_counts_as_a_space_inside_a_phrase(self):
        for text, phrase in (
            ("fully-covered", "fully covered"),
            ("no-impact", "no impact"),
            ("covered-by tests", "covered by"),
            ("no-need-to-test", "no need to test"),
            ("safe-to-release", "safe to release"),
        ):
            with self.subTest(text=text):
                self.assertIn(phrase, check(text))

    def test_the_module_says_how_gaps_are_worded(self):
        self.assertIn("no mapped test", wording.__doc__)

    def test_clean_text_returns_nothing(self):
        self.assertEqual(check("Callers of formatAmount were not established"), [])
        self.assertEqual(check(""), [])

    def test_require_clean_names_the_field_and_the_phrase(self):
        with self.assertRaises(WordingError) as cm:
            require_clean("This is covered by tests", "title")
        message = str(cm.exception)
        self.assertIn("title", message)
        self.assertIn("covered by", message)

    def test_require_clean_passes_clean_text(self):
        require_clean("Screens reached through StatusBadge", "title")

    def test_the_shared_cases_the_frontend_mirror_is_tested_with(self):
        path = Path(__file__).resolve().parents[1] / "frontend" / "fixtures" / "wording_cases.json"
        cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
        self.assertGreater(len(cases), 50)
        for case in cases:
            with self.subTest(text=case["text"]):
                self.assertEqual(check(case["text"]), case["found"])


if __name__ == "__main__":
    unittest.main()
