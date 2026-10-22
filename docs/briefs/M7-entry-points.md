# M7 brief: entry points

Goal: from what the language adapters found (changed symbols, callers, importers), walk
inside one repository up to what a person or a system actually invokes, and say how it
got there. Two entry-point adapters: React Router screens (frontend) and FastAPI routes
(backend). Read `docs/CONTEXT.md` sections 3 (Area), 4, 5 (hubs, "a route is composed on
the router it is declared on", unmounted routers), 6, 7.3 (EntryPointAdapter), 7.5,
`tests/fixtures/README.md` (`screen`, `route`, `chain`, subject formats), and the M5/M6
briefs with implementation notes (`reachmap/adapters/base` shared shell, FileImpact,
ChangeAnalysis, records).

Python 3.11+ stdlib only.

## Files

```text
reachmap/walk.py                         in-repo upward walk over the importer graph (shared by both adapters)
reachmap/adapters/react_router/__init__.py   ReactRouterAdapter (EntryPointAdapter)
reachmap/adapters/fastapi/__init__.py        FastApiAdapter (EntryPointAdapter)
reachmap/entrypoints.py                  registry: config entry_points.kind to adapter
tests/test_walk.py, test_react_router.py, test_fastapi.py
tests/test_scenarios_entry.py            screens and routes against expected.json
```

## The in-repo walk (`walk.py`)

- Input: a ChangeAnalysis from a language adapter (changed symbols per file, callers and
  importers with narrowing applied), the pinned sha, limits from config (`hub_importers`,
  `module_cap` if configured).
- Output: chains. A chain is an ordered list of Evidence from the first hop (the import of
  the changed file in the first importer, or the changed line itself when the entry point
  is in the changed file) to the entry-point line (the `<Route>` line or the route
  decorator plus mounts), exactly in the shape of `expected.json` `chain`.
- Each step asks the language adapter for the importers of the current file (narrowed
  against the names that reached it: the walk carries the set of reached exported names
  per file and narrows each next hop the same way M5/M6 narrow the first hop, with the
  intra-file closure). The walk stops at entry-point files (the adapter says which files
  are entry points: routes files, modules declaring route handlers).
- Hubs: a file with more importers than `hub_importers` is not passed through; record
  `hub_cutoff` (subject the hub path, `importers` count) once per hub, non-blocking. The
  JS and Python adapters' own hub checks move here (or delegate here) so there is one
  rule; keep s02 passing.
- Module cap per hop (`limits.module_cap`, default 200): when more files are reached at
  one depth than the cap, stop expanding that depth, record `module_cap` with subject
  `hop:<n>`. Record it centrally; never present the partial set as complete.
- Every incompleteness from the language adapter on a file in a chain (incomplete
  importer lists, parse problems) makes the areas reached through that file incomplete:
  areas carry a `complete` flag and the records are kept.
- Cycles in the import graph terminate; each file is expanded once per reached-name set.
- Depth is unbounded inside a repo except by caps; the hop cap in config applies to
  edges between repos (M8), not here.

## React Router adapter

- Routes files from config `entry_points.routes_glob` (fixture: `src/modules/*/routes.tsx`).
- Parse `<Route path="..." element={<Page />} />` (JSX, attributes in any order,
  single or multi-line elements, `element={<Page prop={x} />}`, `Component={Page}`),
  and object-style `createBrowserRouter([{ path, element }])` / `{ path, Component }`
  as a second form. Nested routes compose paths (child relative paths join the parent's,
  index routes take the parent path). Paths are full absolute paths in the fixture; both
  must work.
- A page is identified by the import binding used in the element; it maps to a file
  through the routes file's import (static or `React.lazy(() => import("..."))`).
- One screen per page file; several routes rendering the same page are one screen: the
  label is the smallest route in string order, the others are `also_mounted`, with their
  Route lines as evidence (README "screen" rules).
- `entry_points_reached(chains)`: Areas of kind `screen`, chain evidence, priority left
  to the model's `assign_priorities`.
- `routes(repo, sha)`: every route with its page and evidence (for the http edge and M15
  test sources later).
- `routes_changed(before, after)`: removed and added routes from the routes files of the
  two versions.
- A routes file that fails to parse: incompleteness (blocking for screens in that module).

## FastAPI adapter

- Handlers: functions decorated with `@<router>.<method>(path, ...)` (get, post, put,
  patch, delete, options, head, `api_route` with methods=, `websocket`), where `<router>`
  is a module-level name bound to `APIRouter(...)` (with its own `prefix=`) or
  `FastAPI(...)`. Multi-line decorators.
- Mounts: `<parent>.include_router(<child>, prefix="...")` across files: the child is
  resolved by import (aliased imports, module attribute `routers.invoices.router`), the
  parent's own prefix is applied. Compose the full path from the decorator's router up
  through every mount to an app (`FastAPI()` instance). Several mount paths give several
  routes. `app.mount()` of a sub-application composes the same way.
- A router nothing mounts (up to an app): no route claimed; record `router_unmounted`
  with subject `<path>:<router variable>` (non-blocking). A commented-out mount is not a
  mount (code, not comments).
- Prefixes from variables or f-strings that cannot be evaluated statically: the route is
  recorded with the unknown segment marked and an incompleteness (non-blocking, kind
  `route_path_partial`), never guessed.
- `entry_points_reached`: Areas of kind `api_route`, label `"<METHOD> <path>"`, with
  the route evidence in the README's order (decorator, then each `include_router` line
  from the router outwards) plus the chain from the change to the handler.
