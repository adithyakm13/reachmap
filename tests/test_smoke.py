import io
import unittest
from contextlib import redirect_stdout

import reachmap
from reachmap import cli


class SmokeTest(unittest.TestCase):
    def test_version(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(["--version"])
        self.assertEqual(code, 0)
        self.assertIn(reachmap.__version__, out.getvalue())


if __name__ == "__main__":
    unittest.main()
