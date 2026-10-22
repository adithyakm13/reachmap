"""The module map of a Python repository: which dotted names a file has, and which files a dotted name
means, at one commit.

A file is a module under every *mount* that holds it. A mount is a folder together with the dotted name
its contents are mounted at (nothing, for a source root):

- the repository root;
- every folder holding the configured service marker (`pyproject.toml` in the api monorepo): each
  service is a source root of its own, so two services can both have a package called `app`;
- every `library_roots` folder of the config (a shared library, visible to every service);
- a `src/` folder under any of those, unless it is itself a package;
- what a `pyproject.toml` in any of those folders declares: setuptools `package-dir` (`{ acme_contracts =
  "contracts" }` mounts `contracts/` at `acme_contracts`) and `packages.find` `where`, poetry `packages`
  `from`, hatch wheel `packages`, pdm `package-dir`. A layout that is declared in code (`setup.py`,
  `setup.cfg` `package_dir`) or in a way that is not understood is recorded (`module_map_gap`, not
  blocking), never guessed; a `pyproject.toml` that does not parse is a blocking `read_unreadable` and
  the map is incomplete.

Resolution asks "which file does this dotted name mean, for this importer": an importer sees the
mounts of its nearest service folder, the library roots and the repository root (a file under none sees
every mount). Every distinct file found is kept: a clash is ambiguity (`Resolved.ambiguous`), never a
silent pick. Folders without `__init__.py` are namespace packages. Relative imports are resolved by path.
"""

from __future__ import annotations

import posixpath
import tomllib
from dataclasses import dataclass
from typing import Any, Callable, Collection

from reachmap.adapters.base import record_quoted
from reachmap.errors import ModelError
from reachmap.incomplete import kind_spec, record, register_kind
from reachmap.model import ReadResult

__all__ = ["Mount", "ModuleMap", "Resolved", "record_quoted"]

for _kind, _blocking, _template in (
    ("module_map_gap", False,
     "Imports that go through the module names of {subject} were not fully established, because the module layout could not be read completely"),
    ("module_ambiguous", False,
     "Which file {subject} means was not established, because two files have that module name"),
):
    try:
        kind_spec(_kind)
    except ModelError:
        register_kind(_kind, _template, _blocking)

# Folders whose Python files are not modules of the repository (an environment, vendored packages, caches).
SKIP_SEGMENTS = frozenset({".venv", "venv", "site-packages", "node_modules", ".tox", "__pycache__", ".git", ".eggs"})
PYPROJECT = "pyproject.toml"


@dataclass(frozen=True, slots=True)
class Mount:
    """A folder `dir` whose contents are the modules (and packages) mounted at the dotted name `prefix`
    (empty: they are top level names). `owner` is the root that declares it: the repository root (`""`), a
    service folder or a library root. `kind`: repo, service, library, src or package_dir."""

    dir: str
    prefix: str
    owner: str
    kind: str


@dataclass(frozen=True, slots=True)
class Resolved:
    """What a dotted name means: the module files (a module's `.py`, a package's `__init__.py`), and
    whether a folder of that name exists without being a package of its own (a namespace package)."""

    files: tuple[str, ...]
    namespace: bool = False

    @property
    def found(self) -> bool:
        return bool(self.files) or self.namespace

    @property
    def ambiguous(self) -> bool:
        return len(self.files) > 1


def _importable_location(path: str) -> bool:
    return not any(seg in SKIP_SEGMENTS for seg in path.split("/")[:-1])