- `routes_changed(before, after)`: removed and added routes (s10 removed `audit_log`,
  s22 added `get_invoice_pdf`), each with evidence at its own version.
- Changed handler body (s06) reaches its own route directly; a changed service function
  reaches the handlers that call it through the walk (Python callers), then their routes.

## Scenario harness

Extend `tests/scenario_support.py` (or a new module) to check `screen` and `route`
entries (must_find with chain/evidence, must_exclude, `also_mounted`), `complete_for`
for those types, and the records `hub_cutoff`, `router_unmounted`, `module_cap`. Remove
the corresponding skips from the JS and Python skip tables, explicitly. Scenarios whose
screens/routes must now pass: s01, s02, s03, s04, s06, s07, s08, s10 (routes and the
screen reached in the api repo part only), s15 (routes reached, even if expected has no
route entries: then nothing is checked beyond no crash), s19, s20, s22, s23. Screens
reached through an http edge from another repo (s06's web screen, s05, s11) stay skipped
until M8.

## Tests beyond the scenarios

- Hub not passed through, recorded once; module cap recorded with the hop.
- Cycle in imports terminates.
- Page mounted on three routes: one screen, two also_mounted.
- Nested `<Route>` path composition; object routes; lazy page.
- FastAPI: router mounted twice under two prefixes gives two routes; prefix on the
  APIRouter constructor; `api_route(methods=[...])`; unevaluable prefix recorded.
- Every chain hop is real Evidence that resolves at its pinned sha.

## Implementation notes

Where the code differs from, fills in, or goes beyond the text above. Each point is covered by a test unless it says
otherwise. The second half of this section ("Review fixes") records what an independent review of the first version
changed; where the two disagree, the later text is the code.

Files added beyond the list: `reachmap/adapters/react_router/routes.py` (the route parser), and tests in `tests/test_walk.py`,
`test_react_router.py`, `test_fastapi.py`, `test_scenarios_entry.py`. Files changed: `reachmap/adapters/__init__.py` (new
types `Reach`, `Route`, `Reached`, protocol `EntryPointAdapter`, `FileImpact.reached_names`, the record kinds `route_path_partial`
and `mount_unresolved`, `normalize_path`, `diff_routes`), `reachmap/adapters/base.py` (the hooks `_reach`, `_own_reach`, `_patterns`
and the public `reach`, `own_reach`, `survivors`, `sites_for`, `prefetch_importers`, `tree`, `file_text`, `parsed`, `checkout`,
`search`; the hub rule is delegated to `walk.py`), `adapters/js` and `adapters/python` (`_reach`, `_own_reach`, `_patterns`,
`resolve_specifier`, `resolve_module`), `reachmap/gitlayer.py` (a search takes several patterns; a tree listing is kept per
commit), `tests/scenario_support.py`, `tests/support_universe.py`, `tests/test_scenarios_js.py`, `tests/test_scenarios_py.py`.

### The walk (`reachmap/walk.py`)

- `walk(analysis, language, entry, limits=, hub_importers=, module_cap=, record_to=)` returns a `WalkResult`: `chains`, `complete`
  and `reasons`, `expanded` (files whose importers were searched) and `depths` (distinct files reached at each hop). A `Chain` has
  `files` (changed file to entry file), `hops` (the Evidence of each import, the way expected.json writes a chain), `edge` (the
  import in the entry file), `reach` (what inside the entry file the change reaches), `names` (the exported names of the previous
  file that arrive, None for the whole module), `changed` (the changed symbols when the change is inside the entry file), `sha`,
  `pre_sha`, `before_path`, `complete` and `reasons`. The entry-point adapter turns chains into areas; `reachmap.entrypoints.reach`
  does both and sets the priorities with the model's `assign_priorities`.
- The reached names are carried by a hook of the language adapters, `reach(importer, edges, sha, names, sites)` returning
  `Reach(locals, names, lines, complete, reasons, whole)`: the declarations that use a reached import binding are reached, and so is
  every declaration that uses a reached one (to a fixed point); the names the module exports them under are what the next hop is
  narrowed with. `names=None` means the next hop is not narrowed, for one of two reasons that `whole` tells apart. `whole=True`: the
  whole module is reached (a use at module level, an import that is never used, a dynamic import with no binding at module level,
  `export *` of an unknown set, a reached name used by module-level code or called where a constant is initialised, a file that does
  not lex or parse or reaches names dynamically). `whole=False`: the declarations reached are exported by no name, so only what
  `locals` names is touched. Undecidable means affected, as in M5 and M6. Re-exports (`export { a as b } from`) carry the names
  under their new name and `export *` carries them unchanged, so barrels are walked. Python: the receivers a reached declaration
  registers on (`@router.get(...)` registers on `router`) are exported names, but the declarations that only use the receiver are
  not reached, and a receiver used by module-level code (`app.include_router(router)`) does not make the module whole.
- `own_reach(path, sha, seeds)` is the same closure for a change inside a file: the declarations that use the changed ones, to a
  fixed point, the names they are exported by, and `whole`.
- The first hop is the language adapter's: `FileImpact.callers` (narrowing and guards applied) and `FileImpact.reached_names`. A hub
  among the changed files is skipped (the language adapter recorded it) and makes the result incomplete.
- Hop numbers: hop 1 is the importers of the changed file(s). The walk is one breadth first pass over all changed files. A file is
  expanded once per reached-name set and per set of imports that bring the names: two parents that bring the same names through
  different imports reach different declarations (a file importing `{Badge}` from a and `{Badge as B2}` from b) and are both walked.
  A file already on the path is not entered again. The work is bounded by the distinct (names, imports) of each file; a lattice of 301
  files with two parents each and three exports expands 1,139 times and takes 0.3 s.
- Entry-point files end a chain, but the walk goes on past one through the exported names that do not just wire it up, which the
  entry-point adapter gives as `structural_names` (routers and apps for FastAPI, the declarations that hold a `<Route>` for React
  Router): a helper declared in a handler or routes file and imported by another module reaches that module's entry points; whoever
  imports a router mounts it, which the route table already knows. An adapter without `structural_names` is not walked past. A
  change that reaches the whole module of an entry file is not followed past it (the routes it affects are the ones in or through
  the file; who imports the file mounts it).
- Module cap: when more than `limits.module_cap` distinct files are reached at one hop, the first `module_cap` (in path order) are
  expanded, the others are only looked at for being entry files (their chains are kept: that costs nothing), the walk goes no
  further from that hop, `module_cap` is recorded with the subject `hop:<n>` and `WalkResult.complete` is False. No scenario
  exercises it; `tests/test_walk.py` `ModuleCap` and `EntryFilesAtTheCap` do.
- Test files are never walked; a deleted or renamed file is walked from its importers at the pre-change commit (the evidence of those
  hops is pinned to it); a deleted entry file gives no chain, a renamed one is read at the head and its removed lines at its old path.
- One `git grep` per hop: the importers of all the files of a hop are searched with one `-e` per name (`prefetch_importers`), split
  per name afterwards, so each file's importer search reads what its own search would have read, including a search that was cut or
  failed (recorded once by the git layer, and each of the searches is marked incomplete). Import resolution, a tree listing and a
  `reach` of the same file, imports and names are memoised for the life of the adapter.
- `Area` has no completeness field (it is a core model type, M2). `Reached` (area, routes, `complete`, `reasons`) carries it, and
  `EntryResult.complete` / `reasons` is the aggregate: the walk, the entry adapter's `problems` (a routes file that could not be read
  or lexed, a route table that is not whole) and every area. A short list of areas is never "the" list when it is False. The
  records are kept either way.

