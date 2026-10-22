"""Copy the build of the frontend (`frontend/dist/`) into the package (`reachmap/runner/static/`), where `reachmap serve` finds it.

    python -m reachmap.tools.bundle_frontend [--dist DIR] [--dest DIR]

The release build does this inside `python -m build` (the packaging hook in `setup.py` calls `bundle`), so a person who installs the wheel
needs no npm. Run by hand it puts the build into a source checkout, so that `reachmap serve` there serves the workspace.

There is nothing to copy without a build (`dist/index.html`): that is not an error, the package is then built without the workspace and the
runner serves its minimal page. A build that holds anything the runner would not serve (a link, a hidden file, an unknown kind of file,
a source map) is refused whole, and an earlier copy is only replaced by a complete one. The destination must be a folder called `static`
(the copy replaces what is there, so it must never be pointed at another folder).

Standard library only.
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from typing import Sequence

__all__ = ["BundleError", "DEFAULT_DEST", "DEFAULT_DIST", "KINDS", "bundle", "main"]

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIST = ROOT / "frontend" / "dist"
DEFAULT_DEST = ROOT / "reachmap" / "runner" / "static"
# the kinds of file the runner serves (`runner.server.CONTENT_TYPES`; a test holds this list to it), without source maps: a build does not ship them
KINDS = frozenset({".html", ".js", ".mjs", ".css", ".json", ".svg", ".png", ".ico", ".woff2", ".txt"})


class BundleError(ValueError):
    """The build cannot be bundled as it is."""


def _files(dist: Path) -> list[Path]:
    found: list[Path] = []
    for folder, dirs, names in os.walk(dist, followlinks=False):
        here = Path(folder)
        for entry in [*dirs, *names]:
            path = here / entry
            rel = path.relative_to(dist).as_posix()
            mode = os.lstat(path).st_mode
            if stat.S_ISLNK(mode):
                raise BundleError(f"{rel}: a link is not bundled")
            if entry.startswith("."):
                raise BundleError(f"{rel}: a hidden file or folder is not bundled")
            if stat.S_ISREG(mode):
                if path.suffix.lower() not in KINDS:
                    raise BundleError(f"{rel}: the runner does not serve this kind of file")
                found.append(path)
            elif not stat.S_ISDIR(mode):
                raise BundleError(f"{rel}: not a regular file")
    return sorted(found)


def bundle(dist: str | os.PathLike = DEFAULT_DIST, dest: str | os.PathLike = DEFAULT_DEST) -> int:
    """Copy the build in `dist` to `dest` (replacing what is there) and return the number of files; 0, and nothing touched, without a build."""
    dist, dest = Path(dist), Path(dest)
    if dest.name != "static":
        raise BundleError("the destination must be a folder called static")
    if not (dist / "index.html").is_file() or (dist / "index.html").is_symlink():
        return 0
    files = _files(dist)
    dest.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".static-", dir=dest.parent))
    try:
        for path in files:
            target = staging / path.relative_to(dist)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        if dest.exists():
            shutil.rmtree(dest)
        os.replace(staging, dest)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return len(files)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m reachmap.tools.bundle_frontend", description="Copy frontend/dist into reachmap/runner/static.")
    parser.add_argument("--dist", type=Path, default=DEFAULT_DIST, help="the build of the frontend (default: frontend/dist)")
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST, help="the folder to copy into, called static (default: reachmap/runner/static)")
    args = parser.parse_args(argv)
    try:
        count = bundle(args.dist, args.dest)
    except BundleError as exc:
        print(f"bundle_frontend: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"bundle_frontend: {exc.strerror or 'cannot copy'}", file=sys.stderr)
        return 3
    if count == 0:
        print("no frontend build (frontend/dist/index.html), nothing bundled")
    else:
        print(f"bundled {count} files into {args.dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
