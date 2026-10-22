"""`reachmap.report.load`: the report JSON is the contract. A file is read as it is, its schema version is checked (an unknown
major version is refused, never guessed at) and what a renderer reads is checked before it is read."""

import copy
import json
import unittest

from reachmap import report
from tests import support_git as sg
from tests import support_report as sr


class Load(sg.TempDirCase):
    def write(self, data, name="r.json"):
        path = self.tmp / name
        path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
        return path

    def test_a_report_is_returned_as_it_was_written(self):
        data = sr.fixture("complete")
        self.assertEqual(report.load(self.write(data)), data)

    def test_real_reports_of_every_kind_are_accepted(self):
        for name in ("complete", "failed", "timed_out", "hostile"):
            with self.subTest(name):
                self.assertEqual(report.check(sr.fixture(name))["schema"], 1)
        self.assertEqual(report.check(sr.real(*sr.S13))["status"], "completed")

    def test_an_unknown_major_version_is_refused_and_says_which_it_can_read(self):
        data = sr.fixture("complete")
        data["schema"] = 2
        with self.assertRaises(report.UnsupportedSchema) as ctx:
            report.load(self.write(data))
        self.assertIn("2", str(ctx.exception))
        self.assertIn("1", str(ctx.exception))
        self.assertTrue(issubclass(report.UnsupportedSchema, report.ReportError))

    def test_a_schema_that_is_not_a_positive_integer_is_refused(self):
        for bad in ("1", True, 0, -1, 1.0, None, [1]):
            with self.subTest(bad=bad):
                data = sr.fixture("complete")
                data["schema"] = bad
                with self.assertRaises(report.ReportError):
                    report.check(data)
        data = sr.fixture("complete")
        del data["schema"]
        with self.assertRaises(report.ReportError):
            report.check(data)

    def test_what_is_not_a_report_is_refused_with_a_message(self):
        cases = {"not json": "{", "an array": "[]", "a number": "3", "not utf-8": b"\xff\xfe{}", "NaN": '{"schema": NaN}', "empty": ""}
        for name, raw in cases.items():
            with self.subTest(name):
                path = self.tmp / "x.json"
                path.write_bytes(raw if isinstance(raw, bytes) else raw.encode())
                with self.assertRaises(report.ReportError) as ctx:
                    report.load(path)
                self.assertTrue(str(ctx.exception))

    def test_a_missing_file_is_a_report_error_not_an_os_error(self):
        with self.assertRaises(report.ReportError):
            report.load(self.tmp / "none.json")
        with self.assertRaises(report.ReportError):
            report.load(self.tmp)  # a directory

    def test_a_missing_top_level_key_is_named(self):
        for key in ("status", "change", "incompleteness", "findings", "areas", "branches", "propagations", "backend_calls", "suggestions", "notes", "ref"):
            with self.subTest(key):
                data = sr.fixture("complete")
                del data[key]
                with self.assertRaises(report.ReportError) as ctx:
                    report.check(data)
                self.assertIn(key, str(ctx.exception))

    def test_an_unknown_status_is_refused(self):
        data = sr.fixture("complete")
        data["status"] = "mostly"
        with self.assertRaises(report.ReportError):
            report.check(data)

    def test_a_completed_run_has_its_lists_and_a_failed_one_has_none(self):
        for key in ("findings", "areas", "branches", "propagations", "backend_calls"):
            with self.subTest(key):
                data = sr.fixture("complete")
                data[key] = None
                with self.assertRaises(report.ReportError):
                    report.check(data)
        for name in ("failed", "timed_out"):
            data = sr.fixture(name)
            self.assertIsNone(data["findings"])
            self.assertEqual(report.check(data)["status"], data["status"])

    def test_a_failed_run_needs_its_record(self):
        data = sr.fixture("failed")
        data["incompleteness"] = []
        with self.assertRaises(report.ReportError):
            report.check(data)

    def test_a_finding_without_evidence_is_refused_and_so_is_a_bad_enumeration(self):
        data = sr.fixture("complete")
        data["findings"][0]["evidence"] = []
        with self.assertRaises(report.ReportError) as ctx:
            report.check(data)
        self.assertIn("evidence", str(ctx.exception))
        for key, bad in (("risk", "extreme"), ("confidence", "certain"), ("kind", 3)):
            data = sr.fixture("complete")
            data["findings"][0][key] = bad
            with self.subTest(key):
                with self.assertRaises(report.ReportError):
                    report.check(data)

    def test_the_wrong_type_of_a_part_is_refused(self):
        spoil = {
            "areas": lambda d: d["areas"].append("x"), "branches": lambda d: d["branches"].__setitem__(0, 3),
            "records": lambda d: d["incompleteness"].__setitem__(0, {"kind": "x"}), "chains": lambda d: d["areas"][0].__setitem__("chains", "abc"),
            "evidence line": lambda d: d["areas"][0]["route_line"].__setitem__("line", "9"), "propagations": lambda d: d.__setitem__("propagations", {}),
            "notes": lambda d: d.__setitem__("notes", "a note"), "change": lambda d: d.__setitem__("change", []),
        }
        for name, f in spoil.items():
            data = sr.fixture("complete")
            f(data)
            with self.subTest(name):
                with self.assertRaises(report.ReportError):
                    report.check(data)

    def test_a_value_json_cannot_carry_is_a_report_error_when_the_page_is_made(self):
        data = sr.fixture("complete")
        data["propagations"][0]["odd"] = float("nan")
        with self.assertRaises(report.ReportError):
            report.render(data, "html")
        data["propagations"][0]["odd"] = {1, 2}
        with self.assertRaises(report.ReportError):
            report.render(data, "html")

    def test_the_check_does_not_change_what_it_is_given(self):
        data = sr.fixture("hostile")
        before = copy.deepcopy(data)
        report.check(data)
        self.assertEqual(data, before)

    def test_a_report_a_renderer_could_not_read_is_refused_before_rendering(self):
        data = sr.fixture("complete")
        data["areas"][0]["chains"] = [["not evidence"]]
        with self.assertRaises(report.ReportError):
            report.render(data, "md")


