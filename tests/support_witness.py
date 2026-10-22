"""The witness fixtures (M18): the M1 repositories built into a folder of their own, with the witness branches of
`tests/fixtures/eval/` added, and `reachmap witness` run in process on a case file.

The shared M1 build (`scenario_support.env()`) is left alone: other tests list its branches, and these only add `eval/...` branches to a copy
built here, once per process.
"""

from __future__ import annotations

import atexit
import io
import tempfile
from dataclasses import dataclass
from pathlib import Path

from reachmap import cli
from tests import support_git as sg
from tests.fixtures import build
from tests.fixtures.eval import build as witnesses

CONFIG = build.HERE / "reachmap.toml"
CASES = witnesses.CASES


@dataclass(frozen=True)
class World:
    root: Path
    dest: Path
    repos: dict[str, Path]
    made: dict[str, dict]
    env: dict[str, str]
    git_env: dict[str, str]


_WORLD: list[World] = []


def world() -> World:
    if not _WORLD:
        root = Path(tempfile.mkdtemp(prefix="reachmap-witness-"))
        atexit.register(sg.remove_tree, root)
        dest = root / "fx"
        repos = build.build_all(dest)
        for path in repos.values():
            sg.enable_server_filters(path)
        made = witnesses.add_witnesses(dest)
        env = {"REACHMAP_FIXTURES": "file://" + str(dest), "REACHMAP_CACHE": str(root / "cache")}
        _WORLD.append(World(root, dest, repos, made, env, sg.isolated_env(root / "home")))
    return _WORLD[0]


def run(cases: str | Path, *extra: str) -> tuple[int, str, str]:
    """`reachmap witness <cases> --config <the M1 config> ...` in process: exit code, standard output, standard error."""
    w = world()
    path = CASES / cases if not Path(cases).is_absolute() and not Path(cases).exists() else Path(cases)
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(["witness", str(path), "--config", str(CONFIG), *extra], env=w.env, stdout=out, stderr=err, git_env=w.git_env)
    return code, out.getvalue(), err.getvalue()
