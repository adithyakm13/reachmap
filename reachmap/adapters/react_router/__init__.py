"""The React Router entry-point adapter: the screens a change reaches.

A screen is what a person opens: a route of a routes file (the files `entry_points.routes_glob` names, JSX `<Route>`
elements and object routes). A page is identified by the import binding its element uses (a static import, or a
`React.lazy(() => import("..."))` declaration), which maps to a file through the same resolver the JavaScript adapter
imports with. One screen per page file: several routes that render the same pages are one screen, labelled with the
smallest route in string order, the others in `also_mounted` with their own chains (import of the page in their routes
file and the `<Route>` line).

The walk (`reachmap.walk`) brings chains that end in a routes file (an import of a page, or of anything a page is made
of, in the routes file) or start in one (a change inside the routes file). `entry_points_reached` turns them into
`Area`s of kind `screen`; `routes` and `routes_changed` read the route table itself. A routes file that does not lex
cleanly claims no route and records a blocking `parse_degraded`; a path that is not a literal is kept as
`<unknown:EXPR>` and recorded (`route_path_partial`, non-blocking), never guessed. Limits are in
docs/briefs/M7-entry-points.md.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, replace
from typing import Any, Iterable, Sequence

from reachmap import config
from reachmap.adapters import Reached, Route, diff_routes
from reachmap.adapters.base import evidence_at, import_claim, lines_to_names, record_quoted, screen_area
from reachmap.adapters.js import JsAdapter
from reachmap.adapters.react_router.routes import Composed, RouteDecl, RoutesParse, compose, parse_routes
from reachmap.model import Evidence, ReadResult

__all__ = ["DEFAULT_ROUTES_GLOBS", "ReactRouterAdapter", "glob_regex"]

DEFAULT_ROUTES_GLOBS = ("src/**/routes.tsx", "src/**/routes.jsx", "src/**/routes.ts", "src/**/routes.js")


def glob_regex(glob: str) -> re.Pattern[str]:
    """A routes glob as a regular expression over repository paths: `*` stays inside one folder, `**` crosses
    folders, `?` is one character, `{a,b}` alternatives. The first `*` is captured as `m` (the product module)."""
    out, i, first = "", 0, True
    while i < len(glob):
        c = glob[i]
        if glob.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif glob.startswith("**", i):
            out += ".*"
            i += 2
        elif c == "*":
            out += "(?P<m>[^/]*)" if first else "[^/]*"
            first = False
            i += 1
        elif c == "?":
            out += "[^/]"
            i += 1
        elif c == "{" and "}" in glob[i:]:
            end = glob.index("}", i)
            out += "(?:" + "|".join(re.escape(a) for a in glob[i + 1:end].split(",")) + ")"
            i = end + 1
        else:
            out += re.escape(c)
            i += 1
    return re.compile("^" + out + "$")


@dataclass(frozen=True, slots=True)
class _Page:
    """One component a route renders that is a file of the repository."""

    local: str
    imported: str  # default | a name | * ; a dynamic import renders the default export
    file: str
    line: int  # the import (or the import() call) in the routes file
    host: int  # index of the route that renders it


@dataclass(slots=True)
class _Info:
    """A leaf route with what it renders."""

    comp: Composed
    pages: list[_Page]  # ancestors first
    route: Route
    key: tuple[Any, ...]  # what makes two routes one screen: the page files of the route's own element


@dataclass(slots=True)
class _File:
    path: str
    parsed: Any
    parse: RoutesParse
    infos: list[_Info]
    complete: bool
    reasons: tuple[str, ...]


class ReactRouterAdapter:
    """React Router entry points of one repository, built on its JavaScript adapter (which reads the files, parses
    them and resolves specifiers). Use one adapter per run, as the language adapters are."""

    kind = "react-router"

    def __init__(self, repo: str, language: JsAdapter, routes_glob: str | Sequence[str] | None = None, *, record_to: Any = None) -> None:
        self.repo = repo
        self.language = language
        globs = (routes_glob,) if isinstance(routes_glob, str) else tuple(routes_glob) if routes_glob else DEFAULT_ROUTES_GLOBS
        self.globs = globs
        self._default = globs == DEFAULT_ROUTES_GLOBS
        self._regexes = [glob_regex(g) for g in globs]
        self._target = record_to
        self._files: dict[tuple[str, str], _File | None] = {}
        self._tables: dict[str, list[_File]] = {}
        self.problems: list[str] = []  # what could not be established (an unreadable routes file, a file that does not lex)

    @classmethod
    def from_config(cls, repo: config.Repo, language: JsAdapter, *, record_to: Any = None) -> "ReactRouterAdapter":
        options = repo.entry_points or {}
        return cls(repo.name, language, options.get("routes_glob"), record_to=record_to)

    @property
    def target(self) -> Any:
        return self._target if self._target is not None else self.language.target

    # ---- which files

    def _match(self, path: str) -> re.Match[str] | None:
        for rx in self._regexes:
            m = rx.match(path)
            if m:
                return m
        return None

    def unmatched(self, sha: str) -> list[tuple[str, str]]:
        """What the routes glob looks for that matches no file of the tree at `sha`: each pattern of an explicit glob (or list), or the default
        globs as one. Nothing when the tree could not be listed (said as a problem: the read layer recorded why)."""
        tree = self.language.tree(sha)
        if not tree:
            self._problem(f"the files of {self.repo} at {sha[:12]} could not be listed: whether the routes glob matches a file was not established")
            return []
        groups = [("src/**/routes.{tsx,jsx,ts,js}", self._regexes)] if self._default else [(g, [rx]) for g, rx in zip(self.globs, self._regexes)]
        return [(what, "no routes file was read, so the screens of the repository were not established; check routes_glob (a path or a glob inside the repository)")
                for what, rxs in groups if not any(rx.match(p) for rx in rxs for p in tree)]

    def routing_files(self, sha: str, what: str) -> list[str]:
        """The files of the tree at `sha` that the pattern `what` (as `unmatched` names it) matches."""
        rxs = self._regexes if self._default else [rx for g, rx in zip(self.globs, self._regexes) if g == what]
        return [p for p in self.language.tree(sha) if any(rx.match(p) for rx in rxs)]

    def is_entry_file(self, path: str, sha: str | None = None) -> bool:
        return self._match(path) is not None

    def routes_files(self, sha: str) -> list[str]:
        return sorted(p for p in self.language.tree(sha) if self._match(p) is not None)

    def module_of(self, path: str) -> str:
        m = self._match(path)
        if m is not None and "m" in m.groupdict() and m.group("m"):
            return m.group("m")
        return posixpath.basename(posixpath.dirname(path))

    # ---- reading a routes file

    def _evidence(self, sha: str, path: str, parsed: Any, line: int, claim: str | None = None) -> Evidence | None:
        ev = evidence_at(self.repo, path, sha, parsed, line)
        return ev if ev is None or claim is None else replace(ev, claim=claim)

    @staticmethod
    def _page_claim(page: _Page) -> str:
        """The claim of the line that imports a page into a routes file: the file and the name taken (a default or namespace import takes the
        whole module, as the language adapter's own edge says)."""
        return import_claim(page.file, None if page.imported in ("default", "*") else (page.imported,))

    @staticmethod
    def _route_claim(comp: Composed, pages: list[_Page], decl: RouteDecl) -> str:
        """The claim of a `<Route>` line: the path it answers (the full path for the route itself, its own path for an ancestor) and the
        files of the components its element renders, not how the tag is written."""
        leaf = decl.index == comp.decl.index
        path = comp.path_full if leaf else (decl.path if decl.path is not None else decl.path_expr or "")
        files = sorted({p.file for p in pages if p.host == decl.index})
        if not files and leaf:
            own = ReactRouterAdapter._own_page(comp, pages)
            files = [own.file] if own is not None else []
        return f"route:{path}:{','.join(files)}"

    def _load(self, sha: str, path: str) -> _File | None:
        """The routes file at `sha`, parsed; None when it could not be read (the git layer recorded it) or is absent."""
        key = (sha, path)
        if key in self._files:
            return self._files[key]
        self._files[key] = None
        res: ReadResult = self.language.file_text(sha, path)
        parsed = self.language.parsed(sha, path) if res.is_found else None
        if parsed is None:
            self._problem(f"{path} could not be read at {sha[:12]}: the screens it mounts were not established")
            if res.is_absent:  # an unreadable file was recorded by the git layer; an absent one is the walk's own surprise
                record_quoted(self.target, "read_unreadable", f"{path} at {sha[:12]}", "the walk reached the file but it is not in the tree at that commit", once=True)
            return None
        parse = parse_routes(parsed)
        if parse.problems:
            record_quoted(self.target, "parse_degraded", path,
                          "no route is claimed from it, so the screens it mounts were not established", parse.problems[0], blocks_selection=True, once=True)
            self._problem(f"{path} did not lex cleanly ({parse.problems[0]}): the screens it mounts were not established")
            file = _File(path, parsed, parse, [], False, (f"{path} did not lex cleanly ({parse.problems[0]})",))
            self._files[key] = file
            return file
        module = self.module_of(path)
        infos: list[_Info] = []
        for comp in compose(parse):
            if not comp.is_leaf:
                continue
            pages = self._pages(sha, path, parse, comp)
            evidence = self._route_evidence(sha, path, parsed, comp, pages)
            if evidence is None:
                continue
            primary = self._own_page(comp, pages)
            route = Route(
                repo=self.repo, kind="screen", method="", path=comp.path_full, file=path, handler=primary.local if primary else "",
                target=primary.file if primary else "", module=module, evidence=evidence, partial=comp.partial,
            )
            own = tuple(p.file for p in pages if p.host == comp.decl.index)
            infos.append(_Info(comp, pages, route, own or tuple(p.file for p in pages) or ("route", path, comp.path_full)))
        file = _File(path, parsed, parse, infos, True, ())
        self._files[key] = file
        return file

    def _problem(self, why: str) -> None:
        if why not in self.problems:
            self.problems.append(why)

    @staticmethod
    def _own_page(comp: Composed, pages: list[_Page]) -> _Page | None:
        """The page of a route: the innermost component of its own element (`<RequireAuth><A /></RequireAuth>` is A)."""
        own = [p for p in pages if p.host == comp.decl.index]
        return own[-1] if own else (pages[-1] if pages else None)

    def _pages(self, sha: str, path: str, parse: RoutesParse, comp: Composed) -> list[_Page]:
        """What the routes of the chain render, as files of the repository (ancestors first). A name that is not an
        import binding or a lazy declaration of the file, or that means no file of the tree (a package such as
        `react`, a component declared in the file itself), is not a page."""
        pages: list[_Page] = []
        by_index = {r.index: r for r in parse.routes}

        def add(page: _Page) -> None:
            if page not in pages:
                pages.append(page)

        for idx in comp.chain:
            decl = by_index[idx]
            for tag in decl.tags:
                first, _, rest = tag.partition(".")
                if first in parse.imports:
                    spec, imported, line = parse.imports[first]
                    if imported == "*" and rest:
                        imported = rest.split(".")[0]  # `<pages.Home />` renders the member Home of the namespace
                elif first in parse.lazies:
                    (spec, line), imported = parse.lazies[first], "default"
                else:
                    continue
                file = self.language.resolve_specifier(path, spec, sha)
                if file is not None and file != path:
                    add(_Page(first, imported, file, line, idx))
            for spec, line in decl.lazy:
                file = self.language.resolve_specifier(path, spec, sha)
                if file is not None and file != path:
                    add(_Page("", "default", file, line, idx))
        return pages

    def _route_evidence(self, sha: str, path: str, parsed: Any, comp: Composed, pages: list[_Page]) -> tuple[Evidence, ...] | None:
        """The route's own evidence: the import of its page and its `<Route>` line."""
        out: list[Evidence] = []
        primary = self._own_page(comp, pages)
        if primary is not None:
            ev = self._evidence(sha, path, parsed, primary.line, self._page_claim(primary))
            if ev is not None:
                out.append(ev)
        line = self._evidence(sha, path, parsed, comp.decl.first_line, self._route_claim(comp, pages, comp.decl))
        if line is None:
            return None
        out.append(line)
        return tuple(out)

    # ---- the route table

    def _table(self, sha: str) -> list[_File]:
        if sha not in self._tables:
            files = [self._load(sha, p) for p in self.routes_files(sha)]
            self._tables[sha] = [f for f in files if f is not None]
        return self._tables[sha]

    def routes(self, repo: str | None, sha: str) -> list[Route]:
        """Every screen route of the repository at `sha`: leaf routes with their full path, page and evidence."""
        if repo is not None and repo != self.repo:
            raise ValueError(f"this adapter reads {self.repo}, not {repo}")
        return [info.route for f in self._table(sha) for info in f.infos]

    def routes_changed(self, before: str | None, after: str) -> tuple[list[Route], list[Route]]:
        """(removed, added) screen routes between two commits, by full path, each with the evidence of its own
        version. Without a pre-change version nothing is computed: removals cannot be (the change set recorded it)."""
        if before is None:
            return [], []
        return diff_routes(self.routes(None, before), self.routes(None, after))

    def structural_names(self, path: str, sha: str) -> frozenset[str]:
        """The exported names of a routes file that only hold its routes (the declaration a `<Route>` sits in): whoever
        imports one of them mounts the routes, which is not a way the change reaches anything else."""
        f = self._load(sha, path)
        if f is None:
            return frozenset()
        names: set[str] = set()
        for d in f.parsed.declarations:
            if any(d.start <= r.first_line <= d.end for r in f.parse.routes):
                names.add(d.name)
                names.update(d.export_names)
        return frozenset(names)

    # ---- areas

    def entry_points_reached(self, chains: Iterable[Any], sha: str | None = None) -> list[Reached]:
        """The screens the chains reach: one `Area` per page, with the chain of Evidence from the change to each route
        that mounts it (the first chain is the primary route's, the others the `also_mounted` routes'). The areas come
        with their routes and `Reached.complete`, False when a chain or the routes file was not established completely;
        what could not be read at all is in `problems`."""
        hits: list[_Hit] = []
        for chain in chains:
            f = self._load(chain.sha, chain.entry)
            if f is None:
                continue  # `_load` said why (`problems`, and the git layer's record): the screens behind it are not "none"
            reasons = tuple(chain.reasons) + f.reasons
            for info, evidence in (self._via_import(chain, f) if chain.edge is not None else self._direct(chain, f)):
                hits.append(_Hit(info, evidence, chain.complete and f.complete, reasons))
        groups: dict[tuple[Any, ...], list[_Hit]] = {}
        for hit in hits:
            groups.setdefault(hit.info.key, []).append(hit)
        out: list[Reached] = []
        for group in groups.values():
            ordered = sorted(group, key=lambda h: (h.info.route.path, h.info.route.file, h.info.comp.decl.first_line))
            unique: list[_Hit] = []
            for h in ordered:  # the same route reached by two chains is one route: the first chain stays
                if not any(u.info.route == h.info.route for u in unique):
                    unique.append(h)
            primary = unique[0].info.route
            for h in unique:
                if h.info.route.partial:  # recorded when a change reaches the route (the other adapter does the same)
                    for expr in h.info.comp.unknown:
                        record_quoted(self.target, "route_path_partial", f"{h.info.route.file}:{h.info.comp.decl.first_line}",
                                      "the route has a path that is not a string literal; it is kept with the part marked and nothing is guessed", expr, once=True)
            area = screen_area(self.repo, [primary] + [h.info.route for h in unique], [h.evidence for h in unique])
            out.append(Reached(area, tuple(h.info.route for h in unique), all(h.complete for h in unique),
                               tuple(dict.fromkeys(r for h in unique for r in h.reasons))))
        out.sort(key=lambda r: (r.area.label, r.routes[0].file))
        return out

    # the routes a chain reaches, each with the evidence from the change to its route line

    @staticmethod
    def _name_ok(page: _Page, names: frozenset[str] | None) -> bool:
        """A named import is narrowed by the names that arrive; a default or namespace import never is."""
        return names is None or page.imported in ("default", "*") or page.imported in names

    def _via_import(self, chain: Any, f: _File) -> list[tuple[_Info, tuple[Evidence, ...]]]:
        """The routes that render a page the chain arrives at: the hops up to the previous file, then the import of
        the page in the routes file and the `<Route>` line (of each route from the one that renders the page down)."""
        target = chain.files[-2]
        by_index = {r.index: r for r in f.parse.routes}
        out = []
        for info in f.infos:
            pages = [p for p in info.pages if p.file == target and self._name_ok(p, chain.names)]
            if pages:
                page = pages[0]
                tail = [self._evidence(chain.sha, f.path, f.parsed, page.line, self._page_claim(page))]
                for idx in info.comp.chain[info.comp.chain.index(page.host):]:
                    tail.append(self._evidence(chain.sha, f.path, f.parsed, by_index[idx].first_line, self._route_claim(info.comp, info.pages, by_index[idx])))
                out.append((info, _dedupe(chain.hops[:-1] + tuple(e for e in tail if e is not None))))
                continue
            # not a page of the previous file: a component or helper of the routes file that the change reaches (a
            # layout declared here, a loader that wraps a lazy import), or, when the whole module is reached (a
            # statement that runs when the file loads), every route of it
            host = next((idx for idx in info.comp.chain if _renders(by_index[idx], chain.reach.locals)), None)
            if host is None and chain.reach.whole:
                host = info.comp.chain[-1]
            if host is None:
                continue
            tail = [self._evidence(chain.sha, f.path, f.parsed, by_index[idx].first_line, self._route_claim(info.comp, info.pages, by_index[idx]))
                    for idx in info.comp.chain[info.comp.chain.index(host):]]
            out.append((info, _dedupe(chain.hops + tuple(e for e in tail if e is not None))))
        return out

    def _direct(self, chain: Any, f: _File) -> list[tuple[_Info, tuple[Evidence, ...]]]:
        """A change inside the routes file: the routes whose own tag (or an ancestor's, or the import of their page)
        holds a changed line; a changed line that belongs to none of them (the wrapper, an unrelated import) reaches
        every route of the file; a removed line that was a whole leaf route of the pre-change file reaches nothing."""
        parsed = f.parsed
        added = sorted({n for s in chain.changed for n in s.added_lines if n in parsed.code_lines})
        removed = sorted({n for s in chain.changed for n in s.removed_lines})
        route_lines: dict[int, set[int]] = {}  # line to the indexes of the routes whose own tag holds it
        for decl in f.parse.routes:
            for n in decl.own_lines:
                route_lines.setdefault(n, set()).add(decl.index)
        binding_lines = _binding_lines(parsed)
        hops: dict[int, Evidence | None] = {}
        for n in added:
            ev = self._evidence(chain.sha, f.path, parsed, n)
            owners, bound = route_lines.get(n, set()), binding_lines.get(n, set())
            for k, info in enumerate(f.infos):
                if not (owners or bound) or owners & set(info.comp.chain) or _uses(info, bound):
                    hops.setdefault(k, ev)
        first = chain.hops[0] if chain.hops else None
        for k, info in enumerate(f.infos):  # what the changed declarations reach inside the file (a layout component declared here)
            if chain.reach.whole or any(_renders(f.parse.routes[idx], chain.reach.locals) for idx in info.comp.chain):
                hops.setdefault(k, first)
        if removed:
            paths, bound, module_level = self._removed_lines(chain, removed)
            for k, info in enumerate(f.infos):
                if module_level or info.route.path in paths or _uses(info, bound):
                    hops.setdefault(k, chain.hops[0] if chain.hops else None)
        out = []
        for k in sorted(hops):
            info = f.infos[k]
            route_line = self._evidence(chain.sha, f.path, parsed, info.comp.decl.first_line, self._route_claim(info.comp, info.pages, info.comp.decl))
            out.append((info, _dedupe(tuple(e for e in (hops[k], route_line) if e is not None))))
        return out

    def _removed_lines(self, chain: Any, removed: Sequence[int]) -> tuple[set[str], set[str], bool]:
        """Where removed lines (lines of the pre-change text) were: (the full paths of the leaf routes that lost a
        line, the bindings of the import statements that lost one, whether a line was somewhere else). A route that lost
        a line and is still there is changed; a route that is gone (`routes_changed` says so) changes nothing for the
        others. Without the pre-change text every removed line counts as somewhere else."""
        if chain.pre_sha is None:
            return set(), set(), True
        before = self._load(chain.pre_sha, chain.before_path)
        if before is None or not before.complete:
            return set(), set(), True
        paths: set[str] = set()
        bound: set[str] = set()
        outside = False
        binding_lines = _binding_lines(before.parsed)
        for n in removed:
            if n not in before.parsed.code_lines:
                continue
            owners = [i for i in before.infos if n in i.comp.decl.own_lines]
            if owners:
                paths.update(i.route.path for i in owners)
            elif n in binding_lines:
                bound |= binding_lines[n]
            else:
                outside = True
        return paths, bound, outside


@dataclass(slots=True)
class _Hit:
    info: _Info
    evidence: tuple[Evidence, ...]
    complete: bool
    reasons: tuple[str, ...]


def _renders(decl: Any, names: Any) -> bool:
    """Whether a route's own element renders a component named in `names` (the first segment of `A.B`)."""
    return any(tag.split(".")[0] in names for tag in decl.tags)


def _binding_lines(parsed: Any) -> dict[int, set[str]]:
    """For each line of an import statement, the local names the statement binds."""
    return lines_to_names((range(start, line + 1), [local]) for local, (start, line) in _import_spans(parsed).items())


def _uses(info: _Info, bound: set[str]) -> bool:
    """Whether a route renders a name that is one of `bound` (by the names in its elements, resolved or not)."""
    return bool(bound) and any(tag.split(".")[0] in bound for tag in info.comp.tags)


def _import_spans(parsed: Any) -> dict[str, tuple[int, int]]:
    """For each binding of an import statement: the first and the last line of the statement."""
    out: dict[str, tuple[int, int]] = {}
    for rec in parsed.imports:
        if rec.kind != "import":
            continue
        for b in rec.bindings:
            out[b.local] = (rec.start_line, rec.line)
    return out


def _dedupe(items: tuple[Evidence, ...]) -> tuple[Evidence, ...]:
    out: list[Evidence] = []
    for e in items:
        if not out or out[-1] != e:
            out.append(e)
    return tuple(out)
