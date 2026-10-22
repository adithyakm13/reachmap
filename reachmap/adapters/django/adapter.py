"""The Django and Django REST Framework entry-point adapter: the API routes a change reaches, with their full mounted paths.

See `reachmap.adapters.django` for the rules; this module is the `EntryPointAdapter` (`DjangoAdapter`) and the question it answers for the
walk: given a chain that ends in a file the table calls an entry file, which routes does the change reach?
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any, Iterable

from reachmap import config
from reachmap.adapters import Reached, Route
from reachmap.adapters.base import import_lines, record_quoted
from reachmap.adapters.django import known
from reachmap.adapters.django.facts import Facts, extract, names_in, refs_in
from reachmap.adapters.django.table import Comp, Table, WHOLE
from reachmap.adapters.python_routes import Gathered, Shared, nearest_folder
from reachmap.errors import ModelError
from reachmap.incomplete import kind_spec, register_kind
from reachmap.model import Evidence

__all__ = ["DjangoAdapter"]

for _kind, _blocking, _template in (
    ("urlconf_root_unknown", True, "Which URLconf is the root of {subject} was not established, because no literal ROOT_URLCONF names a module of the repository"),
    ("view_unresolved", True, "What {subject} runs was not established, because a view there could not be resolved to code of the repository"),
    ("include_external", False, "The URLs behind {subject} were not read, because the include names a module outside the repository"),
    ("route_methods_unknown", False, "The HTTP methods answered by {subject} were not established, because its class has a base outside the repository that is not known"),
    ("urlconf_unmounted", False, "No route was established for the views in {subject}, because nothing includes that URLconf or router"),
    ("settings_not_followed", True, "What the settings in {subject} change for the routes were not established, because only ROOT_URLCONF is followed"),
):
    try:
        kind_spec(_kind)
    except ModelError:
        register_kind(_kind, _template, _blocking)

STRUCTURAL = frozenset({"urlpatterns", "app_name"})


def _attribute_names(node: ast.AST) -> set[str]:
    return {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}


@dataclass(slots=True)
class Ctx:
    """What a chain says about the entry file it ends in: where the change is (`added` lines, `keys` of spans that were written the same
    before, names a changed import binds, an `other` change outside every route and declaration), the names it reaches in the file (`locals`),
    the references an import brings (`arrivals`, for a chain that arrives by an import), the members of each class that the change touches."""

    entry: str
    direct: bool
    locals: frozenset[str]
    arrivals: frozenset[str]
    added: frozenset[int]
    keys: frozenset[str]
    imported: frozenset[str]
    other: bool
    changed: dict[str, Any]

    def hit(self, node: ast.AST, exclude: Iterable[str] = ()) -> bool:
        skip = set(exclude)
        if self.direct:
            return bool(names_in(node) & ((self.locals | self.imported) - skip))
        return bool(refs_in(node) & (self.arrivals - skip))


def _bindings(parsed: Any) -> dict[str, tuple[Any, ...]]:
    """What each name an import statement binds stands for: (kind, module, level, name). Two statements that bind a name to the same thing say
    the same."""
    out: dict[str, tuple[Any, ...]] = {}
    for rec in getattr(parsed, "imports", ()):
        if rec.kind in ("import", "from") and rec.names:
            for n in rec.names:
                out[n.asname or n.name.split(".")[0]] = (rec.kind, rec.module, rec.level, n.name)
    return out


class DjangoAdapter(Shared):
    """Django entry points of one repository, built on its Python adapter (which reads the files, parses them and resolves modules). Use one
    adapter per run, as the language adapters are. The table per commit, `routes`, `routes_changed` and the areas of a change are `Shared` with the
    FastAPI adapter; the words of the unmounted claim and the order of the reasons are Django's."""

    kind = "django"
    TABLE = Table
    HEAD_REASONS_FIRST = True
    UNMOUNTED_KIND = "urlconf_unmounted"
    UNMOUNTED_DETAIL = "no route is claimed for what it holds: no include carries it up to the root URLconf (an include in a comment is not an include)"

    def __init__(self, repo: str, language: Any, *, record_to: Any = None, root_urlconf: str | None = None, settings: str | None = None) -> None:
        super().__init__(repo, language, record_to=record_to)
        self.root_urlconf = root_urlconf
        self.settings = settings
        self._imported: dict[int, frozenset[str]] = {}  # per chain: the names a changed import statement bound that the change reaches

    @classmethod
    def from_config(cls, repo: config.Repo, language: Any, *, record_to: Any = None) -> "DjangoAdapter":
        options = repo.entry_points or {}
        return cls(repo.name, language, record_to=record_to, root_urlconf=options.get("root_urlconf"), settings=options.get("settings"))

    def module_of(self, path: str, sha: str) -> str:
        """The app a file belongs to: the nearest folder that holds an `apps.py`, else the folder at the top of its path."""
        return nearest_folder(self.language.tree(sha), path, "apps.py") or (path.split("/")[0] if "/" in path else "")

    # ---- what the walk asks

    def structural_names(self, path: str, sha: str) -> frozenset[str]:
        """The names of a file that only wire it up (the views routes name, `urlpatterns`, `app_name`, routers, settings): whoever imports one of
        them reaches nothing the route table does not know already."""
        table = self._table(sha)
        out = {t for t in table.handler_tops.get(path, ()) if not self._called_elsewhere(table, path, sha, t)}
        facts = table.facts_of(path)
        if facts is not None:
            if facts.has_patterns or facts.routers:
                out |= STRUCTURAL | set(facts.routers)
            if path in table.settings:
                out |= facts.declared
        return frozenset(out)

    def _called_elsewhere(self, table: Table, path: str, sha: str, name: str) -> bool:
        """Whether a module that is neither a URLconf nor a test imports `name` from the file (a view that another view calls): the walk then goes on
        past the name, and what it reaches there is reached. The URLconfs that mount the view are the route table's. An importer list that is not whole
        cannot say it is not: the walk goes on (and says what it could not read)."""
        key = (path, name)
        if key not in table.called_elsewhere:
            res = self.language.importers(path, sha)
            found = not res.complete
            for importer in sorted({e.importer_path for e in res.edges}):
                facts = table.facts_of(importer)
                if self.language.is_test_path(importer) or (facts is not None and (facts.has_patterns or facts.routers)):
                    continue
                if self.language.survivors(importer, [e for e in res.edges if e.importer_path == importer], sha, frozenset({name})):
                    found = True
                    break
            table.called_elsewhere[key] = found
        return table.called_elsewhere[key]

    def unmounted(self, sha: str) -> list[tuple[str, str]]:
        """The units (a URLconf as (file, `urlpatterns`), a router as (file, variable)) nothing mounts up to the root URLconf."""
        return self._table(sha).unmounted()

    def url_names(self, sha: str) -> dict[str, list[Route]]:
        """The routes by their URL name, with the namespaces of the includes on the way (`app:name`)."""
        return self._table(sha).url_names()

    # ---- what a chain reaches

    def entry_points_reached(self, chains: Iterable[Any], sha: str | None = None) -> list[Reached]:
        """The API routes the chains reach: one `Area` per route, with the chain of Evidence from the change to the view (the imports on the way, or
        the changed line) followed by the route's own evidence, and whether it was established completely. What could not be established at all (a
        URL table that is not whole) is in `problems`."""
        chains_seen = list(chains)
        gathered = Gathered()
        for chain in chains_seen:
            table = self._table(chain.sha)
            for comp, why in self._reached(table, chain):
                if not comp.app:
                    gathered.unmounted_at(comp.top)
                    continue
                route = comp.route
                hops = self._direct_hop(chain, comp, why) if chain.direct else chain.hops
                gathered.add(chain, table, route, tuple([h for h in hops if h not in route.evidence] + list(route.evidence)), comp=comp)

        def partial(entry: dict[str, Any]) -> None:
            comp: Comp = entry["comp"]
            for text, file, line in comp.notes:
                record_quoted(self.target, "route_path_partial", f"{file}:{line}",
                              "a part of the route is not a string literal or a constant, or it is a regular expression: it is kept as a pattern with the part marked and nothing is guessed", text)
            if comp.methods_unknown:
                record_quoted(self.target, "route_methods_unknown", f"{comp.handler.file}:{comp.view[1] if comp.view else comp.handler.top}",
                              "the class has a base outside the repository that is not known, so the methods it answers may be more than the ones listed", "", once=True)

        return self.settle(gathered, chains_seen, partial)

    def _reached(self, table: Table, chain: Any) -> list[tuple[Comp, str]]:
        """The routes a chain reaches, each with why (`settings`, `whole`, `view`, `route`, `import`, `other`, `namespace`): the way the first hop is cited."""
        entry = chain.entry
        if entry in table.settings:
            return [(c, "settings") for c in self._settings(table, chain)]
        comps = [c for c in table.composed() if entry in c.files]
        if not comps:
            return []
        if chain.reach.whole:  # a statement that runs when the file loads: every route in or through it
            return [(c, "whole") for c in comps]
        ctx = self._context(table, chain)
        self._imported[id(chain)] = ctx.imported
        memo: dict[tuple[str, str], Any] = {}
        hits = [(c, self._hit(table, c, ctx, memo)) for c in comps]
        return [(c, why) for c, why in hits if why]

    def _settings(self, table: Table, chain: Any) -> list[Comp]:
        """A settings file: a change to `ROOT_URLCONF` moves every route; a change to anything else is not followed (a middleware, an installed app
        or a flag may change what every route does), and says so with a blocking record."""
        names = {s.name.split(".")[0] for s in chain.changed if not s.is_module}
        root = "ROOT_URLCONF" in names or (chain.edge is not None and "ROOT_URLCONF" in chain.reach.locals)
        if chain.edge is not None or any(s.is_module for s in chain.changed) or names - {"ROOT_URLCONF"}:
            self.problem(f"{chain.entry} is a settings file: only ROOT_URLCONF is followed, so what its other settings change for the routes is not known")
            record_quoted(self.target, "settings_not_followed", chain.entry,
                          "only a change to ROOT_URLCONF is followed to the routes; the other settings (middleware, installed apps, flags) may change what every route does", "", once=True)
        return [c for c in table.composed() if c.app] if root else []

    def _context(self, table: Table, chain: Any) -> Ctx:
        reach = chain.reach
        entry = chain.entry
        facts = table.facts_of(entry)
        direct = chain.edge is None
        arrivals: set[str] = set()
        if not direct:
            plain = set(reach.locals)
            for b in chain.edge.bindings:
                plain.discard(b.local)
                if b.imported == "*" or "." in b.imported:  # a module: used as `module.name`
                    root = b.imported if "." in b.imported else b.local
                    arrivals |= {root} if chain.names is None else {f"{root}.{n}" for n in chain.names}
                elif chain.names is None or b.imported in chain.names:
                    arrivals.add(b.local)
            arrivals |= plain
        added: set[int] = set()
        keys: set[str] = set()
        imported: set[str] = set()
        other = False
        changed: dict[str, Any] = {}
        if direct and facts is not None:
            added = {n for s in chain.changed for n in s.added_lines}
            imported, other, keys = self._line_changes(chain, facts)
            for s in chain.changed:
                if s.is_module:
                    continue
                top, _dot, rest = s.name.partition(".")
                if not rest:
                    changed[top] = WHOLE  # the class itself (added, removed, its decorator or bases)
                elif s.kind == "function" and "." not in rest:
                    if changed.get(top, frozenset()) is not WHOLE:
                        changed[top] = frozenset(changed.get(top, frozenset())) | {rest}
                else:
                    changed[top] = WHOLE  # a class attribute or a nested class
        return Ctx(entry, direct, frozenset(reach.locals), frozenset(arrivals), frozenset(added), frozenset(keys), frozenset(imported), other, changed)

    def _line_changes(self, chain: Any, facts: Facts) -> tuple[set[str], bool, set[str]]:
        """What the changed lines outside every view and class touch: (the names whose import binding was added, removed or re-targeted, whether some
        line is code that no route owns, the keys of the routes' own spans that were written the same before the change and so are still there).
        A statement that is edited binds the same names to the same things as before for most of them: only the ones that changed are reached."""
        touched: set[str] = set()
        other = False
        keys: set[str] = set()
        new_keys = {sp.key for sp in facts.spans}
        old: Facts | None = None
        old_parsed = self.language.parsed(chain.pre_sha, chain.before_path) if chain.pre_sha else None
        if old_parsed is not None and not old_parsed.problems:
            old = extract(chain.before_path, old_parsed)
        for s in chain.changed:
            for lines, parsed, spans, side in ((s.added_lines, facts.parsed, facts.spans, "after"), (s.removed_lines, old_parsed, old.spans if old else [], "before")):
                if not lines:
                    continue
                if parsed is None or parsed.problems:
                    other = True  # a removed line with no pre-change text to place it in: it could have been anything
                    continue
                imports = import_lines(parsed)
                for n in lines:
                    covering = [sp for sp in spans if sp.covers(n)]
                    if covering:
                        if side == "before":
                            keys |= {sp.key for sp in covering if sp.key in new_keys}  # a route written the same is still there: it is reached
                        continue
                    if n in imports:
                        touched |= imports[n]
                    elif n in parsed.code_lines and (s.is_module or self._is_pattern_list(parsed, n)):
                        other = True
        if old is None:
            return touched, other, keys  # no pre-change text to compare with: every name the statements bind
        before, after = _bindings(old.parsed), _bindings(facts.parsed)
        return {name for name in touched if before.get(name) != after.get(name)}, other, keys

    @staticmethod
    def _is_pattern_list(parsed: Any, line: int) -> bool:
        """Whether a line sits in a declaration of `urlpatterns` (or another variable the patterns are added to) outside every route in it."""
        decl = parsed.placed(line)
        return decl is not None and "urlpatterns" in decl.names

    # ---- is a route reached

    def _hit(self, table: Table, c: Comp, ctx: Ctx, memo: dict[tuple[str, str], Any]) -> str:
        """Why the change reaches the route ('' when it does not): `view` (its code), `import` (a name a changed import statement binds that its view uses),
        `route` (a line that says the route), `namespace`, `other` (a change no route or declaration owns)."""
        entry = ctx.entry
        h = c.handler
        if c.view is not None:
            if any(f == entry for f in (c.view[0], h.file)) or any(a[0] == entry for a in table.ancestors(*c.view)):
                reached = self.members_reached(table, c.view, ctx, memo)
                if reached is WHOLE or (h.member is not None and h.member in reached):
                    return "import" if ctx.imported and not ctx.locals and not ctx.changed else "view"
        elif h.file == entry and h.top in ctx.locals:
            return "view"
        elif h.file == entry and ctx.imported and self._uses(table, h, ctx.imported):
            return "import"
        for sp in c.spans:
            if sp.file != entry:
                continue
            if ctx.direct:
                if any(sp.covers(n) for n in ctx.added) or sp.key in ctx.keys or sp.names & (ctx.locals - STRUCTURAL):
                    return "route"
                if sp.names & ctx.imported:
                    return "import"
            elif sp.refs & ctx.arrivals:
                return "route"
        if ctx.direct and "app_name" in ctx.locals and any(sp.file == entry for sp in c.spans):
            return "namespace"  # a namespace changed: every name in the URLconf changed with it
        return "other" if ctx.other else ""

    @staticmethod
    def _uses(table: Table, h: Any, names: frozenset[str]) -> bool:
        """Whether the declaration a function view is spells one of the names (a changed import binds them)."""
        facts = table.facts_of(h.file)
        decl = next((d for d in facts.parsed.declarations if d.name == h.top), None) if facts is not None else None
        return decl is not None and bool(facts.parsed.decl_references(decl) & names)

    def members_reached(self, table: Table, view: tuple[str, str], ctx: Ctx, memo: dict[tuple[str, str], Any]) -> Any:
        """The members of a class view that a change reaches: WHOLE (None) or the set of members (the methods that answer, the actions, and the
        private helpers they call). A change to the class itself, to a class attribute, to a base class's attribute, or to a public method that
        is not one that answers (the framework may call it) reaches the whole class; a change to one method reaches it and the members that call
        it; what a base class of the repository reaches, a class that inherits it and does not override it reaches."""
        if view in memo:
            return memo[view]
        memo[view] = WHOLE  # a cycle of bases is the whole class
        info = table.class_info(*view)
        if info is None:
            return WHOLE
        own: set[str] = set()
        if info.file == ctx.entry:
            change = ctx.changed.get(info.name)
            if info.name in ctx.changed and change is WHOLE:
                return WHOLE
            own |= set(change or ())
            inherited = {b.name for b in info.bases if b.kind == "repo"}  # reached through what they reach, not through their name
            for deco in info.node.decorator_list + info.node.bases + [k.value for k in info.node.keywords]:
                if ctx.hit(deco, {info.name, *inherited}):
                    return WHOLE
            for stmt in info.node.body:
                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if ctx.hit(stmt, (info.name,)):
                        own.add(stmt.name)
                elif not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant)) and ctx.hit(stmt, (info.name,)):
                    return WHOLE
        for base in info.bases:
            if base.kind != "repo":
                continue
            sub = self.members_reached(table, (base.file, base.name), ctx, memo)
            if sub is WHOLE:
                return WHOLE
            own |= {m for m in sub if m not in info.members}
        closure = set(own)
        grown = True
        while grown:
            grown = False
            for name, node in info.members.items():
                if name not in closure and _attribute_names(node) & closure:
                    closure.add(name)
                    grown = True
        answers = set(known.HTTP_NAMES) | set(known.ACTIONS) | {a.name for _c, a in table.viewset(*view)[1]}
        for m in closure & set(info.members):
            private = m.startswith("_") and not (m.startswith("__") and m.endswith("__"))
            if not private and m.lower() not in answers and m not in answers:
                return WHOLE  # the framework may call it from any method
        memo[view] = frozenset(closure)
        return memo[view]

    def _direct_hop(self, chain: Any, comp: Comp, why: str = "") -> tuple[Evidence, ...]:
        """For a change inside the file: the changed line within the view when there is one; none when the changed line is on the route itself (the
        route's evidence cites it); for a route reached through an import statement, that statement; else the first changed line."""
        h = comp.handler
        lines = [e for s in chain.changed for e in s.evidence if e.ref == chain.sha and e.path == chain.entry]
        if why == "import":
            facts = self._table(chain.sha).facts_of(chain.entry)
            statements = import_lines(facts.parsed) if facts is not None else {}
            names = self._imported.get(id(chain), frozenset())
            imports = [e for e in lines if statements.get(e.line, set()) & names]
            if imports:
                return tuple(imports[:1])
        inside = [e for e in lines if h.file == chain.entry and h.start <= e.line <= h.end]
        on_route = [e for e in lines if any(sp.file == chain.entry and sp.covers(e.line) for sp in comp.spans)]
        if on_route and not inside:
            return ()  # the changed line is part of what says the route: the route's own evidence cites it
        return tuple((inside or lines or list(chain.hops))[:1])
