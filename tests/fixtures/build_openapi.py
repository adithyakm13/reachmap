#!/usr/bin/env python3
"""Build the M16 OpenAPI fixtures: two small git repositories (a Python API with a committed OpenAPI spec and handlers, a
TypeScript web app with a client generated from a copy of the spec) plus the `oa` scenarios.

Usage:
    python3 tests/fixtures/build_openapi.py <dest>

    from tests.fixtures.build_openapi import build_all, scenarios
    repos = build_all(dest)        # workspace name ("acme/ledger-api") to repository path
    for sc in scenarios(dest):     # scenario.json + expected.json, repository path resolved
        ...

It replays data with the machinery of the M1 builder (`tests/fixtures/build.py`: the same fixed identity, dates, git
environment and overlay rules): repositories from `tests/fixtures/repos/<slug>/history.json` and its step folders, listed in
`tests/fixtures/workspaces/openapi.json`; scenarios from `tests/fixtures/scenarios/oaNN-*/`. Nothing in the M1 repositories, their
shas or their expected.json is touched: `build.build_all` and `build.scenarios` skip scenarios of another workspace.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from tests.fixtures import build

WORKSPACE_FILE = build.HERE / "workspaces" / "openapi.json"
CONFIG_FILE = build.HERE / "workspaces" / "openapi.toml"
PREFIX = "oa"


def load_workspace() -> dict:
    return json.loads(WORKSPACE_FILE.read_text(encoding="utf-8"))


def build_all(dest: Path) -> dict[str, Path]:
    """Build both repositories and every scenario branch under dest. Returns a workspace name to path map."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    env = build.git_env(dest)
    built: dict[str, build.Repo] = {}
    for name, info in load_workspace()["repos"].items():
        path = dest / info["slug"]
        if path.exists():
            raise FileExistsError(f"{path} already exists; build into a fresh directory")
        repo = build.Repo(path, env)
        repo.init()
        build._build_history(repo, info["slug"])
        repo.git("checkout", "-q", build.DEFAULT_BRANCH)
        built[name] = repo
    build._build_scenarios(built)  # only the scenarios whose repository is one of these
    return {name: repo.path for name, repo in built.items()}


def scenarios(dest: Path) -> list[dict]:
    """Every `oa` scenario: scenario.json, expected.json and the resolved repository path (keys as `build.scenarios`)."""
    dest = Path(dest)
    workspace = load_workspace()
    out = []
    for folder in sorted(build.SCENARIOS_DIR.iterdir()):
        spec_file = folder / "scenario.json"
        if not folder.name.startswith(PREFIX) or not spec_file.exists():
            continue
        spec = json.loads(spec_file.read_text(encoding="utf-8"))
        expected = json.loads((folder / "expected.json").read_text(encoding="utf-8"))
        out.append({
            "id": spec["id"], "dir": folder, "repo": spec["repo"], "repo_path": dest / workspace["repos"][spec["repo"]]["slug"],
            "change": spec["change"], "scenario": spec, "expected": expected,
        })
    return out


def repo_paths(dest: Path) -> dict[str, Path]:
    return {name: Path(dest) / info["slug"] for name, info in load_workspace()["repos"].items()}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: build_openapi.py <dest>", file=sys.stderr)
        return 2
    for name, path in build_all(Path(argv[1])).items():
        print(f"{name} {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
