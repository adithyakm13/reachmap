"""Build the Django fixtures (M16, adapter `dj`): two small git repositories and their scenarios, with the M1 builder.

    from tests.fixtures.dj import build_dj, scenarios_dj
    repos = build_dj(dest)         # workspace name ("acme/portal") to repository path
    for sc in scenarios_dj(dest):  # scenario.json + expected.json, repository paths resolved
        ...

The data is under this folder like the M1 fixtures: `repos/dj-portal` (a Django project with a DRF API: the repository the scenarios change) and
`repos/dj-portal-web` (its React client), `scenarios/dj01-*` to `dj14-*`, and the workspace in `workspaces/django.json` (the config of it is
`workspaces/django.toml`). Nothing here changes an M1 repository, branch, sha or expected.json: `build.build_all` does not build these repositories,
and `build.scenarios` does not list these scenarios.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.fixtures import build

HERE = Path(__file__).resolve().parent
WORKSPACE_FILE = HERE / "workspaces" / "django.json"
PREFIX = "dj"


def load_workspace() -> dict:
    return json.loads(WORKSPACE_FILE.read_text(encoding="utf-8"))


def build_dj(dest: Path) -> dict[str, Path]:
    """Build both repositories and every scenario branch of them under dest. Returns a name to path map."""
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
    build._build_scenarios(built)
    return {name: repo.path for name, repo in built.items()}


def scenarios_dj(dest: Path) -> list[dict]:
    """Every `dj` scenario: scenario.json, expected.json and the resolved repository path (dest holds a build_dj(dest) result)."""
    workspace = load_workspace()
    out = []
    for folder in sorted(build.SCENARIOS_DIR.iterdir()):
        spec_file = folder / "scenario.json"
        if not folder.name.startswith(PREFIX) or not spec_file.exists():
            continue
        spec = json.loads(spec_file.read_text(encoding="utf-8"))
        out.append({
            "id": spec["id"], "dir": folder, "repo": spec["repo"], "repo_path": Path(dest) / workspace["repos"][spec["repo"]]["slug"],
            "change": spec["change"], "scenario": spec, "expected": json.loads((folder / "expected.json").read_text(encoding="utf-8")),
        })
    return out


def repo_paths(dest: Path) -> dict[str, Path]:
    return {name: Path(dest) / info["slug"] for name, info in load_workspace()["repos"].items()}
