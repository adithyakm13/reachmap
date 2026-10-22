"""The scenario world of the M16 OpenAPI tests: the `oa` fixtures (tests/fixtures/repos/oa-*) built once per process, `reachmap analyse`
run once per scenario, and the checks of `scenario_support` (the M9 harness) pointed at them.

`using()` swaps the process-wide fixture environment of `scenario_support` for this workspace while a check runs (the tests of one
process run one after the other, and the parallel runner uses a process per module), so nothing of the M1 harness is copied.
"""

from __future__ import annotations

import atexit
import dataclasses
import io
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest import mock

from reachmap import cli, config
from tests import scenario_support as ss
from tests import support_cli as sc_cli
from tests import support_git as sg
from tests.fixtures import build_openapi as bo

EXPECTED_FINDING_KINDS = (*sc_cli.EXPECTED_FINDING_KINDS, "operation-changed")
_ADAPTER_FOR = sc_cli.adapter_for  # the real one, before `using()` replaces it
_WORLD: dict[str, Any] = {}
_RESULTS: dict[str, Any] = {}


def world() -> ss.Env:
    """The fixture repositories of the openapi workspace, built on first use."""
    if "env" not in _WORLD:
        root = Path(tempfile.mkdtemp(prefix="reachmap-oa-"))
        atexit.register(sg.remove_tree, root)
        dest = root / "fx"
        repos = bo.build_all(dest)
        for path in repos.values():
            sg.enable_server_filters(path)
        ws = config.load(bo.CONFIG_FILE, env={"REACHMAP_FIXTURES": "file://" + str(dest)})
        _WORLD["env"] = ss.Env(dest, repos, ws, root / "cache", root / "home")
        _WORLD["scenarios"] = {sc["id"]: sc for sc in bo.scenarios(dest)}
    return _WORLD["env"]


def scenarios() -> dict[str, dict]:
    world()
    return _WORLD["scenarios"]


def env_vars() -> dict[str, str]:
    e = world()
    return {"REACHMAP_FIXTURES": "file://" + str(e.dest), "REACHMAP_CACHE": str(e.cache)}


class _Counts:
    """The adapter `ss.check` counts definitions with (`not_distinctive` entries): a subject that names a repository
    (`acme/ledger-web:get`, the record of a pass in another repository) is counted in that repository, at its primary branch."""

    def __init__(self, default: Any, adapters: dict[str, Any], heads: dict[str, str]) -> None:
        self.default, self.adapters, self.heads = default, adapters, heads

    def definitions(self, name: str, sha: str) -> Any:
        repo, sep, bare = name.partition(":")
        if sep and repo in self.adapters:
            return self.adapters[repo].definitions(bare, self.heads[repo])
        return self.default.definitions(name, sha)


@contextmanager
def using() -> Any:
    """The checks of `scenario_support` and `support_cli` read the openapi workspace inside this block."""
    e = world()

    def adapter_for(repo: str, repo_git: Any) -> Any:
        adapters = {name: _ADAPTER_FOR(name, repo_git) for name in e.workspace.repos}
        return _Counts(adapters[repo], adapters, {name: sg.rev(path, "main") for name, path in e.repos.items()})

    with mock.patch.object(ss, "_ENV", e), mock.patch.object(ss, "_REVS", {}), mock.patch.object(sc_cli, "EXPECTED_FINDING_KINDS", EXPECTED_FINDING_KINDS), \
            mock.patch.object(sc_cli, "adapter_for", adapter_for):
        yield e


def cli_run(sc: dict) -> sc_cli.Result:
    """`reachmap analyse` on the scenario with the openapi workspace, made once per process."""
    key = sc["id"]
    if key not in _RESULTS:
        e = world()
        out_dir = Path(tempfile.mkdtemp(prefix="reachmap-oa-out-", dir=e.cache.parent))
        out, err = io.StringIO(), io.StringIO()
        kept: dict[str, Any] = {}
        code = cli.main(["analyse", "--config", str(bo.CONFIG_FILE), *sc_cli.scenario_args(sc), "--out", str(out_dir)], env=env_vars(), stdout=out, stderr=err,
                        git_env=sg.isolated_env(e.home), retain=kept)
        files = sorted(out_dir.glob("*.json"))
        assert len(files) <= 1, files
        _RESULTS[key] = sc_cli.Result(code, out.getvalue(), err.getvalue(), files[0] if files else None, files[0].read_text(encoding="utf-8") if files else None, kept)
    return _RESULTS[key]


def to_run(sc: dict, report: dict[str, Any], *, reach_incomplete: bool = False) -> ss.Run:
    """The `ss.Run` of a report (see `support_cli.to_run`). `reach_incomplete`: the blocking records of the scenario are about an
    edge, not about the changed repository's own entry points, which are read completely (the M9 harness expects the in-repository
    answer to be incomplete wherever a blocking record is expected; that holds for the records of its own pass only)."""
    with using():
        run = sc_cli.to_run(sc, report)
        if reach_incomplete and run.reach is not None:
            run = dataclasses.replace(run, reach=dataclasses.replace(run.reach, complete=False))
        return run


def check(sc: dict, report: dict[str, Any], *, reach_incomplete: bool = False) -> list[str]:
    """Every failure of the M9 harness for the scenario's report, and of the checks for the types it does not know."""
    run = to_run(sc, report, reach_incomplete=reach_incomplete)
    with using():
        return ss.check(run) + extra_checks(sc, report)


def extra_checks(sc: dict, report: dict[str, Any]) -> list[str]:
    """`backend_call` entries (the handler modules a client change reaches, `report["backend_calls"]`): the harness has no such type."""
    exp = sc["expected"]
    failures: list[str] = []
    wanted = [e for e in exp.get("must_find", []) if e["type"] == "backend_call"]
    got = {(b["client"]["path"], b["route"]["method"], b["route"]["path"]): b for b in report.get("backend_calls") or []}
    for entry in wanted:
        key = (entry["from"]["path"], entry["route"]["method"], entry["route"]["path"])
        b = got.get(key)
        if b is None:
            failures.append(f"backend_call {key} not reported; got {sorted(got)}")
            continue
        if entry["from"]["contains"] not in b["client"]["detail"]:
            failures.append(f"backend_call {key}: client line is {b['client']['detail']!r}")
        if b["handler"] != entry["handler"]:
            failures.append(f"backend_call {key}: handler {b['handler']!r}, expected {entry['handler']!r}")
        if not b["complete"]:
            failures.append(f"backend_call {key}: not complete: {b['reasons']}")
        mods = sorted((m["path"], m["depth"]) for m in b["modules"])
        want = sorted((m["path"], m["depth"]) for m in entry["modules"])
        if mods != want:
            failures.append(f"backend_call {key}: modules {mods}, expected {want}")
    if "backend_call" in exp.get("complete_for", []):
        want_keys = {(e["from"]["path"], e["route"]["method"], e["route"]["path"]) for e in wanted}
        if set(got) != want_keys:
            failures.append(f"backend_call is complete_for: expected {sorted(want_keys)}, got {sorted(got)}")
    return failures


def evidence_problems(report: dict[str, Any]) -> list[str]:
    """Every Evidence of the report resolves at its pinned sha (the end to end audit)."""
    from reachmap import payload

    return ss.evidence_problems(world().repos, list(payload.evidence_in(report)))
