"""Helpers for the M4 tests: fixture repositories as file:// remotes, an isolated git, no network.

The M1 fixtures are built once per process (about 1.5 s) into a temporary directory and
shared read-only; a test that must damage a repository copies it first. Every git call
made by the code under test runs with `GIT_ALLOW_PROTOCOL=file`, so even a remote that is
an https URL is refused by git itself before any socket is opened.
"""

import atexit
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.fixtures import build

_ROOT: Path | None = None
_REPOS: dict[str, Path] | None = None


def _rmtree(path: Path) -> None:
    def fix(func, p, _exc):
        os.chmod(p, stat.S_IRWXU)
        func(p)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=fix)
    else:
        shutil.rmtree(path, onerror=fix)


def remove_tree(path) -> None:
    """Remove a directory tree whose files may be read only (a cache checkout)."""
    _rmtree(Path(path))


def isolated_env(home: Path) -> dict[str, str]:
    """Overrides for GitRunner: no user config, only the file transport."""
    return {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".xdg"),
        "GIT_ALLOW_PROTOCOL": "file",
    }


def fixture_repos() -> dict[str, Path]:
    """Workspace name ("acme/web") to repository path; built on first use, with the
    server-side options a blobless fetch needs switched on."""
    global _ROOT, _REPOS
    if _REPOS is None:
        _ROOT = Path(tempfile.mkdtemp(prefix="reachmap-fixtures-"))
        atexit.register(_rmtree, _ROOT)
        _REPOS = build.build_all(_ROOT / "fx")
        for path in _REPOS.values():
            enable_server_filters(path)
    return _REPOS


def enable_server_filters(path: Path) -> None:
    """Let a fixture repository, as a remote, serve blobless fetches and fetches by sha."""
    for key in ("uploadpack.allowFilter", "uploadpack.allowAnySHA1InWant"):
        proc = build.run_git(path, "config", key, "true")
        assert proc.returncode == 0, proc.stderr


def remote_url(path: Path) -> str:
    return "file://" + str(path)


def rev(path: Path, ref: str) -> str:
    proc = build.run_git(path, "rev-parse", "--verify", f"{ref}^{{commit}}")
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def make_repo(root: Path, name: str, commits: list[dict]) -> Path:
    """A small repository with the fixture builder's fixed identity and dates.

    Each commit: {"files": {path: str | bytes}, "delete": [paths], "message": str,
    "branch": optional, "from": optional start for a new branch, "tag": optional}."""
    env = build.git_env(root)
    repo = build.Repo(root / name, env)
    repo.init()
    for spec in commits:
        branch = spec.get("branch", "main")
        repo.checkout(branch, spec.get("from"))
        for rel in spec.get("delete", []):
            (repo.path / rel).unlink()
        for rel, content in spec.get("files", {}).items():
            target = repo.path / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                target.write_bytes(content)
            else:
                target.write_text(content, encoding="utf-8", newline="")
        repo.commit(spec.get("message", "commit"))
        if "tag" in spec:
            repo.tag(spec["tag"])
    repo.git("checkout", "-q", "main")
    enable_server_filters(repo.path)
    return repo.path


def git_raw(repo: Path, *args: str, input: bytes | None = None) -> str:
    """Run git in a repository with the fixture identity; returns stripped stdout, raises on failure."""
    proc = subprocess.run(
        ["git", "-c", "init.defaultBranch=main", *args], cwd=repo, env=build.git_env(Path(repo).parent),
        input=input, capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stderr.decode(errors='replace')}")
    return proc.stdout.decode().strip()


def make_raw_repo(root: Path, name: str, trees: list) -> tuple[Path, list[str]]:
    """A repository built from raw tree objects, for names and modes `git add` would refuse.

    Each tree is a list of (mode, name_bytes, content) where content is bytes (a blob is
    written), or a 40 hex string for a ready object id (a submodule commit), or a nested
    list of entries (a tree). Trees are written with `hash-object --literally`, so a name
    such as `..` or `.git` is stored as given. Commit n has commit n-1 as its parent and
    `main` points at the last one. Returns (path, [commit shas])."""
    repo = root / name
    repo.mkdir(parents=True)
    git_raw(repo, "init", "-q")

    def write_tree(entries: list) -> str:
        raw = b""
        for mode, entry_name, content in sorted(entries, key=lambda e: e[1]):
            if isinstance(content, list):
                oid = write_tree(content)
            elif isinstance(content, str):
                oid = content
            else:
                oid = git_raw(repo, "hash-object", "-w", "--stdin", input=content)
            raw += mode.encode() + b" " + entry_name + b"\0" + bytes.fromhex(oid)
        return git_raw(repo, "hash-object", "-t", "tree", "-w", "--stdin", "--literally", input=raw)

    shas: list[str] = []
    for entries in trees:
        tree = write_tree(entries)
        args = ["commit-tree", tree, "-m", "c"] + (["-p", shas[-1]] if shas else [])
        shas.append(git_raw(repo, *args))
    git_raw(repo, "update-ref", "refs/heads/main", shas[-1])
    enable_server_filters(repo)
    return repo, shas


class TempDirCase(unittest.TestCase):
    """A fresh temporary directory per test, removed afterwards."""

    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp(prefix="reachmap-test-"))
        self.addCleanup(_rmtree, self.tmp)
