"""The reports of every fixture world, for the tests of the evaluation tools (M18): the 23 M1 scenarios and the scenarios of the four M16
workspaces (nextjs, openapi, django, express), each run through `reachmap analyse` once per process by the support module of its world.

`worlds()` gives, per world, the config file, the environment the config and the git cache are read with (`REACHMAP_FIXTURES`,
`REACHMAP_CACHE`), the git environment that cuts git off from the user's configuration, and the report files (they stay on disk until the
process ends). `audit_args(world)` is what `reachmap audit` needs to read the commits of those reports from the cache the runs filled.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tests import scenario_support as ss
from tests import support_cli as sc
from tests import support_git as sg
from tests.fixtures import build

_WORLDS: list["World"] = []


@dataclass(frozen=True)
class World:
    name: str
    config: Path
    env: dict[str, str]
    git_env: dict[str, str]
    reports: dict[str, Path]  # scenario id to the report file
    text: dict[str, str]

    def report(self, scenario: str) -> dict[str, Any]:
        import json

        return json.loads(self.text[scenario])

    def paths(self) -> list[str]:
        return [str(p) for p in self.reports.values()]


def _collect(name: str, config: Path, env: dict[str, str], home: Path, runs: dict[str, sc.Result]) -> World:
    reports = {}
    text = {}
    for sid, result in runs.items():
        assert result.path is not None and result.text is not None, (name, sid, result.code, result.err)
        reports[sid] = result.path
        text[sid] = result.text
    return World(name, config, env, sg.isolated_env(home), reports, text)


def _acme() -> World:
    e = ss.env()
    return _collect("acme", sc.config_path(), sc.env_vars(), e.home, {s["id"]: sc.scenario_run(s) for s in build.scenarios(e.dest)})


def _nextjs() -> World:
    from tests import support_nx as m

    e = m.env()
    return _collect("nextjs", m.nx_build.WORKSPACE_FILE, m.env_vars(), e.home, {s["id"]: m.scenario_run(s) for s in m.scenarios()})


def _openapi() -> World:
    from tests import support_openapi_world as m

    e = m.world()
    return _collect("openapi", m.bo.CONFIG_FILE, m.env_vars(), e.home, {sid: m.cli_run(s) for sid, s in m.scenarios().items()})


def _django() -> World:
    from tests import support_dj as m

    e = m.env()
    return _collect("django", ss.FIXTURES / "workspaces" / "django.toml", m.env_vars(), e.home, {s["id"]: m.scenario_run(s) for s in m.scenarios()})


def _express() -> World:
    from tests import support_ex as m

    e = m.env()
    return _collect("express", m.ex_build.WORKSPACE_FILE, m.env_vars(), e.home, {s["id"]: m.scenario_run(s) for s in m.scenarios()})


def _spring() -> World:
    from tests import support_sb as m

    e = m.env()
    return _collect("spring", m.sb_build.WORKSPACE_FILE, m.env_vars(), e.home, {s["id"]: m.scenario_run(s) for s in m.scenarios()})


BUILDERS = {"acme": _acme, "nextjs": _nextjs, "openapi": _openapi, "django": _django, "express": _express, "spring": _spring}


def world(name: str) -> World:
    """One world, built on first use (the five together take about 80 s, so a test module asks for the one it needs)."""
    for w in _WORLDS:
        if w.name == name:
            return w
    _WORLDS.append(BUILDERS[name]())
    return _WORLDS[-1]


def worlds() -> list[World]:
    return [world(name) for name in BUILDERS]


# What a fixture world's reports cite, pinned: the number of reports, the distinct citations (summed over the reports), and the places that cite
# them by part of the report. The fixtures do not change, so a different number means the analysis cites differently: look, then update it.
PINNED = {
    "acme": (23, 432, {"areas": 168, "backend_calls": 7, "branches": 89, "findings": 232, "propagations": 1117}),
    "nextjs": (18, 246, {"areas": 303, "backend_calls": 2, "branches": 31, "findings": 222, "propagations": 1142}),
    "openapi": (10, 239, {"areas": 96, "backend_calls": 19, "branches": 96, "findings": 105, "propagations": 599}),
    "django": (14, 166, {"areas": 119, "branches": 19, "findings": 142, "propagations": 671}),
    "express": (25, 359, {"areas": 351, "branches": 26, "findings": 354, "propagations": 1858}),
    "spring": (20, 296, {"areas": 263, "backend_calls": 9, "branches": 23, "findings": 266, "propagations": 1360}),
}


def tally(world: World) -> tuple[int, int, dict[str, int]]:
    """(reports, distinct citations summed over the reports, places that cite them by the part of the report) of a world, as the audit collects them."""
    from collections import Counter

    from reachmap.evaluate import audit

    distinct, parts = 0, Counter()
    for sid in world.reports:
        cited = audit.collect(world.report(sid), [])
        distinct += len({c.key for c in cited})
        parts.update(c.site.split("[")[0].split(".")[0] for c in cited)
    return len(world.reports), distinct, dict(sorted(parts.items()))


def audit_args(world: World) -> list[str]:
    """The arguments of `reachmap audit` that point at the world's config (the reports follow)."""
    return ["--config", str(world.config)]
