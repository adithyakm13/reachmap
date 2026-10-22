"""Test path markers match by path segment or file-name part, never as a substring (Shape B)."""

import unittest

from reachmap.testpaths import is_test_path, split_marker, validate_markers
from tests.fixtures.claims import is_test_path as fixture_is_test_path

WEB = ["__tests__/", ".test.", ".spec."]
API = ["tests/", "test_"]


class Directory(unittest.TestCase):
    def test_directory_marker_matches_a_whole_segment(self):
        self.assertTrue(is_test_path("src/components/__tests__/StatusBadge.test.tsx", ["__tests__/"]))
        self.assertTrue(is_test_path("tests/test_money.py", ["tests/"]))
        self.assertTrue(is_test_path("a/b/test/c.ts", ["test/"]))

    def test_backtest_is_not_a_test_directory(self):
        self.assertFalse(is_test_path("src/backtest/engine.ts", ["test/"]))
        self.assertFalse(is_test_path("src/backtest/engine.ts", ["test/", "tests/", "__tests__/", ".test.", ".spec."]))

    def test_backtests_is_not_tests(self):
        self.assertFalse(is_test_path("services/billing/backtests/engine.py", ["tests/"]))

    def test_the_file_name_is_not_a_directory_segment(self):
        self.assertFalse(is_test_path("src/tests", ["tests/"]))  # a file called tests, not a folder
        self.assertFalse(is_test_path("tests.ts", ["tests/"]))

    def test_a_marker_with_several_segments_matches_them_in_order(self):
        self.assertTrue(is_test_path("src/__tests__/unit/a.ts", ["__tests__/unit/"]))
        self.assertFalse(is_test_path("src/unit/__tests__/a.ts", ["__tests__/unit/"]))
        self.assertFalse(is_test_path("src/x__tests__/unit/a.ts", ["__tests__/unit/"]))


class FileName(unittest.TestCase):
    def test_infix_markers_match_a_file_name_part(self):
        self.assertTrue(is_test_path("src/lib/dates.spec.ts", WEB))
        self.assertTrue(is_test_path("src/a.test.tsx", WEB))

    def test_infix_marker_in_a_directory_name_does_not_count(self):
        self.assertFalse(is_test_path("src/a.test.d/engine.ts", [".test."]))

    def test_infix_marker_needs_both_dots(self):
        self.assertFalse(is_test_path("src/contest.ts", [".test."]))
        self.assertFalse(is_test_path("src/a.testing.ts", [".test."]))
        self.assertFalse(is_test_path("src/atest.ts", [".test."]))

    def test_prefix_marker_matches_the_start_of_the_file_name(self):
        self.assertTrue(is_test_path("tests/test_money.py", ["test_"]))
        self.assertTrue(is_test_path("pkg/test_x.py", ["test_"]))

    def test_latest_rates_is_not_a_test(self):
        self.assertFalse(is_test_path("pkg/latest_rates.py", ["test_"]))
        self.assertFalse(is_test_path("pkg/contest_results.py", ["test_"]))

    def test_prefix_marker_does_not_match_a_directory(self):
        self.assertFalse(is_test_path("test_data/a.py", ["test_"]))

    def test_suffix_marker_matches_before_the_extension(self):
        self.assertTrue(is_test_path("pkg/money_test.go", ["_test"]))
        self.assertTrue(is_test_path("money_test", ["_test"]))
        self.assertFalse(is_test_path("pkg/money_tester.go", ["_test"]))
        self.assertFalse(is_test_path("pkg/_test/money.go", ["_test"]))

    def test_no_markers_means_no_test_paths(self):
        self.assertFalse(is_test_path("tests/test_x.py", []))


class Agreement(unittest.TestCase):
    PATHS = [
        "src/backtest/engine.ts", "src/components/__tests__/StatusBadge.test.tsx", "src/lib/dates.spec.ts",
        "src/lib/format.ts", "tests/test_money.py", "services/billing/backtests/engine.py", "pkg/latest_rates.py",
        "pkg/test_x.py", "a/b/tests/c.py",
    ]

    def test_agrees_with_the_fixture_parser_on_the_fixture_markers(self):
        for markers in (WEB, API):
            for path in self.PATHS:
                self.assertEqual(is_test_path(path, markers), fixture_is_test_path(path, markers), (path, markers))


class Markers(unittest.TestCase):
    def test_split_marker_kinds(self):
        self.assertEqual(split_marker("tests/"), ("dir", ("tests",)))
        self.assertEqual(split_marker(".test."), ("infix", ".test."))
        self.assertEqual(split_marker("test_"), ("prefix", "test_"))
        self.assertEqual(split_marker("_test"), ("suffix", "_test"))

    def test_bad_markers_are_refused(self):
        for bad in ("", "/", "//", "a//b/", "../", "./"):
            with self.assertRaises(ValueError, msg=bad):
                validate_markers([bad])

    def test_a_marker_that_is_not_a_string_is_refused(self):
        with self.assertRaises(ValueError):
            validate_markers([3])

    def test_backslash_paths_are_not_special(self):
        self.assertFalse(is_test_path("src\\tests\\a.py", ["tests/"]))


if __name__ == "__main__":
    unittest.main()
