"""What the Django and the FastAPI entry-point adapters share: the Python route tables read from syntax trees.

Both adapters read a repository at one commit the same way. A `git grep` finds the files that may declare routes (a cut or failed search makes the
table incomplete, never empty), each file is parsed with `ast` (a file that cannot be read or does not parse claims nothing and is a blocking
`parse_degraded`), names are followed through imports and module constants, a route is composed from its own declaration and the mounts above it, and
its evidence is the line of each place that says it. What a route table does with a hole (a file or a mount that was not read) is one rule for both:
`guard_route_changes`. What differs is the framework: how a route is declared, how a mount is read, what a path looks like. That stays in the adapters.

- Syntax helpers: `statements`, `callee`, `kw`, `names_in`, `literal_strings`, `nearest_folder`, `FileFacts`.
- `TableBase`: the state and the reads of a route table (search, facts per file, module resolution, constants evaluated to a path string).
- `route_evidence`: the evidence of a composed route.
- `Gathered` and `Shared`: the routes the chains of one call reach, and the part of an adapter that does not depend on the framework (the table per
  commit, `routes`, `routes_changed`, the areas of a change).
"""

from __future__ import annotations

import ast
import posixpath
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Container, Iterable, Iterator, Sequence

from reachmap.adapters import Reached, Route, diff_routes, guard_route_changes
from reachmap.adapters.base import api_area, citable, evidence_at, record_quoted
from reachmap.incomplete import record
from reachmap.model import Evidence, Read

__all__ = [
    "DEPTH", "FileFacts", "Gathered", "Shared", "TableBase", "callee", "kw", "literal_strings", "names_in", "nearest_folder", "route_evidence",
    "statements",
]

DEPTH = 6  # how far a name is followed through imports, re-exports and constants
COMPOUND = (ast.If, ast.Try, getattr(ast, "TryStar", ast.Try), ast.With, ast.AsyncWith, ast.For, ast.AsyncFor, ast.While, getattr(ast, "Match", ast.If))

Ref = tuple[str, str]  # (file, variable of a router or an app, or `urlpatterns`)


# ------------------------------------------------------------------------------ syntax


def callee(func: ast.expr) -> str | None:
    """The last name of what a call calls: `path` of `path(...)` and of `urls.path(...)`."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def kw(call: ast.Call, name: str) -> ast.expr | None:
    """The value of the keyword argument `name` of a call (None when it is not given)."""
    return next((k.value for k in call.keywords if k.arg == name), None)


def names_in(*nodes: ast.AST | None) -> frozenset[str]:
    """Every plain name spelled in the nodes."""
    return frozenset(n.id for node in nodes if node is not None for n in ast.walk(node) if isinstance(n, ast.Name))


def literal_strings(node: ast.expr | None) -> list[str] | None:
    """The values of a list, tuple or set of string literals (`["GET", "post"]`), in order; None when the node is anything else or when
    one element is not a string literal (the caller then does not know the whole)."""
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)) and all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.elts):
        return [e.value for e in node.elts]  # type: ignore[union-attr]
    return None


def statements(body: list[ast.stmt]) -> Iterator[ast.stmt]:
    """The statements at module level, through `if`, `try`, `with`, `for`, `while` and `match` blocks (code there runs when the module loads)."""
    for stmt in body:
        yield stmt
        if isinstance(stmt, COMPOUND):
            for attr in ("body", "orelse", "finalbody"):
                yield from statements(getattr(stmt, attr, None) or [])
            for handler in getattr(stmt, "handlers", ()):
                yield from statements(handler.body)
            for case in getattr(stmt, "cases", ()):
                yield from statements(case.body)


def nearest_folder(files: Container[str], path: str, marker: str) -> str | None:
    """The name of the nearest folder above `path` (the root is not one) that holds a file called `marker`, or None."""
    folder = posixpath.dirname(path)
    while folder:
        if f"{folder}/{marker}" in files:
            return posixpath.basename(folder)
        folder = posixpath.dirname(folder)
    return None


@dataclass(slots=True)
class FileFacts:
    """What every route table keeps of a parsed file: the path, the parse, the names its imports bind and its constants. An adapter's own facts add to it."""

    path: str
    parsed: Any
    from_binding: dict[str, tuple[str, int, str]] = field(default_factory=dict)  # local name to (module, level, name)
    module_binding: dict[str, tuple[str, int]] = field(default_factory=dict)  # local name to (module, level)
    consts: dict[str, ast.expr | None] = field(default_factory=dict)  # None: assigned more than once

    def bind_imports(self) -> list[tuple[str, int]]:
        """Fill `from_binding` and `module_binding` from the imports of the file; returns the star imports as (module, level), which bind no names."""
        stars: list[tuple[str, int]] = []
        for rec in self.parsed.imports:
            if rec.kind == "from":
                for n in rec.names:
                    self.from_binding[n.asname or n.name] = (rec.module, rec.level, n.name)
            elif rec.kind == "star":
                stars.append((rec.module, rec.level))
            elif rec.kind == "import" and rec.names:
                nm = rec.names[0]
                if nm.asname:
                    self.module_binding[nm.asname] = (nm.name, 0)
                else:
                    first = nm.name.split(".")[0]
                    self.module_binding[first] = (first, 0)
        return stars