class NonFinite(sg.TempDirCase):
    def test_a_float_that_is_not_finite_is_refused_in_text_and_in_memory(self):
        base = json.dumps(sr.fixture("complete"))
        for literal in ("1e999", "-1e999", "NaN", "Infinity", "-Infinity"):
            text = base.replace('"elapsed_s": 1.5', f'"elapsed_s": {literal}')
            self.assertIn(literal, text)
            with self.subTest(literal), self.assertRaises(report.ReportError):
                report.parse(text)
        for value in (float("inf"), float("-inf"), float("nan")):
            data = sr.fixture("complete")
            data["propagations"][0]["deep"] = [{"x": value}]
            with self.subTest(value=value), self.assertRaises(report.ReportError):
                report.check(data)

    def test_a_finite_float_passes(self):
        data = sr.fixture("complete")
        data["run"]["elapsed_s"] = 1e300
        self.assertEqual(report.check(data)["run"]["elapsed_s"], 1e300)


class NoInvisibleCharacters(unittest.TestCase):
    """A source file with a bidi override or a zero width character in it can read other than it runs (Trojan Source): the files of the
    package and of the tests say such characters with a `\\u` escape, and the tests that need one build it."""

    def test_no_source_file_holds_a_raw_bidi_control_or_invisible_character(self):
        import re
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        bad = re.compile("[\u00ad\u061c\u200b-\u200f\u2028-\u202e\u2060-\u2064\u2066-\u2069\ufeff]")
        found = []
        for folder in ("reachmap", "tests"):
            for path in sorted((root / folder).rglob("*")):
                if path.suffix in (".py", ".js", ".css", ".html", ".toml") and path.is_file():
                    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                        if bad.search(line):
                            found.append(f"{path.relative_to(root)}:{n}")
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
