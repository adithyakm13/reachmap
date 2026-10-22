"""tests/run_parallel.py: the suite's modules in several worker processes, balanced by a timing file."""

import json
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

from tests import run_parallel
from tests import support_git as sg

SCRIPT = Path(run_parallel.__file__).resolve()
ROOT = SCRIPT.parent.parent


class Binning(unittest.TestCase):
    def test_every_module_is_in_exactly_one_bin(self):
        costs = {f"m{i}": float(i + 1) for i in range(11)}
        bins = run_parallel.bin_modules(costs, 4)
        self.assertEqual(len(bins), 4)
        self.assertEqual(sorted(m for b in bins for m in b), sorted(costs))

    def test_the_bins_are_balanced_by_the_recorded_times_and_the_longest_module_goes_first(self):
        costs = {"big": 50.0, "a": 20.0, "b": 20.0, "c": 10.0, "d": 10.0, "e": 10.0}
        bins = run_parallel.bin_modules(costs, 2)
        loads = sorted(sum(costs[m] for m in b) for b in bins)
        self.assertEqual(loads, [60.0, 60.0])
        self.assertEqual(bins[0][0], "big")

    def test_the_binning_is_deterministic(self):
        costs = {f"m{i}": float(i % 3) for i in range(20)}
        self.assertEqual(run_parallel.bin_modules(costs, 3), run_parallel.bin_modules(dict(reversed(list(costs.items()))), 3))

    def test_fewer_modules_than_workers_leaves_no_empty_worker(self):
        self.assertEqual(run_parallel.bin_modules({"a": 1.0, "b": 2.0}, 4), [["b"], ["a"]])
        self.assertEqual(run_parallel.bin_modules({}, 4), [])

    def test_a_module_with_no_recorded_time_costs_the_default(self):
        got = run_parallel.costs_for(["tests.a", "tests.b"], {"tests.a": 3.0})
        self.assertEqual(got, {"tests.a": 3.0, "tests.b": run_parallel.DEFAULT_COST})

    def test_the_modules_are_the_test_files_of_the_suite_and_not_this_runner(self):
        found = run_parallel.suite_modules()
        self.assertIn("tests.test_run_parallel", found)
        self.assertIn("tests.test_model", found)
        self.assertNotIn("tests.run_parallel", found)
        self.assertNotIn("tests.support_git", found)
        self.assertEqual(found, sorted(found))

    def test_the_committed_timing_file_names_real_modules_with_a_time_each(self):
        data = json.loads(run_parallel.TIMINGS.read_text(encoding="utf-8"))
        self.assertTrue(set(data["modules"]) <= set(run_parallel.suite_modules()), set(data["modules"]) - set(run_parallel.suite_modules()))
        self.assertGreater(len(data["modules"]), 30)
        self.assertTrue(all(isinstance(v, (int, float)) and v >= 0 for v in data["modules"].values()))


class Running(sg.TempDirCase):
    def modules(self):
        (self.tmp / "pkg_ok.py").write_text(textwrap.dedent('''
            import unittest
            class A(unittest.TestCase):
                def test_one(self): pass
                def test_two(self): pass
                @unittest.skip("not now")
                def test_skipped(self): pass
        '''))
        (self.tmp / "pkg_bad.py").write_text(textwrap.dedent('''
            import unittest
            class B(unittest.TestCase):
                def test_fails(self): self.assertEqual(1, 2, "one is not two")
                def test_passes(self): pass
        '''))
        (self.tmp / "pkg_broken.py").write_text("import a_module_that_is_not_there\n")

    def run_it(self, *args):
        proc = subprocess.run([sys.executable, str(SCRIPT), "--path", str(self.tmp), "--timings", str(self.tmp / "t.json"), *args],
                              cwd=ROOT, capture_output=True, text=True, timeout=120)
        return proc.returncode, proc.stdout, proc.stderr

    def test_passing_modules_exit_0_and_every_module_has_its_time_and_count(self):
        self.modules()
        code, out, err = self.run_it("-j", "2", "pkg_ok")
        self.assertEqual((code, err), (0, ""))
        self.assertRegex(out, r"pkg_ok\s+3 tests?\s+[\d.]+ s")
        self.assertIn("OK", out.splitlines()[-1])
        self.assertIn("1 skipped", out)

    def test_a_failure_in_any_worker_exits_1_and_is_shown_with_its_text(self):
        self.modules()
        code, out, err = self.run_it("-j", "2", "pkg_ok", "pkg_bad")
        self.assertEqual(code, 1)
        self.assertIn("one is not two", out + err)
        self.assertIn("FAILED", out.splitlines()[-1])
        self.assertRegex(out, r"pkg_bad\s+2 tests?\s+[\d.]+ s\s+FAILED")
        self.assertRegex(out, r"pkg_ok\s+3 tests?\s+[\d.]+ s\s+ok")

    def test_a_module_that_cannot_be_imported_is_a_failure_and_not_a_silent_skip(self):
        self.modules()
        code, out, err = self.run_it("-j", "1", "pkg_broken", "pkg_ok")
        self.assertEqual(code, 1)
        self.assertIn("a_module_that_is_not_there", out + err)
        self.assertRegex(out, r"pkg_ok\s+3 tests?")  # the others still ran

    def test_a_worker_that_dies_is_a_failure(self):
        self.modules()
        (self.tmp / "pkg_exit.py").write_text("import os, unittest\nclass C(unittest.TestCase):\n    def test_dies(self):\n        os._exit(7)\n")
        code, out, err = self.run_it("-j", "2", "pkg_exit", "pkg_ok")
        self.assertEqual(code, 1)
        self.assertIn("worker", out + err)

    def test_record_writes_the_measured_time_of_every_module_and_keeps_the_others(self):
        self.modules()
        timings = self.tmp / "t.json"
        timings.write_text(json.dumps({"modules": {"pkg_old": 12.5, "pkg_ok": 99.0}}))
        code, out, err = self.run_it("-j", "2", "--record", "pkg_ok", "pkg_bad")
        data = json.loads(timings.read_text())
        self.assertEqual(sorted(data["modules"]), ["pkg_bad", "pkg_ok", "pkg_old"])
        self.assertEqual(data["modules"]["pkg_old"], 12.5)  # not run: kept
        self.assertLess(data["modules"]["pkg_ok"], 99.0)  # run: measured
        self.assertEqual(code, 1)  # the failing module still fails the run

    def test_the_modules_of_one_worker_share_a_process_and_those_of_two_workers_do_not(self):
        for name in ("pkg_p1", "pkg_p2"):
            (self.tmp / f"{name}.py").write_text(
                f"import os, unittest\nclass T(unittest.TestCase):\n    def test_pid(self):\n        open(r'{self.tmp}/{name}.pid', 'w').write(str(os.getpid()))\n")
        pid = lambda name: (self.tmp / f"{name}.pid").read_text()  # noqa: E731
        self.run_it("-j", "1", "pkg_p1", "pkg_p2")
        self.assertEqual(pid("pkg_p1"), pid("pkg_p2"))  # what a process caches is shared by the modules after the first
        self.run_it("-j", "2", "pkg_p1", "pkg_p2")
        self.assertNotEqual(pid("pkg_p1"), pid("pkg_p2"))

    def test_modules_of_the_suite_can_be_named_by_their_dotted_names(self):
        code, out, err = self.run_it("-j", "2", "tests.test_wording", "tests.test_smoke")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("test_wording", out)
        self.assertIn("test_smoke", out)


if __name__ == "__main__":
    unittest.main()
