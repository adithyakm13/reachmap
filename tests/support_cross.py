"""Small multi-repository workspaces for the M8 tests: a few repositories built with one `git fast-import` each,
a `reachmap.toml` that names them, and the propagation of a change from one of them.

    class MyTests(CrossCase):
        def test_x(self):
            self.repos(web=[{"files": {...}}], api=[{"files": {...}}, {"files": {...}}])
            ws = self.workspace(WORKSPACE)               # {web} and {api} in the text are their file:// urls
            result = self.propagate(ws, "t/api", "main~1...main")

A commit is `{"files": {path: text}, "delete": [path], "message": str, "branch": "main", "from": "main", "tag": "v1"}`;
`branch` defaults to the last one used (`main` at first), `from` starts a new branch at the tip of another branch,
`tag` is a lightweight tag on the commit. `self.sha(repo, ref)` pins a ref of a repository.
"""

from __future__ import annotations

import atexit
import subprocess
import tempfile
from pathlib import Path

from reachmap import config, propagate
from reachmap.changeset import resolve
from reachmap.gitlayer import Git
from tests import scenario_support as ss
from tests import support_git as sg
from tests.support_universe import Universe
from tests.fixtures import build


def build_repo(root: Path, name: str, commits: list[dict]) -> Path:
    """A repository with `commits` (see the module docstring), written by one `git fast-import`."""
    env = build.git_env(root)
    repo = build.Repo(root / name, env)
    repo.init()
    sg.enable_server_filters(repo.path)
    stream = bytearray()
    tips: dict[str, int] = {}  # branch name: mark of its tip
    branch = "main"
    for i, spec in enumerate(commits, 1):
        branch = spec.get("branch", branch)
        message = spec.get("message", f"commit {i}").encode()
        stream += b"commit refs/heads/%s\nmark :%d\ncommitter T <t@example.invalid> %d +0000\ndata %d\n%s\n" % (
            branch.encode(), i, build.EPOCH + 3600 * i, len(message), message)
        if branch in tips:
            stream += b"from :%d\n" % tips[branch]
        elif spec.get("from"):
            stream += b"from :%d\n" % tips[spec["from"]]
        for rel in spec.get("delete", []):
            stream += b"D %s\n" % rel.encode()
        for rel, content in spec.get("files", {}).items():
            data = content if isinstance(content, bytes) else content.encode("utf-8")
            stream += b"M 100644 inline %s\ndata %d\n%s\n" % (rel.encode(), len(data), data)
        tips[branch] = i
        if spec.get("tag"):
            stream += b"reset refs/tags/%s\nfrom :%d\n\n" % (spec["tag"].encode(), i)
    done = subprocess.run(["git", "-C", str(repo.path), "fast-import", "--quiet", "--force"], input=bytes(stream), capture_output=True, env=env)
    if done.returncode != 0:
        raise RuntimeError(done.stderr.decode(errors="replace"))
    return repo.path


class Store:
    """The repositories of one test class when its tests share them (`CrossCase.SHARED`): one repository per short name, every
    case a few commits on top of its empty root, one git cache for all of them (the pattern of `support_universe`)."""

    def __init__(self, label: str) -> None:
        self.root = Path(tempfile.mkdtemp(prefix=f"reachmap-cross-{label}-"))
        atexit.register(sg.remove_tree, self.root)
        self.cache = self.root / "cache"
        self.home = self.root / "home"
        self.universes: dict[str, Universe] = {}

    def add(self, name: str, specs: list[dict]) -> tuple[Path, str]:
        """The case as commits on top of the root of the repository `name`; `main` points at its last commit."""
        if name not in self.universes:
            self.universes[name] = Universe(f"reachmap-cross-{name}-")
        u = self.universes[name]
        for spec in specs:
            unsupported = {"branch", "from", "tag"} & set(spec)
            assert not unsupported, f"a shared case has no {sorted(unsupported)}: the test class must not be SHARED"
        shas = u.add(specs)
        u.repo.git("update-ref", "refs/heads/main", shas[-1])
        return u.path, u.url


_STORES: dict[type, Store] = {}


class CrossCase(sg.TempDirCase):
    SHARED = False  # True: the tests of the class share one repository per name and one git cache (no branches or tags in a case)

    def setUp(self) -> None:
        super().setUp()
        self.paths: dict[str, Path] = {}
        self.urls: dict[str, str] = {}
        self.records: list = []
        if self.SHARED:
            store = self.store()
            self.git = Git(store.cache, env=sg.isolated_env(store.home), record_to=self.records)
        else:
            self.git = Git(self.tmp / "cache", env=sg.isolated_env(self.tmp / "home"), record_to=self.records)

    @classmethod
    def store(cls) -> Store:
        if cls not in _STORES:
            _STORES[cls] = Store(cls.__name__.lower())
        return _STORES[cls]

    def repos(self, **commits: list[dict]) -> None:
        """Build repositories by short name (`web`, `api`, ...)."""
        self._round = getattr(self, "_round", 0) + 1  # a test that builds twice gets fresh folders
        for name, specs in commits.items():
            if self.SHARED:
                self.paths[name], self.urls[name] = self.store().add(name, specs)
                continue
            self.paths[name] = build_repo(self.tmp / f"repos{self._round}", name, specs)
            self.urls[name] = sg.remote_url(self.paths[name])

    def sha(self, name: str, ref: str = "main") -> str:
        return sg.rev(self.paths[name], ref)

    def workspace(self, text: str, **extra: str) -> config.Workspace:
        """The workspace the toml names; `{name}` is replaced by the file:// url of the repository of that short name."""
        return config.loads(text.format(**self.urls, **extra), fmt="toml", env={})

    def propagate(self, ws: config.Workspace, repo: str, spec: str, *, kind: str = "compare", limits: config.Limits | None = None, as_of: str | None = None) -> propagate.Propagation:
        """Resolve `spec` (`base...head` or `a..b`) in the repository and follow it along the edges."""
        short = repo.split("/", 1)[1]
        sep = "..." if kind == "compare" else ".."
        base, head = spec.split(sep)
        spec = f"{self.sha(short, base)}{sep}{self.sha(short, head)}"  # revisions like main~1 are the test's, git.pin takes names and shas
        change = resolve(ws, repo, git=self.git, **{kind: spec})
        return propagate.propagate(ws, repo, change, git=self.git, limits=limits, as_of=as_of)

    def kinds(self) -> list[tuple[str, str, bool]]:
        return sorted((r.kind, r.subject, r.blocks_selection) for r in self.records)

    def assert_evidence_resolves(self, result: propagate.Propagation) -> None:
        paths = {}
        for short, path in self.paths.items():
            paths[f"t/{short}"] = path
        problems = ss.evidence_problems(paths, list(result.evidence()))
        self.assertEqual(problems, [])
