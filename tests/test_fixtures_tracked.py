"""A fixture that .gitignore swallows is a test that passes on one machine and fails on every other (the spec fixture's `reports`
folder once was: `reports/` is ignored for the output of `reachmap analyse`). Every file under tests/fixtures is either tracked or
waiting to be added; none is ignored."""

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("git") and (ROOT / ".git").exists(), "needs a git checkout")
class FixturesAreNotIgnored(unittest.TestCase):
    def test_no_file_under_tests_fixtures_is_ignored(self):
        proc = subprocess.run(["git", "ls-files", "--others", "--ignored", "--exclude-standard", "--", "tests/fixtures"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        ignored = [p for p in proc.stdout.splitlines() if "__pycache__" not in p and not p.endswith(".pyc")]
        self.assertEqual(ignored, [])
