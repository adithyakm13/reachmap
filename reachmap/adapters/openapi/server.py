"""The OpenAPI entry-point adapter: the routes of a server as its committed spec describes them.

Configured as `http_server = { kind = "openapi", spec = "api/openapi.json", handlers = "app/handlers/*.py" }` on the repository
that serves the API (any language the repository's own language adapter reads). A route is an operation of the spec under each
of its servers' base paths (`servers`, or `basePath` in Swagger 2); its evidence is the line of the method key, the line of the
path key and, when the path came from a server, the line of that server's URL, all in the spec at the commit the route was read
at. Routes are read as an entry-point adapter's route table (`routes`, `routes_changed`), so the http edge matches client calls
to them exactly as it does the routes of FastAPI.

Handlers. An operation is served by the function of the handler files (`handlers`, a path, a glob or a list) whose name is its
operationId, written as it is or in snake_case (`getInvoice` is `get_invoice`). A file of the handler tree that defines such a
function is an entry file: a change to the function, or to anything it uses (the walk follows imports), reaches the operation. An
operation no single function carries (none, or several) is `operation_handler_unresolved`, said whenever code changed and one
exists; `handlers` is a file, a folder or a glob (or a list), a pattern that matches no file is `handlers_unmatched`, and `http_server`
without `handlers` ties no code to any operation, which a change to code says (`handlers_unconfigured`). Nothing is guessed by a
looser name.

The spec as a change. A changed spec file is read at both versions (`reached_by_files`, called by `reachmap.entrypoints.reach`
for a file no language adapter reads): an operation present in both whose signature differs (method, path, parameters, request
body, responses and every schema they reach through `$ref`), and every added operation, is an area, cited at the first changed
line that falls in the operation or in a schema it reaches, then at its route evidence; each changed operation also gives an
`operation-changed` finding input with evidence at both versions (returned with the areas: `EntryResult.findings`). Removed and added
routes are `routes_changed`'s, which the http edge turns into `removed_route_still_called` and `added_route_uncalled`; a route whose path
item or method the reader could not read at either version (a path item moved to another file) is neither, and that is a blocking
`spec_unreadable`. With no pre-change version, a new spec or a deleted one, every operation is treated as affected, and the answer says it
is not complete.

Failure is never an empty table. A spec that is absent, unreadable, not valid JSON, not OpenAPI or has a duplicate key is a
blocking `spec_unreadable` and claims no route; YAML is `file_not_analysed` (the routes it declares are not known, and the answer
is not complete); parts of a spec that were read but not followed are a `spec_gap`; a handler file that does not parse is a
blocking `parse_degraded`. Every one of those also lands in `problems`, which `reachmap.entrypoints.reach` turns into
`EntryResult.complete = False`. Limits are in docs/briefs/M16-openapi.md.
"""

from __future__ import annotations

import dataclasses
import posixpath
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from reachmap import config
from reachmap.adapters import Reached, Route, diff_routes
from reachmap.adapters.base import dedupe, direct_hop, import_lines, module_changes, record_quoted
from reachmap.adapters.openapi import names
from reachmap.adapters.openapi.options import as_list
from reachmap.adapters.openapi.spec import Changed, Operation, Spec, diff, read_file
from reachmap.adapters.react_router import glob_regex
from reachmap.edges import FindingInput
from reachmap.model import Area, Evidence, MUST_TEST

__all__ = ["OpenApiServer"]


_GLOB = re.compile(r"[*?{]")
JS_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")


def pattern_regex(pattern: str) -> "re.Pattern[str]":
    """A `handlers` entry as a regular expression over repository paths: a glob as `glob_regex` reads it, anything else a file or a folder
    (every path under it, at a folder boundary: `app/handlers` is not `app/handlers2`)."""
    if _GLOB.search(pattern):
        return glob_regex(pattern)
    return re.compile("^" + re.escape(pattern.rstrip("/")) + "(?:/.*)?$")


