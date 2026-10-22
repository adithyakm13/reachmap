"""Builds the M16 Spring Boot fixtures: two small git repositories and their change scenarios.

    from tests.fixtures.sb_build import build_all, scenarios
    repos = build_all(dest)        # workspace name ("acme/ledger") to repository path
    for sc in scenarios(dest):     # scenario.json + expected.json, repo paths resolved
        ...

The data is under tests/fixtures/spring/ (see its README.md): repos/<slug>/history.json and the step overlays, and
scenarios/sbNN-<slug>/{scenario.json,changes/,expected.json}. It is a separate tree from the M1 fixtures so that the repositories, shas
and expected files of M1 are what they were, and so that nothing that walks the M1 scenarios meets an `sb` one. The replay is the M1
builder's (`tests/fixtures/build.py`): the same identity, dates, git environment and overlay rules, so shas are identical on every
machine with the same git. Standard library only, no network.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.fixtures import build

HERE = Path(__file__).resolve().parent / "spring"
REPOS_DIR = HERE / "repos"
SCENARIOS_DIR = HERE / "scenarios"
WORKSPACE_FILE = Path(__file__).resolve().parent / "workspaces" / "spring.toml"
REPOS = {"acme/ledger": "sb-ledger", "acme/ledger-web": "sb-web"}  # workspace name to the folder of the repository's history


def _history(repo: build.Repo, slug: str) -> None:
    history = json.loads((REPOS_DIR / slug / "history.json").read_text(encoding="utf-8"))
    for step in history["steps"]:
        repo.checkout(step["branch"], step.get("from"))
        build.apply_overlay(REPOS_DIR / slug / "steps" / step["dir"], repo.path)
        repo.commit(step["message"])
        for tag in step.get("tags", []):
            repo.tag(tag)


def _scenarios(repos: dict[str, build.Repo]) -> None:
    for folder in sorted(SCENARIOS_DIR.iterdir()):
        spec_file = folder / "scenario.json"
        if not spec_file.exists():
            continue
        spec = json.loads(spec_file.read_text(encoding="utf-8"))
        repo = repos[spec["repo"]]
        repo.git("checkout", "-q", "-b", spec["change"]["head"], spec["change"]["base"])
        build.apply_overlay(folder / "changes", repo.path)
        repo.commit(spec["commit_message"])
        repo.git("checkout", "-q", build.DEFAULT_BRANCH)


def build_all(dest: Path) -> dict[str, Path]:
    """Build both repositories and every scenario branch under dest (a fresh directory). Returns a name to path map."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    env = build.git_env(dest)
    built: dict[str, build.Repo] = {}
    for name, slug in REPOS.items():
        path = dest / slug
        if path.exists():
            raise FileExistsError(f"{path} already exists; build into a fresh directory")
        repo = build.Repo(path, env)
        repo.init()
        _history(repo, slug)
        repo.git("checkout", "-q", build.DEFAULT_BRANCH)
        built[name] = repo
    _scenarios(built)
    return {name: repo.path for name, repo in built.items()}


def scenarios(dest: Path) -> list[dict]:
    """Every scenario: scenario.json, expected.json and the resolved repository path (dest holds a build_all(dest) result)."""
    out = []
    for folder in sorted(SCENARIOS_DIR.iterdir()):
        spec_file = folder / "scenario.json"
        if not spec_file.exists():
            continue
        spec = json.loads(spec_file.read_text(encoding="utf-8"))
        expected = json.loads((folder / "expected.json").read_text(encoding="utf-8"))
        out.append({
            "id": spec["id"], "dir": folder, "repo": spec["repo"], "repo_path": Path(dest) / REPOS[spec["repo"]], "change": spec["change"],
            "scenario": spec, "expected": expected,
        })
    return out
