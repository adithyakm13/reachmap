#!/usr/bin/env python3
"""Build the synthetic Reachmap fixtures: four small git repositories plus scenarios.

Usage:
    python3 tests/fixtures/build.py <dest>

    from tests.fixtures.build import build_all, scenarios
    repos = build_all(dest)        # workspace name ("acme/web") to repository path
    for sc in scenarios(dest):     # scenario.json + expected.json, repo paths resolved
        ...

Everything is data under this folder (see README.md): repos/<slug>/history.json and the
step overlays, scenarios/<id>/{scenario.json,changes/,expected.json}, workspace.json.
This file only replays that data with git. Standard library only, no network.

Determinism: fixed identity, incrementing commit dates per repository, and a git
environment cut off from the user's configuration, so commit shas are identical on every
machine with the same git version.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPOS_DIR = HERE / "repos"
SCENARIOS_DIR = HERE / "scenarios"
WORKSPACE_FILE = HERE / "workspace.json"
REVIEW_DIR = HERE / "review"

AUTHOR_NAME = "Fixture Author"
AUTHOR_EMAIL = "fixtures@example.invalid"
EPOCH = 1_700_000_000  # 2023-11-14, commit n is dated EPOCH + n hours
DELETE_FILE = ".delete"
DEFAULT_BRANCH = "main"

GIT_FLAGS = (
    "-c", "commit.gpgsign=false",
    "-c", "tag.gpgsign=false",
    "-c", "init.defaultBranch=main",
    "-c", "core.autocrlf=false",
    "-c", "core.excludesFile=/dev/null",
    "-c", "core.hooksPath=/dev/null",
    "-c", "gc.auto=0",
)


def load_workspace() -> dict:
    return json.loads(WORKSPACE_FILE.read_text(encoding="utf-8"))


def git_env(home: Path) -> dict[str, str]:
    """A git environment that ignores the user's and the system's configuration."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".xdg"),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_ALLOW_PROTOCOL": "none",  # no transport at all: nothing here may reach a remote
        "GIT_AUTHOR_NAME": AUTHOR_NAME,
        "GIT_AUTHOR_EMAIL": AUTHOR_EMAIL,
        "GIT_COMMITTER_NAME": AUTHOR_NAME,
        "GIT_COMMITTER_EMAIL": AUTHOR_EMAIL,
        "LC_ALL": "C",
        "TZ": "UTC",
    }
    for key in ("SYSTEMROOT", "TMPDIR", "TEMP", "TMP"):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