# ------------------------------------------------------------------------------ the table


class TableBase:
    """The state and the reads a route table starts from: the files found by one search and read once per commit, what is incomplete, how a module
    is resolved and how a prefix made of literals and constants is evaluated. A subclass sets the words and the `Str` it writes paths with, reads the
    facts of a file (`read_facts`), and says what an unknown part looks like (`_unknown`).

    The words are the adapter's own, so that the reasons and the records of each framework read as they always did: `LABEL` (a problem is
    `<LABEL> at <sha>: <why>`), `NOUN` (what a search may miss), `UNPARSED` (what was not read from a file that did not parse) and `DEGRADED`
    (what the blocking `parse_degraded` says)."""

    LABEL = "route table"
    NOUN = "routes"
    UNPARSED = "routes in it were not read"
    DEGRADED = "no route is claimed from it, so routes may be missing"
    PATTERNS: list[str] = []
    Str: Any = None  # the string type of the adapter: `Str(value, unknown)` and `+`

    def __init__(self, adapter: Any, sha: str) -> None:
        self.adapter = adapter
        self.language = adapter.language
        self.sha = sha
        self.facts: dict[str, Any] = {}
        self.complete = True
        self.reasons: list[str] = []
        self.candidates: set[str] = set()
        self.unresolved = False  # something that may carry routes could not be read: what looks unmounted may be mounted there
        self.entry_files: set[str] = set()

    @property
    def target(self) -> Any:
        return self.adapter.target

    def incomplete(self, why: str) -> None:
        """The table is not whole. The reason is kept once and goes to the adapter's problems."""
        self.complete = False
        if why not in self.reasons:
            self.reasons.append(why)
            self.adapter.problem(f"{self.LABEL} at {self.sha[:12]}: {why}")

    def hole_covers(self, path: str) -> bool:
        """Whether routes at `path` may be missing from this table. A table that does not say where it has holes has one everywhere when it is not whole."""
        return not self.complete

    # ---- finding and reading files

    def search(self) -> None:
        """One `git grep` for all of the patterns (a line that holds any of them): the files that may declare routes become candidates. A search that
        failed or was cut makes the table incomplete."""
        res = self.language.search(self.sha, self.PATTERNS)
        shown = " | ".join(self.PATTERNS)
        if res is None:
            self.incomplete("the tree could not be read at this commit")
            return
        if res.state == Read.UNREADABLE:
            self.incomplete(f"the search for {shown!r} was unreadable: {res.detail}")
            return
        if res.truncated_by == "examine_cap":
            self.incomplete(f"search_truncated: the search for {shown!r} examined {res.examined} of {res.total} files")
        for link, why in res.not_searched:
            if link.endswith(".py"):
                self.incomplete(f"{link} is a {why}: git grep does not search it, so {self.NOUN} in it may be missed")
                self.not_searched(link, why)
        for m in res.partial_matches:
            if m.path.endswith(".py") and not self.language.is_test_path(m.path) and citable(m.path):
                self.candidates.add(m.path)

    def not_searched(self, link: str, why: str) -> None:
        """A file the search does not read (a symlink): `incomplete` has said so; an adapter may say more."""

    def read_facts(self, path: str, parsed: Any) -> Any:  # pragma: no cover - overridden
        raise NotImplementedError

    def unread(self, path: str, why: str) -> None:
        """A file that could not be read or did not parse: its facts are None."""
        self.incomplete(why)

    def facts_of(self, path: str) -> Any:
        """The facts of a file, read once. None when it could not be read or did not parse: that is a hole (`unread`) and, for a file that does not
        parse, a blocking `parse_degraded`; never an absence."""
        if path in self.facts:
            return self.facts[path]
        self.facts[path] = None
        parsed = self.language.parsed(self.sha, path) if self.language.file_text(self.sha, path).is_found else None
        if parsed is None:
            self.unread(path, f"{path} could not be read")
            return None
        if parsed.problems:
            self.unread(path, f"{path} did not parse ({parsed.problems[0]}): {self.UNPARSED}")
            record_quoted(self.target, "parse_degraded", path, self.DEGRADED, parsed.problems[0], blocks_selection=True)
            return None
        self.facts[path] = self.read_facts(path, parsed)
        return self.facts[path]

    # ---- names and strings

    def _modules(self, path: str, module: str, level: int) -> tuple[str, ...]:
        return self.language.resolve_module(path, self.sha, module, level)

    def _assigned(self, facts: Any, name: str) -> bool:
        """Whether the file assigns `name` in a way that rules out following an import of the same name."""
        return name in facts.consts

    @staticmethod
    def _unknown(path: str, expr: ast.AST) -> Any:  # pragma: no cover - overridden
        raise NotImplementedError

    def eval(self, path: str, expr: ast.expr | None, depth: int = 0) -> Any:
        """The string an expression makes: literals, f-strings and sums of them, and names that are constants of the file or of a module it imports
        from. What is not known is an unknown part of the result (`_unknown`)."""
        Str = self.Str
        if expr is None:
            return Str()
        if isinstance(expr, ast.Constant):
            if isinstance(expr.value, str):
                return Str(expr.value)
            if expr.value is None:
                return Str()
        elif isinstance(expr, ast.JoinedStr):
            out = Str()
            for v in expr.values:
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    out += Str(v.value)
                elif isinstance(v, ast.FormattedValue) and v.conversion == -1 and v.format_spec is None:
                    out += self.eval(path, v.value, depth + 1)
                else:
                    out += self._unknown(path, v)
            return out
        elif isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
            return self.eval(path, expr.left, depth + 1) + self.eval(path, expr.right, depth + 1)
        elif isinstance(expr, ast.Name) and depth < DEPTH:
            facts = self.facts_of(path)
            if facts is not None:
                if facts.consts.get(expr.id) is not None:
                    return self.eval(path, facts.consts[expr.id], depth + 1)
                if not self._assigned(facts, expr.id) and expr.id in facts.from_binding:
                    module, level, name = facts.from_binding[expr.id]
                    for g in self._modules(path, module, level):
                        other = self.facts_of(g)
                        if other is not None and other.consts.get(name) is not None:
                            return self.eval(g, other.consts[name], depth + 1)
        return self._unknown(path, expr)


