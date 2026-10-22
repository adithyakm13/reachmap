"""What the Express and the NestJS adapter share: the problems list, the route table accessors, how a change inside a file is
cited, and the building of the areas from the routes a change reaches.

An area is one API route, as for FastAPI: the label is `METHOD /full/path`, the chains are the evidence from the change to the
handler (the imports on the way, or the changed line) followed by the route's own evidence, one chain per way the change
reaches it.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, replace
from typing import Any, Iterable, Sequence

from reachmap.adapters import Reached, Route, diff_routes, guard_route_changes
from reachmap.adapters.base import dedupe, evidence_at, record_quoted
from reachmap.adapters.express.source import Str
from reachmap.errors import ModelError
from reachmap.incomplete import kind_spec, record, register_kind
from reachmap.model import Area, Evidence, MUST_TEST, Read

__all__ = ["Hit", "Shared", "TableBase"]

try:
    kind_spec("routes_unread")
except ModelError:
    register_kind(
        "routes_unread",
        "Which routes {subject} serves was not established, because they are made somewhere this adapter does not read (a router built in a function, "
        "an app handed to a function that adds routes, a controller that inherits its routes)",
        True,
    )


@dataclass(slots=True)
class Hit:
    """A route a change reaches: the route, the chain of evidence that leads to it, whether it was established completely."""

    route: Route
    evidence: tuple[Evidence, ...]
    complete: bool
    reasons: tuple[str, ...]
    source: Any
    unknown: tuple[tuple[str, str, int], ...] = ()
    unmounted: tuple[str, str] | None = None  # (file, name) of the router or the controller that nothing mounts: the route is then not claimed


class TableBase:
    """What the route tables of the two adapters share: the files read once per commit (a file that cannot be read or lexed is a hole, said and blocking),
    what is incomplete and where, the specifier a name is imported through, constants (one rule: a name declared twice is unknown), and Evidence."""

    NOUN = "routes and mounts"
    DEPTH = 6

    def __init__(self, adapter: Any, sha: str) -> None:
        self.adapter = adapter
        self.language = adapter.language
        self.sha = sha
        self.facts: dict[str, Any] = {}
        self.complete = True
        self.reasons: list[str] = []
        self.candidates: set[str] = set()
        self.unresolved = False  # something that may serve routes could not be read: what looks unmounted may be mounted there
        self.holes: set[str] = set()  # the files where something was not read (a file that did not lex, a mount that did not resolve): routes through them are not known
        self.global_hole = False  # a hole that may hide any route (a search that was cut, a tree that could not be read)

    @property
    def target(self) -> Any:
        return self.adapter.target

    def incomplete(self, why: str, path: str | None = None) -> None:
        """Something was not read: the table is not complete, and, with a `path`, the routes through that file are not known; without one, any route may be missing."""
        self.complete = False
        if path is None:
            self.global_hole = True
        else:
            self.holes.add(path)
        if why not in self.reasons:
            self.reasons.append(why)
            self.adapter.problem(f"route table at {self.sha[:12]}: {why}")

    def extract(self, path: str, parsed: Any) -> Any:  # pragma: no cover - overridden
        raise NotImplementedError

    def facts_of(self, path: str) -> Any:
        if path in self.facts:
            return self.facts[path]
        self.facts[path] = None
        parsed = self.language.parsed(self.sha, path) if self.language.file_text(self.sha, path).is_found else None
        if parsed is None:
            self.incomplete(f"{path} could not be read", path)
            return None
        if parsed.problems:
            self.incomplete(f"{path} did not lex cleanly ({parsed.problems[0]}): {self.NOUN} in it were not read", path)
            record_quoted(self.target, "parse_degraded", path, f"no route is claimed from it, so routes may be missing ({self.NOUN})", parsed.problems[0], blocks_selection=True, once=True)
            return None
        self.facts[path] = self.extract(path, parsed)
        return self.facts[path]

    def module_file(self, path: str, spec: str) -> str | None:
        return self.language.resolve_specifier(path, spec, self.sha)

    def upstream(self, path: str, spec: str, imported: str) -> tuple[str, str]:
        """The module and the name an import really means, through files of the repository that only re-export it (`export { Router } from "express"`,
        `export * from "@nestjs/common"`): a package name when the chain ends in one, else what was given."""
        for _ in range(4):
            target = self.module_file(path, spec)
            facts = self.facts_of(target) if target is not None else None
            if facts is None:
                return spec, imported
            hop = next((e for e in facts.src.exports() if e.name == imported and e.source is not None), None)
            if hop is not None:
                path, spec, imported = target, hop.source, hop.source_name or imported
                continue
            star = next((x for x in facts.src.parsed.export_stars if self.module_file(target, x) is None), None)
            if star is None or imported in ("default", "*", "*cjs"):
                return spec, imported
            path, spec = target, star
        return spec, imported

    # ---- constants and strings

    def const(self, path: str, src: Any, name: str, depth: int) -> Str | None:
        """The string a name stands for: a constant of the file, or one imported by name (followed through re-exports and CommonJS exports); None
        for a name that is not one, or is declared twice."""
        facts = self.facts_of(path)
        if facts is None or depth > self.DEPTH:
            return None
        if name in facts.consts:
            rng = facts.consts[name]
            return self.eval(path, rng[0], rng[1], depth + 1) if rng is not None else None
        b = facts.src.bindings().get(name)
        if b is not None and b.imported not in ("*", "*cjs", "default"):
            target = self.module_file(path, b.spec)
            if target is not None:
                return self.const_export(target, b.imported, depth + 1)
        return None

    def const_export(self, path: str, name: str, depth: int) -> Str | None:
        facts = self.facts_of(path)
        if facts is None or depth > self.DEPTH:
            return None
        if name in facts.consts:
            rng = facts.consts[name]
            return self.eval(path, rng[0], rng[1], depth + 1) if rng is not None else None
        for e in facts.src.exports():
            if e.name != name:
                continue
            if e.source is not None:
                target = self.module_file(path, e.source)
                return self.const_export(target, e.source_name or name, depth + 1) if target is not None else None
            if e.value is not None:
                return self.eval(path, e.value[0], e.value[1], depth + 1)
            if e.local is not None:
                return self.const(path, facts.src, e.local, depth + 1)
        for spec in facts.src.parsed.export_stars:
            target = self.module_file(path, spec)
            got = self.const_export(target, name, depth + 1) if target is not None else None
            if got is not None:
                return got
        return None

    def eval(self, path: str, a: int, b: int, depth: int = 0) -> Str:
        facts = self.facts_of(path)
        assert facts is not None
        return facts.src.eval(a, b, self, depth)

    # ---- evidence

    def evidence(self, path: str, line: int, claim: str | None = None) -> Evidence | None:
        facts = self.facts.get(path)
        ev = evidence_at(self.adapter.repo, path, self.sha, facts.src.parsed, line) if facts is not None else None
        return ev if ev is None or claim is None else replace(ev, claim=claim)


class Shared:
    """The part of an entry-point adapter that does not depend on the framework. Subclasses set `kind` and `LABEL`."""

    kind = ""
    LABEL = ""
    EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")

    def __init__(self, repo: str, language: Any, *, service_marker: str | None = None, record_to: Any = None) -> None:
        self.repo = repo
        self.language = language
        self.service_marker = service_marker or "package.json"
        self._target = record_to
        self.problems: list[str] = []  # what could not be established (a route table that is not whole, an unreadable file)

    def problem(self, why: str) -> None:
        if why not in self.problems:
            self.problems.append(why)

    @property
    def target(self) -> Any:
        return self._target if self._target is not None else self.language.target

    def module_of(self, path: str, sha: str) -> str:
        """The service the file belongs to: the name of the nearest folder (not the root) that holds the service marker."""
        files = self.language.tree(sha)
        folder = posixpath.dirname(path)
        while folder:
            if f"{folder}/{self.service_marker}" in files:
                return posixpath.basename(folder)
            folder = posixpath.dirname(folder)
        return ""

    def is_source(self, path: str) -> bool:
        """A file this adapter reads for routes: code, not a declaration file, not a dependency folder, not a test."""
        return (path.endswith(self.EXTENSIONS) and not path.endswith((".d.ts", ".d.mts", ".d.cts")) and "node_modules/" not in path
                and not self.language.is_test_path(path))

    def table(self, sha: str) -> Any:  # pragma: no cover - overridden
        raise NotImplementedError

    WHAT = ""  # what the search looks for, in the words of the `routes_unmatched` subject

    def routing_files(self, sha: str, what: str) -> list[str]:
        return list(self.table(sha).entry_files)

    def unmatched(self, sha: str) -> list[tuple[str, str]]:
        """The search when it came back whole and found no file that declares routes (an app, a router, a controller or a module). A search that
        was cut or failed is the table's incompleteness, not an absence."""
        table = self.table(sha)
        if not table.complete or table.entry_files:
            return []
        return [(self.WHAT, f"no file declares {self.WHAT}, so the routes of the repository were not established")]

    def routes(self, repo: str | None, sha: str) -> list[Route]:
        """Every API route of the repository at `sha`, with its full path and evidence."""
        if repo is not None and repo != self.repo:
            raise ValueError(f"this adapter reads {self.repo}, not {repo}")
        return self.table(sha).routes()

    withholds_unread = True  # `routes_changed` guards itself: the http edge does not guard it again

    def routes_complete(self, sha: str) -> tuple[bool, list[str]]:
        """Whether the route table at `sha` was read completely, and why not."""
        table = self.table(sha)
        return table.complete, list(table.reasons)

    def routes_changed(self, before: str | None, after: str) -> tuple[list[Route], list[Route]]:
        """(removed, added) routes between two commits by method and full path, each with the evidence of its own version. Without a pre-change version
        nothing is computed: removals cannot be (the change set recorded it). A route of a file that could not be read is not removed, it was not read
        (`guard_route_changes`): a removal is withheld when the head table has a hole in one of the files the route is made of (its file, its mounts), an
        addition when the base has, and every change when a hole may hide any route (a search that was cut); what is withheld is said."""
        if before is None:
            return [], []
        old, new = self.table(before), self.table(after)
        removed, added = diff_routes(old.routes(), new.routes())

        def touches(table: Any, route: Route) -> bool:
            return bool(({route.file} | {e.path for e in route.evidence}) & table.holes)

        why = "; ".join(([] if new.complete else new.reasons) + ([] if old.complete else old.reasons))[:300]
        extra = self.changes_hidden_by(old, new)
        return guard_route_changes(self.target, self.repo, removed, added, base_ok=not old.global_hole, head_ok=not new.global_hole, why=why,
                                   unsure_removed=lambda r: touches(new, r), unsure_added=lambda r: touches(old, r) or extra)

    def changes_hidden_by(self, old: Any, new: Any) -> bool:
        """Whether a hole in the head table makes the routes it does show wrong (not merely missing); False unless an adapter knows such a hole."""
        return False

    def unmounted(self, sha: str) -> list[tuple[str, str]]:  # pragma: no cover - overridden
        raise NotImplementedError

    # ---- the grep every table starts with

    def grep(self, table: Any, patterns: list[str]) -> set[str]:
        """One `git grep` for all of the patterns: the files that may hold routes. A search that failed or was cut makes the
        table incomplete, never empty."""
        res = self.language.search(table.sha, patterns)
        shown = " | ".join(patterns)
        found: set[str] = set()
        if res is None:
            table.incomplete("the tree could not be read at this commit")
            return found
        if res.state == Read.UNREADABLE:
            table.incomplete(f"the search for {shown!r} was unreadable: {res.detail}")
            return found
        if res.truncated_by == "examine_cap":
            table.incomplete(f"search_truncated: the search for {shown!r} examined {res.examined} of {res.total} files")
        for link, why in res.not_searched:
            if link.endswith(self.EXTENSIONS):
                table.incomplete(f"{link} is a {why}: git grep does not search it, so routes in it may be missed")
        for m in res.partial_matches:
            if self.is_source(m.path) and self._citable(m.path):
                found.add(m.path)
        return found

    @staticmethod
    def _citable(path: str) -> bool:
        from reachmap.adapters.base import citable

        return citable(path)

    # ---- what a chain reaches, as areas

    @staticmethod
    def direct_hop(chain: Any, route: Route, within: Iterable[tuple[int, int]]) -> tuple[Evidence, ...]:
        """For a change inside the file: the first changed line inside one of the spans that belong to the route, else the first
        changed line that sits on one of the route's own lines, else the first changed line."""
        lines = [e for s in chain.changed for e in s.evidence if e.ref == chain.sha and e.path == chain.entry]
        spans = list(within)
        inside = [e for e in lines if any(a <= e.line <= b for a, b in spans)]
        route_lines = {(e.path, e.line) for e in route.evidence}
        on_route = [e for e in lines if (e.path, e.line) in route_lines]
        return tuple((inside or on_route or lines or list(chain.hops))[:1])

    def assemble(self, hits: Sequence[Hit], unresolved: bool) -> list[Reached]:
        """One `Area` per route reached, the chains in the order found, completeness by every chain and table; a route whose
        router nothing mounts is not claimed and is said (`router_unmounted`, only when no mount of the repository is unresolved)."""
        found: dict[tuple[Any, ...], dict[str, Any]] = {}
        order: list[tuple[Any, ...]] = []
        unmounted: dict[str, None] = {}
        for h in hits:
            if h.unmounted is not None:
                unmounted.setdefault(f"{h.unmounted[0]}:{h.unmounted[1]}")
                continue
            r = h.route
            key = (r.method, r.path, r.file, r.handler, r.evidence)
            entry = found.setdefault(key, {"route": r, "chains": [], "sources": [], "complete": True, "reasons": [], "unknown": ()})
            if h.evidence not in entry["chains"]:
                entry["chains"].append(h.evidence)
                entry["sources"].append(h.source)
            if not h.complete:
                entry["complete"] = False
                entry["reasons"] += [x for x in h.reasons if x not in entry["reasons"]]
            entry["unknown"] = tuple(dict.fromkeys(entry["unknown"] + h.unknown))
            if key not in order:
                order.append(key)
        for subject in unmounted:
            if unresolved:
                self.problem(f"{subject} may be mounted by a mount that could not be resolved: it is not said to be unmounted")
                continue
            record(self.target, "router_unmounted", subject,
                   detail="no route is claimed for its handlers: nothing mounts it up to an app (a mount in a comment is not a mount)")
        out: list[Reached] = []
        for key in order:
            e = found[key]
            route: Route = e["route"]
            for expr, path, line in e["unknown"]:
                record_quoted(self.target, "route_path_partial", f"{path}:{line}",
                              "a part of the route's path is not a string literal or a constant; it is kept with the part marked and nothing is guessed", expr, once=True)
            area = Area(
                label=route.label, kind="api_route", repo=self.repo, module=route.module, route_line=route.evidence[0], chains=tuple(e["chains"]),
                also_mounted=(), priority=MUST_TEST, by_name=False,
            )
            out.append(Reached(area, (route,), e["complete"], tuple(e["reasons"]), tuple(e["sources"])))
        out.sort(key=lambda r: (r.area.label, r.routes[0].file))
        return out

    @staticmethod
    def chain_of(hops: Sequence[Evidence], route: Route) -> tuple[Evidence, ...]:
        return tuple(dedupe(list(hops) + list(route.evidence)))
