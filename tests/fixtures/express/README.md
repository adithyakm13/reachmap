# Express and NestJS fixtures (M16, section 3)

Four small synthetic git repositories and 25 change scenarios for the Express and NestJS entry-point adapters
(`reachmap/adapters/express`). They are a tree of their own, apart from the M1 fixtures, because `build.scenarios()` and about a dozen
test modules walk every folder under `tests/fixtures/scenarios/` and assume the acme workspace. Nothing of M1 changed.

```text
tests/fixtures/express/
  repos/<slug>/history.json       one step each (a single commit on main)
  repos/<slug>/steps/NN-x/        the files of the commit
  scenarios/exNN-slug/            scenario.json, changes/ (overlay, optional .delete), expected.json
tests/fixtures/ex_build.py        the builder (it reuses the M1 builder: identity, dates, git environment, overlays)
tests/fixtures/ex_claims.py       an independent reading of the repositories (it does not import the adapters)
tests/fixtures/workspaces/express.toml   the workspace of the four repositories
tests/support_ex.py               the fixtures built once per process, `reachmap analyse` run on a scenario, the report read back
```

| Repo | What it is |
|---|---|
| `ex/orders` | An Express service in TypeScript with a CommonJS corner. `src/app.ts` (the app, `app.use(API_PREFIX, apiRouter)`, a search router mounted at `API_PREFIX + "/search"`, `app.use("/legacy", require("./legacy"))`, a commented-out mount of an unmounted router), `src/routes/index.ts` (`apiRouter` mounts the orders router, the customers router with `mergeParams`, an admin router read through a namespace import and the reports router, by prefixes made of a constant and of a template literal), route files with `router.get`, a `router.route(...).get(...).post(...)` chain, an inline handler, a route middleware, a path that is a call; `src/legacy/*.js` (`require`, a destructured `require`, `module.exports = router`), controllers, services, middleware, tests, and the look-alikes: `src/lib/events.ts` (a `router` made by `createRouter()`), `src/lib/warm.ts` (`cache.get("/orders/:id", load)`), and `get` defined in five files. |
| `ex/ledger` | A NestJS service: `main.ts` (`NestFactory.create(AppModule)`, `app.setGlobalPrefix("api")`, `app.enableVersioning` with URI versioning), `AppModule` importing `LedgerModule` and `BalancesModule`, `LedgerController` (`@Controller(ENTRIES_PATH)`, four methods, two injected services), `BalancesController` (a version), `AuditController` (listed by no module), `ReportsController` (listed by a module nothing imports), and `CustomController`, whose decorators come from `./decorators` (it does import `Param` from `@nestjs/common`). |
| `ex/web` | A React Router client of the orders service (an endpoints file, services, four screens). |
| `ex/console` | A React Router client of the ledger service (an endpoints file, services, three screens). |

The two clients are two repositories because an http edge reads every call of its client against the routes of its server: a client that calls a second server leaves
calls that fit nothing of the first, which is said (`client_url_unmatched`) and keeps `added_route_uncalled` from being claimed.

## Routes of ex/orders (main)

```text
GET /health   GET /legacy/invoices   GET /legacy/invoices/{invoiceId}   GET /api/search
GET /api/v1/admin/stats   GET /api/v1/customers   GET /api/v1/customers/{customerId}   GET /api/v1/customers/{customerId}/orders
GET /api/v1/orders   GET /api/v1/orders/{id}   POST /api/v1/orders   GET /api/v1/orders/{id}/lines   POST /api/v1/orders/{id}/lines
GET /api/v1/reports/<unknown:dailyPath()>   GET /api/v1/reports/monthly
```

The router of `src/routes/draft.ts` is mounted by nothing. Routes of ex/ledger: `GET /api/ledger/entries`, `GET /api/ledger/entries/{id}`, `POST /api/ledger/entries`,
`DELETE /api/ledger/entries/{id}`, `GET /api/<unknown:version>/balances` and `.../balances/{account}`; the audit and the reports controllers are served by nothing.

