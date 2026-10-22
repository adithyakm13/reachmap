"""The decision store: one JSON-lines file per analysis key, beside the reports (`<reports dir>/.reachmap/decisions/<slug>.jsonl`).

The analysis key is (workspace, repository, change identity). The change identity of a pull request is its host and number
(`pr:github:12`); of a comparison or range the spec with the refs as written (`compare:release/3...main`), never shas, so a rerun
on a new commit of the same change finds the decisions of the earlier ones.

A line is one decision: the M2 `Decision` (target id, verdict, reason, author, channel, the target's fingerprint when it was made, the
revision it was made on) and the time it was recorded. The file is append only: a later decision for the same target supersedes the
earlier one and both stay. A line is written with one `os.write` after the file is opened for append, and synced. Lines are
ASCII (every other character is a JSON escape), so that no character of a reason can split one.

Reading never skips a line. A line that is not a decision, a last line that is unfinished, a file that is not UTF-8 or cannot be read is a
`Problem`, the decisions are not shown at all (a later line may have superseded one of the intact ones, so showing them would show a
decision as current that was replaced) and the report carries a `decision_store_unreadable` record, blocking for the decisions view
only. Nothing is appended to a store that has a problem.

The store holds what a person typed (the reason, the name they gave as the author, never the account of this machine), ids, hashes and
numbers. No source line, no secret and no path of this machine.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from reachmap import wording
from reachmap.errors import ModelError
from reachmap.model import Decision
from reachmap.review import lock
from reachmap.review.formats import TIME as _TIME
from reachmap.review.formats import StoreError

__all__ = [
    "MAX_AUTHOR", "MAX_REASON", "Loaded", "Problem", "Stored", "StoreError", "append", "append_lines", "change_identity", "check_folder", "decisions_path", "key_slug", "latest",
    "make_key", "read", "unreadable_record", "validate_time",
]

VERSION = 1
MAX_REASON = 2000  # characters: what a rendering shows whole (report.text.CONTENT_LIMIT)
MAX_AUTHOR = 100
DIR = (".reachmap", "decisions")
_ROW_KEYS = ("v", "target_id", "verdict", "reason", "author", "channel", "fingerprint", "revision", "created_at")


@dataclass(frozen=True, slots=True)
class Stored:
    """A decision as it is kept: the M2 decision and when it was recorded (an ISO time in UTC, `2026-01-02T03:04:05Z`)."""

    decision: Decision
    created_at: str


@dataclass(frozen=True, slots=True)
class Problem:
    """Why a store cannot be read: the line (0 for the file as a whole) and words this tool chose (never a piece of the file)."""

    line: int
    why: str


@dataclass(frozen=True, slots=True)
class Loaded:
    items: tuple[Stored, ...]  # in the order written; empty when there is a problem
    problem: Problem | None


# ------------------------------------------------------------------------------ keys


def change_identity(spec: Mapping[str, str], host: str) -> str:
    """`pr:<host>:<number>`, `compare:<spec as written>` or `range:<spec as written>`, from the report's change spec (`{"pr": "12"}`)."""
    for kind in ("pr", "compare", "range"):
        if kind in spec:
            return f"pr:{host}:{spec[kind]}" if kind == "pr" else f"{kind}:{spec[kind]}"
    raise StoreError("the change has no pull request, comparison or range to identify it by")


def make_key(workspace: str, repo: str, change: str) -> dict[str, str]:
    for name, value in (("workspace", workspace), ("repo", repo), ("change", change)):
        if not isinstance(value, str) or not value:
            raise StoreError(f"the analysis key needs a {name}")
    return {"workspace": workspace, "repo": repo, "change": change}


def _piece(text: str, limit: int) -> str:
    text = re.sub(r"\.{2,}", "-", text)
    return re.sub(r"[^A-Za-z0-9._]+", "-", text).strip("-.")[:limit].strip("-.") or "x"


def key_slug(key: Mapping[str, str]) -> str:
    """A file name for a key: readable parts and an 8 hex hash of the whole key, so that `a/b` and `a-b` do not share a file."""
    digest = hashlib.sha256(json.dumps([key["workspace"], key["repo"], key["change"]], ensure_ascii=True).encode("ascii")).hexdigest()[:8]
    return f"{_piece(key['workspace'], 30)}--{_piece(key['repo'], 40)}--{_piece(key['change'], 50)}--{digest}"


def decisions_path(base: str | Path, key: Mapping[str, str]) -> Path:
    return Path(base).joinpath(*DIR, key_slug(key) + ".jsonl")


# ------------------------------------------------------------------------------ writing


def validate_time(value: Any) -> str:
    if not isinstance(value, str) or not _TIME.match(value):
        raise StoreError("a time is written like 2026-01-02T03:04:05Z (UTC)")
    return value


def _line(item: Stored) -> bytes:
    d = item.decision
    if len(d.reason) > MAX_REASON:
        raise StoreError(f"a reason is at most {MAX_REASON} characters")
    if len(d.author) > MAX_AUTHOR:
        raise StoreError(f"an author name is at most {MAX_AUTHOR} characters")
    row = {"v": VERSION, **d.to_dict(), "created_at": validate_time(item.created_at)}
    return (json.dumps(row, ensure_ascii=True, separators=(",", ":"), allow_nan=False) + "\n").encode("ascii")


NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)  # a store that is a link is neither written nor read


