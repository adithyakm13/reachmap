"""Small readers the detectors share: manifests parsed once per run, test paths, evidence strings."""

from __future__ import annotations

import json
import tomllib
from typing import Any

from reachmap import testpaths
from reachmap.init.registry import Ctx
from reachmap.init.tree import line_of

__all__ = ["JS_SUFFIXES", "PY_SUFFIXES", "dig", "ev", "is_test", "package_jsons", "pyprojects", "is_source"]

JS_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")
PY_SUFFIXES = (".py",)
_TEST_MARKERS = ("__tests__/", "__test__/", "tests/", "test/", ".test.", ".spec.", "test_", "_test", "conftest.py", "__mocks__/", ".stories.", "e2e/", "cypress/")


def is_test(path: str) -> bool:
    """A test, fixture or story path by the usual markers (never a substring: `src/backtest/engine.ts` is not a test)."""
    return testpaths.is_test_path(path, _TEST_MARKERS)


def is_source(path: str) -> bool:
    return not is_test(path) and not path.endswith(".d.ts")


def _why(exc: BaseException) -> str:
    return "nested too deeply" if isinstance(exc, RecursionError) else str(exc)[:120]


def dig(data: Any, *keys: str) -> Any:
    """The table at `data[k1][k2]...`, or an empty dict when a level is missing or is not a table (manifests are other people's files)."""
    for key in keys:
        data = data.get(key) if isinstance(data, dict) else None
    return data if isinstance(data, dict) else {}


def ev(path: str, line: int = 1) -> str:
    """Evidence as `path:line` (line 1 for a whole file)."""
    return f"{path}:{max(1, line)}"


def package_jsons(ctx: Ctx) -> list[tuple[str, dict[str, Any], str]]:
    """Every non-vendored package.json that parses, as (path, data, text). One that does not parse is listed in `tree.unread`."""
    if "package_jsons" not in ctx.cache:
        out: list[tuple[str, dict[str, Any], str]] = []
        for path in ctx.tree.named("package.json"):
            text = ctx.tree.text(path)
            if text is None:
                continue
            try:
                data = json.loads(text)
            except (ValueError, RecursionError) as exc:  # a file nested past what the parser takes is unread, never a crash
                ctx.tree.unread[path] = f"not valid JSON: {_why(exc)}"
                continue
            if isinstance(data, dict):
                out.append((path, data, text))
        ctx.cache["package_jsons"] = out
    return ctx.cache["package_jsons"]


def pyprojects(ctx: Ctx) -> list[tuple[str, dict[str, Any], str]]:
    if "pyprojects" not in ctx.cache:
        out: list[tuple[str, dict[str, Any], str]] = []
        for path in ctx.tree.named("pyproject.toml"):
            text = ctx.tree.text(path)
            if text is None:
                continue
            try:
                data = tomllib.loads(text)
            except (tomllib.TOMLDecodeError, RecursionError) as exc:
                ctx.tree.unread[path] = f"not valid TOML: {_why(exc)}"
                continue
            out.append((path, data, text))
        ctx.cache["pyprojects"] = out
    return ctx.cache["pyprojects"]


def dep_names(data: dict[str, Any]) -> dict[str, str]:
    """The dependency names of a package.json with the section each is in."""
    found: dict[str, str] = {}
    for section in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        table = data.get(section)
        if isinstance(table, dict):
            for name in table:
                found.setdefault(name, section)
    return found


def line_of_key(text: str, key: str) -> int:
    """The line of a quoted key in a JSON text (`"next"`): the first one."""
    return line_of(text, f'"{key}"') or 1