def _norm_dir(text: Any) -> str | None:
    """A repository relative folder from a config value; None when it is not plain text, is absolute or
    goes above the repository."""
    if not isinstance(text, str) or not text.strip():
        return None
    if text.startswith("/") or "\\" in text:
        return None
    parts = [p for p in text.strip().split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        return None
    return "/".join(parts)


class ModuleMap:
    """See the module docstring. Build one with `ModuleMap.build`."""

    def __init__(self, files: frozenset[str], mounts: tuple[Mount, ...], owners: dict[str, str], complete: bool, reasons: tuple[str, ...]) -> None:
        self.mounts = tuple(sorted(mounts, key=lambda m: (-len(m.dir), m.dir, m.prefix)))
        self.owners = dict(owners)
        self.complete = complete
        self.reasons = reasons
        self._py = frozenset(f for f in files if f.endswith(".py") and _importable_location(f))
        self._owners_deepest = sorted(self.owners, key=lambda o: (-len(o), o))
        self._library_owners = frozenset(o for o, kind in self.owners.items() if kind == "library")
        self._by_dotted: dict[str, list[tuple[str, str]]] = {}
        self._ns: dict[str, set[str]] = {}
        self._names: dict[str, tuple[str, ...]] = {}
        self._dirs: set[str] = {""}
        self._visible: dict[str, frozenset[str] | None] = {}
        by_dir: dict[str, list[Mount]] = {}  # the mounts of each folder, in the order of `self.mounts`
        for mount in self.mounts:
            by_dir.setdefault(mount.dir, []).append(mount)
        ident: dict[str, bool] = {}
        for path in sorted(self._py):
            parts = path.split("/")
            self._dirs.update("/".join(parts[:i]) for i in range(1, len(parts)))
            names: list[str] = []
            for depth in range(len(parts) - 1, -1, -1):  # from the deepest ancestor folder (the most specific mount) to the root
                mounts_here = by_dir.get(self._ancestor(parts, depth))
                if not mounts_here:
                    continue
                rel = parts[depth:]
                rel[-1] = rel[-1][:-3]
                if rel[-1] == "__init__":
                    rel.pop()
                if not all(ident.setdefault(x, x.isidentifier()) for x in rel):
                    continue
                for mount in mounts_here:
                    full = ([mount.prefix] if mount.prefix else []) + rel
                    if not full:
                        continue
                    dotted = ".".join(full)
                    if dotted not in names:
                        names.append(dotted)
                    self._by_dotted.setdefault(dotted, []).append((path, mount.owner))
                    package = dotted if parts[-1] == "__init__.py" else dotted.rpartition(".")[0]
                    pieces = package.split(".") if package else []
                    for i in range(1, len(pieces) + 1):
                        self._ns.setdefault(".".join(pieces[:i]), set()).add(mount.owner)
            self._names[path] = tuple(names)

    @staticmethod
    def _ancestor(parts: list[str], depth: int) -> str:
        return "/".join(parts[:depth])

    # ---- building

    @classmethod
    def build(
        cls,
        files: Collection[str],
        *,
        service_marker: str | None = None,
        library_roots: Collection[str] = (),
        read: Callable[[str], ReadResult] | None = None,
        target: Any = None,
        at: str = "",
    ) -> "ModuleMap":
        """The map of the repository whose files are `files`. `read` returns the text of a file (a
        `ReadResult`); `target` receives the records (a list or an Analysis); `at` (a short sha) is added to the
        subject of a record about a file that could not be read."""
        fileset = frozenset(files)
        usable = [f for f in fileset if _importable_location(f)]
        py_dirs: set[str] = set()  # every folder that holds a Python file, directly or below ("" when there is one)
        all_dirs: set[str] = set()  # every folder that holds any file
        for f in usable:
            parts = f.split("/")
            folders = ["/".join(parts[:i]) for i in range(0, len(parts))]
            all_dirs.update(folders)
            if f.endswith(".py"):
                py_dirs.update(folders)

        gaps: list[tuple[str, str, str]] = []
        reasons: list[str] = []
        complete = True
        owners: dict[str, str] = {"": "repo"}
        if service_marker:
            for f in usable:
                if f == service_marker or f.endswith("/" + service_marker):
                    folder = posixpath.dirname(f)
                    owners.setdefault(folder, "service")
        for lib in library_roots:
            folder = _norm_dir(lib)
            if folder is None or folder not in all_dirs:
                gaps.append((lib if isinstance(lib, str) and lib else "library_roots", "the library root is not a folder of the repository at this commit, so it is ignored", ""))
                continue
            owners[folder] = "library"
        mounts: dict[tuple[str, str], Mount] = {}

        def add(folder: str, prefix: str, owner: str, kind: str) -> None:
            mounts.setdefault((folder, prefix), Mount(folder, prefix, owner, kind))

        def has_python(folder: str) -> bool:
            return folder in py_dirs

        for owner, kind in sorted(owners.items()):
            add(owner, "", owner, kind)
            src = f"{owner}/src" if owner else "src"
            if has_python(src) and f"{src}/__init__.py" not in fileset:
                add(src, "", owner, "src")
            pyproject = f"{owner}/{PYPROJECT}" if owner else PYPROJECT
            if pyproject in fileset and read is not None:
                res = read(pyproject)
                if res.is_unreadable:
                    complete = False
                    reasons.append(f"{pyproject} could not be read")
                elif res.is_found:
                    try:
                        data = tomllib.loads(res.text)
                    except (tomllib.TOMLDecodeError, RecursionError, ValueError):
                        complete = False
                        reasons.append(f"{pyproject} is not valid TOML: its source folders were not read")
                        record(target if target is not None else [], "read_unreadable", f"{pyproject} at {at}" if at else pyproject, detail="the file is not valid TOML, so the package folders it declares were not read")
                    else:
                        try:
                            declared = cls._declared(owner, data, pyproject, has_python, gaps)
                        except (AttributeError, TypeError, KeyError, ValueError):  # a shape nothing above expected
                            declared = []
                            complete = False
                            reasons.append(f"{pyproject} has a layout this adapter could not read")
                            gaps.append((pyproject, "its package layout has a shape this adapter could not read, so the package folders it declares were not read", ""))
                        for folder, prefix in declared:
                            add(folder, prefix, owner, "package_dir")
            for setup in ("setup.py", "setup.cfg"):
                path = f"{owner}/{setup}" if owner else setup
                if path in fileset and read is not None:
                    res = read(path)
                    if res.is_found and "package_dir" in res.text:
                        gaps.append((path, "its package_dir is declared in code or in a format this adapter does not read, so a package may have a name that was not mapped", ""))
        if gaps:
            sink = target if target is not None else []
            for subject, fixed, quoted in gaps:
                record_quoted(sink, "module_map_gap", subject, fixed, quoted)
        return cls(fileset, tuple(mounts.values()), owners, complete, tuple(reasons))

    @staticmethod
    def _declared(owner: str, data: dict[str, Any], pyproject: str, has_python: Callable[[str], bool], gaps: list[tuple[str, str, str]]) -> list[tuple[str, str]]:
        """The (folder, prefix) mounts a pyproject.toml declares; what is not understood goes to `gaps`."""
        out: list[tuple[str, str]] = []

        def join(rel: Any, what: str) -> str | None:
            folder = _norm_dir(rel)
            if folder is None:
                gaps.append((pyproject, f"{what} is not a folder inside the repository, so it is ignored", repr(rel)))
                return None
            full = "/".join(p for p in (owner, folder) if p)
            if not has_python(full):
                gaps.append((pyproject, f"{what} names a folder with no Python files, so it is ignored", repr(rel)))
                return None
            return full

        tool = data.get("tool")
        tool = tool if isinstance(tool, dict) else {}
        setuptools = tool.get("setuptools")
        if isinstance(setuptools, dict):
            pd = setuptools.get("package-dir", setuptools.get("package_dir"))
            if pd is not None:
                if not isinstance(pd, dict):
                    gaps.append((pyproject, "tool.setuptools.package-dir is not a table, so it is ignored", ""))
                else:
                    for key, value in pd.items():
                        folder = join(value, f"tool.setuptools.package-dir entry {key!r}") if isinstance(value, str) else None
                        if not isinstance(value, str):
                            gaps.append((pyproject, "a tool.setuptools.package-dir entry is not text, so it is ignored", repr(key)))
                        elif folder is not None:
                            out.append((folder, key))
            packages = setuptools.get("packages")
            find = packages.get("find") if isinstance(packages, dict) else None
            if isinstance(find, dict) and "where" in find:
                where = find["where"]
                for item in ([where] if isinstance(where, str) else where if isinstance(where, list) else []):
                    folder = join(item, "tool.setuptools.packages.find.where")
                    if folder is not None:
                        out.append((folder, ""))
                if not isinstance(where, (str, list)):
                    gaps.append((pyproject, "tool.setuptools.packages.find.where is not text or a list, so it is ignored", ""))
        def table(parent: Any, key: str, what: str) -> dict | None:
            """`parent[key]` as a table; absent is None, a value that is not a table is a gap."""
            if not isinstance(parent, dict) or key not in parent:
                return None
            value = parent[key]
            if not isinstance(value, dict):
                gaps.append((pyproject, f"{what} is not a table, so it is ignored", ""))
                return None
            return value

        poetry = table(tool, "poetry", "tool.poetry")
        if poetry is not None and "packages" in poetry:
            pkgs = poetry["packages"]
            if not isinstance(pkgs, list):
                gaps.append((pyproject, "tool.poetry.packages is not a list, so it is ignored", ""))
            else:
                for entry in pkgs:
                    if not isinstance(entry, dict) or not isinstance(entry.get("include"), str):
                        gaps.append((pyproject, "a tool.poetry.packages entry is not a table with an include, so it is ignored", ""))
                    elif "from" in entry:
                        folder = join(entry["from"], "tool.poetry.packages from")
                        if folder is not None:
                            out.append((folder, ""))
        hatch = table(tool, "hatch", "tool.hatch")
        hbuild = table(hatch, "build", "tool.hatch.build")
        targets = table(hbuild, "targets", "tool.hatch.build.targets")
        wheel = table(targets, "wheel", "tool.hatch.build.targets.wheel")
        if wheel is not None:
            if "sources" in wheel:
                gaps.append((pyproject, "tool.hatch.build.targets.wheel.sources rewrites package names and is not read", ""))
            listed = wheel.get("packages", [])
            for item in listed if isinstance(listed, list) else []:
                if isinstance(item, str) and "/" in item.strip("/"):
                    folder = join(posixpath.dirname(item.strip("/")), "tool.hatch.build.targets.wheel.packages")
                    if folder is not None:
                        out.append((folder, ""))
        pdm = table(tool, "pdm", "tool.pdm")
        build = table(pdm, "build", "tool.pdm.build")
        if build is not None and isinstance(build.get("package-dir"), str):
            folder = join(build["package-dir"], "tool.pdm.build.package-dir")
            if folder is not None:
                out.append((folder, ""))
        return out

    # ---- names of a file

    def is_module_path(self, path: str) -> bool:
        return path in self._py

    def names_of(self, path: str) -> tuple[str, ...]:
        """Every dotted name the file has, from the most specific mount to the repository root."""
        return self._names.get(path, ())

    def stems_of(self, path: str) -> tuple[str, ...]:
        """The last segment of each dotted name of the file (what an import of it spells last); for a file
        that has no dotted name, its file name (its folder name for an `__init__.py`)."""
        names = self.names_of(path)
        if names:
            return tuple(sorted({n.rsplit(".", 1)[-1] for n in names}))
        base = posixpath.basename(path)
        if base == "__init__.py":
            folder = posixpath.basename(posixpath.dirname(path))
            return (folder,) if folder else ()
        return (base[:-3],) if base.endswith(".py") else (base,)

    # ---- resolution

    def owner_of(self, path: str) -> str:
        """The nearest service, library or repository root folder that holds the file."""
        for owner in self._owners_deepest:
            if not owner or path.startswith(owner + "/"):
                return owner
        return ""

    def _visible_owners(self, importer: str) -> frozenset[str] | None:
        nearest = self.owner_of(importer)
        if nearest not in self._visible:
            self._visible[nearest] = None if nearest == "" else frozenset({nearest, ""}) | self._library_owners
        return self._visible[nearest]

    def resolve(self, dotted: str, importer: str) -> Resolved:
        """The files an absolute dotted name means for the importer: from its service's mounts, the library
        roots and the repository root (every mount when it is under none). All of them are kept."""
        visible = self._visible_owners(importer)
        files = tuple(sorted({p for p, owner in self._by_dotted.get(dotted, ()) if visible is None or owner in visible}))
        namespace = any(visible is None or owner in visible for owner in self._ns.get(dotted, ()))
        return Resolved(files, namespace)

    def resolve_relative(self, importer: str, level: int, module: str) -> Resolved | None:
        """`from <dots><module> import ...` in the importer, by path: one dot is the importer's folder, each
        further dot one folder up. None when that goes above the repository (beyond the top level package)."""
        base = posixpath.dirname(importer)
        for _ in range(level - 1):
            if base == "":
                return None
            base = posixpath.dirname(base)
        folder = "/".join([p for p in (base, *(module.split(".") if module else [])) if p])
        files: list[str] = []
        if module:
            for cand in (folder + ".py", folder + "/__init__.py"):
                if cand in self._py:
                    files.append(cand)
        elif (cand := (base + "/__init__.py" if base else "__init__.py")) in self._py:
            files.append(cand)
        return Resolved(tuple(files), folder in self._dirs)
