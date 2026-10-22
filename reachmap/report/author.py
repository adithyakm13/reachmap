"""The "For the author" view model: what the person who made the change should look at, derived from the same report.

Other repositories the change reached (and by which kind of edge), the files beyond the diff that the report cites (the places
the change reaches, not the places it touched; files that were excluded are not reached and are not listed), the other
branches from the branch matrix (those without the change first) and the unit tests the change reaches, each repository's
command as the configuration gave it (`{paths}` already replaced by shell-quoted paths) or, without one, the quoted paths.

Takes the report JSON only. Nothing here does I/O.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Any, Mapping

from reachmap.report.blocks import Content, content
from reachmap.report.read import dct as _d
from reachmap.report.read import lst as _l
from reachmap.report.read import text as _s

__all__ = ["AuthorView", "BranchView", "FileView", "RepoView", "UnitTestView", "build"]


@dataclass(frozen=True, slots=True)
class RepoView:
    repo: Content
    edges: tuple[str, ...]  # the kinds of edge that led there: a fixed word (http, package, contract, event) or, for a kind the view does not know, content


@dataclass(frozen=True, slots=True)
class FileView:
    repo: Content
    path: Content


@dataclass(frozen=True, slots=True)
class BranchView:
    branch: Content
    state: Content
    carries: bool


@dataclass(frozen=True, slots=True)
class UnitTestView:
    """The unit tests of one repository. `command` is the configured command whole (None when there is none); `quoted` are the
    paths, shell-quoted, one per test; `text` is what to copy: the command, or the quoted paths on one line."""

    repo: Content
    command: Content | None
    quoted: tuple[str, ...]
    text: Content


@dataclass(frozen=True, slots=True)
class AuthorView:
    repos: tuple[RepoView, ...]
    files: tuple[FileView, ...]
    branches: tuple[BranchView, ...]
    tests: tuple[UnitTestView, ...]


KNOWN_EDGES = ("http", "package", "contract", "event")  # the kinds of edge the tool follows: fixed words, the only edge kinds it may say itself
WALK = "walk"


def _edge(kind: Any) -> tuple[bool, str]:
    """An edge kind from a report as (is content, text): a known one is a fixed word, anything else is content (never the tool's own text)."""
    k = _s(kind)
    return (k not in KNOWN_EDGES, k)


def _edges(kinds: set[tuple[bool, str]]) -> list[Any]:
    return [content(k) if is_content else k for is_content, k in sorted(kinds, key=lambda x: (x[1], x[0]))]


def _repos(report: Mapping[str, Any], origin: str) -> tuple[RepoView, ...]:
    p = _d((_l(report.get("propagations")) or [{}])[0])
    found: dict[str, set[tuple[bool, str]]] = {}

    def add(repo: Any, kind: tuple[bool, str]) -> None:
        r = _s(repo)
        if r and r != origin:
            found.setdefault(r, set()).add(kind)

    for h in _l(p.get("hops")):
        add(_d(h).get("to_repo"), _edge(_d(h).get("kind")))
    for ps in _l(p.get("passes")):
        add(_d(ps).get("repo"), (False, WALK))
    for c in _l(p.get("consumers")):
        add(_d(c).get("repo"), (False, "package"))
    for e in _l(p.get("http_edges")):
        e = _d(e)
        add(e.get("client_repo"), (False, "http"))
        add(e.get("server_repo"), (False, "http"))
    for r in _l(p.get("contract_reads")) + _l(p.get("contract_candidates")):
        add(_d(r).get("repo"), (False, "contract"))
    for a in _l(report.get("areas")):
        add(_d(a).get("repo"), (False, WALK))
    # a pass or an area only says the walk was in the repository: keep it only when no edge kind says more
    return tuple(RepoView(content(r), tuple(_edges({k for k in kinds if k != (False, WALK)}) or [WALK])) for r, kinds in sorted(found.items()))


def _files(report: Mapping[str, Any], origin: str) -> tuple[FileView, ...]:
    touched = {(origin, _s(_d(f).get("path"))) for f in _l(_d(report.get("change")).get("files"))}
    touched |= {(origin, _s(_d(f).get("old_path"))) for f in _l(_d(report.get("change")).get("files")) if _d(f).get("old_path")}
    seen: set[tuple[str, str]] = set()

    def cite(ev: Any) -> None:
        ev = _d(ev)
        key = (_s(ev.get("repo")), _s(ev.get("path")))
        if key[0] and key[1] and key not in touched:
            seen.add(key)

    for a in _l(report.get("areas")):
        a = _d(a)
        cite(a.get("route_line"))
        for chain in _l(a.get("chains")):
            for ev in _l(chain):
                cite(ev)
    for f in _l(report.get("findings")):
        f = _d(f)
        if f.get("is_exclusion") is True:
            continue
        for ev in _l(f.get("evidence")):
            cite(ev)
    return tuple(FileView(content(r), content(p)) for r, p in sorted(seen))


def _branches(report: Mapping[str, Any]) -> tuple[BranchView, ...]:
    rows = [BranchView(content(_d(b).get("branch")), content(_d(b).get("state")), _s(_d(b).get("state")) == "present") for b in _l(report.get("branches"))]
    return tuple(sorted(rows, key=lambda b: (b.carries, str(b.branch))))  # without the change first


def _tests(report: Mapping[str, Any]) -> tuple[UnitTestView, ...]:
    p = _d((_l(report.get("propagations")) or [{}])[0])
    out = []
    for c in _l(p.get("unit_test_commands")):
        c = _d(c)
        paths = [_s(x) for x in _l(c.get("paths"))]
        quoted = tuple(shlex.quote(x) for x in paths)
        command = c.get("command")
        has = isinstance(command, str) and command.strip() != ""
        out.append(UnitTestView(content(c.get("repo")), content(command) if has else None, quoted, content(command) if has else content(" ".join(quoted))))
    return tuple(sorted(out, key=lambda t: str(t.repo)))


def build(report: Mapping[str, Any]) -> AuthorView:
    origin = _s(_d(report.get("change")).get("repo"))
    return AuthorView(_repos(report, origin), _files(report, origin), _branches(report), _tests(report))
