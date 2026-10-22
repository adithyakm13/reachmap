"""Helpers for the M17 tests (`reachmap init`): in-memory trees for the detectors, and a way to run the generated config of a fixture
world through `reachmap analyse` with that world's own checks.

The documented confirmations (what a person is asked to do with the generated file) are `CONFIRMATIONS`: the fixtures lower
`distinct_max` to 3 (the scenarios define "a generic name" with it; no repository can say what a team's limit is), and nothing else.
"""

from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path
from typing import Any, Callable

from reachmap import cli, config, init
from reachmap.gitlayer import Git
from reachmap.init.detect import guess_repo
from reachmap.init.guess import RepoGuess
from reachmap.init.tree import Tree
from tests import support_git as sg

# (generated text, replaced by): the whole list of edits a person makes to the generated file of any fixture workspace
CONFIRMATIONS = (("distinct_max = 10", "distinct_max = 3"),)


def tree(files: dict[str, str | bytes]) -> Tree:
    return Tree.from_files(files)


def guess(files: dict[str, str | bytes], name: str = "acme/x", **kw: Any) -> RepoGuess:
    return guess_repo(name, tree(files), **kw)


def confirm(text: str, edits: tuple[tuple[str, str], ...] = CONFIRMATIONS) -> str:
    for old, new in edits:
        assert old in text, old
        text = text.replace(old, new)
    return text


def generate(repos: dict[str, Path], home: Path, cache: Path, out_dir: Path):
    """`init.generate` on the fixture repositories as plain paths, each named as the fixture workspace names it."""
    git = Git(cache, env=sg.isolated_env(home))
    return init.generate([f"{name}={path}" for name, path in repos.items()], git, out_dir)


def run_scenarios(text: str, out_dir: Path, scenarios: list[dict], args: Callable[[dict], tuple[str, ...]], check: Callable[[dict, dict], list[str]],
                  cache: Path, home: Path, hops_of: Callable[[dict], int | None] = lambda sc: None) -> dict[str, list[str]]:
    """Write the config, run every scenario through `reachmap analyse` with it and check each report: scenario id to its failures
    (empty dict: all pass). A scenario that does not complete is a failure of its own."""
    out_dir.mkdir(parents=True, exist_ok=True)
    main = out_dir / "reachmap.toml"
    main.write_text(text, encoding="utf-8")
    failures: dict[str, list[str]] = {}
    for sc in scenarios:
        path = main
        hops = hops_of(sc)
        if hops is not None:
            path = out_dir / f"reachmap-hops{hops}.toml"
            assert "hops = 3" in text
            path.write_text(text.replace("hops = 3", f"hops = {hops}"), encoding="utf-8")
        reports = out_dir / ("out-" + sc["id"])
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(["analyse", "--config", str(path), *args(sc), "--out", str(reports)], env={"REACHMAP_CACHE": str(cache)}, stdout=out, stderr=err,
                        git_env=sg.isolated_env(home))
        files = sorted(reports.glob("*.json")) if reports.exists() else []
        if code != 0 or len(files) != 1:
            failures[sc["id"]] = [f"exit {code}: {err.getvalue()[:300]}"]
            continue
        problems = check(sc, json.loads(files[0].read_text(encoding="utf-8")))
        if problems:
            failures[sc["id"]] = problems
    return failures


def plain(mapping: Any) -> Any:
    """A frozen config table as plain dicts and lists (to compare two loaded configs)."""
    if mapping is None:
        return None
    if hasattr(mapping, "items"):
        return {k: plain(v) for k, v in mapping.items()}
    if isinstance(mapping, tuple):
        return [plain(v) for v in mapping]
    return mapping


def temp_dir(prefix: str = "reachmap-init-") -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix))
