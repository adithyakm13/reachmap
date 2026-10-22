# M16 notes: Next.js routes (`nx`, branch `m16-nextjs`)

The brief is section 2 of `docs/briefs/M16-adapters.md` (not edited here). This file is the notes of the Next.js adapter: what was built,
where it differs from or goes beyond the brief, the edits to shared files and why, how each point of the acceptance bar is met, and what
is not done. Where the text and the code disagree, the code is right; each point is covered by a test unless it says otherwise.

## Files

```text
reachmap/adapters/nextjs/routes.py       the file-system routing as a pure function of the paths (no file is read)
reachmap/adapters/nextjs/__init__.py     NextJsAdapter (EntryPointAdapter), the option validator, the registration
tests/fixtures/nextjs/                   two repositories (data), 18 scenarios, README.md
tests/fixtures/workspaces/nextjs.toml    the workspace of the fixtures
tests/fixtures/nx_build.py               builder (the M1 builder's functions, a tree of its own)
tests/fixtures/nx_claims.py              an independent reading of the repositories (shares no code with the adapter)
tests/support_nx.py                      fixtures built once per process, `reachmap analyse` run on a scenario, the report read back
tests/test_nextjs_routes.py              the routing table (paths only)
tests/test_nextjs.py                     the adapter on small repositories
tests/test_nx_fixtures.py                expected.json against the repositories and the oracle
tests/test_nextjs_scenarios.py           the 18 scenarios through the CLI against expected.json
```

Shared files touched (each a registration or a small generic hook; the reasons are below): `reachmap/entrypoints.py`,
`reachmap/config_sources.py`, `reachmap/edges/http.py`, `reachmap/pipeline.py`, and one assertion of `tests/test_scenarios_entry.py`
(`entrypoints.KINDS` is now compared by its first two entries, since a registered adapter adds to the tuple). Nothing in M1's data,
`docs/PROGRESS.md` or `docs/briefs/M16-adapters.md` changed (`git diff` against the base commit shows nothing under
`tests/fixtures/repos`, `scenarios`, `workspace.json`, `README.md`, `build.py`, `claims.py` or `reachmap.toml`).

## Configuration

```toml
[repos."acme/web"]
language = "typescript"
roles = ["frontend", "backend"]
aliases = { from = "tsconfig.json" }
entry_points = { kind = "nextjs" }                     # root = "apps/web", page_extensions = ["tsx", "ts"], base_path = "/docs"
```

`root` is the folder of the Next project in the repository (default the repository root; one project per repository entry),
`page_extensions` the Next `pageExtensions` (default `tsx ts jsx js`), `base_path` the `basePath` of the Next config (put before every
URL). Options are checked by a validator registered with the kind (a key that is not an option, a type, a `root` that leaves the
repository, a `base_path` that is not a plain path: each by key path, never quoting the value). `language` must be `typescript` or
`javascript`: the adapter is built on the M5 JavaScript adapter (aliases from the tsconfig, importers, narrowing) and refuses another
language adapter with a `TypeError`.

## Rules as built

### The route table (`routes.py`)

1. App Router: the folders under `app/` (or `src/app/` when the root has no `app/`, as Next does) are the URL; `page.<ext>` is a
   screen, `route.<ext>` an HTTP handler. A route group `(g)` and a parallel slot `@s` are not in the URL; `(.)x`, `(..)x`,
   `(..)(..)x` and `(...)x` intercept (the URL they intercept is computed on route levels: a group or a slot is not a level) and make the
   route partial; `[id]`, `[...rest]`, `[[...rest]]` are a parameter, a catch-all and an optional catch-all (two URLs); a folder that
   starts with `_` and everything below it is private and never routed. A segment that cannot be read as a route part (`post-[id]`, a
   catch-all that is not last) is kept as `<unknown:TEXT>`, listed in `Entry.unknown`, and the route is partial.