def route_evidence(repo: str, sha: str, places: Iterable[tuple[str, int]], parsed_of: Callable[[str], Any], claim: str) -> tuple[Evidence, ...] | None:
    """The evidence of a composed route: the line of each place that says it, the route's own first (it carries `claim`), then each mount from the route
    outwards. None as soon as one place cannot be cited (its file is not read, or the line is blank): a route that cannot be shown is not claimed."""
    out: list[Evidence] = []
    for n, (path, line) in enumerate(places):
        parsed = parsed_of(path)
        ev = evidence_at(repo, path, sha, parsed, line) if parsed is not None else None
        if ev is None:
            return None
        out.append(replace(ev, claim=claim) if n == 0 else ev)
    return tuple(out)


# ------------------------------------------------------------------------------ what a change reaches


class Gathered:
    """The routes the chains of one `entry_points_reached` call reach: one entry per route (the route, the chains of evidence that lead to it, the walk
    chains that made them, whether each was established completely and why not), in the order found, and the units no mount carries up."""

    def __init__(self) -> None:
        self.found: dict[tuple[Any, ...], dict[str, Any]] = {}
        self.order: list[tuple[Any, ...]] = []
        self.unmounted: dict[str, None] = {}

    def unmounted_at(self, top: Ref) -> None:
        self.unmounted.setdefault(f"{top[0]}:{top[1]}")

    def add(self, chain: Any, table: Any, route: Route, evidence: tuple[Evidence, ...], **kept: Any) -> None:
        """A chain reached the route through `evidence`. `kept` is what the adapter wants back about the route (the first call for a route decides it)."""
        key = (route.method, route.path, route.file, route.handler, route.evidence)
        entry = self.found.setdefault(key, {"route": route, **kept, "chains": [], "sources": [], "complete": True, "reasons": []})
        if evidence not in entry["chains"]:
            entry["chains"].append(evidence)
            entry["sources"].append(chain)
        if not (chain.complete and table.complete):
            entry["complete"] = False
            entry["reasons"] += [r for r in tuple(chain.reasons) + tuple(table.reasons) if r not in entry["reasons"]]
        if key not in self.order:
            self.order.append(key)


