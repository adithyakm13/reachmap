"""The revision log: which earlier analysis of the same change a new one revises.

Two analyses with the same key (workspace, repository, change identity: see `store`) are revisions of each other. The number is the
place in a log kept beside the reports (`<reports dir>/.reachmap/revisions/<slug>.jsonl`, one line per run that completed): 1, 2, 3 in
the order the runs were made. It is stored, never inferred from file names or time stamps (a clock set back, or two runs on the same
commit, still number in order). A log with a gap, a line that is not a revision or an unfinished last line is a `StoreError`: the
number is never guessed again.

A line holds the number, the name of the report file (a name, never a path), the head commit analysed and the time.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from reachmap.review import lock
from reachmap.review.formats import SHA, StoreError
from reachmap.review.store import append_lines, key_slug, read_file, validate_time

__all__ = ["Rev", "find", "next_number", "previous", "read", "record", "revisions_path"]

VERSION = 1
DIR = (".reachmap", "revisions")
_KEYS = ("v", "number", "file", "head_sha", "created_at")


@dataclass(frozen=True, slots=True)
class Rev:
    number: int
    file: str
    head_sha: str
    created_at: str

    def to_dict(self) -> dict[str, object]:
        return {"number": self.number, "file": self.file, "head_sha": self.head_sha}


def revisions_path(base: str | Path, key: Mapping[str, str]) -> Path:
    return Path(base).joinpath(*DIR, key_slug(key) + ".jsonl")


def _check(rev: Rev) -> Rev:
    if isinstance(rev.number, bool) or not isinstance(rev.number, int) or rev.number < 1:
        raise StoreError("a revision number is a whole number from 1")
    if not isinstance(rev.file, str) or not rev.file or "/" in rev.file or "\\" in rev.file or rev.file in (".", ".."):
        raise StoreError("a revision names its report file, not a path")
    if not isinstance(rev.head_sha, str) or not SHA.match(rev.head_sha):
        raise StoreError("a revision names the head commit it analysed")
    validate_time(rev.created_at)
    return rev


def read(base: str | Path, key: Mapping[str, str]) -> list[Rev]:
    """The revisions of `key` in the order made (none when there is no log). A log that cannot be read whole is a `StoreError`."""
    path = revisions_path(base, key)
    try:
        raw = read_file(path)
    except (FileNotFoundError, NotADirectoryError):  # no such file: no revision was made (a folder that is a file cannot hold one)
        return []
    except OSError as exc:
        raise StoreError(f"{path.name}: cannot be read: {exc.strerror or type(exc).__name__}") from None
    if not raw:
        return []
    parts = raw.split(b"\n")
    if parts.pop():
        raise StoreError(f"{path.name}: line {len(parts) + 1} is unfinished; repair the file (revision numbers are never guessed)")
    out: list[Rev] = []
    for number, part in enumerate(parts, 1):
        try:
            row = json.loads(part.decode("utf-8"))
            if not isinstance(row, dict) or tuple(sorted(row)) != tuple(sorted(_KEYS)) or row["v"] != VERSION:
                raise ValueError
            rev = _check(Rev(row["number"], row["file"], row["head_sha"], row["created_at"]))
        except (ValueError, UnicodeDecodeError, RecursionError, StoreError):
            raise StoreError(f"{path.name}: line {number} is not a revision; repair the file (revision numbers are never guessed)") from None
        if rev.number != number:
            raise StoreError(f"{path.name}: line {number} is revision {rev.number}; the numbers must run 1, 2, 3 ... (revision numbers are never guessed)")
        out.append(rev)
    return out


def next_number(log: Sequence[Rev]) -> int:
    return log[-1].number + 1 if log else 1


def previous(log: Sequence[Rev]) -> Rev | None:
    return log[-1] if log else None


def find(log: Sequence[Rev], number: int) -> Rev | None:
    return next((r for r in log if r.number == number), None)


def record(base: str | Path, key: Mapping[str, str], rev: Rev) -> None:
    """Add a revision at the end of the log. Its number must follow the log's."""
    _check(rev)
    with lock.held(base):  # the check and the write are one step
        log = read(base, key)
        if rev.number != next_number(log):
            raise StoreError(f"{revisions_path(base, key).name}: the next revision is {next_number(log)}, not {rev.number}")
        row = {"v": VERSION, "number": rev.number, "file": rev.file, "head_sha": rev.head_sha, "created_at": rev.created_at}
        line = (json.dumps(row, ensure_ascii=True, separators=(",", ":")) + "\n").encode("ascii")
        append_lines(revisions_path(base, key), [line], "the revision")
