"""Test path markers, matched by path segment or file-name part, never as a substring.

A marker is a string from the repository's `tests.markers` list:

- `foo/` is a directory: it matches when a folder segment of the path equals `foo` (several
  segments, `a/b/`, must be consecutive folders in that order). The file name is never a
  folder: `src/tests` as a file does not match `tests/`.
- `.test.` (starting with a dot) is a file-name part: the file name contains it, delimited
  by dots; `.test` without the closing dot also matches `a.test` and `a.test.ts` but not
  `a.testing.ts`.
- `test_` is a file-name prefix: the name starts with it.
- `_test` (starting with an underscore) is a suffix before the last extension: `money_test.go`.

`src/backtest/engine.ts` is not a test for `test/`, and `latest_rates.py` is not a test for
`test_`: the substring is inside a longer segment or name. Shared by the language adapters.
"""

from __future__ import annotations

import re
from typing import Iterable, Sequence

__all__ = ["is_test_path", "split_marker", "validate_markers"]

DIR, INFIX, PREFIX, SUFFIX = "dir", "infix", "prefix", "suffix"


def split_marker(marker: str) -> tuple[str, object]:
    """Classify one marker: ("dir", (segments...)), ("infix", text), ("prefix", text) or
    ("suffix", text). Raises ValueError for a marker that is empty or not a plain relative
    path piece."""
    if not isinstance(marker, str) or not marker.strip():
        raise ValueError(f"a test marker must be a non-empty string, not {marker!r}")
    if marker.endswith("/"):
        segments = tuple(marker[:-1].split("/"))
        if any(seg in ("", ".", "..") for seg in segments):
            raise ValueError(f"test marker {marker!r}: a directory marker is plain folder names like `tests/`")
        return DIR, segments
    if "/" in marker:
        raise ValueError(f"test marker {marker!r}: only a directory marker may contain `/`, and it ends with one")
    if marker.startswith("."):
        return INFIX, marker
    if marker.startswith("_"):
        return SUFFIX, marker
    return PREFIX, marker


def validate_markers(markers: Iterable[str]) -> tuple[str, ...]:
    out = tuple(markers)
    for marker in out:
        split_marker(marker)
    return out


def _contains_run(parts: Sequence[str], run: Sequence[str]) -> bool:
    n = len(run)
    return any(tuple(parts[i : i + n]) == tuple(run) for i in range(len(parts) - n + 1))


def is_test_path(path: str, markers: Iterable[str]) -> bool:
    """Whether a repository relative POSIX path is a test file under the markers."""
    parts = path.split("/")
    folders, name = parts[:-1], parts[-1]
    stem = name.rsplit(".", 1)[0] if "." in name else name
    for marker in markers:
        kind, value = split_marker(marker)
        if kind == DIR:
            if _contains_run(folders, value):  # type: ignore[arg-type]
                return True
        elif kind == INFIX:
            text = value  # type: ignore[assignment]
            if text.endswith("."):
                if text in name:
                    return True
            elif re.search(re.escape(text) + r"(?:\.|$)", name):
                return True
        elif kind == PREFIX:
            if name.startswith(value):  # type: ignore[arg-type]
                return True
        elif stem.endswith(value) and len(stem) > len(value):  # type: ignore[arg-type]
            return True
        elif name == value:  # a file called exactly `_test`
            return True
    return False