@dataclass(frozen=True, slots=True)
class _Handler:
    """The function that serves an operation: its name, the file, and the lines of its declaration (decorators included)."""

    name: str
    file: str
    start: int
    end: int


@dataclass(slots=True)
class _Table:
    """The spec of one commit and what was made of it. `state` is `found`, `absent` (git says the path is not in the tree),
    `unreadable` (the read failed), `invalid` (not JSON, not OpenAPI), `yaml` (not read)."""

    sha: str
    state: str
    spec: Spec | None = None
    why: str = ""
    routes: list[Route] = field(default_factory=list)
    handlers: dict[tuple[str, str], _Handler | None] = field(default_factory=dict)  # (METHOD, full path) to the handler, None when unresolved
    unresolved: list[tuple[str, str, str]] = field(default_factory=list)  # (METHOD, full path, why)
    by_file: dict[str, list[tuple[Route, _Handler]]] = field(default_factory=dict)
    ops: dict[tuple[str, str], Operation] = field(default_factory=dict)
    complete: bool = True
    reasons: list[str] = field(default_factory=list)
    claimed: bool = False  # the gaps of the spec were recorded


class OpenApiServer:
    """OpenAPI entry points of one repository, built on its language adapter (which lists and reads the files). One adapter per
    run, as the language adapters are (a commit never changes, so a table is kept for the life of the adapter)."""

    kind = "openapi"

    def __init__(self, repo: str, language: Any, spec_path: str, handlers: Sequence[str] = (), *, record_to: Any = None) -> None:
        self.repo = repo
        self.language = language
        self.spec_path = spec_path
        self.handler_globs = tuple(handlers)
        self._regexes = [pattern_regex(g) for g in self.handler_globs]
        self._target = record_to
        self._tables: dict[str, _Table] = {}
        self._said: set[tuple[Any, ...]] = set()
        self.problems: list[str] = []  # what could not be established (a spec that cannot be read, handlers that are not known)

    @classmethod
    def from_config(cls, repo: config.Repo, language: Any, *, record_to: Any = None) -> "OpenApiServer":
        options = repo.http_server or {}
        return cls(repo.name, language, str(options.get("spec") or ""), as_list(options.get("handlers")), record_to=record_to)

    @property
    def target(self) -> Any:
        return self._target if self._target is not None else self.language.target

    def problem(self, why: str) -> None:
        if why not in self.problems:
            self.problems.append(why)

    def _once(self, *key: Any) -> bool:
        """True the first time a key is seen (records are made once per cause)."""
        if key in self._said:
            return False
        self._said.add(key)
        return True

    # ---- the spec at a commit

    def table(self, sha: str) -> _Table:
        if sha not in self._tables:
            self._tables[sha] = self._read(sha)
        return self._tables[sha]

    def _read(self, sha: str) -> _Table:
        got = read_file(self.language, self.spec_path, sha)
        if got.spec is None:
            return _Table(sha, got.state, why=got.why)
        table = _Table(sha, "found", spec=got.spec)
        self._compose(table)
        return table

    def _usable(self, table: _Table) -> bool:
        """Whether the table has a spec to claim routes from. When it has not, say so once (a record and a problem): an empty
        table is never an answer. A spec that is not in the tree is not "no routes" either: the handlers may still serve them (a
        deleted spec is not a removed route), so it is a blocking `spec_unreadable` like a spec that cannot be read."""
        if table.state == "found":
            self._say_gaps(table)
            return True
        subject = f"{self.repo}:{self.spec_path}"
        if self._once("unreadable", table.sha, table.state):
            if table.state == "yaml":  # no YAML reader: a removed operation of a changed spec would give no finding, so this blocks like any spec that cannot be read
                record_quoted(self.target, "spec_unreadable", subject,
                              "the OpenAPI spec is YAML and this adapter reads JSON only: the routes it declares are not known, so no route is claimed and nothing is said to be absent from it", once=True)
            else:
                record_quoted(self.target, "spec_unreadable", subject, "no route is claimed and nothing is said to be absent from it", table.why)  # one per version and cause: no `once`
        self.problem(f"{table.why}: the routes of {self.repo} are not known")
        return False

    def _say_gaps(self, table: _Table) -> None:
        if table.claimed or table.spec is None:
            return
        table.claimed = True
        gaps = list(table.spec.problems)
        if gaps and self._once("gap", self.spec_path, table.sha):
            more = f" (and {len(gaps) - 3} more)" if len(gaps) > 3 else ""
            record_quoted(self.target, "spec_gap", f"{self.repo}:{self.spec_path}",
                          "the operations that depend on these parts are treated as changed whenever the spec changes and the routes read may be incomplete",
                          "; ".join(gaps[:3]) + more, once=True)
        for why in gaps:
            self.problem(f"{self.spec_path}: {why}")
            table.complete = False
            if why not in table.reasons:
                table.reasons.append(why)

    # ---- the table: routes with their handlers

    def _matches(self, path: str) -> bool:
        return any(rx.match(path) for rx in self._regexes)

    def _compose(self, table: _Table) -> None:
        spec = table.spec
        assert spec is not None
        sha = table.sha
        index = self._handler_index(table)
        seen: set[tuple[str, str]] = set()
        for op in spec.operations:
            handler: _Handler | None = None
            why = ""
            if not self.handler_globs:
                why = "no handler files are configured"
            elif not op.operation_id:
                why = "the operation has no operationId"
            else:
                wanted = {op.operation_id, names.snake(op.operation_id)}
                found = {(h.file, h.name): h for n in sorted(wanted) for h in index.get(n, ())}
                if len(found) == 1:
                    (handler,) = found.values()
                elif found:
                    why = f"{len(found)} functions in the handler files carry the operationId {op.operation_id} (" + ", ".join(sorted(f"{f}:{n}" for f, n in found)) + ")"
                else:
                    why = f"no function named {op.operation_id} or {names.snake(op.operation_id)} in the handler files"
            for path, base in op.routes():
                key = (op.method, path)
                if key in seen:
                    table.complete = False
                    table.reasons.append(f"two operations have the method and path {op.method} {path}: the first is read")
                    continue
                seen.add(key)
                partial = base.partial or "<unknown" in path
                lines = [(op.line, f"route:{op.method} {path}:{self.spec_path}"), (op.path_line, None)]
                if base.line and base.path:
                    lines.append((base.line, None))
                evidence = tuple(e for n, claim in lines if (e := self._ev(spec, sha, n, claim)) is not None)
                module = op.tags[0] if op.tags else (posixpath.splitext(posixpath.basename(handler.file))[0] if handler else "")
                route = Route(self.repo, "api_route", op.method, path, self.spec_path, handler.name if handler else (op.operation_id or ""),
                              handler.file if handler else "", module, evidence, partial)
                table.routes.append(route)
                table.ops[key] = op
                table.handlers[key] = handler
                if handler is not None:
                    table.by_file.setdefault(handler.file, []).append((route, handler))
                else:
                    table.unresolved.append((op.method, path, why))
        table.routes.sort(key=lambda r: (r.path, r.method))

    def _ev(self, spec: Spec, sha: str, line: int, claim: str | None = None) -> Evidence | None:
        detail = spec.line_text(line)
        if not detail.strip():
            return None
        return Evidence(self.repo, self.spec_path, line, sha, detail, claim)

    def _handler_index(self, table: _Table) -> dict[str, list[_Handler]]:
        """The functions of the handler files by name. A handler file that cannot be read or does not parse is a stated gap (and
        a blocking `parse_degraded`): which operations it serves is not known."""
        index: dict[str, list[_Handler]] = {}
        if not self.handler_globs:
            return index
        sha = table.sha
        tree = self.language.tree(sha)
        if not tree:
            table.complete = False
            table.reasons.append(f"the files of {self.repo} at {sha[:12]} could not be listed: the handler files are not known")
            return index
        python = "python" in getattr(self.language, "languages", ())
        suffixes = (".py",) if python else JS_SUFFIXES
        code = sorted(p for p in tree if p.endswith(suffixes) and not self.language.is_test_path(p))
        for pattern, rx in zip(self.handler_globs, self._regexes):  # a pattern that matches no file is not an empty handler tree
            if not any(rx.match(p) for p in code):
                if self._once("unmatched", pattern):
                    record_quoted(self.target, "handlers_unmatched", f"{self.repo}:{pattern}",
                                  "no operation is tied to code through it; check the pattern (a file, a folder or a glob inside the repository)", once=True)
                table.complete = False
                table.reasons.append(f"the handlers pattern {pattern} matches no code file of {self.repo} at {sha[:12]}: the handler files are not known")
                self.problem(f"the handlers pattern {pattern} matches no code file of {self.repo}")
        for path in (p for p in code if self._matches(p)):
            parsed = self.language.parsed(sha, path)
            if parsed is None:
                table.complete = False
                table.reasons.append(f"{path} could not be read: the functions it defines were not listed")
                continue
            if parsed.problems:
                table.complete = False
                table.reasons.append(f"{path} did not parse cleanly ({parsed.problems[0]}): the functions it defines were not listed")
                record_quoted(self.target, "parse_degraded", path, "no function of it is read as the handler of an operation", parsed.problems[0], blocks_selection=True, once=True)
                continue
            for d in parsed.declarations:
                if d.kind == "function" or (not python and d.kind == "variable"):
                    index.setdefault(d.name, []).append(_Handler(d.name, path, d.start, d.end))
        return index

    # ---- what the walk asks

    def is_entry_file(self, path: str, sha: str) -> bool:
        """A file of the handler tree that defines the function of at least one operation. Asked for every code file a change
        reaches, so it is where a change to code finds out that the handlers are not known."""
        if not self.handler_globs:
            if self._once("unconfigured"):
                record_quoted(self.target, "handlers_unconfigured", self.repo,
                              "a change to code reaches no operation through the spec; name the handler files in http_server to tie code to operations")
            self.problem(f"{self.repo} names no handler files in http_server, so which code serves each operation is not known")
            return False
        table = self.table(sha)
        if not self._usable(table):
            return False
        self._note_unresolved(table)  # code changed or was reached, and an operation has no handler: that code may be it
        return path in table.by_file

    def _note_unresolved(self, table: _Table) -> None:
        for method, path, why in table.unresolved:
            if self._once("unresolved", table.sha, method, path):
                record_quoted(self.target, "operation_handler_unresolved", f"{self.repo}:{method} {path}",
                              "code changed and this operation cannot be tied to a function of the handler files", why)
            self.problem(f"{method} {path}: {why}")

    def structural_names(self, path: str, sha: str) -> frozenset[str]:
        """No name of a handler file only wires it up: whoever imports a handler (a registration module) does not mount it, the
        spec does."""
        return frozenset()

    def routes(self, repo: str | None, sha: str) -> list[Route]:
        """Every route of the spec at `sha` with its full path and evidence. Empty, with a record and a problem, when the spec cannot
        be read."""
        if repo is not None and repo != self.repo:
            raise ValueError(f"this adapter reads {self.repo}, not {repo}")
        table = self.table(sha)
        return list(table.routes) if self._usable(table) else []

    withholds_unread = True  # `routes_changed` guards itself (an unusable spec claims nothing): the http edge does not guard it again

    def routes_changed(self, before: str | None, after: str) -> tuple[list[Route], list[Route]]:
        """(removed, added) routes between two commits by method and full path, each with the evidence of its own version. A spec that
        cannot be read at either side (not in the tree, not JSON, YAML) claims nothing, with a record: a deleted spec is not a removed
        route, and a spec that first appears does not make its routes new. A route whose path item or method the reader could not read at
        the other version (a path item moved to another file) is neither removed nor added, and that is said. Without a pre-change version
        nothing is computed (the change set recorded it)."""
        if before is None:
            return [], []
        old, new = self.table(before), self.table(after)
        if not self._usable(new):
            return [], []
        if old.state == "absent":
            self._new_spec(new)
            return [], []
        if not self._usable(old):
            return [], []
        removed, added = diff_routes(old.routes, new.routes)
        removed, added, cut = self._cut(removed, added, old, new)
        self._say_cut(cut)
        return removed, added

    def _cut(self, removed: list[Route], added: list[Route], old: _Table, new: _Table) -> tuple[list[Route], list[Route], list[Route]]:
        """Take out of a diff the routes that only look removed or added because the reader could not read their path item or method at
        the other version."""
        assert old.spec is not None and new.spec is not None
        keep_removed = [r for r in removed if not self._unread_at(new, old, r)]
        keep_added = [r for r in added if not self._unread_at(old, new, r)]
        cut = [r for r in removed if r not in keep_removed] + [r for r in added if r not in keep_added]
        return keep_removed, keep_added, cut

    @staticmethod
    def _unread_at(other: _Table, own: _Table, route: Route) -> bool:
        op = own.ops.get((route.method, route.path))
        return op is not None and other.spec is not None and other.spec.is_unread(op.template, route.method)

    def _say_cut(self, cut: list[Route]) -> None:
        if not cut:
            return
        labels = ", ".join(sorted(r.label for r in cut)[:3]) + (f" (and {len(cut) - 3} more)" if len(cut) > 3 else "")
        if self._once("cut", labels):
            record_quoted(self.target, "spec_unreadable", f"{self.repo}:{self.spec_path}",
                          "a path item or method of the spec could not be read at one version, so these routes are not said to be removed or added", labels)
        self.problem(f"{labels}: its path item or method was not read at one version of {self.spec_path}, so it is not said to be removed or added")

    def _new_spec(self, after: _Table) -> None:
        """The spec did not exist before the change: which of its routes are new, and what changed in the others, is not known."""
        if self._once("new-spec", self.spec_path):
            record_quoted(self.target, "spec_gap", f"{self.repo}:{self.spec_path}",
                          "the spec is new in this change, so which of its routes are new and what changed in the others was not established; every operation is treated as affected",
                          once=True)
        self.problem(f"{self.spec_path} is new in this change: which routes are new was not established")

    # ---- what a chain reaches

    def entry_points_reached(self, chains: Iterable[Any], sha: str | None = None) -> list[Reached]:
        """The routes the chains reach: one area per route, its chain the evidence from the change to the handler (the imports on the
        way, or the changed line) and then the route's own evidence."""
        found: dict[tuple[str, str], dict[str, Any]] = {}
        order: list[tuple[str, str]] = []
        for chain in chains:
            table = self.table(chain.sha)
            if table.state != "found":
                continue
            for route, handler in self._reached(table, chain):
                key = (route.method, route.path)
                hops = direct_hop(chain, (handler.start, handler.end)) if chain.direct else chain.hops
                evidence = tuple(dedupe(list(hops) + list(route.evidence)))
                entry = found.setdefault(key, {"route": route, "chains": [], "sources": [], "complete": True, "reasons": []})
                if evidence not in entry["chains"]:
                    entry["chains"].append(evidence)
                    entry["sources"].append(chain)
                if not (chain.complete and table.complete):
                    entry["complete"] = False
                    entry["reasons"] += [r for r in (*chain.reasons, *table.reasons) if r not in entry["reasons"]]
                if key not in order:
                    order.append(key)
        out = [self._reached_of(found[k]["route"], found[k]["chains"], tuple(found[k]["sources"]), found[k]["complete"], tuple(found[k]["reasons"])) for k in order]
        out.sort(key=lambda r: (r.area.label, r.routes[0].file))
        return out

    def _reached_of(self, route: Route, chains: Sequence[tuple[Evidence, ...]], sources: tuple[Any, ...], complete: bool, reasons: tuple[str, ...]) -> Reached:
        area = Area(label=route.label, kind="api_route", repo=self.repo, module=route.module, route_line=route.evidence[0], chains=tuple(chains),
                    also_mounted=(), priority=MUST_TEST, by_name=False)
        return Reached(area, (route,), complete, reasons, sources)

    def _reached(self, table: _Table, chain: Any) -> list[tuple[Route, _Handler]]:
        here = table.by_file.get(chain.entry, [])
        if not here:
            return []
        if chain.reach.whole:  # a statement that runs when the file loads: every route it serves
            return list(here)
        if chain.edge is not None:  # reached through an import: the handlers that use what arrives
            return [(r, h) for r, h in here if h.name in chain.reach.locals]
        picked = [(r, h) for r, h in here if h.name in chain.reach.locals]
        if any(s.is_module for s in chain.changed):  # a change outside every declaration could change anything the file does
            imported, other = module_changes(chain.changed, self.language.parsed(chain.sha, chain.entry), self.language.parsed(chain.pre_sha, chain.before_path) if chain.pre_sha else None, import_lines)
            if other:
                return list(here)
            if imported:  # only import statements changed: the handlers that spell a name they bind
                parsed = self.language.parsed(chain.sha, chain.entry)
                picked += [(r, h) for r, h in here if (r, h) not in picked and self._spells(parsed, h, imported)]
        return picked

    @staticmethod
    def _spells(parsed: Any, handler: _Handler, names: set[str]) -> bool:
        decl = parsed.placed(handler.start) if parsed is not None else None
        decl = decl[0] if isinstance(decl, list) and decl else decl
        return bool(decl is not None and not isinstance(decl, list) and parsed.decl_references(decl) & names)

    # ---- the spec as a change

    def reached_by_files(self, change: Any, reached: Sequence[Reached]) -> tuple[list[Reached], list[FindingInput]]:
        """The areas the walk found plus those a change to the spec file reaches (see the module docstring), and the `operation-changed`
        finding inputs of that change. `reachmap.entrypoints.reach` calls it for every change; a seed (lines handed over an edge)
        changes no spec."""
        out = list(reached)
        inputs: list[FindingInput] = []
        if getattr(change, "kind", "") == "seed" or not self.spec_path:
            return out, inputs
        fc = next((f for f in change.files if f.path == self.spec_path or f.old_path == self.spec_path), None)
        if fc is None:
            return out, inputs
        head, pre = change.head_sha, change.pre_change_sha
        after = self.table(head)
        if not self._usable(after):  # a deleted spec, one that cannot be read: no route is claimed, the blocking record says so
            old = self.table(pre) if pre is not None and fc.status == "deleted" else None
            if old is not None and self._usable(old):  # undecidable means affected: every operation it declared, cited at the base
                why = f"{self.spec_path} is deleted by this change: the routes it declared are not known to be gone, so every operation is treated as affected"
                self.problem(why)
                for route in old.routes:
                    out = self._merge(out, self._reached_of(route, [route.evidence], (), False, (why,)))
                out.sort(key=lambda r: (r.area.label, r.routes[0].file))
            return out, inputs
        assert after.spec is not None
        before = self.table(pre) if pre is not None else None
        new_spec = fc.status == "added" or (before is not None and before.state == "absent")
        if before is None or new_spec or not self._usable(before):
            if before is None:
                why = "there is no pre-change version of the spec"
            elif new_spec:
                self._new_spec(after)
                why = "the spec is new in this change"
            else:
                why = before.why
            self.problem(f"{why}: every operation of {self.spec_path} is treated as affected")
            for route in after.routes:
                out = self._merge(out, self._spec_reached(change, after, None, route, None, False, (f"{why}: the operation is treated as affected",)))
            return sorted(out, key=lambda r: (r.area.label, r.routes[0].file)), inputs
        assert before.spec is not None
        delta = diff(before.spec, after.spec)
        removed, added = diff_routes(before.routes, after.routes)
        _removed, new, cut = self._cut(removed, added, before, after)
        self._say_cut(cut)
        for c in delta.changed:
            route = next((r for r in after.routes if (r.method, r.path) == (c.method, c.path)), None)
            if route is None:
                continue
            out = self._merge(out, self._spec_reached(change, after, before, route, c, "unresolved" in c.why, ()))
            found = self._input(after, before, c)
            if found is not None and all(i.identity != found.identity for i in inputs):
                inputs.append(found)
        for route in new:
            out = self._merge(out, self._spec_reached(change, after, before, route, None, False, ()))
        return sorted(out, key=lambda r: (r.area.label, r.routes[0].file)), inputs

    def _spec_reached(self, change: Any, after: _Table, before: _Table | None, route: Route, c: Changed | None, unknown: bool,
                      reasons: tuple[str, ...]) -> Reached:
        first = self._first_changed(change, after, before, route, c)
        chain = tuple(dedupe(([first] if first is not None else []) + list(route.evidence)))
        complete = not reasons and not unknown and after.complete
        why = tuple(reasons) + (("an operation or a schema it reaches points outside the spec: whether it changed is not known, so it is treated as changed",) if unknown else ()) + tuple(after.reasons)
        return self._reached_of(route, [chain], (), complete, why)

    def _first_changed(self, change: Any, after: _Table, before: _Table | None, route: Route, c: Changed | None) -> Evidence | None:
        """The first changed line of the spec that falls in the operation, or in a schema it reaches (the way the change got there):
        an added line at the head, else a removed line at the pre-change version."""
        fc = next((f for f in change.files if f.path == self.spec_path or f.old_path == self.spec_path), None)
        op = after.ops.get((route.method, route.path))
        if fc is None or op is None or after.spec is None:
            return None
        spans = [(op.start, op.end), *op.shared] + [s for r in op.refs if (s := after.spec.span_of(r)) is not None]
        for n in sorted(dl.number for dl in fc.added_lines):
            if any(a <= n <= b for a, b in spans):
                return self._ev(after.spec, after.sha, n)
        if before is not None and before.spec is not None and c is not None:
            old = c.before
            spans = [(old.start, old.end), *old.shared] + [s for r in old.refs if (s := before.spec.span_of(r)) is not None]
            for n in sorted(dl.number for dl in fc.removed_lines):
                if any(a <= n <= b for a, b in spans):
                    return self._ev(before.spec, before.sha, n)
        return None

    def _input(self, after: _Table, before: _Table, c: Changed) -> FindingInput | None:
        """The `operation-changed` finding input of one changed operation: the method key line at both versions."""
        old_ev = self._ev(before.spec, before.sha, c.before.line, f"operation:{c.method} {c.path}:{c.before.signature}")  # type: ignore[arg-type]
        new_ev = self._ev(after.spec, after.sha, c.after.line, f"operation:{c.method} {c.path}:{c.after.signature}")  # type: ignore[arg-type]
        if old_ev is None or new_ev is None:
            return None
        unknown = "unresolved" in c.why
        return FindingInput(
            "operation-changed", "medium", "medium" if unknown else "high",
            "both versions of the spec were read and the signature of the operation (parameters, request body, responses and the schemas they reach) was compared"
            if not unknown else "both versions of the spec were read, but a reference of the operation points outside the spec, so whether it changed is not known",
            (old_ev, new_ev), ("operation-changed", self.repo, c.method, c.path),
            f"{c.method} {c.path} differs in: {', '.join(c.why)}" + ("" if not unknown else " (a reference points outside the spec and is read as unresolved)"),
        )

    @staticmethod
    def _merge(out: list[Reached], new: Reached) -> list[Reached]:
        for i, r in enumerate(out):
            if r.area.label == new.area.label and r.area.repo == new.area.repo:
                more = tuple(c for c in new.area.chains if c not in r.area.chains)
                out[i] = dataclasses.replace(
                    r, area=dataclasses.replace(r.area, chains=r.area.chains + more), complete=r.complete and new.complete,
                    reasons=tuple(dict.fromkeys(r.reasons + new.reasons)))
                return out
        out.append(new)
        return out
