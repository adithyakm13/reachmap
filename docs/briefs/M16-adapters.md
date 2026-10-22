# M16 brief: first-release adapters (run in parallel)

Goal: five adapters from context 13.3, each in its own branch and git worktree, each with
its own synthetic fixture, each meeting the adapter acceptance bar of context section 10.
They run in parallel because they touch disjoint files; shared registries are touched in
one line each (resolve conflicts at merge). Read `docs/CONTEXT.md` sections 3, 4, 5, 7.3,
7.4, 7.5, 10 (acceptance bar), 13.3; `tests/fixtures/README.md`; and the Implementation
notes of M3 (registries: `register_kind`, `register_language`, `register_edge_keys`), M5
and M6 (language adapter base, records, completeness, narrowing lessons), M7 (walk,
EntryPointAdapter protocol, Reached, EntryResult, route tables), M8 (edges, http matching,
`client_url_*`, records invariant), M9 (pipeline registry, scenario harness, e2e).

## Common rules for every adapter

- Python 3.11+ stdlib only. No compiler, no JVM, no node, no Django import: parse text.
- Register through the existing registries; no change to the core beyond a registry entry
  and, if truly needed, a small generic hook (say why in the notes; never adapter-specific
  code in the core).
- New synthetic fixture: a new small git repository (or two) built by the M1 builder from
  data under `tests/fixtures/repos/<new-slug>/` with scenarios under
  `tests/fixtures/scenarios/<prefix>NN-*/` (prefix per adapter: `oa`, `nx`, `ex`, `dj`,
  `sb`) and hand-traced `expected.json` (schema 1, same rules as M1, including the
  recompute checks in `tests/test_fixtures.py`, extended for the new repo shapes in a
  separate helper module so other branches do not conflict). Existing repos, branches,
  shas and expected files stay byte-identical. A separate workspace config
  `tests/fixtures/workspaces/<adapter>.toml` (and its JSON twin if the harness needs it).
- Acceptance bar (context 10): reproduces a hand trace on the fixture and invents nothing
  beyond it; every Shape A trap that applies has a fixture or test that makes the read fail
  and asserts an incompleteness; every name match has a fixture with a generic word and
  asserts it is dropped; every citation resolves at its pinned commit (the e2e evidence
  audit).
- End to end: the new scenarios pass through `reachmap analyse` (M9 harness), with an
  explicit list of expected types per scenario; no skip left.
- Undecidable means affected; never an empty result marked complete when a read, parse or
  match could not be established.
- Tests: parallel runner on 3.11, 3.12, 3.13 plus one serial discover. Small plain commits,
  no push, no PROGRESS edit. Notes appended to this brief's section for the
  adapter (each branch edits only its own section file: `docs/briefs/M16-<adapter>.md`).

## 1. OpenAPI as an HTTP edge (`oa`, branch `m16-openapi`)

- `reachmap/adapters/openapi/`: read a committed spec (`openapi.json`, `openapi.yaml`
  limited: JSON required; YAML only if a tiny safe subset parser is justified, else record
  YAML as `file_not_analysed` and say so) at a configured path per repo
  (`http_server = { kind = "openapi", spec = "api/openapi.json" }` on the server repo,
  or `http_client = { kind = "openapi", spec = ... }` on a client repo that uses a
  generated client). Routes from `paths` and `servers` base paths, `$ref` resolution with
  cycle detection, `operationId`.
- Server side: routes from the spec (as M7 route tables), with evidence at the spec line
  (line numbers from a JSON tokenizer that tracks positions).
- Client side: generated client calls by `operationId` method names (e.g.
  `getInvoice(`) matched to operations, distinctiveness-capped (`get`, `list` alone are
  generic: dropped and recorded).
- A spec diff is a change: removed or changed operations (method, path, required params,
  request/response schema refs) produce `removed_route_still_called` / a new
  `operation-changed` finding kind with evidence at both spec versions; added operations
  `added_route_uncalled` (same gating as M8).
- Fixture: a server repo with a spec and a handler tree in a language we already parse
  (Python FastAPI-less plain handlers) and a TS client with a generated client file.