The evidence of a route is its own line (the line of the method call; for a `.route(path)` chain also the `.route(` line; for Nest the method decorator), then each mount line
from the router outwards (Nest: the controller decorator when it has a path, the module's `controllers` line, the global prefix line).

## Scenarios

| id | repo | change | what it proves |
|---|---|---|---|
| ex01-service-narrowed | orders | `findOrder` in a service | named-export narrowing across two files, a route through two mounts, the http edge and the client screen, a unit test |
| ex02-inline-handler | orders | a line inside a handler written in a route call | a module-level change reaches the statement that holds it, one route |
| ex03-nested-mounts | orders | `stats` of a controller | a namespace import, a template literal prefix, a constant imported by name, two mounts |
| ex04-route-chain | orders | `addLine` | one call of a `router.route(...).get(...).post(...)` chain |
| ex05-unmounted-router | orders | a handler of an unmounted router | `router_unmounted`, no route, a mount in a comment is not a mount |
| ex06-commonjs-legacy | orders | `getInvoice` | `require`, destructured `require`, `module.exports`, an inline `require` mount; no narrowing by name for `require` |
| ex07-partial-path | orders | `daily` | a path that is a call: marked, partial, `route_path_partial` |
| ex08-app-middleware | orders | `requestLog` used by `app.use` | middleware of the app reaches every route (undecidable means affected) |
| ex09-route-middleware | orders | `requireAuth` named in two route calls | the two routes and not the others |
| ex10-removed-route | orders | a route statement removed | `removed_route_still_called` and the client screen |
| ex11-added-route | orders | a route statement and a controller added | `added_route_uncalled`; a client parameter does not fit a route literal |
| ex12-router-in-function | orders | a router factory | `routes_unread`, blocking, never read as no route |
| ex13-generic-names | orders | `get` of a controller | `not_distinctive` (`get` defined five times); the reach is by import and by the member a route spells |
| ex14-lookalike-router | orders | a `router` and a `cache.get` that are not Express | nothing claimed |
| ex15-nest-injected-property | ledger | `AuditLog.record` | the methods that use the injected property, not the others; an unserved controller said |
| ex16-nest-method-body | ledger | a line inside `findOne` | one route, the http edge and the client screen |
| ex17-nest-unreachable-module | ledger | a controller of a module nothing imports | `router_unmounted` |
| ex18-nest-unregistered | ledger | a controller no module lists | `router_unmounted` |
| ex19-nest-module-change | ledger | the providers of `LedgerModule` | every route of the controllers the module lists |
| ex20-nest-bootstrap | ledger | a line of `bootstrap` in `main.ts` | every served route, not the unserved ones; a versioned controller partial; a by-name candidate screen |
| ex21-nest-versioned-partial | ledger | a method of a versioned controller | `<unknown:version>`, partial, `route_path_partial` |
| ex22-nest-custom-decorators | ledger | a method decorated by decorators of the repository | not a route (the import decides, not the word `Get`) |
| ex23-nest-inherited-routes | ledger | a controller that extends a class with routes | `routes_unread`, blocking |
| ex24-unreadable-router | orders | the customers router does not lex | not a removed route (the client calls its routes): `parse_degraded`, `mount_unresolved`, `route_change_withheld`, all blocking, no finding |
| ex25-unreadable-module | ledger | the ledger module does not lex | not four removed routes: the same three records, no finding |

`expected.json` is schema 1 as in M1 (`tests/fixtures/README.md`), written by hand from the files by the scenario author; the new kinds of record are `routes_unread` (subject
`<path>:<line>`), and, with the formats FastAPI uses, `router_unmounted` (`<path>:<variable>` for Express, `<path>:<Class>` for Nest), `route_path_partial` (`<path>:<line>`) and
`mount_unresolved` (`<path>:<line>`).
