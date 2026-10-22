"""Parse unified diffs (`git diff -M -U0`) into files, hunks and line numbers.

The parser never places a line by its text: every removed line carries its old line
number and every added line its new one, taken from the hunk headers. It copes with
renames (with or without edits), added and deleted files, mode-only changes, binary
files, quoted names (spaces, quotes, control characters), the "no newline at end of file"
marker, CRLF text, and context lines when a diff was made with more context than -U0.

`ChangeSet` is the result of a diff, and (with its optional fields filled in by
`reachmap.changeset.resolve`) the pinned description of one change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

ADDED, COPIED, DELETED, MODIFIED, RENAMED, TYPE_CHANGED = "added", "copied", "deleted", "modified", "renamed", "type_changed"
STATUSES = (ADDED, COPIED, DELETED, MODIFIED, RENAMED, TYPE_CHANGED)

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}


class DiffParseError(ValueError):
    """The text is not a diff this parser understands (a broken hunk, a missing path)."""


@dataclass(frozen=True, slots=True)
class DiffLine:
    number: int  # 1-based: the old line number for a removed line, the new one for an added line
    text: str  # without the newline and without a trailing carriage return (CRLF text)


@dataclass(frozen=True, slots=True)
class ContextLine:
    old: int
    new: int
    text: str


@dataclass(frozen=True, slots=True)
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    removed: tuple[DiffLine, ...]
    added: tuple[DiffLine, ...]
    context: tuple[ContextLine, ...] = ()  # empty for a -U0 diff


@dataclass(frozen=True, slots=True)
class FileChange:
    """One changed file. `path` is the path after the change (before it, for a deletion);
    `old_path` is set for a rename only. A binary file has no hunks and
    `content_available` False; so has any file whose text the source did not give."""

    path: str
    old_path: str | None
    status: str
    hunks: tuple[Hunk, ...]
    binary: bool
    content_available: bool
    similarity: int | None = None  # percent, renames only

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"FileChange.status must be one of {STATUSES}, got {self.status!r}")
        if self.status in (RENAMED, COPIED) and not self.old_path:
            raise ValueError(f"a {self.status} file needs its old_path")

    @property
    def path_is_text(self) -> bool:
        """False when a name is not valid UTF-8 (it holds surrogate-escaped bytes)."""
        return not any(_has_surrogate(name) for name in (self.path, self.old_path) if name)

    @property
    def added_lines(self) -> tuple[DiffLine, ...]:
        return tuple(line for hunk in self.hunks for line in hunk.added)

    @property
    def removed_lines(self) -> tuple[DiffLine, ...]:
        return tuple(line for hunk in self.hunks for line in hunk.removed)


@dataclass(frozen=True, slots=True)
class ChangeSet:
    """A diff between two commits, and (when made by `changeset.resolve`) a pinned change.

    `base_sha` and `head_sha` are the commits the diff was taken between. The resolved
    fields tell where they came from: `pre_change_sha` is the version before the change
    (None when it could not be established, with a blocking `pre_change_unavailable`
    record), `how` says how each sha was found, `incompleteness` holds the records made
    while resolving.
    """

    base_sha: str
    head_sha: str
    files: tuple[FileChange, ...]
    rename_detection_skipped: bool = False  # git gave up on inexact renames (too many files)
    kind: str = "diff"  # diff | pr | compare | range
    repo: str = ""
    remote: str = ""
    pre_change_sha: str | None = None
    how: Mapping[str, str] = field(default_factory=dict)
    change_request: Any = None  # a hosts.ChangeRequest for a pr
    incompleteness: tuple[Any, ...] = ()

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(f.path for f in self.files)


def _has_surrogate(text: str) -> bool:
    return any("\ud800" <= ch <= "\udfff" for ch in text)


def unquote(name: str) -> str:
    """Undo git's C-style quoting of a path (a name wrapped in double quotes, with
    backslash escapes and octal bytes). An unquoted name is returned as it is."""
    if len(name) >= 2 and name[0] == '"' and name[-1] == '"':
        raw = bytearray()
        i, body = 0, name[1:-1]
        while i < len(body):
            ch = body[i]
            if ch != "\\":
                raw += ch.encode("utf-8")
                i += 1
                continue
            i += 1
            if i >= len(body):
                raise DiffParseError(f"bad escape at the end of {name!r}")
            nxt = body[i]
            if nxt in "01234567":
                digits = body[i:i + 3]
                m = re.match(r"[0-7]{1,3}", digits)
                assert m
                raw.append(int(m.group(0), 8) & 0xFF)
                i += len(m.group(0))
            elif nxt in _ESCAPES:
                raw.append(_ESCAPES[nxt])
                i += 1
            else:
                raise DiffParseError(f"unknown escape \\{nxt} in {name!r}")
        return raw.decode("utf-8", errors="surrogateescape")
    return name


def _strip_prefix(name: str, prefix: str) -> str:
    return name[len(prefix):] if name.startswith(prefix) else name


def _split_header(rest: str) -> tuple[str, str]:
    """The two names of a `diff --git a/X b/Y` line (prefixes still on)."""
    if '"' not in rest:
        # No quoting: without a rename the two names are equal, so the line is "a/P b/P".
        if len(rest) % 2 == 1 and len(rest) >= 5:
            name = rest[2:2 + (len(rest) - 5) // 2]
            if rest == f"a/{name} b/{name}":
                return f"a/{name}", f"b/{name}"
        left, sep, right = rest.partition(" b/")
        return left, ("b/" + right) if sep else right
    tokens: list[str] = []
    i = 0
    while i < len(rest) and len(tokens) < 2:
        while i < len(rest) and rest[i] == " ":
            i += 1
        if i >= len(rest):
            break
        if rest[i] == '"':
            j = i + 1
            while j < len(rest) and rest[j] != '"':
                j += 2 if rest[j] == "\\" else 1  # an escape takes the next character with it
            tokens.append(rest[i:j + 1])
            i = j + 1
        else:
            j = rest.find(" ", i)
            j = len(rest) if j < 0 else j
            tokens.append(rest[i:j])
            i = j
    if len(tokens) != 2:
        raise DiffParseError(f"cannot read the file names in: diff --git {rest}")
    return tokens[0], tokens[1]


class _Entry:
    def __init__(self, a: str, b: str) -> None:
        self.a, self.b = a, b
        self.old_path: str | None = None
        self.new_path: str | None = None
        self.new_file = self.deleted = self.binary = False
        self.renamed = False
        self.copied = False
        self.similarity: int | None = None
        self.hunks: list[Hunk] = []

    def finish(self) -> FileChange:
        if self.renamed or self.copied:
            status = RENAMED if self.renamed else COPIED
            path, old = self.new_path or self.b, self.old_path or self.a
        elif self.new_file:
            status, path, old = ADDED, self.new_path or self.b, None
        elif self.deleted:
            status, path, old = DELETED, self.old_path or self.a, None
        else:
            status, path, old = MODIFIED, self.new_path or self.old_path or self.b, None
        return FileChange(path, old, status, tuple(self.hunks), self.binary, not self.binary, self.similarity)


def _header_name(value: str, prefix: str) -> str | None:
    value = value.rstrip("\t")
    if value == "/dev/null":
        return None
    return _strip_prefix(unquote(value), prefix)


def parse(diff: bytes | str) -> tuple[FileChange, ...]:
    """The files of a `git diff` made with `--src-prefix=a/ --dst-prefix=b/`.

    Raises DiffParseError for a hunk that ends before its counts say it should."""
    text = diff.decode("utf-8", errors="replace") if isinstance(diff, (bytes, bytearray)) else diff
    entries: list[_Entry] = []
    cur: _Entry | None = None
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        i += 1
        if line.startswith("diff --git "):
            a, b = _split_header(line[len("diff --git "):])
            cur = _Entry(_strip_prefix(unquote(a), "a/"), _strip_prefix(unquote(b), "b/"))
            entries.append(cur)
            continue
        if cur is None:
            continue
        if line.startswith("new file mode"):
            cur.new_file = True
        elif line.startswith("deleted file mode"):
            cur.deleted = True
        elif line.startswith("similarity index "):
            m = re.match(r"similarity index (\d+)%", line)
            cur.similarity = int(m.group(1)) if m else None
        elif line.startswith("rename from "):
            cur.renamed = True
            cur.old_path = unquote(line[len("rename from "):])
        elif line.startswith("rename to "):
            cur.renamed = True
            cur.new_path = unquote(line[len("rename to "):])
        elif line.startswith("copy from "):
            cur.copied = True
            cur.old_path = unquote(line[len("copy from "):])
        elif line.startswith("copy to "):
            cur.copied = True
            cur.new_path = unquote(line[len("copy to "):])
        elif line.startswith("Binary files ") or line.startswith("GIT binary patch"):
            cur.binary = True
        elif line.startswith("--- "):
            name = _header_name(line[4:], "a/")
            if name is not None and not (cur.renamed or cur.copied):
                cur.old_path = name
        elif line.startswith("+++ "):
            name = _header_name(line[4:], "b/")
            if name is not None and not (cur.renamed or cur.copied):
                cur.new_path = name
        elif line.startswith("@@ "):
            m = _HUNK.match(line)
            if not m:
                raise DiffParseError(f"bad hunk header: {line!r}")
            old_start, old_count = int(m.group(1)), int(m.group(2) if m.group(2) is not None else 1)
            new_start, new_count = int(m.group(3)), int(m.group(4) if m.group(4) is not None else 1)
            removed: list[DiffLine] = []
            added: list[DiffLine] = []
            context: list[ContextLine] = []
            old_no = old_start if old_count else old_start + 1  # a zero count points at the line before
            new_no = new_start if new_count else new_start + 1
            left_old, left_new = old_count, new_count
            while (left_old > 0 or left_new > 0) or (i < n and lines[i].startswith("\\")):
                if i >= n:
                    raise DiffParseError("a hunk ends before its line counts are reached")
                body = lines[i]
                i += 1
                if body.startswith("\\"):  # "\ No newline at end of file"
                    continue
                kind, content = (body[:1], body[1:]) if body else (" ", "")  # a blank context line may lose its space
                if content.endswith("\r"):
                    content = content[:-1]
                if kind == "-" and left_old > 0:
                    removed.append(DiffLine(old_no, content))
                    old_no += 1
                    left_old -= 1
                elif kind == "+" and left_new > 0:
                    added.append(DiffLine(new_no, content))
                    new_no += 1
                    left_new -= 1
                elif kind == " " and left_old > 0 and left_new > 0:
                    context.append(ContextLine(old_no, new_no, content))
                    old_no += 1
                    new_no += 1
                    left_old -= 1
                    left_new -= 1
                else:
                    raise DiffParseError(f"unexpected line inside a hunk: {body!r}")
            cur.hunks.append(Hunk(old_start, old_count, new_start, new_count, tuple(removed), tuple(added), tuple(context)))
    return _merge_type_changes([entry.finish() for entry in entries])


def _merge_type_changes(files: list[FileChange]) -> tuple[FileChange, ...]:
    """A file that becomes a symlink (or the reverse) is shown as a deletion followed by an
    addition of the same path. It is one changed file."""
    out: list[FileChange] = []
    for change in files:
        prev = out[-1] if out else None
        if prev is not None and prev.path == change.path and {prev.status, change.status} == {ADDED, DELETED}:
            out[-1] = FileChange(
                change.path, None, TYPE_CHANGED, prev.hunks + change.hunks,
                prev.binary or change.binary, not (prev.binary or change.binary),
            )
        else:
            out.append(change)
    return tuple(out)
