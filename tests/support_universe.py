"""One git repository for a whole test module, one branch of commits per case, one git cache.

Building a case costs a few commits instead of a repository, and the Git layer fetches into one bare
cache that every test of the module shares (the M5 notes: this was the fast shape). Every case is cut from
the same empty root commit, so its files are only its own, and its commits are on no branch (the repository
has one ref, `main`, so a fetch does not negotiate over every case).

`Base` is the test case class: `self.repo(*commits)` builds a case, `self.use_shared(key, *commits)` builds
it once for the tests that only read it, `self.sha("main")` and `self.sha("main~1")` name the case's last
and first commit.
"""

from __future__ import annotations

import atexit
import dataclasses
import hashlib
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from unittest import mock

from reachmap.gitlayer import Git
from tests import scenario_support as ss
from tests import support_git as sg
from tests.fixtures import build


class Universe:
    def __init__(self, prefix: str) -> None:
        self.root = Path(tempfile.mkdtemp(prefix=prefix))
        atexit.register(sg.remove_tree, self.root)
        self.path = self.root / "universe"
        self.repo = build.Repo(self.path, build.git_env(self.root))
        self.repo.init()
        self.repo.commits += 1
        self.repo.git("commit", "--allow-empty", "-q", "--no-verify", "-m", "root", date=f"{build.EPOCH + 3600} +0000")
        sg.enable_server_filters(self.path)
        self.url = sg.remote_url(self.path)
        self.cases = 0
        self.root_sha: str | None = None
        self.bases: dict[str, str] = {}  # the first commit of a case, by its content: cases that start alike share it
        self.keyed: dict[str, list[str]] = {}

    def add(self, commits: list[dict]) -> list[str]:
        """Build the commits on top of the root; returns their shas, first to last. One `git fast-import` writes the
        whole case (a few loose objects, no checkout, no index: a case used to cost a dozen git processes)."""
        if self.root_sha is None:
            self.root_sha = self.repo.git("rev-parse", "main").strip()
        return self.add_on(self.root_sha, commits)

    def add_on(self, parent: str, commits: list[dict]) -> list[str]:
        """The same on top of any commit of the universe."""
        self.cases += 1
        stream = bytearray()
        for i, spec in enumerate(commits, 1):
            message = spec.get("message", "commit").encode()
            stream += b"commit refs/heads/case\nmark :%d\ncommitter T <t@example.invalid> %d +0000\ndata %d\n%s\n" % (
                i, build.EPOCH + 7200 + 3600 * i, len(message), message)
            stream += b"from %s\n" % (parent.encode() if i == 1 else b":%d" % (i - 1))
            for rel in spec.get("delete", []):
                stream += b"D %s\n" % rel.encode()
            for rel, content in spec.get("files", {}).items():
                data = content if isinstance(content, bytes) else content.encode("utf-8")
                stream += b"M 100644 inline %s\ndata %d\n%s\n" % (rel.encode(), len(data), data)
        marks = self.root / f"marks-{self.cases}"
        done = subprocess.run(["git", "-C", str(self.path), "fast-import", "--quiet", "--force", f"--export-marks={marks}"], input=bytes(stream),
                              capture_output=True, env=build.git_env(self.root))
        if done.returncode != 0:
            raise RuntimeError(done.stderr.decode(errors="replace"))
        shas = dict(line.split()[:2] for line in marks.read_text().splitlines())
        marks.unlink()
        return [shas[f":{i}"] for i in range(1, len(commits) + 1)]


def build_raw(u: Universe, commits: list[dict[bytes, bytes]]) -> list[str]:
    """Commits on top of the root with file names that are bytes (a name that is not UTF-8): `git add` would
    refuse them on some systems, `git fast-import` writes them as given. Returns the shas, first to last."""
    import subprocess

    u.cases += 1
    ref = f"refs/heads/raw-{u.cases}"
    parent = u.repo.git("rev-parse", "main").strip().encode()
    data = b""
    for i, files in enumerate(commits):
        data += b"commit " + ref.encode() + b"\ncommitter T <t@example.invalid> 1700000000 +0000\ndata 1\nx\n"
        data += (b"from " + parent + b"\n") if i == 0 else b""
        for name, content in files.items():
            data += b"M 100644 inline " + name + b"\ndata " + str(len(content)).encode() + b"\n" + content + b"\n"
    done = subprocess.run(["git", "-C", str(u.path), "fast-import", "--quiet"], input=data, capture_output=True, env=build.git_env(u.root))
    if done.returncode != 0:
        raise RuntimeError(done.stderr.decode(errors="replace"))
    shas = subprocess.run(["git", "-C", str(u.path), "rev-list", "--reverse", f"-{len(commits)}", ref], capture_output=True, check=True).stdout.decode().split()
    u.repo.git("update-ref", "-d", ref)
    return shas


