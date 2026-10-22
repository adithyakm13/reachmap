"""The Next.js entry-point adapter: the screens and API routes a change reaches, read from the folder tree.

Next.js has no routes file. The path of a file is its route (`routes.py`): `app/**/page.tsx` and `pages/**` are screens,
`app/**/route.ts` and `pages/api/**` are API routes. The walk (`reachmap.walk`) is the one React Router and FastAPI use: it
brings chains that end in a routing file (an import of changed code in a page, a layout or a handler) or start in one (a
change inside it). `entry_points_reached` turns them into `Area`s:

- A page is reached by any import or edit in its file: one screen per URL (a page with an optional catch-all has two URLs, one
  screen with the second in `also_mounted`; a parallel slot's page and an intercepting page that share a URL with another page
  are one screen with a chain each).
- A route handler is reached per HTTP method: the exported `GET`, `POST`, ... that use the changed code (the whole module when
  the change is a statement that runs when the file loads). A Pages Router API file answers every method (`ANY`).
- A wrapper reaches every page it wraps: a layout (and `template`, `loading`, `error`, `not-found`, `global-error`, `default`)
  the pages below its folder, `_app` and `_document` every Pages Router page, `middleware` (`proxy` in Next.js 16) and
  `instrumentation` every page and handler (the matcher is not evaluated: undecidable means affected). Nothing in a file states this nesting, so the chain runs
  from the change to the wrapper's line that renders its children (the last line that names `children`, `Component` or `Main`;
  else the wrapper's default export) and on to the page's default export, and the run's notes say how many screens one change
  reaches (the priority by count is the model's: eight screens or fewer are must test, more are sample).
- A wrapper that is deleted or renamed is no chain of the walk (nothing imports it, and a deleted file is not walked):
  `begin_change` hands the adapter the change, and the pages that lost it are reached through the wrapper's line as it was at
  the pre-change commit.
- The file-based metadata routes (`sitemap.ts`, `robots.ts`, `manifest.ts`, `icon.tsx`, ...) are GET routes of their own.
- An entry file that imports another entry file (`export { default } from "../a/page"`, a handler re-exported by name) reaches
  what that file reaches: followed through the imports of the routing files themselves, narrowed by the names that arrive.

A file that cannot be read, or a route handler that does not lex (its methods cannot be told), is a problem of the route table
(`problems`, a blocking record): no screen or route is silently missing. Limits are in docs/briefs/M16-nextjs.md.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping, Sequence

from reachmap import config
from reachmap.adapters import Binding, ImportEdge, Reach, Reached, Route, diff_routes, guard_route_changes
from reachmap.adapters.base import api_area, dedupe, evidence_at, import_claim, module_changes, record_quoted, screen_area
from reachmap.adapters.js import JsAdapter
from reachmap.adapters.nextjs import routes as rt
from reachmap.errors import ModelError
from reachmap.incomplete import kind_spec, register_kind
from reachmap.model import Evidence

__all__ = ["KIND", "NextJsAdapter", "entry_adapter", "register", "validate_options"]

KIND = "nextjs"

try:
    kind_spec("route_intercepted")
except ModelError:
    register_kind(
        "route_intercepted", "Which navigations render {subject} was not established, because it intercepts another route and is shown in its place only on some of them", False,
    )


try:
    kind_spec("route_conflict")
except ModelError:
    register_kind(
        "route_conflict", "Which of {subject} answers their URL was not established, because Next.js refuses to build when two files answer one URL", False,
    )


def is_test_file(path: str) -> bool:
    """A test file in a routing folder: `.test.` or `.spec.` in its name, or a `__tests__` folder. Not the folder markers of the repository's
    `tests.markers`: a folder called `test` or `tests` in `app/` is a URL segment, and a route there is a route (the language adapter does
    not walk through it, as it does not walk through a test, which the notes say)."""
    parts = path.split("/")
    return any(m in parts[-1] for m in (".test.", ".spec.")) or "__tests__" in parts[:-1]


STAR_DEPTH = 5  # how many files deep `export *` of a route file is followed
ANY = "ANY"  # a Pages Router API file answers every method; the ones it tells apart by `req.method` are not read


@dataclass(slots=True)
class _Info:
    """What was read of one routing file: its routes (a page's screens, a handler's methods), the line that cites a wrapper,
    and whether the reading was whole."""

    entry: rt.Entry
    parsed: Any
    routes: tuple[Route, ...] = ()
    methods: tuple[str, ...] = ()
    tie: Evidence | None = None
    complete: bool = True
    reasons: tuple[str, ...] = ()


@dataclass(slots=True)
class _Via:
    """An entry file the change reaches, and how: the evidence up to it, what inside it is reached, whether the chain is whole."""

    entry: rt.Entry
    prefix: tuple[Evidence, ...]
    reach: Reach  # what inside the file the change reaches (for a change inside the file itself: its own closure)
    complete: bool
    reasons: tuple[str, ...]
    source: Any
    direct: bool = False


@dataclass(slots=True)
class _Hit:
    route: Route
    entry: rt.Entry  # the routing file the route is of
    sha: str  # the commit the route table was read at
    evidence: tuple[Evidence, ...]
    complete: bool
    reasons: tuple[str, ...]
    source: Any


def validate_options(options: Mapping[str, Any], describe: Any) -> list[str]:
    """The options of an `entry_points = { kind = "nextjs" }` table: `root` (the folder of the Next project in the repository,
    default the repository root), `page_extensions` (default tsx, ts, jsx, js) and `base_path` (the `basePath` of the Next
    config, put before every URL). A value is never quoted."""
    out: list[str] = []
    for key in options:
        if key not in ("kind", "root", "page_extensions", "base_path"):
            out.append(f"{key}: not an option of the nextjs entry points; allowed keys: root, page_extensions, base_path")
    root = options.get("root")
    if root is not None:
        if not isinstance(root, str):
            out.append(f"root: expected a folder of the repository (a string), got {describe(root)}")
        elif root.startswith("/") or ".." in root.split("/") or "\\" in root:
            out.append("root: must be a relative folder inside the repository (no leading slash, no `..`)")
    base = options.get("base_path")
    if base is not None:
        if not isinstance(base, str):
            out.append(f"base_path: expected the basePath of the Next config (a string such as \"/docs\"), got {describe(base)}")
        elif not base.startswith("/") or "?" in base or "#" in base or " " in base or "//" in base:
            out.append("base_path: must start with a slash and be a plain path (no query, no fragment, no spaces)")
    exts = options.get("page_extensions")
    if exts is not None:
        if not isinstance(exts, (list, tuple)) or not exts or not all(isinstance(e, str) and e and e.isalnum() for e in exts):
            out.append(f"page_extensions: expected a non-empty list of extensions such as \"tsx\" (letters and digits), got {describe(exts)}")
    return out


class NextJsAdapter:
    """Next.js entry points of one repository, built on its JavaScript adapter (which reads the files, parses them, resolves
    specifiers and lists the tree). Use one adapter per run, as the language adapters are."""

    kind = KIND

    def __init__(self, repo: str, language: JsAdapter, *, root: str = "", extensions: Iterable[str] | None = None, base_path: str = "",
                 record_to: Any = None) -> None:
        self.repo = repo
        self.language = language
        self.root = root or ""
        self.base_path = base_path or ""
        self.extensions = tuple(extensions) if extensions else rt.PAGE_EXTENSIONS
        self._target = record_to
        self._tables: dict[str, rt.Table] = {}
        self._infos: dict[tuple[str, str], _Info | None] = {}
        self._imports: dict[str, dict[str, list[tuple[str, Any]]]] = {}
        self.problems: list[str] = []  # what could not be established (an unreadable routing file, a handler whose methods are unknown)
        self.notes: list[str] = []  # what the run says about how it reached areas through a wrapper (the run's notes)
        self._change: Any = None

    @classmethod
    def from_config(cls, repo: config.Repo, language: JsAdapter, *, record_to: Any = None) -> "NextJsAdapter":
        options = repo.entry_points or {}
        exts = options.get("page_extensions")
        return cls(repo.name, language, root=options.get("root") or "", extensions=tuple(exts) if exts else None,
                   base_path=options.get("base_path") or "", record_to=record_to)

    @property
    def target(self) -> Any:
        return self._target if self._target is not None else self.language.target

    def problem(self, why: str) -> None:
        if why not in self.problems:
            self.problems.append(why)

    # ---- the table

    def table(self, sha: str) -> rt.Table:
        """The routing files of the tree at `sha`. An empty tree (the checkout failed: the git layer recorded it) is a problem,
        not "no routes"."""
        if sha not in self._tables:
            files = self.language.tree(sha)
            if not files:
                self.problem(f"the file tree at {sha[:12]} could not be read: no route was established")
            self._tables[sha] = rt.classify(files, root=self.root, extensions=self.extensions, is_test=is_test_file, base_path=self.base_path)
        return self._tables[sha]

    def unmatched(self, sha: str) -> list[tuple[str, str]]:
        """The router folders when the tree at `sha` holds no routing file at all (no `app` or `pages` folder, `src/` too, with a file Next reads).
        Nothing when the tree could not be read (`table` says so as a problem)."""
        table = self.table(sha)  # first: a tree that cannot be read adds its own problem (`table`)
        if not self.language.tree(sha) or table.entries:
            return []
        base = self.root.strip("/")
        return [(f"{base}/{{app,pages}}" if base else "{app,pages}",
                 "no page, route handler or wrapper was found, so the screens and routes were not established; check root and page_extensions (src/app and src/pages are looked in too)")]

    def routing_files(self, sha: str, what: str) -> list[str]:
        return [e.path for e in self.table(sha).entries]

    def is_entry_file(self, path: str, sha: str) -> bool:
        return path in self.table(sha).by_path

    def structural_names(self, path: str, sha: str) -> frozenset[str]:
        """The exports Next itself reads (the default export, `metadata`, `GET`, `getStaticProps`, ...): whoever imports one of
        them mounts the file, which is no way for a change to reach anything else. Any other export is a helper, and the walk
        goes on past the file through it."""
        return rt.RESERVED_EXPORTS

    # ---- reading a routing file

    def _ev(self, sha: str, path: str, parsed: Any, line: int, claim: str | None = None) -> Evidence | None:
        ev = evidence_at(self.repo, path, sha, parsed, line)
        return ev if ev is None or claim is None else replace(ev, claim=claim)

    def _first_code_line(self, parsed: Any) -> int:
        return min(parsed.code_lines) if parsed.code_lines else 1

    def _load(self, sha: str, path: str) -> _Info | None:
        """The routing file read at `sha`: its routes and what cites it. None when it could not be read (the git layer
        recorded an unreadable one; `problems` says the routes behind it were not established)."""
        key = (sha, path)
        if key in self._infos:
            return self._infos[key]
        self._infos[key] = None
        entry = self.table(sha).by_path[path]
        res = self.language.file_text(sha, path)
        parsed = self.language.parsed(sha, path) if res.is_found else None
        if parsed is None:
            self.problem(f"{path} could not be read at {sha[:12]}: what it routes was not established")
            if res.is_absent:
                record_quoted(self.target, "read_unreadable", f"{path} at {sha[:12]}", "the tree lists the file but it is not there at that commit", once=True)
            return None
        info = {"page": self._page, "route": self._metadata if entry.kind == "metadata" else self._handler, "api": self._api, "wrapper": self._wrapper}[entry.role](sha, entry, parsed)
        self._infos[key] = info
        return info

    def _degraded(self, entry: rt.Entry, parsed: Any, blocking: bool, consequence: str) -> tuple[bool, tuple[str, ...]]:
        if not parsed.problems:
            return True, ()
        record_quoted(self.target, "parse_degraded", entry.path, consequence, parsed.problems[0], blocks_selection=blocking, once=True)
        return False, (f"{entry.path} did not lex cleanly ({parsed.problems[0]}): {consequence}",)

    def _route(self, sha: str, entry: rt.Entry, parsed: Any, method: str, path: str, handler: str, line: int) -> Route | None:
        label = f"{method} {path}" if method else path
        ev = self._ev(sha, entry.path, parsed, line, f"route:{label}:{entry.path}")
        if ev is None:
            ev = self._ev(sha, entry.path, parsed, self._first_code_line(parsed), f"route:{label}:{entry.path}")
        if ev is None:
            return None
        return Route(
            repo=self.repo, kind="api_route" if method else "screen", method=method, path=path, file=entry.path, handler=handler,
            target=entry.path, module=entry.module, evidence=(ev,), partial=entry.partial,
        )

    def _default(self, parsed: Any) -> tuple[int, str]:
        """The line and the name of the default export; the first code line and `default` when there is none."""
        ent = parsed.exports.get("default")
        if ent is None:
            return self._first_code_line(parsed), "default"
        return ent.line, (ent.local if ent.local not in (None, "*") else "default")

    def _page(self, sha: str, entry: rt.Entry, parsed: Any) -> _Info:
        complete, reasons = self._degraded(entry, parsed, False, "the page is cited at its first line, not at its default export")
        line, handler = self._default(parsed)
        found = tuple(r for p in entry.paths if (r := self._route(sha, entry, parsed, "", p, handler, line)) is not None)
        return _Info(entry, parsed, found, (), None, complete, reasons)

    def _api(self, sha: str, entry: rt.Entry, parsed: Any) -> _Info:
        complete, reasons = self._degraded(entry, parsed, False, "the handler is cited at its first line, not at its default export")
        line, handler = self._default(parsed)
        found = tuple(r for p in entry.paths if (r := self._route(sha, entry, parsed, ANY, p, handler, line)) is not None)
        return _Info(entry, parsed, found, (ANY,), None, complete, reasons)

    def _metadata(self, sha: str, entry: rt.Entry, parsed: Any) -> _Info:
        """A file-based metadata route (`sitemap.ts`, `robots.ts`, `icon.tsx`, ...): a GET route cited at its default export."""
        complete, reasons = self._degraded(entry, parsed, False, "the route is cited at its first line, not at its default export")
        line, handler = self._default(parsed)
        found = tuple(r for p in entry.paths if (r := self._route(sha, entry, parsed, "GET", p, handler, line)) is not None)
        return _Info(entry, parsed, found, ("GET",), None, complete, reasons)

    def _handler(self, sha: str, entry: rt.Entry, parsed: Any) -> _Info:
        """An App Router route file: one route per exported HTTP method per URL. A file that does not lex has methods that
        cannot be told: no route is claimed, a blocking record says so."""
        complete, reasons = self._degraded(entry, parsed, True, "no route is claimed from it, so the methods it serves were not established")
        if not complete:
            self.problem(reasons[0])
            return _Info(entry, parsed, (), (), None, False, reasons)
        lines, why = self._methods(sha, entry.path, parsed, (entry.path,))
        for reason in why:
            self.problem(reason)
            record_quoted(self.target, "specifier_unresolved", entry.path, "an `export *` of the route file names a module that could not be read, so the methods it serves were not established", reason, once=True)
        found = tuple(
            r for m in sorted(lines, key=rt.HTTP_METHODS.index) for p in entry.paths
            if (r := self._route(sha, entry, parsed, m, p, m, lines[m])) is not None
        )
        return _Info(entry, parsed, found, tuple(sorted(lines, key=rt.HTTP_METHODS.index)), None, not why, tuple(why))

    def _methods(self, sha: str, path: str, parsed: Any, seen: tuple[str, ...]) -> tuple[dict[str, int], list[str]]:
        """The exported HTTP methods of a route file with the line that exports each (a method that arrives through
        `export * from` is cited at that line), and why some could not be told."""
        lines = {m: parsed.exports[m].line for m in rt.HTTP_METHODS if m in parsed.exports}
        why: list[str] = []
        for rec in parsed.imports:
            if rec.kind != "export_star":
                continue
            target = self.language.resolve_specifier(path, rec.specifier, sha)
            if target is None:
                why.append(f"{path} has `export * from` a module that does not resolve to a file of the repository: its methods were not established")
                continue
            if target in seen:
                continue  # a cycle: the file's methods are already counted
            if len(seen) > STAR_DEPTH:
                why.append(f"{path} has `export * from` {target}, more than {STAR_DEPTH} files deep: the methods past it were not established")
                continue
            inner = self.language.parsed(sha, target)
            if inner is None or inner.problems:
                why.append(f"{target} (exported by `export *` in {path}) could not be read or lexed: its methods were not established")
                continue
            more, more_why = self._methods(sha, target, inner, (*seen, target))
            why += more_why
            for m in more:
                lines.setdefault(m, rec.line)
        return lines, why

    def _wrapper(self, sha: str, entry: rt.Entry, parsed: Any) -> _Info:
        complete, reasons = self._degraded(entry, parsed, False, "the wrapper is cited at its first code line")
        tie = self._tie(sha, entry, parsed)
        return _Info(entry, parsed, (), (), tie, complete and tie is not None, reasons)

    def _tie(self, sha: str, entry: rt.Entry, parsed: Any) -> Evidence | None:
        """The line that says the wrapper wraps something: the last line that names what it renders (`children` of a layout, `Component`
        of `_app`, `Main` of `_document`), else the line of its default export (the `middleware` function for middleware)."""
        word = {"_app": "Component", "_document": "Main"}.get(entry.kind, "children")
        lines = [t.line for t in parsed.tokens if t.kind == "ident" and t.value == word]
        if entry.kind in ("middleware", "instrumentation"):
            decl = next((d for d in parsed.declarations if d.name in ("middleware", "proxy", "register")), None)
            line = decl.start if decl is not None else None
        else:
            line = lines[-1] if lines else None
        if line is None:
            line = self._default(parsed)[0]
        ev = self._ev(sha, entry.path, parsed, line, f"wraps:{entry.path}")
        return ev if ev is not None else self._ev(sha, entry.path, parsed, self._first_code_line(parsed), f"wraps:{entry.path}")

    # ---- the route table

    def routes(self, repo: str | None, sha: str) -> list[Route]:
        """Every screen and API route of the repository at `sha`, with its path and evidence (the handler's line, a page's default
        export)."""
        if repo is not None and repo != self.repo:
            raise ValueError(f"this adapter reads {self.repo}, not {repo}")
        out: list[Route] = []
        for e in self.table(sha).entries:
            if e.role != "wrapper":
                info = self._load(sha, e.path)
                if info is not None:
                    out.extend(info.routes)
        return out

    def routes_changed(self, before: str | None, after: str) -> tuple[list[Route], list[Route]]:
        """(removed, added) API routes between two commits by method and path, each with the evidence of its own version. Only API
        routes: the http edge reads every route this returns as an HTTP route (`screens_changed` has the screens). Without a
        pre-change version nothing is computed: removals cannot be (the change set recorded it)."""
        if before is None:
            return [], []
        return self._guarded(before, after, self._api_routes(before), self._api_routes(after))

    withholds_unread = True  # `routes_changed` and `screens_changed` guard themselves: the http edge does not guard them again

    def routes_complete(self, sha: str) -> tuple[bool, list[str]]:
        """Whether every routing file at `sha` was read: a file that could not be read or lexed has routes that were not read, not routes
        that were deleted."""
        why: list[str] = []
        if not self.language.tree(sha):
            why.append("the file tree could not be read")
        why += [f"{e.path} could not be read or lexed" for e in self.table(sha).entries if e.role != "wrapper" and self._load(sha, e.path) is None]
        return not why, why

    def _guarded(self, before: str, after: str, old: list[Route], new: list[Route]) -> tuple[list[Route], list[Route]]:
        removed, added = diff_routes(old, new)
        base_ok, base_why = self.routes_complete(before)
        head_ok, head_why = self.routes_complete(after)
        why = "; ".join((base_why if not base_ok else []) + (head_why if not head_ok else []))[:300]
        return guard_route_changes(self.target, self.repo, removed, added, base_ok=base_ok, head_ok=head_ok, why=why)

    def screens_changed(self, before: str | None, after: str) -> tuple[list[Route], list[Route]]:
        """(removed, added) screens between two commits by path."""
        if before is None:
            return [], []
        return self._guarded(before, after, self._screen_routes(before), self._screen_routes(after))

    def _api_routes(self, sha: str) -> list[Route]:
        return [r for r in self.routes(None, sha) if r.kind == "api_route"]

    def _screen_routes(self, sha: str) -> list[Route]:
        return [r for r in self.routes(None, sha) if r.kind == "screen"]

    # ---- areas

    def begin_change(self, analysis: Any) -> None:
        """Told by `entrypoints.reach` which change is being analysed: a deleted or renamed wrapper is no chain of the walk (a
        deleted file has no importers to walk from) and yet every page it wrapped lost it."""
        self._change = analysis.change

    def _removals(self) -> list[_Hit]:
        """The pages that lost a wrapper: for each deleted file (and the old path of a renamed one) that was a wrapper at the
        pre-change commit, the pages it wrapped that are still there at the head, through the wrapper's line as it was."""
        change = self._change
        pre, head = getattr(change, "pre_change_sha", None), getattr(change, "head_sha", None)
        if pre is None or head is None:
            return []  # without a pre-change version removals are not computed: the change set recorded it
        out: list[_Hit] = []
        before, after = self.table(pre), self.table(head)
        for fc in change.files:
            old = fc.path if fc.status == "deleted" else fc.old_path if fc.status == "renamed" else None
            entry = before.by_path.get(old) if old else None
            if entry is None or entry.role != "wrapper":
                continue
            info = self._load(pre, entry.path)
            if info is None or info.tie is None:
                continue
            below = before.wrapped(entry)
            made: list[_Hit] = []
            for target in below:
                still = after.by_path.get(target.path)
                tinfo = self._load(head, target.path) if still is not None else None
                if tinfo is None:
                    continue
                for r in tinfo.routes:
                    made.append(_Hit(r, still, head, tuple(dedupe([info.tie, *r.evidence])), info.complete and tinfo.complete, info.reasons + tinfo.reasons, None))
            self._say(entry, made, removed=True)
            out.extend(made)
        return out

    def entry_points_reached(self, chains: Iterable[Any], sha: str | None = None) -> list[Reached]:
        """The screens and API routes the chains reach, with the chain of Evidence from the change to each (see the module
        docstring). `Reached.complete` is False when a chain or a routing file was not established completely; what could not be
        read at all is in `problems`."""
        hits: list[_Hit] = []
        for chain in chains:
            table = self.table(chain.sha)
            start = table.by_path.get(chain.entry)
            if start is None:
                self.problem(f"{chain.entry} is not a routing file at {chain.sha[:12]}: the walk reached it but no route was established")
                continue
            prefix = chain.hops if chain.edge is not None else self._direct_prefix(chain)
            first = _Via(start, prefix, chain.reach, chain.complete, tuple(chain.reasons), chain, direct=chain.edge is None)
            for via in self._closure(chain, table, first):
                hits.extend(self._expand(chain, table, via))
        if self._change is not None:
            hits.extend(self._removals())
        return self._assemble(hits)

    @staticmethod
    def _direct_prefix(chain: Any) -> tuple[Evidence, ...]:
        """For a change inside the file: its first changed line (added lines at the head commit; removed ones are cited at the
        commit they were read at)."""
        lines = [e for s in chain.changed for e in s.evidence if e.ref == chain.sha and e.path == chain.entry]
        return tuple((lines or list(chain.hops))[:1])

    # what the change reaches through the imports between routing files (a page that re-exports a page, a handler re-exported by name)

    def _import_index(self, sha: str, table: rt.Table) -> dict[str, list[tuple[str, Any]]]:
        """For each routing file that another routing file imports (or re-exports): the importer's path and the import record."""
        if sha not in self._imports:
            index: dict[str, list[tuple[str, Any]]] = {}
            for e in table.entries:
                parsed = self.language.parsed(sha, e.path)
                if parsed is None:  # (the Git layer recorded an unreadable file)
                    self.problem(f"{e.path} could not be read at {sha[:12]}: whether it re-exports a routing file the change reaches was not established")
                    continue
                for rec in parsed.imports:
                    target = self.language.resolve_specifier(e.path, rec.specifier, sha)
                    if target is not None and target != e.path and target in table.by_path:
                        index.setdefault(target, []).append((e.path, rec))
            self._imports[sha] = index
        return self._imports[sha]

    def _closure(self, chain: Any, table: rt.Table, first: _Via) -> Iterable[_Via]:
        """The reached routing file, then every routing file that imports one of the reached ones (to a fixed point), narrowed by the
        names that arrive. A change that reaches the whole module is carried to every importer (the walk stops there: its limit,
        which would hide a page that re-exports it); one that reaches only declarations exported by no name goes no further."""
        yield first
        index = self._import_index(chain.sha, table)
        seen = {first.entry.path}
        queue = [first]
        while queue:
            via = queue.pop(0)
            reach = via.reach
            if reach.names is None and not reach.whole:
                continue
            names = reach.names
            for importer, rec in sorted(index.get(via.entry.path, ()), key=lambda x: (x[0], x[1].line)):
                if importer in seen or (names is not None and rec.names is not None and not (set(rec.names) & set(names))):
                    continue
                parsed = self.language.parsed(chain.sha, importer)
                ev = self._ev(chain.sha, importer, parsed, rec.line, import_claim(via.entry.path, rec.names)) if parsed is not None else None
                if ev is None:
                    continue
                edge = ImportEdge(importer, rec.specifier, rec.names, ev, rec.kind, rec.type_only,
                                  tuple(Binding(b.imported, b.local, b.type_only) for b in rec.bindings), via.entry.path)
                sites = self.language.sites_for(importer, [edge], chain.sha, names)
                inner = self.language.reach(importer, [edge], chain.sha, names, sites)
                seen.add(importer)
                nxt = _Via(table.by_path[importer], tuple(dedupe([*via.prefix, ev])), inner, via.complete and inner.complete,
                           via.reasons + (() if inner.complete else tuple(f"{importer}: {why}" for why in inner.reasons)), via.source)
                queue.append(nxt)
                yield nxt

    def _expand(self, chain: Any, table: rt.Table, via: _Via) -> list[_Hit]:
        """The routes one reached routing file gives: its own for a page or a handler, the pages it wraps for a wrapper."""
        entry = via.entry
        info = self._load(chain.sha, entry.path)
        if info is None:
            return []
        complete = via.complete and info.complete
        reasons = via.reasons + info.reasons
        if entry.role in ("page", "api") or entry.kind == "metadata":  # the file is the route: anything that reaches it reaches it
            return [_Hit(r, entry, chain.sha, tuple(dedupe([*via.prefix, *r.evidence])), complete, reasons, via.source) for r in info.routes]
        if entry.role == "route":
            methods = self._reached_methods(chain, via, info)
            return [_Hit(r, entry, chain.sha, tuple(dedupe([*self._handler_prefix(chain, via, info, r), *r.evidence])), complete, reasons, via.source)
                    for r in info.routes if r.method in methods]
        out: list[_Hit] = []
        wrapped = table.wrapped(entry)
        for target in wrapped:  # a wrapper: every route it wraps, through the line that says it wraps them
            tinfo = self._load(chain.sha, target.path)
            if tinfo is None:
                continue
            for r in tinfo.routes:
                evidence = tuple(dedupe([*via.prefix, *([info.tie] if info.tie is not None else []), *r.evidence]))
                out.append(_Hit(r, target, chain.sha, evidence, complete and tinfo.complete, reasons + tinfo.reasons, via.source))
        self._say(entry, out)
        return out

    def _say(self, wrapper: rt.Entry, hits: Sequence[_Hit], removed: bool = False) -> None:
        """The note of a wrapper that reaches routes: nothing in a file states the nesting, so the report says that one change reaches
        every route it wraps, and how the chain shows it."""
        screens = len(self._group_screens([h for h in hits if h.route.kind == "screen"]))
        apis = len({(h.route.method, h.route.path) for h in hits if h.route.kind == "api_route"})
        if removed:
            note = (f"{wrapper.path} was removed, or moved away from its folder: the {screens} screen(s) that were below it lost it and are reached through the "
                    f"line it had at the pre-change commit")
        elif wrapper.kind == "instrumentation":
            note = (f"{wrapper.path} is the instrumentation file of the project and runs for the whole server, so every one of the {screens} screens and {apis} routes is reached "
                    f"(undecidable means affected); each chain runs from the change to the register function and on to the route")
        elif wrapper.kind == "middleware":
            note = (f"{wrapper.path} is the middleware of the project: its matcher is not evaluated, so every one of the {screens} screens and {apis} routes is reached "
                    f"(undecidable means affected); each chain runs from the change to the middleware function and on to the route")
        elif wrapper.router == "pages":
            note = (f"{wrapper.path} wraps every page of the Pages Router ({screens} screen(s)): a change to it reaches each of them; each chain runs through the line of the "
                    f"wrapper that renders the page and on to the page's default export")
        else:
            note = (f"{wrapper.path} wraps the {screens} screen(s) below its folder: a change to it reaches each of them; no line states the nesting (it is the folder tree), "
                    f"so each chain runs through the line of the wrapper that renders its children and on to the page's default export")
        if note not in self.notes:
            self.notes.append(note)

    @staticmethod
    def _handler_prefix(chain: Any, via: _Via, info: _Info, route: Route) -> tuple[Evidence, ...]:
        """For a change inside a handler file: the changed line inside the method's declaration when there is one, else the first
        changed line. For a change that arrives by an import: the hops."""
        if not via.direct:
            return via.prefix
        decls = [d for d in info.parsed.declarations if route.method in d.export_names]
        changed = [e for s in chain.changed for e in s.evidence if e.ref == chain.sha and e.path == chain.entry]
        inside = [e for e in changed if any(d.start <= e.line <= d.end for d in decls)]
        return tuple((inside or changed or list(via.prefix))[:1])

    def _reached_methods(self, chain: Any, via: _Via, info: _Info) -> set[str]:
        """The methods of a handler file a change reaches: all of them for a statement that runs when the file loads, else those
        whose declaration (or the binding they export) is in the reached closure, or that a re-export carries."""
        reach = via.reach
        if reach is None or reach.whole or not info.complete:
            return set(info.methods)
        parsed = info.parsed
        methods: set[str] = set()
        locals_, names = set(reach.locals), set(reach.names or ())
        for m in info.methods:
            ent = parsed.exports.get(m)
            if m in names or (ent is not None and ent.local in locals_):
                methods.add(m)
            elif any(d.name in locals_ and m in d.export_names for d in parsed.declarations):
                methods.add(m)
        if via.direct:
            imported, other = self._module_changes(chain, parsed)
            if other:
                return set(info.methods)
            for m in info.methods:
                ent = parsed.exports.get(m)
                decls = [d for d in parsed.declarations if m in d.export_names]
                if (ent is not None and ent.local in imported) or any(parsed.decl_references(d) & imported for d in decls):
                    methods.add(m)
        return methods

    def _module_changes(self, chain: Any, parsed: Any) -> tuple[set[str], bool]:
        """What the changed lines outside every declaration of a handler file touch: (the names the changed import statements
        bind, whether some changed line is not an import statement, which could change anything the file does when it loads)."""
        before = self.language.parsed(chain.pre_sha, chain.before_path) if chain.pre_sha else None
        return module_changes(chain.changed, parsed, before, _import_spans)

    # ---- the areas, from the hits

    def _assemble(self, hits: list[_Hit]) -> list[Reached]:
        out: list[Reached] = []
        apis: dict[tuple[str, str], list[_Hit]] = {}
        screens: list[_Hit] = []
        for h in hits:
            if h.route.kind == "api_route":
                apis.setdefault((h.route.method, h.route.path), []).append(h)
            else:
                screens.append(h)
        for key in sorted(apis):
            group = self._unique(apis[key])
            route = group[0].route
            self._note_partial(group[0])
            chains, sources = self._chains(group)
            area = api_area(self.repo, route, chains)
            out.append(Reached(area, (route,), all(h.complete for h in group), tuple(dict.fromkeys(r for h in group for r in h.reasons)), sources))
        for group in self._group_screens(screens):
            ordered = sorted(group, key=self._screen_order)
            unique = self._unique(ordered)
            routes: list[Route] = []
            for h in unique:
                self._note_partial(h)
                if h.route not in routes:
                    routes.append(h.route)
            primary = routes[0]
            chains, sources = self._chains(unique)
            area = screen_area(self.repo, routes, chains)
            out.append(Reached(area, tuple(routes), all(h.complete for h in unique), tuple(dict.fromkeys(r for h in unique for r in h.reasons)), sources))
        out.sort(key=lambda r: (r.area.label, r.routes[0].file))
        return out

    @staticmethod
    def _screen_order(h: _Hit) -> tuple[Any, ...]:
        """Of one URL the page of its own folder comes first, then a parallel slot's page, then an intercepting page (partial)."""
        return (h.route.path, h.route.partial, h.entry.slot, h.route.file)

    @staticmethod
    def _chains(hits: Sequence[_Hit]) -> tuple[tuple[tuple[Evidence, ...], ...], tuple[Any, ...]]:
        """The distinct chains of a group of hits, in order, and the walk chain behind each (M8 shows the way inside the repository)."""
        chains: list[tuple[Evidence, ...]] = []
        sources: list[Any] = []
        for h in hits:
            if h.evidence not in chains:
                chains.append(h.evidence)
                sources.append(h.source)
        return tuple(chains), tuple(sources)

    @staticmethod
    def _unique(hits: Sequence[_Hit]) -> list[_Hit]:
        """One hit per route and evidence: the same route reached by two chains keeps both chains, the same chain twice once."""
        seen: set[tuple[Any, ...]] = set()
        out: list[_Hit] = []
        for h in hits:
            key = (h.route, h.evidence)
            if key not in seen:
                seen.add(key)
                out.append(h)
        return out

    @staticmethod
    def _group_screens(hits: list[_Hit]) -> list[list[_Hit]]:
        """Screens are grouped: routes that share a file (an optional catch-all has two paths) or a path (a page, a parallel
        slot's page and an intercepting page of one URL) are one screen."""
        parent: dict[Any, Any] = {}

        def find(x: Any) -> Any:
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for h in hits:
            parent[find(("path", h.route.path))] = find(("file", h.route.file))
        groups: dict[Any, list[_Hit]] = {}
        for h in hits:
            groups.setdefault(find(("path", h.route.path)), []).append(h)
        return list(groups.values())

    def _note_partial(self, hit: _Hit) -> None:
        """A route that is partial is said when a change reaches it: the intercepting route is shown in place of another one only
        on some navigations, an unreadable segment is kept as written."""
        entry, route = hit.entry, hit.route
        if route.file != entry.path:
            return
        others = self.table(hit.sha).conflicts.get(entry.path)
        if others:  # a build error in Next.js: both files are reached (affected) and the run says there are two
            record_quoted(self.target, "route_conflict", " and ".join(sorted((entry.path, *others))),
                          "two files answer one URL, so which of them serves it was not established; both are reached", route.path, once=True)
        if entry.intercepting:
            record_quoted(self.target, "route_intercepted", route.file,
                          "it is shown in place of the route it intercepts only on a soft navigation from a page under the folder that holds it", route.path, once=True)
        for text in entry.unknown:
            record_quoted(self.target, "route_path_partial", f"{route.file}:{route.line.line}",
                          "a folder name could not be read as a route segment; it is kept as written and nothing is guessed", text, once=True)


def _import_spans(parsed: Any) -> dict[int, set[str]]:
    """For each line of an import statement, the local names the statement binds."""
    return {n: {b.local for b in rec.bindings} for rec in parsed.imports if rec.kind == "import" for n in range(rec.start_line, rec.line + 1)}


def entry_adapter(repo: config.Repo, language: Any, record_to: Any = None) -> NextJsAdapter:
    """The factory the entry-point registry calls."""
    if not isinstance(language, JsAdapter):
        raise TypeError("the nextjs entry points are read with the JavaScript adapter")
    return NextJsAdapter.from_config(repo, language, record_to=record_to)


def register(register_entry: Any, register_config_kind: Any = None) -> None:
    """Make `nextjs` a known `entry_points` kind: in the registry of adapters (`register_entry(kind, factory)`) and in the config
    (`register_config_kind(section, kind, validator)`, default `reachmap.config.register_kind`; an already registered kind is left)."""
    register_entry(KIND, entry_adapter)
    add = register_config_kind or config.register_kind
    try:
        add("entry_points", KIND, validate_options)
    except ValueError:
        pass  # registered before (a second import, a test): the same kind
