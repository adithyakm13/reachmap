"""Entry functions for the runner's child process in tests (spawn-safe: the child imports this module by name).

Each takes the request dict the runner gives an entry (`spec`, `config`, `reports`, `env`, `git_env`, `args`, `job`) and returns
`{"code": int, "report": str | None}`, or ends the process some other way. `args` carries `dir`, a directory the hooks write to.
"""

from __future__ import annotations

import json
import os
import signal
import time
from pathlib import Path


def _note(request: dict, name: str, **values: object) -> None:
    folder = Path(request["args"]["dir"])
    with open(folder / f"{request['job']}.{name}.json", "w", encoding="utf-8") as f:
        json.dump({"pid": os.getpid(), "time": time.time(), **values}, f)


def recorder(request: dict) -> dict:
    """Notes when it starts and ends (wall clock), takes `args.seconds`, and completes."""
    _note(request, "start")
    time.sleep(float(request["args"].get("seconds", 0.4)))
    _note(request, "end")
    return {"code": 0, "report": None}


def sleeper(request: dict) -> dict:
    """Notes its process id and sleeps far longer than any test waits."""
    _note(request, "start")
    time.sleep(120)
    return {"code": 0, "report": None}


def stubborn(request: dict) -> dict:
    """Ignores SIGTERM: only a kill ends it."""
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    _note(request, "start")
    time.sleep(120)
    return {"code": 0, "report": None}


def crasher(request: dict) -> dict:
    _note(request, "start")
    os._exit(7)


def silent(request: dict) -> dict:
    """Ends without giving a result."""
    os._exit(0)


def raiser(request: dict) -> dict:
    raise RuntimeError("boom-hook-secret")


def refused(request: dict) -> dict:
    return {"code": 3, "report": None}


def echo(request: dict) -> dict:
    """Writes what it was given (the spec and the argument names) so a test can read what reached the child."""
    _note(request, "echo", spec=request["spec"], keys=sorted(request))
    return {"code": 0, "report": None}


# ------------------------------------------------------------------------------ process tree, results, environment


def git_sleeper(request: dict) -> dict:
    """Runs a stand-in git (`args.fake_git`, a script that notes its pid and starts a `sleep` beside it) through the git layer, as an
    analysis does, and waits: the sleeps are in a process group of their own that only the registry of the runner knows. It also starts a
    `sleep` of its own, as a host command is started, in the child's group and registered nowhere."""
    import threading

    from reachmap.gitlayer import GitRunner

    folder = Path(request["args"]["dir"])
    os.environ["REACHMAP_TEST_PIDFILE"] = str(folder / f"{request['job']}.sleep")
    _note(request, "start")
    threading.Thread(target=lambda: GitRunner(git=request["args"]["fake_git"]).run(["x"], timeout=300), daemon=True).start()
    import subprocess

    group = subprocess.Popen(["/bin/sleep", "47"])  # started as a host command is: in the child's own group, registered nowhere
    (folder / f"{request['job']}.group").write_text(str(group.pid), encoding="utf-8")
    time.sleep(120)
    return {"code": 0, "report": None}


def cooperative_timeout(request: dict) -> dict:
    """What `reachmap analyse` does at its own time limit: a report of a run that timed out, and the exit code 4."""
    from reachmap import cli, config, pipeline

    workspace = config.load(request["config"], env=request["env"])
    spec = pipeline.ChangeSpec(compare="main...topic")
    name = cli.write_unfinished(workspace, request["config"], request["reports"], "acme/web", spec, None, "timed_out", "the time limit was reached", time.time(), 1.0)
    return {"code": 4, "report": name}


def completed_then_hang(request: dict) -> dict:
    """Writes a completed report and says so in the job's result file (as the command does), then never answers."""
    from reachmap.runner import jobs

    name = f"acme-web-compare-x-{request['job'][:8]}-r1.json"
    Path(request["reports"]).mkdir(parents=True, exist_ok=True)
    (Path(request["reports"]) / name).write_text("{}", encoding="utf-8")
    jobs.note_result(request, name, "completed")
    _note(request, "start")
    time.sleep(120)
    return {"code": 0, "report": None}


def dump(request: dict) -> dict:
    """Writes the environment, the command line and the request of the child, for a test to look for secrets in."""
    _note(request, "dump", env=dict(os.environ), argv=__import__("sys").argv, given=request)
    return {"code": 0, "report": None}
