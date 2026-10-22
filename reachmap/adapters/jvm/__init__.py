"""The Java and Kotlin language adapter (M16, section 5 of the brief).

Given a change, it answers which symbols changed (placed by diff line number: a member of a type is a symbol, `Class.member`), who
imports or uses the changed file (an import by package and class name, a static import, a wildcard import, a reference to a class of
the same package that needs no import, a fully qualified name in code), which importers narrowing excludes, which call sites
survive, which paths are tests, and how many times a name is defined. Every answer is Evidence pinned to a commit, or an
incompleteness record; a list that could not be established completely says so (`complete=False`) and is never presented as the
whole list.

Standard library only: the files are read through the git layer at the pinned commit and analysed with the lexer and the reader of
declarations in this package (`lexer`, `syntax`). The shell it shares with the JavaScript and Python adapters is
`reachmap.adapters.base`. See docs/briefs/M16-spring.md for the rules as built and the limits.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, replace
from typing import Any, Sequence

from reachmap import config
from reachmap.adapters import Binding, CallSite, ImportEdge, MODULE, Reach, Symbol, third_party
from reachmap.adapters.base import (
    AdapterBase, Context, Narrowing, Search, dedupe as _dedupe, ev_list as _ev_list, evidence_at as _evidence, import_claim, not_distinctive_detail,
    record_quoted,
)
from reachmap.adapters.jvm.closure import Closure, close
from reachmap.adapters.jvm.syntax import ImportRecord, Parsed, Unit, parse_java, parse_kotlin
from reachmap.errors import ModelError
from reachmap.gitlayer import Git
from reachmap.incomplete import kind_spec, record, register_kind
from reachmap.model import Evidence, Read

__all__ = ["DEFAULT_MARKERS", "JvmAdapter", "LANGUAGES", "file_info", "is_jvm_path", "symbols_for_change"]

LANGUAGES = ("java", "kotlin")
DEFAULT_MARKERS = ("src/test/", "src/integrationTest/", "src/it/")
BUILD_MARKERS = ("pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts")
_PACKAGE_LINE = re.compile(r"^\s*package\s+([A-Za-z_][\w.]*)")
DISPATCH_DEPTH = 6  # how many levels of supertypes a change is followed up

for _lang in LANGUAGES:
    config.register_language(_lang)

try:
    kind_spec("type_ambiguous")
except ModelError:
    register_kind("type_ambiguous", "Which file {subject} means was not established, because more than one file in the repository declares it", False)


def is_jvm_path(path: str) -> bool:
    return path.endswith((".java", ".kt"))


def is_kotlin_path(path: str) -> bool:
    return path.endswith(".kt")


def _package_of_path(path: str) -> str:
    """The package a folder spells: what follows `src/<set>/java/` or `src/<set>/kotlin/`, else the folder as it is."""
    parts = path.split("/")[:-1]
    for i in range(len(parts) - 2):
        if parts[i] == "src" and parts[i + 2] in ("java", "kotlin"):
            return ".".join(parts[i + 3:])
    return ".".join(parts)


def _parse_for(path: str, text: str) -> Parsed:
    return parse_kotlin(text) if is_kotlin_path(path) else parse_java(text)


# ------------------------------------------------------------------------------ what a file declares


@dataclass(frozen=True, slots=True)
class FileInfo:
    """What other files can name in a file: `package`, the top-level types (`types`), the Kotlin top-level functions and properties
    (`funcs`), and the name Java sees those under (`file_class`, `UtilKt`: empty when the file has none)."""

    package: str
    types: tuple[str, ...]
    funcs: tuple[str, ...]
    file_class: str

    @property
    def names(self) -> tuple[str, ...]:
        return self.types + self.funcs + ((self.file_class,) if self.file_class else ())


def file_info(path: str, parsed: Parsed) -> FileInfo:
    types: list[str] = []
    funcs: list[str] = []
    for d in parsed.declarations:
        for n in d.names:
            if d.is_type or d.kind == "typealias":
                if n not in types:
                    types.append(n)
            elif d.kind != "other" and n not in funcs:
                funcs.append(n)
    file_class = ""
    if funcs and is_kotlin_path(path):
        file_class = next((_string_arg(parsed, a) for a in parsed.file_annotations if a.name == "JvmName" and _string_arg(parsed, a)), "")
        if not file_class:
            stem = posixpath.splitext(posixpath.basename(path))[0]
            file_class = (stem[:1].upper() + stem[1:]) + "Kt"
    return FileInfo(parsed.package, tuple(types), tuple(funcs), file_class)


def _string_arg(parsed: Parsed, ann: Any) -> str:
    for _name, a, b in ann.args:
        if b - a == 1 and parsed.code[a].kind == "string" and parsed.code[a].text:
            return parsed.code[a].text
    return ""


def _kind_of(u: Unit) -> str:
    if u.is_type:
        return "class"
    if u.kind in ("method", "function", "constructor", "init"):
        return "function"
    return "variable"


# ------------------------------------------------------------------------------ changed symbols


def symbols_for_change(
    repo: str,
    path: str,
    before: str | None,
    after: str | None,
    added: Sequence[int],
    removed: Sequence[int],
    *,
    before_sha: str | None,
    after_sha: str | None,
    status: str = "modified",
    before_path: str | None = None,
    parsed_before: Parsed | None = None,
    parsed_after: Parsed | None = None,
) -> list[Symbol]:
    """The symbols a change touches. Each added line (its number in `after`) is placed in the declaration whose span holds it in the
    after text, each removed line (its number in `before`) in the span in the before text; a line is never searched for by its text.
    `name` is the dotted name of the member (`InvoiceController.get`, `Outer.Inner.f`), of the type for a line of its header (annotations,
    supertypes, a closing brace), or of the Kotlin top-level function or property. A changed line outside every declaration that holds
    code (a package or import line) is module level (`<module>`); a comment or blank line between members changes nothing. If either
    text does not parse every changed line is module level. `change` is judged on the top-level declaration: a new method of an
    existing class modifies the class; a declaration that is new or gone is one `added` or `removed` symbol for the whole of it."""
    pa = parsed_after if parsed_after is not None else (_parse_for(path, after) if after is not None else None)
    pb = parsed_before if parsed_before is not None else (_parse_for(before_path or path, before) if before is not None else None)
    degraded = bool((pa is not None and pa.problems) or (pb is not None and pb.problems))
    groups: dict[str, dict[str, Any]] = {}
    mod: dict[str, list[int]] = {"after": [], "before": []}
    names_after = {n for d in pa.declarations for n in d.names} if pa is not None else set()
    names_before = {n for d in pb.declarations for n in d.names} if pb is not None else set()

    def change_of(top: str, side: str) -> str:
        if status == "added":
            return "added"
        if status == "deleted":
            return "removed"
        if side == "after" and pb is not None and top not in names_before:
            return "added"
        if side == "before" and pa is not None and top not in names_after:
            return "removed"
        return "modified"

    def place(parsed: Parsed, lines: Sequence[int], side: str) -> None:
        for n in sorted(set(lines)):
            placed = parsed.placements(n)
            if not placed:
                if n in parsed.code_lines:
                    mod[side].append(n)
                continue
            for p in placed:
                top = p.decl.name
                name, kind, unit = p.name, _kind_of(p.unit), p.unit
                if change_of(top, side) != "modified":  # a declaration that is new or gone is one symbol
                    name, kind, unit = top, _kind_of(p.decl), p.decl
                g = groups.setdefault(name, {"after": [], "before": [], "decl_after": None, "decl_before": None, "top": top, "kind": kind, "changes": set()})
                g[side].append(n)
                g["changes"].add(change_of(top, side))
                if g[f"decl_{side}"] is None:
                    g[f"decl_{side}"] = p.decl

    if degraded:
        mod["after"] = sorted(set(added)) if pa is not None else []
        mod["before"] = sorted(set(removed)) if pb is not None else []
    else:
        if pa is not None:
            place(pa, added, "after")
        if pb is not None:
            place(pb, removed, "before")

    def decls_named(parsed: Parsed | None, top: str) -> list[Unit]:
        return [d for d in parsed.declarations if top in d.names] if parsed is not None else []

    out: list[Symbol] = []
    for name, g in groups.items():
        top = g["top"]
        decl_a = g["decl_after"] or next(iter(decls_named(pa, top)), None)
        decl_b = g["decl_before"] or next(iter(decls_named(pb, top)), None)
        changes = g["changes"]
        change = "added" if changes == {"added"} else "removed" if changes == {"removed"} else "modified"
        side = "after" if decl_a is not None else "before"
        decl = decl_a if side == "after" else decl_b
        assert decl is not None
        evidence: list[Evidence] = []
        if pa is not None and after_sha is not None:
            evidence += _ev_list(repo, path, after_sha, pa, g["after"])
        if pb is not None and before_sha is not None:
            evidence += _ev_list(repo, before_path or path, before_sha, pb, g["before"])
        if not evidence:
            parsed_d, sha_d, path_d = (pa, after_sha, path) if side == "after" else (pb, before_sha, before_path or path)
            if parsed_d is not None and sha_d is not None:
                evidence += _ev_list(repo, path_d, sha_d, parsed_d, [decl.start])
        exports: set[str] = set()
        for parsed, p_path in ((pa, path), (pb, before_path or path)):
            if parsed is not None and parsed.declarations:
                info = file_info(p_path, parsed)
                for d in decls_named(parsed, top):
                    exports.update(d.names)
                    if not d.is_type and info.file_class:
                        exports.add(info.file_class)
        out.append(Symbol(
            repo=repo, path=path, name=name, kind=g["kind"], start=decl.start, end=decl.end, exported=bool(exports),
            nested=(), side=side, change=change, export_names=tuple(sorted(exports)), evidence=tuple(evidence),
            added_lines=tuple(sorted(set(g["after"]))), removed_lines=tuple(sorted(set(g["before"]))),
        ))
    if mod["after"] or mod["before"]:
        side = "after" if mod["after"] else "before"
        lines = mod[side]
        evidence = []
        if pa is not None and after_sha is not None:
            evidence += _ev_list(repo, path, after_sha, pa, mod["after"])
        if pb is not None and before_sha is not None:
            evidence += _ev_list(repo, before_path or path, before_sha, pb, mod["before"])
        out.append(Symbol(
            repo=repo, path=path, name=MODULE, kind="module", start=min(lines), end=max(lines), exported=False, nested=(), side=side, change="modified",
            evidence=tuple(evidence), added_lines=tuple(sorted(set(mod["after"]))), removed_lines=tuple(sorted(set(mod["before"]))),
        ))
    out.sort(key=lambda s: (s.is_module, s.start, s.name))
    return out


# ------------------------------------------------------------------------------ the adapter


@dataclass(frozen=True, slots=True)
class SuperTarget:
    """A supertype of a type of the changed file that is declared in the repository: `path` is its file, `name` the top-level name it is
    declared under there, `unit` its declaration."""

    path: str
    name: str
    parsed: Parsed
    unit: Unit


@dataclass(frozen=True, slots=True)
class _Identity:
    """One thing an import can name: a file with its package and the names others use it by. `dispatch` marks a supertype of the
    changed file (an importer of it reaches the changed code through the supertype)."""

    path: str
    package: str
    types: tuple[str, ...]
    funcs: tuple[str, ...]
    file_class: str
    members: Any  # callable: a top-level name to its member names (for a static wildcard import)
    dispatch: bool = False

    @property
    def names(self) -> tuple[str, ...]:
        return self.types + self.funcs + ((self.file_class,) if self.file_class else ())


class JvmAdapter(AdapterBase):
    """Java / Kotlin adapter for one repository (see `AdapterBase`)."""

    languages = LANGUAGES
    DEFAULT_MARKERS = DEFAULT_MARKERS
    PROBLEMS_STOP_READING = True
    DYNAMIC_CALLS = "a reflective lookup"
    LANGUAGE = "Java and Kotlin adapter"

    def __init__(
        self,
        repo: str,
        remote: str,
        git: Git,
        *,
        test_markers: Sequence[str] | None = None,
        record_to: Any = None,
        examine_cap: int | None = None,
        hub_importers: int | None = None,
        distinct_max: int | None = None,
        framework: str | None = None,
    ) -> None:
        super().__init__(repo, remote, git, test_markers=test_markers, record_to=record_to, examine_cap=examine_cap,
                         hub_importers=hub_importers, distinct_max=distinct_max)
        self.conventions = third_party.names_for(framework)[1]  # per entry-point kind of the repository (`reachmap.adapters.third_party`)
        self._supers: dict[tuple[str, str], tuple[SuperTarget, ...]] = {}
        self._declaring: dict[tuple[str, str, str], tuple[str, ...]] = {}
        self.ambiguities: dict[str, dict[str, tuple[str, str, str]]] = {}  # by commit: (file, name as written, the files that declare it) of every name more than one file declares
        self._pkg_index: dict[str, tuple[dict[str, list[str]], str | None]] = {}
        self.gaps: dict[str, dict[str, str]] = {}  # by commit: the files of a package that was searched for a name that could not be read, with the reason
        self._dirs: dict[str, dict[str, list[str]]] = {}

    @classmethod
    def from_config(
        cls,
        repo: config.Repo,
        git: Git,
        *,
        limits: config.Limits | None = None,
        remote: str | None = None,
        record_to: Any = None,
        examine_cap: int | None = None,
    ) -> "JvmAdapter":
        """The adapter a repository's config selects: `tests.markers` and the hub and distinctiveness limits of the workspace."""
        tests = repo.tests or {}
        return cls(
            repo.name, remote or repo.remote or "", git, test_markers=tests.get("markers"), record_to=record_to, examine_cap=examine_cap,
            hub_importers=limits.hub_importers if limits else None, distinct_max=limits.distinct_max if limits else None,
            framework=(repo.entry_points or {}).get("kind"),
        )

    # ---- hooks of the shell

    def _parse_text(self, text: str) -> Parsed:
        return parse_java(text)

    def _parse(self, sha: str, path: str) -> Parsed | None:
        key = (sha, path)
        if key not in self._parsed:
            res = self._read(sha, path)
            self._parsed[key] = _parse_for(path, res.text) if res.is_found else None
        return self._parsed[key]

    def _is_code_path(self, path: str) -> bool:
        return is_jvm_path(path)

    def _build_symbols(self, fc, before, after, added, removed, pre, head, before_path, pb, pa) -> list[Symbol]:
        return symbols_for_change(
            self.repo, fc.path, before, after, added, removed, before_sha=pre, after_sha=head,
            status=fc.status, before_path=before_path, parsed_before=pb, parsed_after=pa,
        )

    def _convention(self, leaf: str) -> bool:
        return leaf in self.conventions

    def _distinct_name(self, name: str) -> str | None:
        leaf = name.rsplit(".", 1)[-1]
        return None if leaf in ("<init>", "<other>") else leaf

    def _definition_lines(self, parsed: Parsed, name: str) -> list[int]:
        return parsed.definition_lines(name)

    def info(self, sha: str, path: str) -> FileInfo | None:
        parsed = self._parse(sha, path)
        if parsed is None or parsed.problems:
            return None
        return file_info(path, parsed)

    def _distinct(self, name: str, sha: str, record_it: bool = True) -> bool:
        """Whether the repository defines `name` few enough times to link code by it (`limits.distinct_max`); a count that could not
        be made is never distinctive. A name that is too common is recorded (`not_distinctive`, non-blocking)."""
        if self.distinct_max is None:
            return True
        found = self.definitions(name, sha)
        if found.distinctive(self.distinct_max):
            return True
        if record_it and found.state == "found" and found.count is not None and found.count > self.distinct_max:
            record(self.target, "not_distinctive", name, detail=not_distinctive_detail(found, sha, self.distinct_max))
        return False

    # ---- the tree: packages and files

    def _directories(self, ctx: Context) -> dict[str, list[str]]:
        key = ctx.checkout.sha
        if key not in self._dirs:
            by_dir: dict[str, list[str]] = {}
            for f in sorted(ctx.files):
                if is_jvm_path(f):
                    by_dir.setdefault(posixpath.dirname(f), []).append(f)
            self._dirs[key] = {"dirs": by_dir}  # type: ignore[dict-item]
        return self._dirs[key]["dirs"]  # type: ignore[return-value]

    def package_index(self, sha: str) -> tuple[dict[str, list[str]], str | None]:
        """(the files by the package their own `package` line says, why that list is not whole or None). One search for the word, the
        statement read from the line it found: a Kotlin file does not have to sit in the folders of its package, and a Java file may not."""
        if sha not in self._pkg_index:
            by_pkg: dict[str, list[str]] = {}
            why: str | None = None
            res = self.search(sha, "package ")
            if res is None:
                why = "the tree could not be searched for the package statements of the files"
            elif res.state == Read.UNREADABLE:
                why = f"the search for the package statements of the files was unreadable: {res.detail}"
            else:
                if res.truncated_by is not None:
                    why = f"the search for the package statements of the files was cut after {res.examined} of {res.total} files"
                seen: set[str] = set()
                for m in res.partial_matches:
                    if m.path in seen or not is_jvm_path(m.path):
                        continue
                    hit = _PACKAGE_LINE.match(m.text)
                    if hit is not None:
                        seen.add(m.path)
                        by_pkg.setdefault(hit.group(1), []).append(m.path)
            self._pkg_index[sha] = ({k: sorted(v) for k, v in by_pkg.items()}, why)
        return self._pkg_index[sha]

    def files_in_package(self, sha: str, package: str) -> list[str]:
        """The files whose folder spells the package (`com/acme/billing` ends the folder of `com.acme.billing`) and the files whose package
        statement says it; the statement of each is checked by whoever reads it."""
        ctx = self._context(sha)
        if ctx is None:
            return []
        want = package.replace(".", "/")
        out: set[str] = set()
        for d, files in self._directories(ctx).items():
            if d == want or d.endswith("/" + want) or (not want and d == ""):
                out.update(files)
        out.update(self.package_index(sha)[0].get(package, ()))
        return sorted(out)

    def module_of(self, sha: str, path: str) -> str:
        """The folder of the nearest build file above `path` (the module of a multi-module build); empty when there is none."""
        files = self.tree(sha)
        folder = posixpath.dirname(path)
        while folder:
            if any(f"{folder}/{m}" in files for m in BUILD_MARKERS):
                return folder
            folder = posixpath.dirname(folder)
        return ""

    @staticmethod
    def source_root(path: str) -> str:
        """The folder above the packages: what comes before `src/<set>/java` or `src/<set>/kotlin`, else the folder as it is."""
        parts = path.split("/")[:-1]
        for i in range(len(parts) - 2):
            if parts[i] == "src" and parts[i + 2] in ("java", "kotlin"):
                return "/".join(parts[:i + 3])
        return "/".join(parts)

    def prefer(self, sha: str, path: str, files: Sequence[str]) -> list[str]:
        """Of the files that declare one name, those `path` can mean: itself, else those of its module, else those of its source root.
        More than one left means nothing tells which is meant."""
        left = list(files)
        if len(left) <= 1:
            return left
        if path in left:
            return [path]
        for same in (lambda f: self.module_of(sha, f) == self.module_of(sha, path), lambda f: self.source_root(f) == self.source_root(path)):
            narrowed = [f for f in left if same(f)]
            if narrowed:
                left = narrowed
            if len(left) == 1:
                break
        return left

    def ambiguous(self, sha: str, path: str, written: str, files: Sequence[str]) -> None:
        """Said, not guessed: `written` in `path` could mean any of `files`."""
        self.ambiguities.setdefault(sha, {}).setdefault(f"{path}: {written}", (path, written, ", ".join(files)))
        record_quoted(self.target, "type_ambiguous", written, "more than one file declares it and nothing tells which one the code means", f"{path}: {', '.join(files)}", once=True)

    def declaring_files(self, sha: str, package: str, name: str) -> tuple[str, ...]:
        """The files of the package that declare the top-level name (a type, or a Kotlin top-level function or property, or the file
        class of a Kotlin file), checked against the package statement."""
        key = (sha, package, name)
        if key not in self._declaring:
            found: list[str] = []
            for f in self.files_in_package(sha, package):
                parsed = self._parse(sha, f)
                if parsed is None or parsed.problems:  # it may declare the name: said, never taken for a file that does not
                    self.gaps.setdefault(sha, {})[f] = "it could not be read" if parsed is None else f"it did not parse cleanly ({parsed.problems[0]})"
                    continue
                if parsed.package != package:
                    continue
                if name in file_info(f, parsed).names:
                    found.append(f)
            self._declaring[key] = tuple(found)
        return self._declaring[key]

    def resolve_fqn(self, sha: str, fqn: str) -> tuple[tuple[str, ...], str, str, tuple[str, ...]]:
        """The files that declare what a dotted name means: (files, package, top-level name, the rest of the name: a nested type or a
        member). The longest package is tried first; empty files when nothing in the repository declares it."""
        parts = fqn.split(".")
        for k in range(len(parts) - 1, 0, -1):
            package, name = ".".join(parts[:k]), parts[k]
            files = self.declaring_files(sha, package, name)
            if files:
                return files, package, name, tuple(parts[k + 1:])
        return (), "", "", ()

    def resolve_module(self, importer: str, sha: str, module: str, level: int = 0) -> tuple[str, ...]:
        """The files a dotted name means for `importer` at `sha` (the same-package files of a simple name too)."""
        files, _p, _n, _rest = self.resolve_fqn(sha, module)
        return tuple(files)

    # ---- supertypes

    def resolve_type(self, sha: str, path: str, parsed: Parsed, written: str) -> tuple[str, str] | None:
        """The (file, top-level name) a type written in `path` means, when the repository declares it: through a single-type import, the
        package of the file, a wildcard import or a qualified name."""
        simple = written.rsplit(".", 1)[-1]
        fqns: list[str] = []
        if "." in written:
            fqns.append(written)
        for r in parsed.imports:
            if not r.wildcard and not r.static and r.local == simple:
                fqns.append(r.module)
        fqns.append(f"{parsed.package}.{simple}" if parsed.package else simple)
        for r in parsed.imports:
            if r.kind == "wildcard":
                fqns.append(f"{r.module}.{simple}")
        for fqn in dict.fromkeys(fqns):
            if "." not in fqn:
                files = self.declaring_files(sha, "", fqn)
                top = fqn
            else:
                files, _pkg, top, rest = self.resolve_fqn(sha, fqn)
                if rest:
                    continue
            if files:
                files = tuple(self.prefer(sha, path, files))
                if len(files) > 1:
                    self.ambiguous(sha, path, written, files)
                    return None
                return files[0], top
        return None

    def supertypes(self, sha: str, path: str) -> tuple[SuperTarget, ...]:
        """The supertypes of the types of a file that the repository declares, and theirs in turn (up to `DISPATCH_DEPTH` levels)."""
        key = (sha, path)
        if key in self._supers:
            return self._supers[key]
        self._supers[key] = ()
        out: list[SuperTarget] = []
        seen: set[tuple[str, str]] = {(path, "")}
        queue = [(path, 0)]
        while queue:
            cur, depth = queue.pop(0)
            parsed = self._parse(sha, cur)
            if parsed is None or parsed.problems or depth >= DISPATCH_DEPTH:
                continue
            for u in parsed.units():
                if not u.is_type:
                    continue
                for ref in u.supertypes:
                    hit = self.resolve_type(sha, cur, parsed, ref.qualified)
                    if hit is None or hit[0] == path:
                        continue
                    f, top = hit
                    if (f, top) in seen:
                        continue
                    seen.add((f, top))
                    sp = self._parse(sha, f)
                    if sp is None or sp.problems:
                        continue
                    unit = next((d for d in sp.declarations if d.name == top), None)
                    if unit is None:
                        continue
                    out.append(SuperTarget(f, top, sp, unit))
                    queue.append((f, depth + 1))
        self._supers[key] = tuple(out)
        return self._supers[key]

    # ---- importers

    def _identities(self, sha: str, path: str) -> list[_Identity] | None:
        """What an import of the file can name (and what an import of one of its supertypes names). A file that does not parse is named by
        its path alone (its file name and the package its folder spells): the importers found that way are a list that is not whole, and
        the search says so (`_gather`)."""
        parsed = self._parse(sha, path)
        if parsed is None:
            return None
        if parsed.problems:
            stem = posixpath.splitext(posixpath.basename(path))[0]
            return [_Identity(path, _package_of_path(path), (stem,), (), "", lambda n: ())]
        info = file_info(path, parsed)

        def members_of(parsed: Parsed, info: FileInfo, name: str) -> tuple[str, ...]:
            if name == info.file_class:
                return info.funcs
            d = next((d for d in parsed.declarations if d.name == name), None)
            if d is None:
                return ()
            return tuple(dict.fromkeys(n for u in d.walk() if u is not d and u.kind not in ("constructor", "init", "other", "companion") for n in u.names))

        out = [_Identity(path, info.package, info.types, info.funcs, info.file_class, lambda n, p=parsed, i=info: members_of(p, i, n))]
        for st in self.supertypes(sha, path):
            si = file_info(st.path, st.parsed)
            out.append(_Identity(st.path, si.package, (st.name,), (), "", lambda n, p=st.parsed, i=si: members_of(p, i, n), dispatch=True))
        return out

    def _patterns(self, ctx: Context, path: str) -> list[str]:
        ids = self._identities(ctx.checkout.sha, path)
        if ids is None:
            return []
        names: list[str] = []
        for ident in ids:
            for n in ident.names:
                if n not in names:
                    names.append(n)
        return names

    def _gather(self, ctx: Context, s: Search) -> None:
        parsed = self._parse(s.sha, s.path)
        if parsed is None or parsed.problems:
            why = "it could not be read" if parsed is None else f"it did not parse cleanly ({parsed.problems[0]})"
            s.incomplete(f"{s.path}: {why}, so what an import of it names is known only by its path")
            record_quoted(self.target, "parse_degraded", s.path, "what an import of the file names was taken from its path, so its importers may be missed",
                          parsed.problems[0] if parsed is not None else "", blocks_selection=True, once=True)
        names = self._patterns(ctx, s.path)
        if not names:
            return  # a file that declares nothing has no importers
        for name in names:
            self._grep_for(s, name, is_jvm_path)

    def _scan_candidate(self, ctx: Context, s: Search, cand: str, parsed: Parsed) -> None:
        ids = self._identities(s.sha, s.path)
        if ids is None:
            return
        is_test = self.is_test_path(cand)
        ambiguous: set[str] = set()
        for ident in ids:
            if ident.path == cand:
                continue
            if ident.dispatch and is_test:
                continue  # a test of an interface is not a test of the class that implements it
            self._scan_identity(s, cand, parsed, ident, ambiguous)
        for fqn in sorted(ambiguous):
            files, _p, _n, _rest = self.resolve_fqn(s.sha, fqn)
            if len(files) > 1 and s.path in files:
                record_quoted(self.target, "type_ambiguous", fqn, "more than one file declares it; each is kept as the one an importer means", f"{cand}: {', '.join(files)}")

    def _scan_identity(self, s: Search, cand: str, parsed: Parsed, ident: _Identity, ambiguous: set[str]) -> None:
        sha = s.sha
        pkg = ident.package
        by_local = {r.local: r for r in parsed.imports if not r.wildcard}
        declared = {n for d in parsed.declarations for n in d.names}
        named = {n: ("type" if n in ident.types else "func" if n in ident.funcs else "fileclass") for n in ident.names}
        claim_target = ident.path

        def add(line: int, specifier: str, name: str, kind: str, bindings: tuple[Binding, ...]) -> None:
            ev = _evidence(self.repo, cand, sha, parsed, line)
            if ev is None:
                return
            s.edges.append(ImportEdge(
                importer_path=cand, specifier=specifier, names=(name,), evidence=replace(ev, claim=import_claim(claim_target, (name,))),
                kind=kind, type_only=False, bindings=bindings, target=s.path,
            ))

        def first_use_line(local: str, member: bool) -> int | None:
            uses = parsed.uses(local, member=member)
            return uses[0].line if uses else None

        for r in parsed.imports:
            fqn = r.module
            if not r.wildcard:
                for n in named:
                    base = f"{pkg}.{n}" if pkg else n
                    if fqn == base or fqn.startswith(base + "."):
                        rest = fqn[len(base) + 1:] if fqn != base else ""
                        imported = n + ("." + rest if rest else "")
                        add(r.line, r.specifier, n, "static" if r.static else "import", (Binding(imported, r.local, False, 0),))
                        ambiguous.add(base)
                continue
            if not r.static and fqn == pkg:  # `import pkg.*`: the names of the package the importer uses
                for n, kind in named.items():
                    local_shadow = n in by_local and by_local[n].module != (f"{pkg}.{n}" if pkg else n)
                    if n in declared or local_shadow:
                        continue
                    line = first_use_line(n, kind == "func")
                    if line is None:
                        continue
                    if kind == "func" and not self._distinct(n, sha):
                        continue  # a function is found by its name alone: a name defined many times is not followed
                    add(line, r.specifier, n, "import", (Binding(n, n, False, 0),))
                continue
            # `import static pkg.Type.*` (Java), `import pkg.Object.*` (Kotlin): the members of a type, used on their own
            for n in named:
                base = f"{pkg}.{n}" if pkg else n
                if fqn != base:
                    continue
                for m in ident.members(n):
                    if m in declared:
                        continue
                    line = first_use_line(m, parsed.kotlin)
                    if line is None or not self._distinct(m, sha):
                        continue
                    add(line, r.specifier, n, "static", (Binding(f"{n}.{m}", m, False, 0),))
        # the same package needs no import
        if parsed.package == pkg and cand != ident.path:
            for n, kind in named.items():
                if n in declared:
                    continue
                shadow = by_local.get(n)
                if shadow is not None and shadow.module != (f"{pkg}.{n}" if pkg else n):
                    continue
                line = first_use_line(n, kind == "func")
                if line is None:
                    continue
                if kind == "func" and (not self._distinct(n, sha) or any(n in u.names for u in parsed.units())):
                    continue
                add(line, f"{pkg}.{n}" if pkg else n, n, "same_package", (Binding(n, n, False, 0),))
                ambiguous.add(f"{pkg}.{n}" if pkg else n)
        # a fully qualified name in code
        for text, line, _tok in parsed.chains():
            for n in named:
                base = f"{pkg}.{n}" if pkg else n
                if text == base or text.startswith(base + "."):
                    add(line, text, n, "qualified", ())

    # ---- narrowing

    def _closure_for(self, path: str, head: str, pre: str | None, symbols: Sequence[Symbol], status: str) -> tuple[Closure, list[Parsed], bool] | None:
        """The closure of the changed lines inside their file, over the after and the before text, and whether the whole file is reached
        (a changed line outside every declaration that is not a single-type import: the package, a wildcard import, a file annotation);
        None when a text is not available. A changed import statement reaches the units that spell the name it binds."""
        pa = self._parse(head, path)
        pb = self._parse(pre, path) if pre is not None and status != "added" else None
        if pa is None or (pre is not None and status != "added" and pb is None):
            return None
        versions = [pa] + ([pb] if pb is not None else [])
        seeds: list[tuple[int, Unit, bool]] = []
        bound: set[str] = set()
        module_whole = False
        for s in symbols:
            for vi, lines in ((0, s.added_lines), (1, s.removed_lines)):
                if vi >= len(versions):
                    continue
                p = versions[vi]
                for n in lines:
                    if s.is_module:
                        rec = next((r for r in p.imports if r.line == n), None)
                        if rec is not None and not rec.wildcard and not rec.static:
                            bound.add(rec.local)
                        elif rec is not None and rec.static and not rec.wildcard:
                            bound.add(rec.local)
                        else:
                            module_whole = True
                        continue
                    for pl in p.placements(n):
                        seeds.append((vi, pl.unit, pl.whole))
        return close(versions, seeds, lambda n, rec=True: self._distinct(n, head, rec), bound), versions, module_whole

    def _narrowing(self, fc: Any, symbols: Sequence[Symbol], head: str, pre: str | None) -> Narrowing | None:
        """Name narrowing, or None when it does not apply (everything is then affected).

        It applies only with a pre-change version, for a file that is neither deleted nor renamed, when no change is module level, and when
        both versions parse and use no reflection. The names compared with an importer's are those of `Closure.exports`: the types and
        members the change reaches inside the file, in the before and the after text, and the names of the supertypes they are also
        reached by (`dispatch`). A Kotlin top-level property that is initialised by a call to something reached runs for every importer
        of the file: narrowing stops."""
        if pre is None or fc.status in ("deleted", "renamed") or not symbols:
            return None
        got = self._closure_for(fc.path, head, pre, symbols, fc.status)
        if got is None:
            return None
        closure, versions, module_whole = got
        if any(p.problems or p.uses_dynamic_access() for p in versions) or module_whole or closure.import_time:
            return None
        info = file_info(fc.path, versions[0])
        names = set(closure.exports(info.file_class))
        names |= self._dispatch_names(head, fc.path, closure, versions)
        if not names:
            return None
        changed_keys = {(vi, id(pl.unit)) for s in symbols if not s.is_module for vi, lines in ((0, s.added_lines), (1, s.removed_lines)) if vi < len(versions) for n in lines for pl in versions[vi].placements(n)}
        reach: list[Evidence] = []
        for vi, sha in ((0, head), (1, pre)):
            if vi >= len(versions):
                continue
            p = versions[vi]
            for u in p.units():
                if (vi, id(u)) in closure.reached and (vi, id(u)) not in changed_keys and u.parent is not None:
                    ev = _evidence(self.repo, fc.path, sha, p, u.start)
                    if ev is not None and not any((e.path, e.line, e.detail) == (ev.path, ev.line, ev.detail) for e in reach):
                        reach.append(ev)
        return Narrowing(names, reach[:12])

    def _dispatch_names(self, sha: str, path: str, closure: Closure, versions: Sequence[Parsed]) -> set[str]:
        """The names of the supertypes (declared in the repository) of the reached types: a caller of the supertype reaches the changed
        code through it. A type reached as a whole is reached by its supertype's name; a member by `Super.member` when the supertype (or one
        above it) declares a member of that name."""
        out: set[str] = set()
        supers = self.supertypes(sha, path)
        if not supers:
            return out
        by_name: dict[str, list[SuperTarget]] = {}
        for st in supers:
            by_name.setdefault(st.name, []).append(st)
        for vi, p in enumerate(versions):
            for u in p.units():
                if not u.is_type or not u.supertypes:
                    continue
                whole = (vi, id(u)) in closure.whole
                leafs = {n for c in u.children if (vi, id(c)) in closure.reached and c.kind not in ("constructor", "init", "other") for n in c.names}
                if not whole and not leafs:
                    continue
                for st in self.supers_of(sha, path, p, u):
                    if whole:
                        out.add(st.name)
                        continue
                    declared = {n for c in st.unit.walk() if c is not st.unit for n in c.names}
                    for leaf in leafs & declared:
                        out.add(f"{st.name}.{leaf}")
        return out

    def supers_of(self, sha: str, path: str, parsed: Parsed, unit: Unit) -> list[SuperTarget]:
        """The supertypes of one type: its own supertypes and theirs, from the file's table."""
        wanted: list[SuperTarget] = []
        frontier = [(path, parsed, unit)]
        seen: set[tuple[str, str]] = set()
        while frontier:
            cur_path, cur_parsed, cur = frontier.pop()
            for ref in cur.supertypes:
                hit = self.resolve_type(sha, cur_path, cur_parsed, ref.qualified)
                if hit is None or hit in seen:
                    continue
                seen.add(hit)
                f, top = hit
                sp = self._parse(sha, f)
                if sp is None or sp.problems:
                    continue
                su = next((d for d in sp.declarations if d.name == top), None)
                if su is None or f == path:
                    continue
                st = SuperTarget(f, top, sp, su)
                wanted.append(st)
                frontier.append((f, sp, su))
        return wanted

    def _survives(self, importer: str, edges: Sequence[ImportEdge], sha: str, names: set[str]) -> bool:
        """Whether any import of the importer can reach a name of the narrowing set. A top-level name in the set (a type reached as a
        whole, a Kotlin function) is reached by every import of it; `Type.member` only by an importer that spells the member (or by
        any importer when a member is too common to be linked by its name)."""
        parsed = self._parse(sha, importer)
        for e in edges:
            top = e.names[0] if e.names else None
            if top is None or top in names:
                return True
            members = {n.split(".", 1)[1] for n in names if n.startswith(top + ".")}
            if not members:
                continue
            if parsed is None:
                return True
            binding = e.bindings[0] if e.bindings else None
            if binding is not None and e.kind == "static":  # one member of a type: reached when it is one of the reached members
                return binding.imported.split(".", 1)[1].split(".")[0] in members
            spelled = parsed.spelled_all()
            for m in sorted(members):
                if not self._distinct(m, sha) or m in spelled:
                    return True
        return False

    # ---- uses

    def _call_sites(self, importer: str, edges: Sequence[ImportEdge], sha: str, changed_names: set[str] | None) -> list[CallSite]:
        """The uses in `importer` of what it takes from the changed file: of the changed names only when narrowing applies (a member is
        looked for by its name, through any receiver, unless it is too common: then every use of its type counts), else of everything
        it takes. An importer with no use gets one site at its import line (`form` is `import`)."""
        parsed = self._parse(sha, importer)
        sites: list[CallSite] = []
        if parsed is not None:
            seen: set[tuple[int, str, str]] = set()

            def add(symbol: str, line: int, form: str, args: tuple[str, ...], edge: ImportEdge) -> None:
                key = (line, symbol, form)
                if key in seen:
                    return
                ev = _evidence(self.repo, importer, sha, parsed, line)
                if ev is None:
                    return
                seen.add(key)
                sites.append(CallSite(repo=self.repo, path=importer, line=line, symbol=symbol, evidence=ev, import_evidence=edge.evidence, args=args,
                                      caller_symbol=parsed.enclosing(line), form=form))

            for edge in edges:
                top = edge.names[0] if edge.names else None
                if top is None:
                    continue
                whole = changed_names is None or top in changed_names
                members = {n.split(".", 1)[1] for n in (changed_names or ()) if n.startswith(top + ".")}
                if not whole and not members:
                    continue
                if edge.kind == "qualified":
                    for text, line, tok in parsed.chains():
                        if text != edge.specifier:
                            continue
                        end = tok + 2 * text.count(".") + 1
                        add(top, line, "call" if end < len(parsed.code) and parsed.code[end].value == "(" else "reference", (), edge)
                    continue
                for b in edge.bindings:
                    if "." in b.imported:  # one member of a type (a static import), or a nested type
                        member = b.imported.split(".", 1)[1].split(".")[0]
                        if not whole and member not in members:
                            continue
                        for u in parsed.uses(b.local):
                            add(b.imported, u.line, u.form, parsed.args_of(u), edge)
                        continue
                    as_member = parsed.kotlin and b.local[:1].islower()  # a Kotlin extension function is called as a member of its receiver
                    if whole:
                        for u in parsed.uses(b.local, member=as_member):
                            add(b.imported, u.line, u.form, parsed.args_of(u), edge)
                        continue
                    generic = [m for m in sorted(members) if not self._distinct(m, sha)]
                    if generic:  # too common to link code by its name: every use of the type counts
                        for u in parsed.uses(b.local):
                            add(b.imported, u.line, u.form, parsed.args_of(u), edge)
                    for m in sorted(members - set(generic)):
                        for u in parsed.uses(m, member=True):
                            add(f"{top}.{m}", u.line, u.form, parsed.args_of(u), edge)
        if not sites:
            edge = edges[0]
            first = edge.bindings[0] if edge.bindings else None
            sites.append(CallSite(
                repo=self.repo, path=importer, line=edge.evidence.line, symbol=MODULE if first is None else first.imported, evidence=edge.evidence,
                import_evidence=edge.evidence, args=(), caller_symbol=MODULE, form="import",
            ))
        sites.sort(key=lambda c: (c.line, c.symbol))
        return sites

    # ---- reach (the walk's hop)

    def _reach(self, importer: str, edges: Sequence[ImportEdge], sha: str, names: frozenset[str] | None, sites: Sequence[CallSite]) -> Reach:
        """What the use of `names` touches inside `importer`: the units that hold a use are reached, and every unit that spells a reached
        member (or a type reached as a whole), to a fixed point. The names the next hop is narrowed with are `Closure.exports`, with the
        supertype names of the reached types. The whole module is reached (None) when an import is never used, the file does not parse or
        reaches names by reflection, or something that runs when the file loads is reached."""
        parsed = self._parse(sha, importer)
        if parsed is None:
            return Reach(frozenset(), None, (), False, ("the file could not be read",), True)
        if parsed.problems:
            return Reach(frozenset(), None, (), False, (f"the file did not parse ({parsed.problems[0]})",), True)
        whole = parsed.uses_dynamic_access()
        lines: list[int] = []
        seeds: list[tuple[int, Unit, bool]] = []
        locals_ = {b.local for e in edges for b in e.bindings}
        for cs in sites:
            if cs.form == "import":
                whole = True
                continue
            lines.append(cs.line)
            names = locals_ | {cs.symbol.rsplit(".", 1)[-1]}
            toks = [u.tok for n in names for u in parsed.uses(n, member=True) if u.line == cs.line and u.tok >= 0]
            for pl in parsed.placements(cs.line):
                u = pl.unit
                if u.kind == "constructor" and toks and all(u.name_tok + 1 < t <= parsed.match.get(u.name_tok + 1, -1) for t in toks):
                    continue  # the parameter list of a constructor is where something is injected: the field that keeps it carries the change
                seeds.append((0, u, pl.whole))
        return self._finish_reach(parsed, importer, sha, seeds, whole, lines)

    def _finish_reach(self, parsed: Parsed, path: str, sha: str, seeds: list[tuple[int, Unit, bool]], whole: bool, lines: list[int], count: bool = True) -> Reach:
        closure = close([parsed], seeds, (lambda n, rec=True: self._distinct(n, sha, rec)) if count else (lambda n, rec=True: True))
        if closure.import_time:
            whole = True
        info = file_info(path, parsed)
        out = set(closure.exports(info.file_class)) | self._dispatch_names(sha, path, closure, [parsed])
        names = None if whole or not out else frozenset(out)
        return Reach(closure.locals(0), names, tuple(sorted(set(lines))), True, (), whole)

    def _own_reach(self, path: str, sha: str, seeds: set[str]) -> Reach:
        parsed = self._parse(sha, path)
        if parsed is None:
            return Reach(frozenset(seeds), None, (), False, ("the file could not be read",), True)
        if parsed.problems:
            return Reach(frozenset(seeds), None, (), False, (f"the file did not parse ({parsed.problems[0]})",), True)
        units = [(0, d, True) for d in parsed.declarations if set(d.names) & set(seeds)]
        # the whole declaration is the seed: no name of it is linked by name here, so none is counted (and none is recorded as too common)
        return self._finish_reach(parsed, path, sha, units, parsed.uses_dynamic_access(), [], count=False)

    def reach_of_change(self, path: str, sha: str, pre: str | None, symbols: Sequence[Symbol], status: str = "modified") -> Reach:
        """What a change inside `path` reaches inside the file itself, member by member (the entry-point adapter asks for it: a change to
        one handler reaches that handler, not the others)."""
        got = self._closure_for(path, sha, pre, symbols, status)
        if got is None:
            return Reach(frozenset(), None, (), False, ("the file could not be read",), True)
        closure, versions, module_whole = got
        bad = [p for p in versions if p.problems]
        if bad:
            return Reach(closure.locals(0), None, (), False, (f"the file did not parse ({bad[0].problems[0]})",), True)
        whole = any(p.uses_dynamic_access() for p in versions) or closure.import_time or module_whole
        info = file_info(path, versions[0])
        out = set(closure.exports(info.file_class)) | self._dispatch_names(sha, path, closure, versions)
        lines = sorted({n for s in symbols for n in s.added_lines})
        return Reach(closure.locals(0), None if whole or not out else frozenset(out), tuple(lines), True, (), whole)