class Shared:
    """The part of a Python route adapter that does not depend on the framework: the table per commit, the route accessors, `routes_changed` and the
    areas a change reaches. A subclass sets `kind`, `TABLE` (its table class) and the words of the unmounted claim."""

    kind = ""
    TABLE: Any = None
    HEAD_REASONS_FIRST = False  # `routes_changed` says the reasons of the head table before those of the base (the order the adapter always had)
    UNMOUNTED_KIND = "router_unmounted"
    UNMOUNTED_DETAIL = ""
    UNMOUNTED_UNSURE = "{subject} may be mounted by something that could not be read: it is not said to be unmounted"

    def __init__(self, repo: str, language: Any, *, record_to: Any = None) -> None:
        self.repo = repo
        self.language = language
        self._target = record_to
        self._tables: dict[str, Any] = {}
        self.problems: list[str] = []  # what could not be established (a route table that is not whole, an unreadable file)

    def problem(self, why: str) -> None:
        if why not in self.problems:
            self.problems.append(why)

    @property
    def target(self) -> Any:
        return self._target if self._target is not None else self.language.target

    def _table(self, sha: str) -> Any:
        if sha not in self._tables:
            self._tables[sha] = self.TABLE(self, sha)
        return self._tables[sha]

    # ---- what the walk asks

    def is_entry_file(self, path: str, sha: str) -> bool:
        """A file the table says holds entry points."""
        return path in self._table(sha).entry_files

    def routes(self, repo: str | None, sha: str) -> list[Route]:
        """Every API route of the repository at `sha`, with its full path and evidence."""
        if repo is not None and repo != self.repo:
            raise ValueError(f"this adapter reads {self.repo}, not {repo}")
        return self._table(sha).routes()

    def routes_changed(self, before: str | None, after: str) -> tuple[list[Route], list[Route]]:
        """(removed, added) routes between two commits by method and full path, each with the evidence of its own version. Without a pre-change version
        nothing is computed: removals cannot be (the change set recorded it). A route of something that was not read is not removed, and one the base
        could not show is not new (`guard_route_changes`): a removal is withheld where the head table has a hole that covers its path, an addition where
        the base has."""
        if before is None:
            return [], []
        old, new = self._table(before), self._table(after)
        removed, added = diff_routes(old.routes(), new.routes())
        tables = (new, old) if self.HEAD_REASONS_FIRST else (old, new)
        why = "; ".join(r for table in tables if not table.complete for r in table.reasons)[:300]
        return guard_route_changes(self.target, self.repo, removed, added, base_ok=True, head_ok=True, why=why,
                                   unsure_removed=lambda r: new.hole_covers(r.path), unsure_added=lambda r: old.hole_covers(r.path))

    withholds_unread = True  # `routes_changed` guards itself: the http edge does not guard it again

    def routes_complete(self, sha: str) -> tuple[bool, list[str]]:
        """Whether the route table at `sha` was read completely, and why not."""
        table = self._table(sha)
        return table.complete, list(table.reasons)

    # ---- what a chain reaches, as areas

    def settle(self, gathered: Gathered, chains: Sequence[Any], on_route: Callable[[dict[str, Any]], None]) -> list[Reached]:
        """One `Area` per route reached, ordered by label and file. A unit that no mount carries up is said (`UNMOUNTED_KIND`), unless a mount of the
        repository could not be read: it may be what mounts it, and that is a problem instead. `on_route(entry)` is called for each route, in the order
        found, before its area is made (the adapter records what it knows about the route there)."""
        for subject in gathered.unmounted:
            if any(self._table(chain.sha).unresolved for chain in chains):
                self.problem(self.UNMOUNTED_UNSURE.format(subject=subject))
                continue  # that claim cannot be made while a mount of the repository is unresolved (the blocking record says so)
            record(self.target, self.UNMOUNTED_KIND, subject, detail=self.UNMOUNTED_DETAIL)
        out: list[Reached] = []
        for key in gathered.order:
            e = gathered.found[key]
            route: Route = e["route"]
            on_route(e)
            area = api_area(self.repo, route, e["chains"])
            out.append(Reached(area, (route,), e["complete"], tuple(e["reasons"]), tuple(e["sources"])))
        out.sort(key=lambda r: (r.area.label, r.routes[0].file))
        return out
