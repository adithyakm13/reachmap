"""The citation audit on the reports of the scenarios of the Spring workspace (M16), through real repositories and the real command. The
other workspaces are in `test_eval_audit_m16a`, `_m16b` and `_acme`."""

import io
import re
import unittest

from reachmap import cli
from tests import support_eval as se

COUNTS = {"spring": 20}


def audit_cli(world, *extra):
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(["audit", "--config", str(world.config), *extra, *world.paths()], env=world.env, stdout=out, stderr=err, git_env=world.git_env)
    return code, out.getvalue(), err.getvalue()


class EveryWorld(unittest.TestCase):
    def test_every_report_of_every_workspace_passes_with_nothing_but_ok(self):
        for name, scenarios in COUNTS.items():
            with self.subTest(name):
                world = se.world(name)
                self.assertEqual(len(world.reports), scenarios)
                code, out, err = audit_cli(world)
                self.assertEqual((code, err), (0, ""), out[-3000:])
                last = [line for line in out.splitlines() if re.match(r"^\d+ reports", line)][0]
                self.assertRegex(last, rf"^{scenarios} reports: \d+ distinct citations \(ok \d+, mismatch 0, absent 0, unreadable 0\), 0 structure problems; 0 report\(s\) failed$")
                self.assertEqual(out.count(": passed"), scenarios)
                self.assertEqual(se.tally(world), se.PINNED[name])  # what the reports cite, pinned: the distinct citations and the places by part of the report
                self.assertIn(f"{scenarios} reports: {se.PINNED[name][1]} distinct citations (ok {se.PINNED[name][1]},", out)


if __name__ == "__main__":
    unittest.main()