class Repo:
    """One repository being built. Not used after build_all returns."""

    def __init__(self, path: Path, env: dict[str, str]) -> None:
        self.path = path
        self.env = env
        self.commits = 0
        self.branches: set[str] = set()

    def git(self, *args: str, date: str | None = None) -> str:
        env = self.env
        if date is not None:
            env = {**env, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
        proc = subprocess.run(
            ["git", *GIT_FLAGS, *args],
            cwd=self.path,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed in {self.path}: {proc.stderr.strip()}")
        return proc.stdout

    def init(self) -> None:
        self.path.mkdir(parents=True)
        self.git("init", "-q", "-b", DEFAULT_BRANCH)

    def checkout(self, branch: str, start: str | None = None) -> None:
        if branch in self.branches:
            self.git("checkout", "-q", branch)
        elif start is not None:
            self.git("checkout", "-q", "-b", branch, start)
            self.branches.add(branch)
        else:
            self.branches.add(branch)  # the unborn first branch, created by init

    def commit(self, message: str) -> None:
        self.commits += 1
        date = f"{EPOCH + self.commits * 3600} +0000"
        self.git("add", "-A")
        self.git("commit", "-q", "--no-verify", "-m", message, date=date)

    def tag(self, name: str) -> None:
        self.git("tag", name)  # lightweight tags


def wanted(rel: Path) -> bool:
    """Whether a file under a step or scenario folder is fixture content.

    Dotfiles and dot folders (.DS_Store, editor swap files) and backup files ending in ~
    are skipped. The one dotfile with a meaning is the top-level .delete list.
    """
    if rel.as_posix() == DELETE_FILE:
        return True
    return not any(part.startswith(".") for part in rel.parts) and not rel.name.endswith("~")


def _files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file() and wanted(p.relative_to(root)))


def apply_overlay(src: Path, dest: Path, generated: list[dict] | None = None) -> None:
    """Write src over dest. Paths listed in src/.delete are removed first."""
    delete_file = src / DELETE_FILE
    if delete_file.exists():
        for line in delete_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            target = dest / line
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
            else:
                raise FileNotFoundError(f"{src}: .delete names a path that does not exist: {line}")
    for path in _files(src):
        rel = path.relative_to(src)
        if rel.as_posix() == DELETE_FILE:
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    for rule in generated or []:
        GENERATORS[rule["rule"]](rule, dest)


# ----------------------------------------------------------------------- generators
# Repetitive files come from a rule named in history.json ("generate") and a template
# here. Only one rule exists: hub_importers.

WIDGET_TEMPLATE = """import {{ truncate }} from "{spec}";

export function {name}() {{
  return <span className="widget">{{truncate("{label}", 8)}}</span>;
}}
"""


def _hub_importers(rule: dict, dest: Path) -> None:
    """Write `count` small components under `dir`, each importing the hub file `target`.

    Odd numbers import it by the "@/" alias, even numbers relatively, so a hub is reached
    by both kinds of specifier.
    """
    out_dir = dest / rule["dir"]
    out_dir.mkdir(parents=True, exist_ok=True)
    for i in range(1, rule["count"] + 1):
        name = f"Widget{i:02d}"
        spec = "@/lib/format" if i % 2 else "../lib/format"
        (out_dir / f"{name}.tsx").write_text(
            WIDGET_TEMPLATE.format(spec=spec, name=name, label=f"Widget {i:02d}"),
            encoding="utf-8",
        )


GENERATORS = {"hub_importers": _hub_importers}


# ------------------------------------------------------------------------- building


def _build_history(repo: Repo, slug: str) -> None:
    history = json.loads((REPOS_DIR / slug / "history.json").read_text(encoding="utf-8"))
    steps_dir = REPOS_DIR / slug / "steps"
    for step in history["steps"]:
        repo.checkout(step["branch"], step.get("from"))
        overlay = steps_dir / step.get("overlay", step["dir"])
        apply_overlay(overlay, repo.path, step.get("generate"))
        repo.commit(step["message"])
        for tag in step.get("tags", []):
            repo.tag(tag)


def _build_scenarios(repos: dict[str, Repo]) -> None:
    for folder in sorted(SCENARIOS_DIR.iterdir()):
        spec_file = folder / "scenario.json"
        if not spec_file.exists():
            continue
        spec = json.loads(spec_file.read_text(encoding="utf-8"))
        changes = folder / "changes"
        if not changes.is_dir() or spec["repo"] not in repos:
            continue  # a range scenario (the refs already exist in the history), or a scenario of another workspace (M16 adapters have their own)
        repo = repos[spec["repo"]]
        head = spec["change"]["head"]
        if spec.get("orphan"):
            repo.git("checkout", "-q", "--orphan", head)
            repo.git("rm", "-rq", "-f", "--ignore-unmatch", ".")
        else:
            repo.git("checkout", "-q", "-b", head, spec["change"]["base"])
        apply_overlay(changes, repo.path)
        repo.commit(spec["commit_message"])
        repo.git("checkout", "-q", DEFAULT_BRANCH)


def m1_scenario_dirs() -> list[Path]:
    """The folders of the M1 scenarios (`sNN-slug`), in order: the other prefixes belong to the adapters of M16, each with its own repositories."""
    return sorted(p for p in SCENARIOS_DIR.iterdir() if p.is_dir() and re.match(r"^s\d\d-", p.name))


def revision_steps(scenario_id: str) -> list[dict]:
    """The later commits of a scenario branch, in order (review/<scenario id>/revisions.json): each has `name`, `dir` (an overlay folder
    beside the file, with an optional .delete list) and `message`. Empty for a scenario with none."""
    spec_file = REVIEW_DIR / scenario_id / "revisions.json"
    if not spec_file.exists():
        return []
    return json.loads(spec_file.read_text(encoding="utf-8"))["revisions"]


def apply_revision(dest: Path, scenario_id: str, name: str) -> str:
    """Commit the revision `name` of a scenario on the scenario's own branch in the repository already built under dest, and return the new
    head sha. The commit is dated like the builder's own (one hour after the last commit made in that repository), so two builds that
    apply the same revisions have the same shas. Nothing a build_all(dest) made is changed: the branch only moves forward, so a copy of dest
    taken before is a repository on its way to a second revision of the same change (a pull request that got another commit)."""
    dest = Path(dest)
    spec = json.loads((SCENARIOS_DIR / scenario_id / "scenario.json").read_text(encoding="utf-8"))
    step = next((s for s in revision_steps(scenario_id) if s["name"] == name), None)
    if step is None:
        raise KeyError(f"scenario {scenario_id} has no revision {name!r}")
    slug = load_workspace()["repos"][spec["repo"]]["slug"]
    repo = Repo(dest / slug, git_env(dest))
    repo.commits = int(repo.git("rev-list", "--all", "--count").strip())
    head = spec["change"]["head"]
    repo.branches.add(head)
    repo.git("checkout", "-q", head)
    apply_overlay(REVIEW_DIR / scenario_id / step["dir"], repo.path)
    repo.commit(step["message"])
    sha = repo.git("rev-parse", "HEAD").strip()
    repo.git("checkout", "-q", DEFAULT_BRANCH)
    return sha


def build_all(dest: Path) -> dict[str, Path]:
    """Build every repository and scenario branch under dest. Returns a name to path map."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    env = git_env(dest)
    workspace = load_workspace()
    built: dict[str, Repo] = {}
    for name, info in workspace["repos"].items():
        path = dest / info["slug"]
        if path.exists():
            raise FileExistsError(f"{path} already exists; build into a fresh directory")
        repo = Repo(path, env)
        repo.init()
        _build_history(repo, info["slug"])
        repo.git("checkout", "-q", DEFAULT_BRANCH)
        built[name] = repo
    _build_scenarios(built)
    return {name: repo.path for name, repo in built.items()}


# ----------------------------------------------------------------------- scenarios


def scenarios(dest: Path) -> list[dict]:
    """Every scenario: scenario.json, expected.json and the resolved repository path.

    dest must already hold a build_all(dest) result. Each item has the keys id, dir,
    repo (workspace name), repo_path, change (kind, base, head), scenario, expected.
    """
    dest = Path(dest)
    workspace = load_workspace()
    out = []
    for folder in sorted(SCENARIOS_DIR.iterdir()):
        spec_file = folder / "scenario.json"
        if not spec_file.exists():
            continue
        spec = json.loads(spec_file.read_text(encoding="utf-8"))
        if spec["repo"] not in workspace["repos"]:
            continue  # a scenario of another workspace (M16 adapters have their own, see tests/fixtures/workspaces)
        expected = json.loads((folder / "expected.json").read_text(encoding="utf-8"))
        out.append(
            {
                "id": spec["id"],
                "dir": folder,
                "repo": spec["repo"],
                "repo_path": dest / workspace["repos"][spec["repo"]]["slug"],
                "change": spec["change"],
                "scenario": spec,
                "expected": expected,
            }
        )
    return out


def repo_paths(dest: Path) -> dict[str, Path]:
    """Workspace name to path map for an already built dest."""
    workspace = load_workspace()
    return {name: Path(dest) / info["slug"] for name, info in workspace["repos"].items()}


def evidence_revision(
    item: dict, repo: str, scenario_repo: str, change: dict, parent: dict | None = None
) -> str:
    """The git revision a locator ({path, contains}) is read at.

    The item's own "at": "base" reads the scenario base; its own "branch" or "ref" reads
    that ref in the item's repo. An item that names none of them inherits the "branch" of
    its parent, the nearest enclosing entry that has one (a consumer entry names the
    branch its evidence is read at). With nothing named the revision is the scenario head
    when the locator is in the scenario's repo, and main in any other repo (cross-repo
    reads use the other repo's primary branch).
    """
    if item.get("at") == "base":
        return change["base"]
    if "branch" in item:
        return item["branch"]
    if "ref" in item:
        return item["ref"]
    if parent is not None and "branch" in parent:
        return parent["branch"]
    if repo == scenario_repo:
        return change["head"]
    return DEFAULT_BRANCH


def run_git(repo_path: Path, *args: str) -> subprocess.CompletedProcess:
    """Run git in a built repository with the isolated environment. Never raises."""
    return subprocess.run(
        ["git", *GIT_FLAGS, *args],
        cwd=repo_path,
        env=git_env(Path(repo_path).parent),
        capture_output=True,
        text=True,
        check=False,
    )


ABSENT_MESSAGES = ("does not exist in", "exists on disk, but not in")


def file_at(repo_path: Path, rev: str, path: str) -> tuple[str, str | None]:
    """Read a file at a revision, three ways (git show).

    ("found", text); ("absent", None) only when git says the path does not exist at that
    revision; ("unreadable", stderr) for any other failure (a bad revision, a corrupt
    object, git not runnable). An unreadable file is never an absence.
    """
    try:
        proc = run_git(repo_path, "show", f"{rev}:{path}")
    except OSError as exc:
        return "unreadable", str(exc)
    if proc.returncode == 0:
        return "found", proc.stdout
    if any(message in proc.stderr for message in ABSENT_MESSAGES):
        # git words a missing path the same for a revision that does not exist, so the
        # revision must resolve to a commit before the path can be called absent.
        if run_git(repo_path, "rev-parse", "--verify", "-q", f"{rev}^{{commit}}").returncode == 0:
            return "absent", None
    return "unreadable", proc.stderr.strip()


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: build.py <dest>", file=sys.stderr)
        return 2
    paths = build_all(Path(argv[1]))
    for name, path in paths.items():
        print(f"{name} {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