def check_folder(base: str | Path) -> None:
    """`<reports dir>/.reachmap` must be a folder or not be there: a file in its place would make every write fail late."""
    folder = Path(base) / DIR[0]
    if (folder.exists() or folder.is_symlink()) and not folder.is_dir():
        raise StoreError(f"{DIR[0]} is not a folder; move it away (the review layer keeps its files in it)")


def _write_all(fd: int, data: bytes) -> None:
    """Write every byte: `os.write` may take less than it is given."""
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError(0, "the write took no bytes")
        view = view[written:]


def read_file(path: Path) -> bytes:
    """The bytes of a file of the review layer, never through a link."""
    fd = os.open(path, os.O_RDONLY | NOFOLLOW)
    try:
        chunks = []
        while chunk := os.read(fd, 1 << 20):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def append_lines(path: Path, lines: Sequence[bytes], what: str) -> None:
    """Append whole lines to a JSON-lines file, under the lock of the reports directory: made if missing, one write (repeated until every
    byte is taken) and a sync per line. A file whose last line is unfinished is refused (a new line would be glued to it)."""
    base = path.parent.parent.parent  # <reports dir>/.reachmap/<kind>/<file>
    with lock.held(base):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size:
                with open(path, "rb") as f:
                    f.seek(-1, os.SEEK_END)
                    if f.read(1) != b"\n":
                        raise StoreError(f"{path.name}: the last line is unfinished; repair the file before adding to it (nothing was written)")
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | NOFOLLOW, 0o644)
            try:
                for done, line in enumerate(lines):
                    try:
                        _write_all(fd, line)
                        os.fsync(fd)
                    except OSError as exc:
                        raise StoreError(f"{path.name}: cannot write {what}: {exc.strerror or type(exc).__name__} ({done} of {len(lines)} were written)") from None
            finally:
                os.close(fd)
        except OSError as exc:
            raise StoreError(f"{path.name}: cannot write {what}: {exc.strerror or type(exc).__name__}") from None


def append(base: str | Path, key: Mapping[str, str], items: Sequence[Stored]) -> None:
    """Add decisions at the end of the store of `key`. Everything is checked before a byte is written; an unreadable store is refused."""
    lines = [_line(i) for i in items]
    path = decisions_path(base, key)
    with lock.held(base):  # the read and the write are one step: nobody adds a line between them
        current = read(base, key)
        if current.problem is not None:
            raise StoreError(f"{path.name}: line {current.problem.line}: {current.problem.why}; repair the file before adding to it (nothing was written)")
        append_lines(path, lines, "the decision")


# ------------------------------------------------------------------------------ reading


def _parse(raw: bytes, number: int) -> Stored | Problem:
    if not raw:
        return Problem(number, "the line is empty")
    try:
        row = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return Problem(number, "the line is not JSON (a last line may be unfinished)")
    if not isinstance(row, dict):
        return Problem(number, "the line is not a JSON object")
    if tuple(sorted(row)) != tuple(sorted(_ROW_KEYS)):
        return Problem(number, "the line does not have the keys of a decision")
    if row["v"] != VERSION or isinstance(row["v"], bool):
        return Problem(number, "the line is of a version this reachmap does not read")
    try:
        decision = Decision.from_dict({k: row[k] for k in _ROW_KEYS if k not in ("v", "created_at")})
        created = validate_time(row["created_at"])
    except (ModelError, StoreError, TypeError):
        return Problem(number, "the line is not a valid decision")
    return Stored(decision, created)


def read(base: str | Path, key: Mapping[str, str]) -> Loaded:
    """Every decision of `key` in the order written, or a problem (and no decision) when any line cannot be read. No file is no decisions."""
    path = decisions_path(base, key)
    try:
        raw = read_file(path)
    except (FileNotFoundError, NotADirectoryError):  # no such file: nothing is recorded (a folder that is a file cannot hold one)
        return Loaded((), None)
    except OSError as exc:
        return Loaded((), Problem(0, f"the file cannot be read ({exc.strerror or type(exc).__name__})"))
    if not raw:
        return Loaded((), None)
    parts = raw.split(b"\n")
    tail = parts.pop()  # what follows the last newline: empty for a file that ends with one
    items: list[Stored] = []
    for number, part in enumerate(parts, 1):
        got = _parse(part, number)
        if isinstance(got, Problem):
            return Loaded((), got)
        items.append(got)
    if tail:
        return Loaded((), Problem(len(parts) + 1, "the last line is unfinished (no newline at its end)"))
    return Loaded(tuple(items), None)


def latest(items: Sequence[Stored]) -> dict[str, Stored]:
    """The decision in force for each target: the last one written."""
    out: dict[str, Stored] = {}
    for item in items:
        out.pop(item.decision.target_id, None)  # the order is that of the decisions in force, by where each was written
        out[item.decision.target_id] = item
    return out


def unreadable_record(key: Mapping[str, str], problem: Problem) -> dict[str, Any]:
    """The record for the decisions view when the store cannot be read: the shape of an incompleteness record, blocking, and worded by
    this tool (a banned phrase is a bug here, not content). It is kept in `decisions.records`, not in `incompleteness`: it blocks the
    decisions view only and never changes whether the analysis is whole."""
    where = "the file as a whole" if problem.line == 0 else f"line {problem.line}"
    record = {
        "kind": "decision_store_unreadable",
        "subject": key_slug(key) + ".jsonl",
        "not_established": "which decisions were recorded for this change",
        "detail": f"{where}: {problem.why}",
        "blocks_selection": True,
    }
    for field in ("not_established", "detail"):
        wording.require_clean(record[field], f"decision store record {field}")
    return record
