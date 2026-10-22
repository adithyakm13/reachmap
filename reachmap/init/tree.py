"""A repository at one commit, as the detectors read it: the file list and three-state reads.

`Tree.from_checkout` wraps a git layer `Checkout` (files exactly as git stores them, read from the cache folder); `Tree.from_files`
is an in-memory tree for tests. A read answers found, absent or unreadable (`model.ReadResult`): an unreadable read is kept in
`Tree.unread` so the output can say what was not looked at, never turned into an absence. Vendored folders are not scanned
(`VENDORED`); a scan has a file budget and a size limit, and a scan cut by either is listed in `Tree.limits`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Iterable, Iterator, Mapping, Sequence

from reachmap.model import Read, ReadResult

__all__ = ["Tree", "VENDORED", "code_line", "line_of"]

VENDORED = (
    "node_modules", "dist", "build", ".venv", "venv", "vendor", "__pycache__", "site-packages", ".next", ".nuxt", ".tox",
    "coverage", "bower_components", ".git",
)
MAX_SCAN_FILES = 20000
MAX_FILE_BYTES = 1024 * 1024
MAX_CACHE_BYTES = 64 * 1024 * 1024  # texts kept between reads; past this the cache is emptied (a read is a file read from the cache folder)
_NON_CODE_START = ("#", "//", "*", "/*", '"""', "'''")


def code_line(line: str) -> bool:
    """A line that is not blank and does not start as a comment or a docstring."""
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith(_NON_CODE_START)


def line_of(text: str, needle: str | re.Pattern[str], start: int = 0) -> int:
    """The 1-based line of the first match of `needle` (a string or a pattern) at or after character `start`; 0 when absent."""
    if isinstance(needle, str):
        at = text.find(needle, start)
    else:
        m = needle.search(text, start)
        at = m.start() if m else -1
    return text.count("\n", 0, at) + 1 if at >= 0 else 0


def _vendored(path: str) -> bool:
    return any(part in VENDORED for part in path.split("/")[:-1])


class Tree:
    """The files of a repository at one commit. `files` leaves out the vendored folders; `all_files` has every regular file."""

    def __init__(self, files: Iterable[str], reader: Callable[[str], ReadResult], skipped: Mapping[str, str] | None = None) -> None:
        self.all_files: tuple[str, ...] = tuple(sorted(set(files)))
        self.files: tuple[str, ...] = tuple(f for f in self.all_files if not _vendored(f))
        self._all = frozenset(self.all_files)
        self._reader = reader
        self._cache: dict[str, ReadResult] = {}
        self._cached_bytes = 0
        self.skipped: dict[str, str] = dict(skipped or {})  # symlinks, submodules and unsafe names the checkout did not write
        self.unread: dict[str, str] = {}  # path to why, for every read that was unreadable
        self.limits: list[str] = []
        self._dirs: frozenset[str] | None = None

    # ---- construction

    @classmethod
    def from_files(cls, files: Mapping[str, str | bytes]) -> "Tree":
        def read(path: str) -> ReadResult:
            data = files[path]
            if isinstance(data, bytes):
                try:
                    return ReadResult.found(data.decode("utf-8"))
                except UnicodeDecodeError:
                    return ReadResult.unreadable("the file is not valid UTF-8")
            return ReadResult.found(data)

        return cls(files, read)

    @classmethod
    def from_checkout(cls, checkout: object) -> "Tree":
        root = Path(checkout.path)  # type: ignore[attr-defined]

        def read(path: str) -> ReadResult:
            try:
                data = (root / path).read_bytes()
            except OSError as exc:
                return ReadResult.unreadable(f"cannot read the file: {exc.strerror or type(exc).__name__}")
            if len(data) > MAX_FILE_BYTES * 5:
                return ReadResult.unreadable("the file is over 5 MB")
            try:
                return ReadResult.found(data.decode("utf-8"))
            except UnicodeDecodeError:
                return ReadResult.unreadable("the file is not valid UTF-8")

        return cls(checkout.files, read, dict(checkout.skipped))  # type: ignore[attr-defined]

    # ---- questions about the file list

    def has(self, path: str) -> bool:
        return path in self._all

    def dirs(self) -> frozenset[str]:
        """Every folder that holds a file (and their parents), repository relative, without the root."""
        if self._dirs is None:
            found: set[str] = set()
            for f in self.files:
                parts = f.split("/")[:-1]
                for i in range(1, len(parts) + 1):
                    found.add("/".join(parts[:i]))
            self._dirs = frozenset(found)
        return self._dirs

    def is_dir(self, path: str) -> bool:
        return path.strip("/") in self.dirs() if path.strip("/") else True

    def with_suffix(self, suffixes: Sequence[str]) -> list[str]:
        return [f for f in self.files if f.endswith(tuple(suffixes))]

    def named(self, name: str) -> list[str]:
        return [f for f in self.files if f == name or f.endswith("/" + name)]

    # ---- reads

    def read(self, path: str) -> ReadResult:
        got = self._cache.get(path)
        if got is not None:
            return got
        if path in self.skipped:
            got = ReadResult.unreadable(f"not written to the checkout: {self.skipped[path]}")
        elif path not in self._all:
            got = ReadResult.absent()
        else:
            got = self._reader(path)
        if got.state == Read.UNREADABLE:
            self.unread[path] = got.detail or "unreadable"
        elif got.state == Read.FOUND:
            self._cached_bytes += len(got.text)
            if self._cached_bytes > MAX_CACHE_BYTES:
                self._cache.clear()  # unreadable answers are kept in `unread`, and a file is read again when it is asked for again
                self._cached_bytes = len(got.text)
        self._cache[path] = got
        return got

    def text(self, path: str) -> str | None:
        got = self.read(path)
        return got.text if got.state == Read.FOUND else None

    def scan(self, suffixes: Sequence[str], *, skip: Callable[[str], bool] | None = None) -> Iterator[tuple[str, str]]:
        """(path, text) of the non-vendored files with one of the suffixes, in path order; `skip` leaves out paths. A file over
        1 MB is not read (listed in `unread`), and the scan stops at `MAX_SCAN_FILES` files (listed in `limits`)."""
        count = 0
        paths = [f for f in self.files if f.endswith(tuple(suffixes)) and not (skip and skip(f))]
        for path in paths:
            if count >= MAX_SCAN_FILES:
                note = f"only the first {MAX_SCAN_FILES} of {len(paths)} files with these suffixes were scanned"
                if note not in self.limits:
                    self.limits.append(note)
                return
            count += 1
            text = self.read(path)
            if text.state != Read.FOUND:
                continue
            if len(text.text) > MAX_FILE_BYTES:
                self.unread[path] = "over 1 MB: not scanned"
                continue
            yield path, text.text