### React Router (`reachmap/adapters/react_router`)

- `ReactRouterAdapter(repo, JsAdapter, routes_glob)`, built from the config by `from_config` or `entrypoints.entry_adapter`. The
  glob accepts `*` (one folder), `**`, `?` and `{a,b}`; the first `*` names the product module (`billing` for
  `src/modules/billing/routes.tsx`, the folder of the file otherwise). Without `routes_glob` the default is `src/**/routes.{tsx,jsx,ts,js}`
  (a choice: the brief leaves it open).
- Routes are read from tokens: JSX `<Route>` with attributes in any order, `path` as a string, a template without substitution or
  a `{"..."}` literal, `index`, `element` and `Component`, nested children; object routes (`path`, `index`, `element`, `Component`,
  `lazy`, `children`), including a layout route with no path (`{ element, children }`), found as objects of an array or argument.
  A path that is not a literal is kept as `<unknown:EXPR>` (`partial`). A relative path with no route above it in the file
  (`<Routes>` inside a component that a route mounts elsewhere) is `/<unknown:mount>/invoices`, partial, not claimed to be the
  root; descendant routes are not composed through the component's mount (a limit). Paths are joined without doubled or trailing
  slashes (`/a/` and `b/` give `/a/b`, an empty child path is the parent's).
- A page is an import binding or a declaration that holds `import("...")` named in the element (`React.Suspense` and other wrappers
  are skipped because their import does not resolve to a file of the repository); it maps to a file through the JavaScript adapter's
  resolver. A route's page is the innermost component of its own element (`<RequireAuth><A /></RequireAuth>` is A). Only leaf routes
  are screens: a parent route is rendered around its children and has no screen of its own.
- One screen per page file: the routes whose own element renders the same page files are one screen, whatever layouts they sit
  under. The label is the smallest route path in string order; the others are `also_mounted` (sorted). `Area.chains` holds the
  primary route's chain first and one chain per other mounting route; each chain is the hops up to the page, the import of the page
  in its routes file and the `<Route>` line of each route from the one that renders it down to the leaf. A page under two layouts
  and a bare route is one screen with two `also_mounted`.
- A change reaches a route when its page is a file the chain arrives at (a named page import is narrowed by the names that
  arrive; a default or namespace import is not), when its element (or an ancestor's) renders a component the change reaches inside
  the routes file (a layout declared there, a loader helper that wraps a lazy import, a wrapper imported from another file), and,
  when the whole module is reached (a statement that runs when the file loads), every route of the file.
- A change inside a routes file: a changed line in a route's own tag (or an ancestor's, which reaches its descendants), in the import
  statement of its page, or in a declaration whose closure it renders, reaches that route; a changed code line that belongs to none of
  those (the wrapper, an unrelated import) reaches every route of the file. A removed line that sat in a leaf route that is gone
  reaches nothing (`routes_changed` says it is removed); one that sat in a route that is still there reaches it; a removed import
  statement reaches the routes that render its names; any other removed line reaches every route; with no pre-change text it counts
  as "any other". A change that only removes lines with no pre-change version is not seen at all (no symbol is placed, as in M5).
- A routes file that does not lex cleanly claims no route, records a blocking `parse_degraded` (once: the JavaScript adapter's
  record of the same subject is not repeated) and makes the result incomplete. A chain into a routes file that cannot be read is not
  dropped silently: `problems` says so (and a `read_unreadable` is recorded when the file is absent from the tree).
- `route_path_partial` is recorded when a change reaches a partial route, in both adapters; `routes()` records nothing.

### FastAPI (`reachmap/adapters/fastapi`)

- Handlers, mounts and the path composition are as the brief says. The order of the path is the framework's (the app's mount prefix,
  then each router's own prefix and mount prefix, then the decorator path). The evidence is the decorator, the constructor line of a
  router that has a `prefix=` (not in the brief; the prefix came from it; documented in `tests/fixtures/README.md`), and each mount
  line from the router outwards. A multi-line decorator or mount is cited at its first line. Paths are joined without doubled or
  trailing slashes.
- Files are found by one `git grep` for `APIRouter`, `FastAPI`, `include_router` and `.mount(`, plus the importers of every file that
  declares a router or an app (a router imported under another name, `from app.core import router as r`, and decorated in a file that
  names none of these is found that way; the importer search says when it was not complete). A cut or failed search makes the table
  incomplete (`problems`, `Reached.complete`; the git layer's blocking record stays).
- `is_entry_file`: a file that declares handlers, routers or mounts. A change inside one reaches: the routes of a changed handler or of
  a handler that uses a changed declaration (`own_reach` closure: a helper called by a handler), the routes on a router whose
  declaration is in that closure, the routes with a changed line among their evidence (a mount line, a constructor line) wherever the
  file is a mount file, the routes that use the names a changed import statement binds, every route in or through the file when the
  whole module is reached (a statement that runs when the file loads, a constant a mount takes its prefix from, a dependency or
  middleware of an app or of an `include_router`) or for any other statement outside every declaration. The same rules for a change
  that arrives by an import (a dependency module that a mount file uses at module level reaches every route through that file). This
  is broader than the precise rule (an app-level dependency reaches the routes of that app, a router-level one the routes under that
  router): it errs toward affected.
- A router no mount carries up to an app has no route; `router_unmounted` (non-blocking) names the one nothing mounts
  (`<path>:<variable>`) and is recorded only when a change reaches a handler on it. A mount in a function or another scope, one on or of
  something that does not resolve to a router (except `mount` of a call such as `StaticFiles(...)`) is a blocking `mount_unresolved`
  (subject `<path>:<line>`): while one exists in the repository `router_unmounted` is not recorded (the router may be that mount's)
  and `problems` says so. A router mounted into itself or in a cycle is cut.
- Prefixes and paths are evaluated from string literals, `+`, f-strings and module constants (this file's or one imported by `from m
  import NAME`, not assigned twice); a prefix or a path that is unknown as a whole stands for a segment, `/<unknown:EXPR>` (FastAPI
  requires them to start with `/`), an unknown part of an f-string is `<unknown:EXPR>` in place. A `methods=` list that is not a
  literal gives the method `<unknown:EXPR>` and a partial route. `route_path_partial` has the subject `<path>:<line>` of the
  expression and is recorded when a change reaches the route. A sub application that is mounted is served only where it is mounted.
- `unmounted(sha)` lists the routers with handlers that nothing mounts (used by the tests; the oracle of `claims.py` agrees).

### The harness and the scenarios

- `tests/scenario_support.py` runs the entry-point adapter the repository's config selects after the language adapter
  (`Run.entry`, `Run.reach`) and `check` compares `screen` and `route` entries (chain in order with the same length, `also_mounted`,
  `also_mounted_evidence` among the other chains, `must_exclude`, `complete_for`, route evidence in order and as the end of the chain),
  the records `router_unmounted` and `module_cap` besides `hub_cutoff`, and `EntryResult.complete` against the expected records (a
  hub, a module cap or a blocking record other than `pre_change_unavailable` means incomplete). The skip tables of
  `test_scenarios_js.py` and `test_scenarios_py.py` no longer list screens and routes; what is left is `http_edge`, `finding`,
  `branch_state`, `contract_read`, `consumer`, `hop_cap` and the entries about another repository (web screens of the api scenarios, the
  route of the contracts scenarios), still pinned, and both scenario tests again assert that every implemented type appears in some
  scenario (the web scenarios have no route). s05 is checked for its screen (it is reached inside the web repository; the brief lists
  it as skipped, but nothing in it needs the http edge), and its http edge stays skipped.
- Scenarios that now pass for screens and routes: s01, s02, s03, s04, s05, s19, s20 (web), s06, s07, s08, s10, s22 (api, with
  `route` entries or the `router_unmounted` record), s09, s13, s14, s15, s16, s23 (no route or screen expected, or none checked beyond
  `complete_for`). The route tables are compared with `claims.api_routes` (every route and its evidence, and the unmounted routers, at
  main and at every scenario head) and `claims.route_table_of_pages` in `tests/test_scenarios_entry.py`.

### Review fixes

The first version of M7 let a screen or a route disappear while the walk said complete. What changed, each with its tests
(`tests/test_walk.py`, `test_react_router.py`, `test_fastapi.py`):

- A second parent that brings the same names through other imports is walked (the expansion key holds the imports).
- A change inside an entry file reaches what its in-file closure reaches, not only the handlers it names, and the walk goes on past
  an entry file through the names that do not wire it up (a helper in a handler file used by another router file).
- A reach that is the whole module reaches every route in or through the file (module-level statements, dependencies, middleware,
  a prefix constant); a component or helper declared in a routes file that a change reaches reaches its routes; an object layout
  route with no path is a route; a relative top-level path is not claimed as absolute.
- Completeness is not lost at the interface: `entry_points_reached` returns `Reached` (area, routes, complete, reasons),
  `EntryResult.complete` aggregates the walk, the route tables and every area, an unreadable routes file is a problem and not a
  silent skip, a router is not called unmounted while a mount is unresolved.
- The tests that survived mutation now fail for them (incomplete importer lists further out than the first hop, the cycle guard);
  the vacuous cut-search test, the unreadable-file test and the harness (completeness, every implemented type seen) were fixed.
- Minor: a non-literal `methods=` is marked and recorded; entry files beyond the cap still count; `parse_degraded` is recorded
  once per subject; one screen per page file (layouts do not split it); `route_path_partial` is recorded when a route is reached in
  both adapters; paths are joined without slash artefacts; the import-lines-to-bindings maps and `routes_changed` are written once.
- Performance (a generated TypeScript repository of 2,002 files, 30 routes files, a change reaching 40 mids, 400 components and 600
  pages): the walk took 19.5 s and 2,000 git processes, now 0.43 s and 2 (one search per hop, a listing kept per commit, import
  resolution kept per specifier, one `reach` per file and imports); the first-hop analysis took 1.2 s, now 0.5 s.

### Known limits

- Exclusions are stated for the first hop only (the language adapter's); an importer excluded by narrowing at a later hop is not
  listed with its evidence.
- A change that reaches the whole module of an entry file is not followed past it to the files that import it.
- React Router: descendant routes (`<Routes>` inside a component, a `*` splat) are composed only inside the file that declares them
  and are marked partial when nothing above them is in the file; routes outside `routes_glob` are not read; `lazy` object routes
  name the module, whose exported component is taken as its default export; a changed import statement reaches the routes that render
  any of its names, not only the one that changed; a layout route that matches its own path with nothing inside is not listed.
- FastAPI: routers and apps are module-level variables; `add_api_route`, `Mount` and `Route` of Starlette, routers built by a factory
  function, class-based views, `self.router` and decorators on anything that is not a name or a module attribute are not read (a mount
  inside a function is recorded). `FastAPI(root_path=...)` and tags are ignored. A handler of an entry file is reached by its name
  only. The decorators of a handler give its routes whether or not the function is reachable at run time. A route with a trailing
  slash is the same route as without (FastAPI redirects between them).
- Several chains of one area are the also-mounted routes' (React Router) or the chains of different changed files or different entry
  paths (FastAPI); a second path to a file with the same names and the same imports adds no chain.
- Not done: integration with `cli.py`, `Analysis` or findings (the adapters return data and write records; turning them into
  findings and areas of an `Analysis` is the core's job), a Celery adapter (config kind `celery` has none: `entry_adapter` returns
  None), edges between repositories (`hop_cap`, M8). The suite time target of the review (under 160 s) is not met, see below.

### Tests, speed

`tests/test_walk.py`, `test_react_router.py`, `test_fastapi.py` and `test_scenarios_entry.py` build each case as commits on one
empty root in a shared repository (`tests/support_universe.py`), as the M5 and M6 tests do; a case is written with one `git
fast-import` and cases that start with the same first commit share it and the git cache that fetched it. The fixture runs are
shared with the JS and Python scenario modules (`ss.run_scenario` caches them per process, now including the walk and the areas).

Measured on this machine with the full suite (1,451 tests; 1,271 before M7, so 180 are new: walk 45, React Router 59, FastAPI 52,
scenarios 24): Python 3.11 171.5 s, 3.12 174.8 s, 3.13 182.5 s, all passing. Before the review fixes it was 1,406 tests and about
190 s; before M7 about 151 s. The review asked for under 160 s on 3.12 and that is not met: the 180 new tests build about 150
small repositories and a test is mostly git processes (a fetch, a listing, a few reads and searches: 30 to 40 processes at 6 to 9 ms
each on this machine; sys time is half of the suite). What was done: a case is one `git fast-import` instead of about a dozen
processes (this also took the Python adapter tests from 32 s to 26 s), cases share their first commit and git cache, one search per
hop and per table, a listing kept per commit. Alone, on 3.12: walk 9.5 s, React Router 9.0 s, FastAPI 10.3 s, scenarios (entry, cold:
it builds the runs the JS and Python scenario modules otherwise share) 9 s. `perf_gen` (above): walk 19.5 s to 0.43 s; a lattice of
301 files with two parents each: 669 expansions in 3.4 s to 1,139 in 0.3 s.
