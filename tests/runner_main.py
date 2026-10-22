"""A runner as a process of its own, for the test that kills it: `python -m tests.runner_main CONFIG REPORTS HOOKDIR FAKE_GIT`.
Prints one line of JSON (port, token) and serves until it is killed."""

from __future__ import annotations

import json
import sys

from reachmap import config, runner
from tests.support_runner import NEVER


def main() -> None:
    cfg, reports, hooks, fake = sys.argv[1:5]
    workspace = config.load(cfg, env=NEVER)
    app = runner.create(workspace, cfg, reports, port=0, env=NEVER, entry="tests.runner_hooks:git_sleeper", entry_args={"dir": hooks, "fake_git": fake})
    print(json.dumps({"port": app.server_address[1], "token": app.token}), flush=True)
    app.serve_forever()


if __name__ == "__main__":  # the spawned child imports this module too, and must not start another runner
    main()
