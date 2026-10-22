"""Running `reachmap analyse` on the Django fixtures (`tests/fixtures/dj.py`) for the tests that read its output, and checking a report against
expected.json with the harness of the M1 scenarios (`tests/scenario_support.py`).

The fixture repositories are built once per process into a folder of their own and served as file:// remotes. The harness of the M1 scenarios reads
the fixture environment from `scenario_support.env()`: `using()` swaps it for the Django one while a check runs, so `ss.check` and
`support_cli.to_run` are used as they are, and puts the M1 one back.
"""

from __future__ import annotations

import atexit
import contextlib
import io
import tempfile
from pathlib import Path
from typing import Any

from reachmap import cli, config
from reachmap.model import Analysis
from tests import scenario_support as ss
from tests import support_cli as sc_cli
from tests import support_git as sg
from tests.fixtures import dj

_ENV: ss.Env | None = None
_RESULTS: dict[str, sc_cli.Result] = {}
_ROOT: list[Path] = []


def env() -> ss.Env:
    """The Django fixture environment (built on first use)."""
    global _ENV
    if _ENV is None:
        root = Path(tempfile.mkdtemp(prefix="reachmap-dj-"))
        atexit.register(sg.remove_tree, root)
        _ROOT.append(root)
        repos = dj.build_dj(root / "fx")
        for path in repos.values():
            sg.enable_server_filters(path)
        dest = root / "fx"
        ws = config.load(ss.FIXTURES / "workspaces" / "django.toml", env={"REACHMAP_FIXTURES": "file://" + str(dest)})
        _ENV = ss.Env(dest, repos, ws, root / "cache", root / "home")
    return _ENV


@contextlib.contextmanager
def using():
    """The Django fixture environment is the harness's environment inside the block."""
    e = env()
    old, old_revs = ss._ENV, dict(ss._REVS)
    ss._ENV = e
    ss._REVS.clear()
    try:
        yield e
    finally:
        ss._ENV = old
        ss._REVS.clear()
        ss._REVS.update(old_revs)


def scenarios() -> list[dict]:
    return dj.scenarios_dj(env().dest)


def config_path(hops: int | None = None) -> Path:
    path = _ROOT[0] / "config" / "django.toml"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text((ss.FIXTURES / "workspaces" / "django.toml").read_text(encoding="utf-8"), encoding="utf-8")
    return path


def env_vars() -> dict[str, str]:
    e = env()
    return {"REACHMAP_FIXTURES": "file://" + str(e.dest), "REACHMAP_CACHE": str(e.cache)}


def scenario_args(sc: dict) -> tuple[str, ...]:
    c = sc["change"]
    return ("--repo", sc["repo"], "--compare", f"{c['base']}...{c['head']}")


def run(*args: str) -> sc_cli.Result:
    """`reachmap analyse --config <the Django config> <args> --out <dir>` in process, made once per process for equal arguments."""
    key = " ".join(args)
    if key not in _RESULTS:
        e = env()
        out_dir = _ROOT[0] / f"out{len(_RESULTS)}"
        out, err = io.StringIO(), io.StringIO()
        kept: dict[str, Any] = {}
        code = cli.main(["analyse", "--config", str(config_path()), *args, "--out", str(out_dir)], env=env_vars(), stdout=out, stderr=err,
                        git_env=sg.isolated_env(e.home), retain=kept)
        files = sorted(out_dir.glob("*.json")) if out_dir.exists() else []
        assert len(files) <= 1, files
        _RESULTS[key] = sc_cli.Result(code, out.getvalue(), err.getvalue(), files[0] if files else None, files[0].read_text(encoding="utf-8") if files else None, kept)
    return _RESULTS[key]


def scenario_run(sc: dict) -> sc_cli.Result:
    return run(*scenario_args(sc))


def to_run(sc: dict, report: dict[str, Any]) -> ss.Run:
    with using():
        return sc_cli.to_run(sc, report)


def check(sc: dict, report: dict[str, Any]) -> list[str]:
    """The failures of the report of a scenario against its expected.json (the checks of the M1 scenarios, nothing skipped)."""
    with using():
        return ss.check(sc_cli.to_run(sc, report))
