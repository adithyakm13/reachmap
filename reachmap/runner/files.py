"""Reading a file the runner serves: the one way, for reports and for static files.

A file is served only when it is a regular file, reached without following a link (the last step is opened with `O_NOFOLLOW`, and a link at
any step is refused by the callers), has no second hard link (a hard link to a file elsewhere would be a way round both), and is not too big.
Names are matched exactly, by the entry the directory really has: a file system that ignores case would otherwise answer `COMPLETE-R1` with
`complete-r1`. A name that is a report's is one pattern, here.
"""

from __future__ import annotations

import os
import re
import stat

__all__ = ["MAX_LISTED", "NAME", "listed", "is_report_file", "read_regular"]

MAX_LISTED = 500  # reports a listing names (the runner's and the MCP server's)

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}$")  # a report's name without `.json`: what the report slugs are made of


def is_report_file(name: object) -> bool:
    """`<name>.json` for a name of `NAME`."""
    return isinstance(name, str) and name.endswith(".json") and bool(NAME.match(name[:-5]))


def listed(folder: str | os.PathLike, name: str) -> bool:
    """Whether the folder has an entry with exactly this name."""
    try:
        with os.scandir(folder) as entries:
            return any(e.name == name for e in entries)
    except OSError:
        return False


def read_regular(path: str | os.PathLike, limit: int) -> bytes | None:
    """The bytes of a regular file with one hard link, at most `limit` of them, or None for anything else (a link, a folder, a file that is too
    big, a file that cannot be read)."""
    try:
        if stat.S_ISLNK(os.lstat(path).st_mode):
            return None
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
            return None
        with os.fdopen(fd, "rb", closefd=False) as f:
            data = f.read(limit + 1)
        return data if len(data) <= limit else None
    except OSError:
        return None
    finally:
        os.close(fd)