2. Pages Router: every file of `pages/` is a route (`index` is its folder), `pages/api/**` are API routes, `_app` and `_document` are
   wrappers, `_error` is not a route, a `.d.ts` file is not a route, and `pages/api.ts` is an API route (`/api` itself, as Next's `isAPIRoute`). `_`, `@` and `(...)` mean nothing there: a name is a segment.
3. Screen paths are written the way React Router writes them (`/products/:slug`, a catch-all as a trailing `*`) so the M15 spec
   matcher works on them unchanged; API paths the way FastAPI writes them (`/api/orders/{id}`, `{rest:path}`) so the http edge matches
   them. One test file never is a route: `.test.` or `.spec.` in the name, or a `__tests__` folder; the repository's folder markers
   (`test/`, `tests/`) are not used for routing, because a folder called `test` in `app/` is a URL segment (see Known limits for what
   the language adapter does with it).
4. Wrappers and what they wrap (`Table.wrapped`): `layout`, `template`, `loading`, `error`, `not-found`, `global-error` the pages of
   the App Router below their folder (the folders compared as written, groups and slots included); `default` the pages below the folder
   that holds its slot; `forbidden` and `unauthorized` are wrappers like `not-found`; `_app` and `_document` the pages of the Pages Router;
   `middleware` (and `proxy`, the Next.js 16 name) and `instrumentation` (it runs for the whole server) beside the router folders every
   page, handler and API route. That folder is found as Next finds it: the parent of `pages/` when there is one, else of `app/`
   (`join(pagesDir || appDir, "..")`), so a root `pages/` with `src/app` reads the root files. A layout does not wrap a route handler.
5. File-based metadata routes (`sitemap`, `robots`, `manifest`, `icon`, `apple-icon`, `opengraph-image`, `twitter-image` with a code
   extension) are GET routes at the folder's URL plus `sitemap.xml`, `robots.txt`, `manifest.webmanifest` or the file's name.

### What a change reaches (`entry_points_reached`)

6. The walk is M7's. `is_entry_file` is the table of routing files at the commit; `structural_names` are the exports Next itself reads
   (`default`, `metadata`, `generateMetadata`, `generateStaticParams`, `GET` ..., `getServerSideProps` ...): the walk does not go past
   an entry file through them, and does through any other export (a helper exported from a page).
7. A page, a Pages API file and a metadata route are reached by anything that reaches their file (an import of changed code, an edit):
   the file is the route. One `Reached` per screen: routes that share a path or a file are one screen (a page and the page of a
   parallel slot of one URL, a page and an intercepting page, an optional catch-all's two paths). The label is the smallest path, the
   others `also_mounted`; of one URL the page of its own folder is the primary route, then a slot page, then an intercepting one; one
   chain per route file.
8. A route handler gets a route per exported HTTP method per URL (`export async function GET`, `export const GET`, `export { h as GET }`,
   `export { GET } from`, `export * from` followed up to five files deep (a sixth is a blocking `specifier_unresolved` and the route table and the reach are not complete, never a silent cut), `export const { GET, POST } = handlers`). A change reaches the
   methods whose declaration (or exported binding, or re-export) the reached closure holds; a statement that runs when the file loads
   (a changed line outside every declaration that is not an import) reaches every method; a changed import line reaches the methods
   that use its bindings. Never HEAD or OPTIONS from a guess: only names that are exported.
9. A Pages API file is one route, method `ANY` (a Pages API handler answers every method; the methods it tells apart by `req.method`
   are not read, which would be a guess that could drop one).
10. A wrapper reaches every page it wraps. The chain is the changed line (or the hops of the import), the line of the wrapper that
    renders its children (the last line that names `children`, `Component` of `_app`, `Main` of `_document`, the `middleware` function;
    else the wrapper's default export), the page's default export. That line carries the claim `wraps:<wrapper path>`: a decision does
    not go stale when the wrapper is reformatted. The run's notes say it (`EntryResult.notes`, see the hooks): "<path> wraps the N
    screen(s) below its folder: a change to it reaches each of them; no line states the nesting (it is the folder tree) ...". The
    priority by count is the model's `assign_priorities` (eight screens or fewer must test, more sample).
11. A deleted wrapper, or the old path of a renamed one, is no chain of the walk. `begin_change(analysis)` gives the adapter the change
    and the pages that were below the wrapper at the pre-change commit and still exist at the head are reached, through the wrapper's
    line as it was (pinned at the pre-change commit) and the page's line at the head. Without a pre-change version this is not
    computed (the change set already recorded `pre_change_unavailable`).
12. An entry file that imports another entry file (a page that is `export { default } from "../a/page"`, a handler re-exported by name)
    is reached with it: the adapter keeps an index of the imports between routing files (parsed once per commit) and follows it from
    each reached file to a fixed point, narrowed by the names that arrive; a change that reaches the whole module of an entry file is
    carried to every such importer (the walk stops there; this does not).
13. The evidence of a route is one line: a page's default export (claim `route:<path>:<file>`), a handler's method export (claim
    `route:<METHOD> <path>:<file>`). `Route.partial` is set for an intercepting route and for an unknown segment.

### The route table is a result, not a guess

14. Every read answers found, absent or unreadable. An unreadable routing file, a file the tree lists that is absent, a tree that could
    not be listed, a routing file that cannot be parsed while the index of imports is built, an `export *` that resolves to nothing,
    and a walk chain whose entry is not a routing file are `problems` (so `EntryResult.complete` is False and the http edge says the
    routes were not read completely) and, where the Git layer does not already record it, a record.
15. A route file that does not lex claims no route: its methods cannot be told. The record is a blocking `parse_degraded` (subject
    the path). A page, a Pages API file, a wrapper or a metadata route that does not lex is still a route (its URL comes from its path,
    not its text): it is cited at its first code line, its area is not complete, and the `parse_degraded` is not blocking.
16. `routes_changed(before, after)` returns API routes only: the http edge reads every route it returns as an HTTP route (a new page
    would otherwise be `added_route_uncalled`). `screens_changed` has the screens.

## Records

New kinds (registered by the package, non-blocking, selection scope): `route_intercepted` (subject the path of the intercepting file): an intercepting
route is shown in place of another one only on some navigations; `route_conflict` (subject the two files, `a and b`, sorted): two files answer one URL,
which Next.js refuses to build (a `page` and a `route` of one folder, an App Router page and a Pages Router page, two route groups, a route handler and a
Pages API file; parameter names are not compared; a slot page, an intercepting page and a metadata route are not conflicts), so which serves it is not
established and both are reached (affected). Both are made when a change reaches the route. Existing kinds used:
`parse_degraded`, `specifier_unresolved` (an `export *` that does not resolve), `read_unreadable`, `route_path_partial` (an unknown
segment, subject `<path>:<line>`), `not_distinctive` (by the language adapter: `GET` defined in more than `distinct_max` files).

## Edits to shared files, and why

| File | Change | Why |
|---|---|---|
| `reachmap/entrypoints.py` | the registration (`from reachmap.adapters.nextjs import register`, `register(...)`: the kind in the registry of adapters and in `config.register_kind`, with its option validator) | the registry call the brief asks for |
| `reachmap/entrypoints.py` | `reach` calls `entry.begin_change(analysis)` when the adapter has it; `EntryResult.notes` (default empty) carries `entry.notes` | a generic hook, not adapter code: a deleted layout has no importer, so the walk gives no chain for it, and a wrapper's fan-out needs a place in the report to be said. Adapters that do not define them are untouched |
| `reachmap/pipeline.py` | `notes.extend(reach.notes)` after the entry step | the notes of the entry-point adapter reach the report (two lines) |
| `reachmap/config_sources.py` | `"nextjs"` in `SCREEN_ENTRY_KINDS` | the M15 spec sources accept a Next.js repository as the source of screens (one tuple entry). Tested with the spec matcher on the adapter's routes |
| `reachmap/adapters/base.py` | `module_changes`, `screen_area`, `api_area`, `distinct_chains`: the near copy of FastAPI's `_module_changes` and the `Area` assembly React Router and FastAPI each wrote, used by all three adapters | review: one implementation. FastAPI and React Router call them; their tests are unchanged |
| `reachmap/model.py` | the `Evidence.claim` docstring names the role `wraps:<wrapper path>`; `tests/test_claims.py` accepts it and has a case on the nx reports | the claim is a new role |
| `tests/test_edge_http.py` | a unit test: a route of method `ANY` fits a call of any method exactly and nothing else is a wildcard | the `_fits` change below |
| `reachmap/edges/http.py` | `_fits`: a route whose method is `ANY` fits a client call of any method | a Pages API file answers every method; without this a client's GET fell back to a flagged guess, and a removed `ANY` route was never `removed_route_still_called` (the fallback is skipped for removals by design). One line, generic |

## Fixtures

`tests/fixtures/nextjs/` (README there): `nx/shop` (both routers, route groups, dynamic, catch-all and optional catch-all segments, a parallel slot,
an intercepting route, a private folder, route handlers with several methods, a Pages API, middleware, a shared component imported by an alias and by a relative path, a
test, names that look like routing files and are not) and `nx/mobile` (a client with an endpoints file and React Router screens, for the http edge). The slots have a `default.tsx` each (`@analytics`, `@modal`: wrappers of the folders that hold the slots; no scenario changes
one, and no expected answer depends on them). 18
scenarios `nx01` to `nx18`, hand-traced (`explains` in each `expected.json`), run through `reachmap analyse` by `test_nextjs_scenarios.py` with `scenario_support.check` and no
type skipped. nx17 lists all 23 areas of the shop and the three client screens by hand and is `complete_for` screens, routes and http edges; the oracle test still says the list is the route table. The fixtures are in their own tree (not `tests/fixtures/scenarios`) because the M1 builder, `build.scenarios` and about a dozen test modules walk every folder there and
assume the acme workspace; a new scenario folder would break them, and the brief forbids touching them. The M1 repositories, shas and expected files are unchanged
(`test_the_m1_fixtures_are_untouched_by_the_nx_ones`, and the diff against the base commit shows no change to the M1 fixture files).

## Acceptance bar (context section 10 and the M16 brief)

| Point | Tests |
|---|---|
| Reproduces a hand trace on the fixture and invents nothing beyond it | `test_nextjs_scenarios.EndToEnd.test_the_report_of_every_scenario_matches_expected_json` (18 scenarios; `complete_for` on changed symbols, callers, screens, routes, http edges, findings where the scenario says so, the complete list of records, `complete`), `test_nx_fixtures.*` (the expected files against the repositories and the independent oracle: screens, routes, wrapper scope, importers, import hops, locators in code at their revision, URL fit), `test_nextjs_scenarios.EndToEnd.test_the_harness_fails_when_the_report_is_wrong` |
| Shape A: a failed read is an incompleteness, never an absence | `test_nextjs.ShapeA.test_an_unreadable_page_is_a_problem_and_not_an_empty_route_table`, `..._a_file_the_tree_lists_that_is_absent_is_recorded_not_dropped`, `..._a_tree_that_cannot_be_read_is_a_problem_and_not_no_routes`, `..._a_chain_that_is_not_complete_makes_its_screens_not_complete`, `..._a_chain_to_a_file_that_is_not_a_routing_file_is_a_problem`, `Wrappers.test_a_wrapper_that_cannot_be_read_is_a_problem_not_an_empty_answer`, `NextAuthShapes.test_an_unreadable_routing_file_met_while_looking_for_re_exports_is_a_problem`, `RouteHandlers.test_a_handler_file_that_does_not_lex_claims_no_route_and_blocks`, `RouteHandlers.test_an_export_star_of_a_module_that_does_not_resolve_is_a_blocking_record`, `DirectChange.test_a_page_that_does_not_lex_is_still_a_screen_and_says_so`, `Removals.test_without_a_pre_change_version_removals_are_not_computed_and_nothing_is_claimed`, scenario nx16 (`test_an_unlexable_handler_makes_the_answer_not_complete_and_claims_no_route`) |
| Every name match is dropped when the word is generic | `test_nextjs_routes`: `AppRouterPages.test_only_the_special_file_names_are_routed`, `test_a_private_folder_and_everything_below_it_is_not_routed`, `RootsAndSrc.test_files_outside_the_router_folders_are_not_routes`, `Middleware.test_a_file_called_middleware_anywhere_else_is_not`, `MetadataAndProxy.*`, `Names.test_http_methods_are_exact_upper_case_names`; `test_nextjs`: `Wrappers.test_a_file_called_layout_outside_the_app_folder_wraps_nothing`, `..._in_a_private_folder_wraps_nothing`, `..._a_file_called_middleware_that_is_not_beside_the_router_folders_is_no_middleware`, `RouteHandlers.test_get_in_lower_case_a_helper_called_handler_and_other_exports_are_not_methods`, `..._a_file_called_route_outside_the_app_folder_is_not_a_handler`, `Citations.*` (a comment or a string that names `children` is not the line that renders them); scenarios nx05 and nx14 (`GET` is `not_distinctive`, and links nothing), nx12 (four files named like routing files, every page and route a name match would claim is excluded) |
| Every citation resolves at its pinned commit | `test_nextjs_scenarios.EndToEnd.test_every_evidence_in_every_report_resolves_at_its_pinned_sha` (the e2e audit, 18 reports), and `Base.reach` of `test_nextjs` asserts `scenario_support.evidence_problems` for every chain of every adapter test |
| Through `reachmap analyse`, an explicit list of types per scenario, no skip | `EndToEnd.test_no_type_is_skipped_in_any_scenario`, every `expected.json` lists its `must_find`, `must_exclude`, `complete_for` and `not_established` |
| Determinism, wording, nothing about the machine, a fresh process | `EndToEnd.test_two_runs_give_the_same_bytes_except_the_run_object`, `..._runs_as_a_separate_process_and_the_kind_is_known_to_a_fresh_interpreter`, `..._passes_the_wording_check`, `..._says_anything_about_the_machine_it_ran_on` |

Mutation check (each mutation made by hand, the three nextjs modules run, the mutation reverted; all killed): the fan-out ignoring the folder, private folders
routed, a lower case `get` a method, the tie at the first line, no import between entry files, no removed wrapper, middleware that wraps nothing, every
method reached, an unreadable file not a problem, every export structural, an optional catch-all of one path, a route group in the path, an intercepting route
keeping its folder, the slot page first, `ANY` not fitting a method, `begin_change` not called, a handler that does not lex not blocking, the base path dropped, a test file a
route, module-level statements reaching nothing.

## Choices (where the brief is open)

- Screens and API paths use the two existing spellings (React Router and FastAPI), so M7's `Reached`, M8's matcher and M15's spec matcher work unchanged.
- One screen per URL, not per page file, as the brief says "one screen per page file": a parallel slot's page and an intercepting page are parts of the screen of the URL they
  answer, and two screens with one label would collide in an area id. A page file with two URLs is one screen with `also_mounted`.
- `ANY` for a Pages API file (and not the seven standard methods, which would be seven areas for one handler, and not a read of `req.method`, which would be a guess).
- A wrapper's reach is stated in the run's notes (a small hook) and cited in the chain (`wraps:` claim on the line that renders the children); the finding's confidence is the
  usual (import-backed).
- Middleware reaches everything, its matcher not evaluated. `next.config.*` is not treated as a wrapper (a rewrite, a redirect or a base path would reach every route; `base_path`
  is an option, nothing else is read).
- Routing ignores the repository's folder test markers (a route called `/test` is a route) and uses file-name test patterns only.

## Known limits

- `page_extensions` such as `["page.tsx", "api.ts"]` (the Next.js trick to colocate tests in `pages/`) are not supported: the validator refuses an extension that is not letters
  and digits, and test files are excluded by name (`.test.`, `.spec.`, `__tests__`) instead.
- Two files that answer one URL (a Next.js build error) are recorded (`route_conflict`) and both are reached; which one serves is not decided. Dynamic parameter names are not compared
  (`[id]` and `[slug]` at one place are a conflict, as they are in Next).
- `page.mdx`, `layout.mdx` and other markdown routes are not read (the JavaScript adapter does not parse `.mdx`: it records `file_not_analysed` for a changed one). `pageExtensions` is
  an option, but only extensions the JavaScript adapter parses can be read.
- A non-code file that a routing file imports (`globals.css` in the root layout) is not an importer search target for the JavaScript adapter (it records `file_not_analysed`,
  not blocking): a change to it reaches nothing here.
- The middleware matcher, `next.config` rewrites, redirects, headers and i18n routing, `generateStaticParams` and `dynamicParams` are not evaluated; a client-side `<Link href>` or
  `redirect()` is not an edge; a server action is reached by the import of its file, not as a route.
- A Pages API handler is `ANY`: the methods it tells apart by `req.method` are not read. A route file whose methods are computed (`export const GET = ...` inside a condition,
  `export { ...handlers }` through a spread) is read only as far as the names that are exported. `export *` is followed five files deep and a deeper one makes the answer incomplete.
- Several Next projects in one repository need one repository entry each (one `root` per entry); a monorepo package imported by an app (`@acme/ui`) is not a repository file.
- `pages/_error` and a custom `pages/500` special case are not modelled: `_error` is not a route, so a change to it reaches no screen; `500.tsx` is a page.
- Parallel slots: a slot page joins the screen of its URL; a `default.tsx` wraps the pages below the folder that holds its slot (the scope is the folder, not the slot's own
  navigation state); a layout inside a slot wraps the pages inside that folder. An intercepting route is partial and merged by URL; whether the interception happens is not decided.
- Two page files that answer one URL by route groups (a build error in Next) are one screen.
- The spec matcher of M15 reads a trailing `*` as zero or more segments, a Next.js required catch-all `[...slug]` needs at least one: a visit to `/docs` matches the screen `/docs/*`.
- The JavaScript adapter treats a folder called `test` or `tests` as a test folder: a page there is routed here (the table has it) but the walk never goes through it (tests are not
  callers), so an import of changed code in `app/test/page.tsx` does not reach `/test`. Configure `tests.markers` without those two to change that.
- `Propagation.backend_calls` for a Next.js handler says "the server's language adapter does not list imports" (incomplete): the modules a handler runs are listed for Python only.
- The importer search for a file called `page.tsx`, `layout.tsx` or `route.ts` uses the stem (`page`) as the candidate filter, so it parses every file that has the word; the answer
  is complete, the cost grows with the repository (a 2,112 file repository: the language analysis 0.84 s and the entry-point step 0.37 s for a change reaching 220 areas, 1.2 s together; see Tests, speed).

## Not done

- No `next.config` reading, no `basePath` detection (an option instead), no i18n domain routing, no matcher evaluation.
- No `.mdx` pages, no `generateSitemaps` routes, no route handlers declared through `export const runtime` alone.
- Evaluation against a real Next.js repository (the fixtures are synthetic, shaped from the documented conventions of Next.js 13 to 16; nothing was run against Next itself).

## Tests, speed

3,304 tests on 3.12 (3,131 before: 173 are new; 3,309 on 3.11, which skips one fewer): `test_nextjs_routes` 40, `test_nextjs` 93, `test_nx_fixtures` 22,
`test_nextjs_scenarios` 18. All pass on 3.11, 3.12 and 3.13 with the parallel runner (4 workers) and in one serial `unittest discover` on 3.12. The machine was shared with other
agents' runs the whole time (load average 4 to 15 during these runs, 1.5 for the baseline), so the times say little about the change:

| Python | parallel (`run_parallel.py`, 4 workers) | serial discover |
|---|---|---|
| 3.12 | 252 s (the baseline before this branch, at load 1.5: 178 s for 3,131 tests) | 808 s (load 7 to 11) |
| 3.11 | 261 s | not run |
| 3.13 | 342 s | not run |

The new modules alone on 3.12 with nothing else of mine running: `test_nextjs` 16 to 18 s, `test_nextjs_scenarios` 9 s (the fixtures are built once per process, 18 pipeline
runs), `test_nx_fixtures` 5 s, `test_nextjs_routes` under 0.1 s; under the load above in the parallel run: 54 s, 26 s, 16 s and 0 s. `test_nextjs` is now about as long as the
longest M5 module under load. `tests/.timings.json` was not refreshed (a busy machine would write the wrong weights).

Performance of the adapter itself (a throwaway generator, not a test): a repository of 2,112 files (1,500 pages in 300 folders, 400 components, 60 route files with two methods,
150 layouts), a change to a library file reached by 40 components, 160 pages and 60 routes: the language analysis 0.84 s and the entry-point step 0.37 s (220 areas); a layout
change reaching 5 pages: 0.67 s and 0.49 s. The import index between routing files is parsed once per commit.

### After the review fixes and the merge of `main`

`main` (M13b frontend redesign, runner static nonce, docs) was merged into the branch with no conflict. The whole suite on 3.12 with the parallel runner: 3,335 tests, all pass, 234 s,
load average 5 to 7 (other agents' runs). The nx modules: `test_nextjs_routes` 50, `test_nextjs` 103, `test_nx_fixtures` 23, `test_nextjs_scenarios` 19 (195 of the 3,335; 24 more than before the
review fixes), `test_claims` 10 and `test_edge_http` 53 (one new test each); `test_react_router`, `test_fastapi`, `test_walk` and the scenario modules pass unchanged after the helpers moved to
`adapters/base.py`. The 3.11 and 3.13 runs and the serial discover were not repeated after the fixes.
