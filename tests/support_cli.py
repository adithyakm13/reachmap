"""Running `reachmap analyse` on the M1 fixtures for the tests that read its output, and reading a report back as the shapes
the scenario harness (`tests/scenario_support.py`) checks.

A run is deterministic, so one is made once per process for each (config, arguments) and shared by the tests that only
read it: `cli_run` writes into a directory of its own and keeps the exit code, both streams and the report. The git cache
is the one the scenario modules fill (`ss.env().cache`), so the commits are already fetched.

`to_run` maps a report to an `ss.Run`: the language analysis, the entry points, the propagation and the branch matrix
come back from the JSON as the types the analyser used (`reachmap.payload`), the records from `Analysis.from_dict`, so
`ss.check` compares what the CLI wrote, not what a library call returned.
"""

from __future__ import annotations

import atexit
import io
import json
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from typing import Any

from reachmap import branches, cli, payload
from reachmap.adapters.js import JsAdapter
from reachmap.adapters.python import PyAdapter
from reachmap.model import Analysis
from tests import scenario_support as ss
from tests import support_git as sg

ANALYSIS_KEYS = ("change", "ref", "branches", "findings", "areas", "propagations", "backend_calls", "suggestions", "mapped_tests",
                 "incompleteness", "notes", "revision", "status")
# The kinds of finding the expected.json `finding` entries are about (README, "Kinds used"): the pipeline builds other kinds
# too (direct, cross-repo, exclusion, ...), which the scenarios make no claim about.
EXPECTED_FINDING_KINDS = ("removed_route_still_called", "added_route_uncalled")

_ROOT: list[Path] = []
_RESULTS: dict[tuple, "Result"] = {}


@dataclass(frozen=True)
class Result:
    code: int
    out: str
    err: str
    path: Path | None
    text: str | None
    internals: dict[str, Any] | None = None  # what the pipeline's steps made (`Options.retain`): the harness checks the adapters' own results

    @property
    def report(self) -> dict[str, Any]:
        assert self.text is not None, "the run wrote no report"
        return json.loads(self.text)


def root() -> Path:
    if not _ROOT:
        _ROOT.append(Path(tempfile.mkdtemp(prefix="reachmap-cli-")))
        atexit.register(sg.remove_tree, _ROOT[0])
    return _ROOT[0]


def env_vars() -> dict[str, str]:
    e = ss.env()
    return {"REACHMAP_FIXTURES": "file://" + str(e.dest), "REACHMAP_CACHE": str(e.cache)}


def config_path(hops: int | None = None) -> Path:
    """The M1 fixture config as a file of its own (`hops` replaced for s21)."""
    name = f"reachmap-hops{hops}.toml" if hops is not None else "reachmap.toml"
    path = root() / "config" / name
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        text = (ss.FIXTURES / "reachmap.toml").read_text(encoding="utf-8")
        if hops is not None:
            assert "hops = 3" in text
            text = text.replace("hops = 3", f"hops = {hops}")
        path.write_text(text, encoding="utf-8")
    return path


def cli_run(*args: str, hops: int | None = None) -> Result:
    """`reachmap analyse --config <fixture config> <args> --out <dir>` in process; made once per process for equal arguments."""
    key = (args, hops)
    if key not in _RESULTS:
        out_dir = root() / f"out{len(_RESULTS)}"
        out, err = io.StringIO(), io.StringIO()
        kept: dict[str, Any] = {}
        code = cli.main(["analyse", "--config", str(config_path(hops)), *args, "--out", str(out_dir)], env=env_vars(), stdout=out, stderr=err,
                        git_env=sg.isolated_env(ss.env().home), retain=kept)
        files = sorted(out_dir.glob("*.json")) if out_dir.exists() else []
        assert len(files) <= 1, files
        _RESULTS[key] = Result(code, out.getvalue(), err.getvalue(), files[0] if files else None, files[0].read_text(encoding="utf-8") if files else None, kept)
    return _RESULTS[key]


def scenario_args(sc: dict) -> tuple[str, ...]:
    c = sc["change"]
    flag, spec = ("--compare", f"{c['base']}...{c['head']}") if c["kind"] == "compare" else ("--range", f"{c['base']}..{c['head']}")
    return ("--repo", sc["repo"], flag, spec)


def scenario_hops(sc: dict) -> int | None:
    return (sc["scenario"].get("limits") or {}).get("hops")


def scenario_run(sc: dict) -> Result:
    return cli_run(*scenario_args(sc), hops=scenario_hops(sc))


def adapter_for(repo: str, git: Any) -> Any:
    cfg = ss.env().workspace.repos[repo]
    cls = JsAdapter if cfg.language in ("typescript", "javascript") else PyAdapter
    return cls.from_config(cfg, git, limits=ss.env().workspace.limits)


def to_run(sc: dict, report: dict[str, Any]) -> ss.Run:
    """An `ss.Run` made of a report: what `ss.check` reads is rebuilt from the JSON, nothing from a library call but the
    adapter the `not_distinctive` entries count definitions with (an independent read of the repository)."""
    with mock.patch("reachmap.model._verify_claim", lambda *args: None):  # a test may have edited the report: the id and fingerprint it holds are not checked here
        analysis = Analysis.from_dict({k: report[k] for k in ANALYSIS_KEYS})
    prop = payload.propagation_from_dict(report["propagations"][0])
    inputs = [
        SimpleNamespace(kind=f.kind, risk=f.risk.value, confidence=f.confidence.value, evidence=f.evidence)
        for f in analysis.findings if f.kind in EXPECTED_FINDING_KINDS
    ]
    prop = replace(prop, findings=tuple(inputs), records=tuple(analysis.incompleteness))
    origin = prop.passes[0]
    matrix = branches.Matrix(analysis.branches, (), analysis.notes, ss.env().workspace.repos[sc["repo"]].primary_branch)
    git = ss.make_git()
    change = SimpleNamespace(head_sha=report["change"]["head_sha"])
    return ss.Run(
        sc, change, origin.analysis, list(analysis.incompleteness), git, adapter_for(sc["repo"], git), entry=None, reach=origin.reach, propagation=prop,
        branches=matrix, report_findings=analysis.findings, report_areas=analysis.areas,
    )
