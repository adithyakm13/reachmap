"""The witness changes of the M18 witness cases: follow-up commits on new branches of the built M1 repositories.

    from tests.fixtures import build
    from tests.fixtures.eval import build as witnesses
    repos = build.build_all(dest)
    made = witnesses.add_witnesses(dest)      # id to {"repo", "branch", "head", "date"}

Only branches named `eval/...` are created, from a branch or tag that already exists, and nothing else in any repository is touched, so every
sha `build_all` made is the same with and without this (a test compares them). A witness commit is dated a number of days after the head of
the original change it follows (`witnesses.json`: `after`, `days`); the `merged_at` of a case is the date of that head, which
`merge_time` computes, so the case files and the repositories cannot drift apart unnoticed (a test compares them).

Standard library only, like the fixture builder it extends. The repositories are written to by the builder's own git environment (fixed
identity, no configuration, no transport).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from tests.fixtures import build

HERE = Path(__file__).resolve().parent
SPEC = HERE / "witnesses.json"
CASES = HERE / "cases"
DAY = 86_400


def specs() -> list[dict]:
    return json.loads(SPEC.read_text(encoding="utf-8"))["witnesses"]


def _repo(dest: Path, name: str) -> build.Repo:
    slug = build.load_workspace()["repos"][name]["slug"]
    return build.Repo(Path(dest) / slug, build.git_env(Path(dest)))


def commit_time(dest: Path, repo: str, ref: str) -> int:
    """The committer time (epoch seconds) of a commit of a built repository."""
    return int(_repo(dest, repo).git("log", "-n", "1", "--format=%ct", ref, "--").strip())


def merge_time(dest: Path, repo: str, ref: str) -> str:
    """The time an original change counts as merged, as a case file writes it: the date of its head commit, in UTC."""
    return datetime.fromtimestamp(commit_time(dest, repo, ref), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def add_witnesses(dest: Path) -> dict[str, dict]:
    """Make every witness of `witnesses.json` in the repositories built under `dest`; returns what was made, by witness id."""
    dest = Path(dest)
    made: dict[str, dict] = {}
    for spec in specs():
        repo = _repo(dest, spec["repo"])
        after = spec["after"]
        stamp = commit_time(dest, after["repo"], after["ref"]) + int(spec["days"]) * DAY
        repo.git("checkout", "-q", "-b", spec["branch"], spec["from"])
        if spec.get("revert"):
            repo.git("revert", "--no-edit", "HEAD", date=f"{stamp} +0000")
        if spec.get("edits"):  # a commit of edits: the whole witness, or a fix after the revert
            for edit in spec["edits"]:
                target = repo.path / edit["path"]
                text = target.read_text(encoding="utf-8")
                target.write_text(text + ("" if text.endswith("\n") else "\n") + edit["append"] + "\n", encoding="utf-8", newline="")
            repo.git("add", "-A")
            repo.git("commit", "-q", "--no-verify", "-m", spec["message"], date=f"{stamp} +0000")
        head = repo.git("rev-parse", "HEAD").strip()
        repo.git("checkout", "-q", build.DEFAULT_BRANCH)
        made[spec["id"]] = {"repo": spec["repo"], "branch": spec["branch"], "head": head, "date": stamp}
    return made
