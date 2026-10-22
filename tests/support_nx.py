"""The M16 Next.js fixtures for the tests that run `reachmap analyse` on them: built once per process, run through the CLI, the
report read back as the shapes the scenario harness (`tests/scenario_support.py`) checks.

The M1 harness is bound to the acme workspace (`ss.env()`); the nx scenarios use the same checks (`ss.check`) with an
environment of their own, swapped in for the length of a call (`nx_env`). Nothing here changes what the acme tests see.
"""

from __future__ import annotations

import atexit
import contextlib
import io
import json
import tempfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator
from unittest import mock

from reachmap import branches, cli, config, payload
from reachmap.adapters.js import JsAdapter
from reachmap.model import Analysis
from tests import scenario_support as ss
from tests import support_cli as sc_cli
from tests import support_git as sg
from tests.fixtures import nx_build

_ENV: ss.Env | None = None
_RESULTS: dict[tuple, sc_cli.Result] = {}
_ROOT: list[Path] = []


def env() -> ss.Env:
    """The process-wide environment of the nx fixtures: built on first use."""
    global _ENV
    if _ENV is None:
        root = Path(tempfile.mkdtemp(prefix="reachmap-nx-"))
        atexit.register(sg.remove_tree, root)
        repos = nx_build.build_all(root / "fx")
        for path in repos.values():
            sg.enable_server_filters(path)
        dest = repos["nx/shop"].parent
        ws = config.load(nx_build.WORKSPACE_FILE, env={"REACHMAP_FIXTURES": "file://" + str(dest)})
        _ENV = ss.Env(dest, repos, ws, root / "cache", root / "home")
        _ROOT.append(root)
    return _ENV


@contextlib.contextmanager
def nx_env() -> Iterator[ss.Env]:
    """`ss.env()` answers with the nx environment inside the block (the checks of the scenario harness read it)."""
    e = env()
    saved = ss._ENV
    ss._ENV = e
    try:
        yield e
    finally:
        ss._ENV = saved


def env_vars() -> dict[str, str]:
    e = env()
    return {"REACHMAP_FIXTURES": "file://" + str(e.dest), "REACHMAP_CACHE": str(e.cache)}


def cli_run(*args: str) -> sc_cli.Result:
    """`reachmap analyse --config <the nx workspace> <args>` in process; made once per process for equal arguments."""
    if args not in _RESULTS:
        e = env()
        out_dir = _ROOT[0] / f"out{len(_RESULTS)}"
        out, err = io.StringIO(), io.StringIO()
        kept: dict[str, Any] = {}
        code = cli.main(["analyse", "--config", str(nx_build.WORKSPACE_FILE), *args, "--out", str(out_dir)], env=env_vars(), stdout=out, stderr=err,
                        git_env=sg.isolated_env(e.home), retain=kept)
        files = sorted(out_dir.glob("*.json")) if out_dir.exists() else []
        assert len(files) <= 1, files
        _RESULTS[args] = sc_cli.Result(code, out.getvalue(), err.getvalue(), files[0] if files else None, files[0].read_text(encoding="utf-8") if files else None, kept)
    return _RESULTS[args]


def scenario_args(sc: dict) -> tuple[str, ...]:
    c = sc["change"]
    return ("--repo", sc["repo"], "--compare", f"{c['base']}...{c['head']}")


def scenario_run(sc: dict) -> sc_cli.Result:
    return cli_run(*scenario_args(sc))


def scenarios() -> list[dict]:
    return nx_build.scenarios(env().dest)


def to_run(sc: dict, report: dict[str, Any]) -> ss.Run:
    """An `ss.Run` made of a report (as `support_cli.to_run` does for the acme workspace): what `ss.check` reads is rebuilt from the JSON."""
    e = env()
    with mock.patch("reachmap.model._verify_claim", lambda *args: None):
        analysis = Analysis.from_dict({k: report[k] for k in sc_cli.ANALYSIS_KEYS})
    prop = payload.propagation_from_dict(report["propagations"][0])
    inputs = [
        SimpleNamespace(kind=f.kind, risk=f.risk.value, confidence=f.confidence.value, evidence=f.evidence)
        for f in analysis.findings if f.kind in sc_cli.EXPECTED_FINDING_KINDS
    ]
    prop = replace(prop, findings=tuple(inputs), records=tuple(analysis.incompleteness))
    origin = prop.passes[0]
    matrix = branches.Matrix(analysis.branches, (), analysis.notes, e.workspace.repos[sc["repo"]].primary_branch)
    git = ss.make_git()
    change = SimpleNamespace(head_sha=report["change"]["head_sha"])
    cfg = e.workspace.repos[sc["repo"]]
    adapter = JsAdapter.from_config(cfg, git, limits=e.workspace.limits)
    return ss.Run(
        sc, change, origin.analysis, list(analysis.incompleteness), git, adapter, entry=None, reach=origin.reach, propagation=prop,
        branches=matrix, report_findings=analysis.findings, report_areas=analysis.areas,
    )


def check(sc: dict, report: dict[str, Any]) -> list[str]:
    """`ss.check` of the scenario's report against its expected.json, in the nx environment."""
    with nx_env():
        return ss.check(to_run(sc, report))
