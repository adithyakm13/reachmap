"""The advisory lock of the review layer: `<reports dir>/.reachmap/lock`.

`analyse` takes it once, after the analysis has finished, for one short critical section: it assigns the revision number, carries the
decisions, writes the report and enters the log. `store.append` (so `decide` and an import) and `revisions.record` take it too. It is taken
with `fcntl.flock` (an exclusive lock on the open file) on POSIX and `msvcrt.locking` on Windows, and a take that waits longer than
`timeout` seconds gives up with a message instead of waiting for ever. A thread that holds it takes it again without waiting for itself.

It is advisory and it is not safe on a network file system (NFS may not honour `flock`): the reports directory is for one machine.
Where neither module exists there is no lock, and two runs at the same moment can again take the same revision number.
"""

from __future__ import annotations

import contextlib
import os
import threading
import time
from pathlib import Path
from typing import Iterator

from reachmap.review.formats import StoreError

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None  # type: ignore[assignment]
try:
    import msvcrt
except ImportError:  # POSIX
    msvcrt = None  # type: ignore[assignment]

__all__ = ["held", "lock_path"]

DIR = ".reachmap"
NAME = "lock"
TIMEOUT = 60.0
_mine = threading.local()


def lock_path(base: str | Path) -> Path:
    return Path(base) / DIR / NAME


def _try(fd: int) -> bool:
    """One non-blocking attempt; False when somebody else holds the lock."""
    if fcntl is not None:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False
    if msvcrt is not None:  # pragma: no cover (Windows)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    return True  # no locking on this platform


def _release(fd: int) -> None:
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_UN)
    elif msvcrt is not None:  # pragma: no cover (Windows)
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)


@contextlib.contextmanager
def held(base: str | Path, timeout: float = TIMEOUT) -> Iterator[None]:
    """Hold the lock of the reports directory `base` for the block. `StoreError` when the lock cannot be made or is not let go in `timeout` seconds."""
    path = lock_path(base)
    key = os.path.abspath(path)
    mine: set[str] = _mine.__dict__.setdefault("paths", set())
    if key in mine:
        yield
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o644)
    except OSError as exc:
        raise StoreError(f"{DIR}/{NAME}: the lock cannot be made: {exc.strerror or type(exc).__name__}") from None
    try:
        deadline = time.monotonic() + timeout
        while not _try(fd):
            if time.monotonic() >= deadline:
                raise StoreError(f"{DIR}/{NAME}: another reachmap has held the lock for more than {int(timeout)} seconds; wait for it or end it")
            time.sleep(0.02)
        mine.add(key)
        try:
            yield
        finally:
            mine.discard(key)
            _release(fd)
    finally:
        os.close(fd)