## 2. Next.js routes (`nx`, branch `m16-nextjs`)

- `reachmap/adapters/nextjs/`: entry-point adapter for `app/` (App Router: `page.tsx`,
  `layout.tsx`, route groups `(group)`, dynamic `[id]`, catch-all `[...slug]`, optional
  `[[...slug]]`, parallel `@slot`, intercepting `(.)` segments recorded as partial,
  `route.ts` API handlers with exported `GET`/`POST`) and `pages/` (Pages Router: files,
  `index`, `[id]`, `_app`/`_document` excluded, `pages/api/*` as API routes).
- Screens from the folder tree; one screen per page file; layouts reach every page below
  them (a changed layout reaches all child screens: say so, priority by count).
- Uses the M5 JS adapter for imports (`@/` aliases from tsconfig).
- Fixture: a Next.js-shaped repo with both routers, route groups, dynamic segments, a
  shared component and a layout.

## 3. Express and NestJS servers (`ex`, branch `m16-express`)

- `reachmap/adapters/express/`: `app.get/post/...(path, ...handlers)`, `router.METHOD`,
  `router.route(path).get(...)`, `app.use(prefix, router)` mounts across files (by import
  resolution through the M5 adapter, `require` and ESM), nested routers, `express.Router()`
  with `mergeParams`; unmounted routers recorded (`router_unmounted`); non-literal paths
  partial.
- NestJS: `@Controller('prefix')` + `@Get(':id')` etc., global prefix from
  `app.setGlobalPrefix('api')` in `main.ts`, module `controllers: [...]` registration
  (a controller not registered in any module is unmounted).
- Fixture: an Express service and a NestJS service (TypeScript), plus a small client file
  in the new fixture repo for an http edge (existing fixtures stay unchanged).

## 4. Django and Django REST Framework (`dj`, branch `m16-django`)

- `reachmap/adapters/django/`: `urls.py` `urlpatterns` with `path()`, `re_path()`
  (regex routes kept as partial patterns), `include()` chains across apps (string module
  paths and module objects), namespaces, `ROOT_URLCONF` from settings; views (functions,
  `as_view()` classes), DRF `routers.DefaultRouter().register(prefix, ViewSet)` expanding
  to list/detail routes and `@action` extras.
- Uses the M6 Python adapter for imports and callers.
- Fixture: a Django project with two apps, an included urls chain, a DRF viewset, a router,
  an unused urls module (unmounted), and a generic view name.

## 5. Spring Boot with a Java / Kotlin language adapter (`sb`, branch `m16-spring`)

- `reachmap/adapters/jvm/`: a language adapter for Java and Kotlin (lexer: comments,
  strings, text blocks, char literals, annotations, generics; declarations: package,
  imports including static and wildcard, classes, interfaces, methods, Kotlin `fun`,
  `object`, companion; spans by braces; changed symbols by diff line number; importers by
  package + class name resolution across the source roots `src/main/java`,
  `src/main/kotlin`; same-package references without import (record as by-name
  candidates when ambiguous); callers; tests by `src/test/`); definitions for
  distinctiveness.
- `reachmap/adapters/spring/`: `@RestController`/`@Controller` with class-level
  `@RequestMapping` and method-level `@GetMapping`, `@PostMapping`, `@RequestMapping(method
  = ...)`, path variables, constants in annotations (resolved when a static final String in
  the same project, else partial), `server.servlet.context-path` from
  `application.properties`/`application.yml` (simple key lookup; YAML partial support or
  recorded).
- Fixture: a small Spring Boot service in Java with one Kotlin controller, a service
  layer, a controller calling a service, a test, and a constant-based path.

## Coordination

- Each adapter session works in its own git worktree on its own branch cut from `main`
  (M15 is merged). Files touched
  by more than one adapter: the registry lists (`reachmap/config.py` kinds,
  `reachmap/entrypoints.py`, `reachmap/edges/__init__.py`, language registry) and
  `tests/test_fixtures.py` hooks. Keep each of those changes to one added line or a
  registration call inside the adapter package, so merges are trivial; the coordinator
  merges them one at a time, rebasing the others.
