"""The Spring Boot entry-point adapter: the API routes a change reaches, with their full paths (context path, class path, method path).

A route is a handler method of a controller (`table.py`); the evidence of a route is the mapping annotation of the method, the lines that
gave its path a constant or a property, the class level mapping and the application configuration line of the context path. What a change
reaches is read by member, not by class: a change inside one handler reaches the routes of that handler; a change to a service reaches the
handlers that spell what changed (the language adapter, `reachmap.adapters.jvm`, narrows by member); a change to the class header of a
controller (its annotations, its supertypes), to a constructor or an initializer reaches every route of the class.

A removed route is a removal only when the table of the head is whole, an added route only when the table of the base is: a controller that
cannot be read or does not parse is a file whose routes were not read, not routes that are gone (`routes_changed`).

What a change to the application configuration reaches: the context path and the other keys that make a path (`spring.mvc.servlet.path`,
a key a mapping names in a placeholder) reach every route they are part of; any other key, and any class that wires the application (a
`@Configuration`, a `@ControllerAdvice`, an aspect, a filter, an interceptor, a security configuration, a `@Bean` method) is not followed:
a blocking `config_not_followed`, never an empty answer.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import replace
from typing import Any, Iterable

from reachmap import config
from reachmap.adapters import Reached, Route, diff_routes, guard_route_changes
from reachmap.adapters.base import api_area, dedupe, direct_hop, evidence_at, record_quoted
from reachmap.adapters.jvm import JvmAdapter, is_jvm_path
from reachmap.adapters.spring import records  # noqa: F401  (registers the incompleteness kinds of the adapter)
from reachmap.adapters.spring.props import PATH_KEYS, read_config
from reachmap.adapters.spring.table import CONFIG_NAME, Composed, Table
from reachmap.incomplete import record
from reachmap.model import Evidence

__all__ = ["SpringAdapter"]

ADVICE_ANNOTATIONS = frozenset({"ControllerAdvice", "RestControllerAdvice"})  # reach the routes they apply to (`_advice_reach`)
WIRING_ANNOTATIONS = frozenset({
    "Configuration", "Aspect", "EnableWebSecurity", "EnableMethodSecurity", "EnableGlobalMethodSecurity", "EnableWebMvc", "EnableWebFlux", "SpringBootApplication",
    "SpringBootConfiguration", "AutoConfiguration", "ComponentScan", "EnableAutoConfiguration", "ConfigurationProperties", "EnableAspectJAutoProxy",
})
WIRING_SUPERTYPES = frozenset({
    "OncePerRequestFilter", "Filter", "GenericFilterBean", "HandlerInterceptor", "AsyncHandlerInterceptor", "WebMvcConfigurer", "WebMvcConfigurationSupport",
    "WebSecurityConfigurerAdapter", "SecurityFilterChain", "HandlerMethodArgumentResolver", "ResponseBodyAdvice", "RequestBodyAdvice", "WebFilter",
    "CommandLineRunner", "ApplicationRunner", "ErrorController",
})


class _Lines:
    """A text that gives lines to `evidence_at`."""

    def __init__(self, text: str) -> None:
        self.lines = text.split("\n")

    def line_text(self, number: int) -> str:
        return self.lines[number - 1].rstrip("\r") if 1 <= number <= len(self.lines) else ""


class SpringAdapter:
    """Spring entry points of one repository, built on its JVM adapter (which reads, parses and resolves). One adapter per run."""

    kind = "spring"

    def __init__(self, repo: str, language: JvmAdapter, *, context_path: str | None = None, record_to: Any = None) -> None:
        self.repo = repo
        self.language = language
        self.context_path = context_path
        self._target = record_to
        self._tables: dict[str, Table] = {}
        self.problems: list[str] = []

    def problem(self, why: str) -> None:
        if why not in self.problems:
            self.problems.append(why)

    @classmethod
    def from_config(cls, repo: config.Repo, language: JvmAdapter, *, record_to: Any = None) -> "SpringAdapter":
        options = repo.entry_points or {}
        return cls(repo.name, language, context_path=options.get("context_path"), record_to=record_to)

    @property
    def target(self) -> Any:
        return self._target if self._target is not None else self.language.target

    def _table(self, sha: str) -> Table:
        if sha not in self._tables:
            self._tables[sha] = Table(self, sha)
        return self._tables[sha]

    # ---- what the walk asks

    def unmatched(self, sha: str) -> list[tuple[str, str]]:
        """The search for controllers when the table was read whole and no file declares a controller or a mapping. A search that was cut or
        failed is the table's incompleteness, not an absence."""
        table = self._table(sha)
        if not table.complete or table.entry_files:
            return []
        return [("@Controller or a request mapping", "no file declares a controller or a mapping, so the routes of the repository were not established")]

    def routing_files(self, sha: str, what: str) -> list[str]:
        return list(self._table(sha).entry_files)

    def is_entry_file(self, path: str, sha: str) -> bool:
        """A file that declares a controller or a type whose mappings a controller inherits."""
        return path in self._table(sha).entry_files

    def structural_names(self, path: str, sha: str) -> frozenset[str]:
        return frozenset()  # nothing a controller file exports only wires it up: whoever imports one of its types uses it

    def routes(self, repo: str | None, sha: str) -> list[Route]:
        if repo is not None and repo != self.repo:
            raise ValueError(f"this adapter reads {self.repo}, not {repo}")
        return self._table(sha).routes()

    def routes_changed(self, before: str | None, after: str) -> tuple[list[Route], list[Route]]:
        """(removed, added) routes between two commits by method and full path, each with the evidence of its own version. Without a
        pre-change version nothing is computed: removals cannot be (the change set recorded it). A removal is not claimed from a head table
        that is not whole (a controller that does not parse or cannot be read: its routes were not read, they were not deleted), nor an
        addition against a base table that is not whole."""
        if before is None:
            return [], []
        old, new = self._table(before), self._table(after)
        removed, added = diff_routes(old.routes(), new.routes())
        why = "; ".join(([] if new.complete else new.reasons) + ([] if old.complete else old.reasons))[:300]
        # only the routes a hole may hide are withheld: one in a file that was not read (or that names its controller), and every route when
        # the hole is anywhere (a search that failed, a package that could not be listed)
        return guard_route_changes(self.target, self.repo, removed, added, base_ok=True, head_ok=True, why=why,
                                   unsure_removed=new.hole_covers, unsure_added=old.hole_covers)

    withholds_unread = True  # `routes_changed` guards itself: the http edge does not guard it again

    def routes_complete(self, sha: str) -> tuple[bool, list[str]]:
        """Whether the route table at `sha` was read completely, and why not."""
        table = self._table(sha)
        return table.complete, list(table.reasons)

    # ---- what a chain reaches

    def entry_points_reached(self, chains: Iterable[Any], sha: str | None = None) -> list[Reached]:
        """The API routes the chains reach: one `Area` per route, with the chain of Evidence from the change to the handler (the imports on
        the way, or the changed line) followed by the route's own evidence, and whether it was established completely."""
        found: dict[tuple[Any, ...], dict[str, Any]] = {}
        order: list[tuple[Any, ...]] = []
        for chain in chains:
            table = self._table(chain.sha)
            for comp in self._reached(table, chain):
                route = comp.route
                key = (route.method, route.path, route.file, route.handler, route.evidence)
                hops = self._direct_hop(chain, comp) if chain.direct else chain.hops
                evidence = tuple(dedupe(list(hops) + list(route.evidence)))
                entry = found.setdefault(key, {"route": route, "chains": [], "sources": [], "complete": True, "reasons": [], "unknown": comp.unknown})
                if evidence not in entry["chains"]:
                    entry["chains"].append(evidence)
                    entry["sources"].append(chain)
                if not (chain.complete and table.complete):
                    entry["complete"] = False
                    entry["reasons"] += [r for r in tuple(chain.reasons) + tuple(table.reasons) if r not in entry["reasons"]]
                if key not in order:
                    order.append(key)
        out: list[Reached] = []
        for key in order:
            e = found[key]
            route: Route = e["route"]
            for expr, path, line in e["unknown"]:
                record_quoted(self.target, "route_path_partial", f"{path}:{line}",
                              "a part of the route is not a string literal or a constant of the project; it is kept with the part marked and nothing is guessed", expr)
            out.append(Reached(api_area(self.repo, route, e["chains"]), (route,), e["complete"], tuple(e["reasons"]), tuple(e["sources"])))
        out.sort(key=lambda r: (r.area.label, r.routes[0].file))
        return out

    def _reached(self, table: Table, chain: Any) -> list[Composed]:
        facts = table.facts_of(chain.entry)
        if facts is None:
            return []
        if chain.direct:
            reach = self.language.reach_of_change(chain.entry, chain.sha, chain.pre_sha, chain.changed)
        else:
            reach = chain.reach
        whole = reach.whole or not reach.complete
        locals_ = set(reach.locals)
        spans = [(u.start, u.end) for name in locals_ for u in facts.parsed.unit_of(name)]
        out: list[Composed] = []
        for c in table.composed():
            here = [e for e in c.route.evidence if e.path == chain.entry]
            if c.route.file != chain.entry and not here:
                continue  # the route neither runs in the changed file nor cites a line of it
            hit = whole or (c.route.file == chain.entry and c.handler.runs.dotted in locals_) or any(a <= e.line <= b for e in here for a, b in spans)
            if hit:
                out.append(c)
        return out

    def _direct_hop(self, chain: Any, comp: Composed) -> tuple[Evidence, ...]:
        h = comp.handler
        inside = (h.runs.start, h.runs.end) if h.runs_path == chain.entry else None
        return direct_hop(chain, inside, [(e.path, e.line) for e in comp.route.evidence])

    # ---- the configuration and the wiring: files no chain of the walk reaches

    def reached_by_files(self, change: Any, reached: list[Reached]) -> tuple[list[Reached], list[Any]]:
        """What a change to the application configuration reaches: every route when a key that makes a path changed; a record for any other
        key, and for a class that wires the application (not followed). Added to what the walk reached."""
        extra: list[Reached] = []
        have = {(r.routes[0].method, r.routes[0].path) for r in reached if r.routes}
        for fc in change.files:
            path = fc.path
            if self.language.is_test_path(path):
                continue
            if CONFIG_NAME.match(posixpath.basename(path)):
                for r in self._config_change(change, fc):
                    k = (r.routes[0].method, r.routes[0].path)
                    if k not in have:
                        have.add(k)
                        extra.append(r)
            elif is_jvm_path(path):
                for r in self._wiring_change(change, fc):
                    k = (r.routes[0].method, r.routes[0].path)
                    if k not in have:
                        have.add(k)
                        extra.append(r)
        extra.sort(key=lambda r: (r.area.label, r.routes[0].file))
        return list(reached) + extra, []

    def _config_text(self, sha: str | None, path: str) -> str | None:
        if sha is None:
            return None
        read = self.language.file_text(sha, path)
        return read.text if read.is_found else None

    def _config_change(self, change: Any, fc: Any) -> list[Reached]:
        head, pre = change.head_sha, change.pre_change_sha
        new_text = self._config_text(head, fc.path) if fc.status != "deleted" else None
        old_path = fc.old_path if fc.status in ("renamed", "copied") and fc.old_path else fc.path
        old_text = self._config_text(pre, old_path) if fc.status != "added" and pre is not None else None
        if (fc.status != "deleted" and new_text is None) or (fc.status != "added" and pre is not None and old_text is None):
            self.problem(f"{fc.path} could not be read: what a change to it reaches was not established")
            return []
        after = read_config(fc.path, new_text) if new_text is not None else None
        before = read_config(old_path, old_text) if old_text is not None else None
        added = {dl.number for dl in fc.added_lines}
        removed = {dl.number for dl in fc.removed_lines}
        changed: dict[str, tuple[str, int]] = {}  # key to (side, line)
        unknown = False
        for side, cfg, lines in (("after", after, added), ("before", before, removed)):
            if cfg is None:
                continue
            by_line = {e.line: e for e in cfg.entries}
            skipped = {int(m.group(1)) for _p, why in cfg.unread if (m := re.match(r"line (\d+):", why))}
            for n in sorted(lines):
                if n in by_line:
                    changed.setdefault(by_line[n].key, (side, n))
                elif n in skipped:
                    unknown = True
        table_after = self._table(head)
        table_after.composed()
        keys_used = set(PATH_KEYS) | table_after.keys_used
        if pre is not None:
            before_table = self._table(pre)
            before_table.composed()
            keys_used |= before_table.keys_used
        relevant = [k for k in changed if k in keys_used]
        other = [k for k in changed if k not in keys_used]
        out: list[Reached] = []
        if relevant:
            side, line = changed[sorted(relevant)[0]]
            sha, p, text = (head, fc.path, new_text) if side == "after" else (pre, old_path, old_text)
            ev = evidence_at(self.repo, p, sha, _Lines(text or ""), line) if sha is not None else None
            if ev is None:
                self.problem(f"{fc.path}: the changed line of {relevant[0]} could not be cited")
            else:
                moved = set(relevant)
                before_handlers = {(c.route.file, c.route.handler) for c in self._table(pre).composed() if moved & set(c.keys)} if pre is not None else set()
                for c in table_after.composed():
                    if not (moved & set(c.keys)) and (c.route.file, c.route.handler) not in before_handlers:
                        continue  # a route whose path this key is no part of
                    r = c.route
                    chain = r.evidence if ev in r.evidence else (ev, *r.evidence)  # the changed line is where the path comes from: a line of the route's own evidence
                    out.append(Reached(api_area(self.repo, r, [chain]), (r,), table_after.complete, tuple(table_after.reasons), ()))
                    for expr, p2, line2 in c.unknown:
                        record_quoted(self.target, "route_path_partial", f"{p2}:{line2}", "a part of the route is not a string literal or a constant of the project; it is kept with the part marked and nothing is guessed", expr)
        if other or unknown:
            self.problem(f"{fc.path}: a key that does not make a path changed; what it reaches is not followed")
            record_quoted(self.target, "config_not_followed", fc.path, "only the keys that make the path of a route are followed in the application configuration",
                          ", ".join(sorted(other)[:3]) if other else "", blocks_selection=True, once=True)
        return out

    def _wiring_change(self, change: Any, fc: Any) -> list[Reached]:
        """A change inside a class that advises controllers reaches the routes it applies to; one inside a class that wires the application
        (not followed) is a blocking record."""
        head, pre = change.head_sha, change.pre_change_sha
        old_path = fc.old_path if fc.status in ("renamed", "copied") and fc.old_path else fc.path
        out: list[Reached] = []
        for sha, p, lines in ((head if fc.status != "deleted" else None, fc.path, {dl.number for dl in fc.added_lines}),
                              (pre if fc.status != "added" else None, old_path, {dl.number for dl in fc.removed_lines})):
            if sha is None or not lines:
                continue
            parsed = self.language.parsed(sha, p)
            if parsed is None or parsed.problems:
                continue
            for u in parsed.units():
                if not u.is_type:
                    continue
                touched = sorted(n for n in lines if u.start <= n <= u.end)
                if not touched:
                    continue
                if any(a.name in ADVICE_ANNOTATIONS for a in u.annotations):
                    out += self._advice_reach(change, sha, p, parsed, u, touched[0])
                elif self._is_wiring(parsed, u):
                    self.problem(f"{fc.path}: a class that wires the application changed; what it reaches is not followed")
                    record_quoted(self.target, "config_not_followed", fc.path, "beans, filters, interceptors and security configuration are not followed", u.name,
                                  blocks_selection=True, once=True)
                    return out
        return out

    def _advice_reach(self, change: Any, sha: str, path: str, parsed: Any, unit: Any, line: int) -> list[Reached]:
        """The routes a `@ControllerAdvice` class applies to, as areas whose chain is the changed line and then the route's own evidence. With no
        scope it applies to every controller; `basePackages` (or `value`) and `assignableTypes` narrow it; any other selector is not read, and the
        advice then applies to every route (an advice that may apply is affected)."""
        table = self._table(change.head_sha)
        ev = evidence_at(self.repo, path, sha, parsed, line)
        if ev is None:
            self.problem(f"{path}: the changed line of an advice could not be cited")
            return []
        ann = next(a for a in unit.annotations if a.name in ADVICE_ANNOTATIONS)
        packages: list[str] = []
        types: list[str] = []
        everything = False
        for i, (name, a, b) in enumerate(ann.args):
            if name in ("value", "basePackages") or (name is None and i == 0):
                values = table.ev.values(path, parsed, unit, a, b)
                if any(v.unknown for v in values):
                    everything = True
                packages += [v.value for v in values]
            elif name == "assignableTypes":
                for x, y in table._items(parsed, a, b):
                    first = next((t.value for t in parsed.code[x:y] if t.kind == "ident"), None)
                    if first is None:
                        everything = True
                    else:
                        types.append(first)
            else:
                everything = True
        out: list[Reached] = []
        for c in table.composed():
            if not (everything or not (packages or types) or self._advised(table, c, packages, types)):
                continue
            r = c.route
            chain = r.evidence if ev in r.evidence else (ev, *r.evidence)
            out.append(Reached(api_area(self.repo, r, [chain]), (r,), table.complete, tuple(table.reasons), ()))
            for expr, p2, line2 in c.unknown:
                record_quoted(self.target, "route_path_partial", f"{p2}:{line2}", "a part of the route is not a string literal or a constant of the project; it is kept with the part marked and nothing is guessed", expr)
        return out

    def _advised(self, table: Table, comp: Composed, packages: list[str], types: list[str]) -> bool:
        facts = table.facts.get(comp.route.file)
        if facts is None:
            return True  # not read: it may be advised
        package = facts.parsed.package or ""
        if any(package == p or package.startswith(p + ".") for p in packages if p):
            return True
        unit = next((t.unit for t in facts.types if t.unit.dotted == comp.controller), None)
        names = {comp.controller.split(".")[0]}
        if unit is not None:
            names |= {st.name for st in self.language.supers_of(table.sha, comp.route.file, facts.parsed, unit)}
        return bool(names & set(types))

    @staticmethod
    def _is_wiring(parsed: Any, unit: Any) -> bool:
        if any(a.name in WIRING_ANNOTATIONS for a in unit.annotations):
            return True
        if any(s.name in WIRING_SUPERTYPES for s in unit.supertypes):
            return True
        return any(c.kind in ("method", "function") and any(a.name == "Bean" for a in c.annotations) for c in unit.children)