class Base(sg.TempDirCase):
    """Subclasses set `UNIVERSE_PREFIX` and `REPO` and provide `adapter()`."""

    UNIVERSE_PREFIX = "reachmap-adapter-"
    REPO = "acme/api"
    _universes: dict[str, Universe] = {}

    @classmethod
    def universe(cls) -> Universe:
        if cls.UNIVERSE_PREFIX not in Base._universes:
            Base._universes[cls.UNIVERSE_PREFIX] = Universe(cls.UNIVERSE_PREFIX)
        return Base._universes[cls.UNIVERSE_PREFIX]

    def setUp(self) -> None:
        super().setUp()
        self.git: Git | None = None
        self.shas: list[str] = []

    def _use(self, shas: list[str], cache: Path | None = None) -> None:
        u = self.universe()
        self.shas, self.path, self.url = shas, u.path, u.url
        self.records: list = []
        self.git = Git(cache or self.tmp / "cache", env=sg.isolated_env(self.tmp / "home"), record_to=self.records)

    def use_shared(self, key: str, *commits: dict) -> None:
        """The same case for the tests that only read it: built on first use."""
        u = self.universe()
        if key not in u.keyed:
            u.keyed[key] = u.add(list(commits))
        self._use(u.keyed[key], u.root / f"cache-{key}")

    def repo(self, *commits: dict) -> Path:
        """A case: `commits` on top of the root. Cases that start with the same first commit share it (and the Git
        cache that fetched it): only the commits after it are built, and fetched, for each."""
        u = self.universe()
        key = hashlib.sha1(repr(commits[0]).encode()).hexdigest()
        if key not in u.bases:
            u.bases[key] = u.add([commits[0]])[0]
        rest = u.add_on(u.bases[key], list(commits[1:])) if len(commits) > 1 else []
        self._use([u.bases[key], *rest], u.root / f"cache-base-{key[:12]}")
        return self.path

    def repo_raw(self, *commits: dict[bytes, bytes]) -> Path:
        """A case whose file names are bytes (see `build_raw`)."""
        self._use(build_raw(self.universe(), list(commits)))
        return self.path

    def sha(self, ref: str) -> str:
        if ref == "main":
            return self.shas[-1]
        if ref.startswith("main~"):
            return self.shas[-1 - int(ref[5:])]
        return sg.rev(self.path, ref)

    def change(self, base: str = "main~1", head: str = "main", pre: str | None = "merge_base") -> Any:
        """A ChangeSet between two refs; the pre-change version is the base unless `pre` is None."""
        b, h = self.sha(base), self.sha(head)
        cs = self.git.diff(self.url, b, h)
        return dataclasses.replace(cs, repo=self.REPO, remote=self.url, kind="compare", pre_change_sha=None if pre is None else b)

    def make_adapter(self, **kw: Any) -> Any:
        raise NotImplementedError

    def analyze(self, adapter: Any = None, **kw: Any) -> Any:
        adapter = adapter or self.make_adapter()
        cs = self.change(**kw)
        self.analysis = adapter.analyze(cs)
        self.assert_evidence_resolves(self.analysis)
        return self.analysis

    def impact(self, path: str) -> Any:
        return next(i for i in self.analysis.impacts if i.path == path)

    def assert_evidence_resolves(self, analysis: Any) -> None:
        run = mock.Mock()
        run.analysis = analysis
        problems = ss.evidence_problems({self.REPO: self.path}, ss.iter_evidence(run))
        self.assertEqual(problems, [])

    def kinds(self) -> list[tuple[str, bool]]:
        return sorted((r.kind, r.blocks_selection) for r in self.records)


def f(text: list[str]) -> str:
    """Source text from lines."""
    return "\n".join(text) + "\n"
