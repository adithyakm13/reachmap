"""`python -m reachmap`: the same command line as the `reachmap` script."""

import sys

from reachmap.cli import main

if __name__ == "__main__":
    sys.exit(main())
