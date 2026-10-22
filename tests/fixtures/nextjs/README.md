# Next.js fixtures (M16)

Two small invented git repositories and 18 change scenarios for the Next.js entry-point adapter
(`reachmap/adapters/nextjs/`), the acceptance data of `docs/briefs/M16-nextjs.md`. Company "acme" is not used here: the shop
is `nx/shop`, its client `nx/mobile`. The files are plausible small TypeScript and React code; they are never executed.

They are a tree of their own, apart from the M1 fixtures (`tests/fixtures/repos`, `scenarios`, `workspace.json`), so that the M1
repositories, branches, shas and `expected.json` files are what they were and nothing that walks the M1 scenarios meets an `nx`
one. The M1 builder's functions replay the data (same identity, dates and git environment, so shas are identical on every
machine with the same git): `tests/fixtures/nx_build.py`.

```python
from tests.fixtures.nx_build import build_all, scenarios
repos = build_all(dest)        # "nx/shop" and "nx/mobile" to repository paths
for sc in scenarios(dest): ... # scenario.json + expected.json, repo paths resolved
```

```text
tests/fixtures/nextjs/
  repos/<slug>/history.json         ordered steps (all on main): message and overlay folder
  repos/<slug>/steps/NN-x/          file overlay for one commit
  scenarios/nxNN-slug/              scenario.json, changes/ (overlay, optional .delete), expected.json
tests/fixtures/workspaces/nextjs.toml   the workspace: both repos, the http edge from nx/mobile to nx/shop (${REACHMAP_FIXTURES} for the remotes)
tests/fixtures/nx_build.py          the builder
tests/fixtures/nx_claims.py         an independent reading of the repositories (no import of the adapter)
tests/support_nx.py                 builds once per process, runs `reachmap analyse` on a scenario, reads the report back as the harness shapes
tests/test_nx_fixtures.py           expected.json against the repositories and nx_claims
tests/test_nextjs_scenarios.py      every scenario through the CLI against expected.json (`scenario_support.check`, nothing skipped)
```

## nx/shop (Next.js 14 shape, `src/app` and `src/pages`, `@/*` alias)

`entry_points = { kind = "nextjs" }`, tests `__tests__/`, `.test.`, `.spec.`, `distinct_max = 3`, `hub_importers = 50`.

| Router | File | URL (screen) or route |
|---|---|---|
| app | `layout.tsx` | wraps every App Router page |
| app | `page.tsx` | `/` |
| app | `(shop)/layout.tsx`, imports `Nav` | wraps the three pages of the group |
| app | `(shop)/products/page.tsx` (alias import of `StatusBadge`) | `/products` |
| app | `(shop)/products/[slug]/page.tsx` (relative import of `StatusBadge`) | `/products/:slug` |
| app | `(shop)/cart/page.tsx` (imports `Frame` from `components/layout.tsx`, `listOrders`) | `/cart` |
| app | `(marketing)/about/page.tsx` | `/about` |
| app | `docs/[...slug]/page.tsx` | `/docs/*` |
| app | `help/[[...topic]]/page.tsx` | `/help`, also `/help/*` (one screen) |
| app | `dashboard/layout.tsx`, `dashboard/page.tsx`, `dashboard/@analytics/page.tsx` (a slot) and `@analytics/default.tsx`, `dashboard/settings/page.tsx` | `/dashboard` (page and slot page: one screen), `/dashboard/settings` |
| app | `feed/layout.tsx`, `feed/page.tsx`, `feed/@modal/(..)photos/[id]/page.tsx` (intercepting) and `feed/@modal/default.tsx`, `photos/[id]/page.tsx` | `/feed`, `/photos/:id` (the page and the intercepting page: one screen, the intercepting route partial) |
| app | `_private/page.tsx` | not routed (private folder) |
| app | `api/health/route.ts`, `api/orders/route.ts` (`GET`, `POST`), `api/orders/[id]/route.ts` (`GET`, `DELETE`), `api/products/route.ts` | `GET /api/health`, `GET` and `POST /api/orders`, `GET` and `DELETE /api/orders/{id}`, `GET /api/products` |
| pages | `_app.tsx`, `_document.tsx` | wrap every Pages Router page |
| pages | `legacy/index.tsx`, `legacy/[id].tsx`, `legacy/archive/[...path].tsx`, `404.tsx` | `/legacy`, `/legacy/:id`, `/legacy/archive/*`, `/404` |
| pages | `api/legacy/status.ts`, `api/legacy/items/[id].ts` | `ANY /api/legacy/status`, `ANY /api/legacy/items/{id}` |
| root | `src/middleware.ts` | wraps every screen and route |

Fifteen screens (eleven App Router) and eight routes. Shared code: `components/StatusBadge.tsx` (imported by both routers, by an
alias and by a relative path, and by a test), `Nav`, `Markdown`, `PhotoCard`, `Chart`, `lib/format.ts`, `lib/orders.ts`,
`lib/session.ts`. Names that look like routing files and are not: `components/layout.tsx` (a component called `Frame`),
`lib/route.ts`, `utils/middleware.ts`, `app/_private/page.tsx`. `GET` is defined in four route files (more than `distinct_max`),
`handler` in two.

## nx/mobile (the client)

`src/api/endpoints.ts` (`ORDERS_URL`, `ORDER_URL(orderId)`, `HEALTH_URL`, `STATUS_URL`), `api/http.ts` (`apiGet`, `apiDelete`),
`services/orders.ts` and `services/health.ts`, three screens mounted by React Router in `src/routes.tsx`: `/orders`,
`/orders/:orderId`, `/status`. `http_client = endpoints-file`, an http edge `from nx/mobile to nx/shop`.

## Scenarios

| id | repo | change | key expected claims | rule or trap |
|---|---|---|---|---|
| nx01-shared-component | shop | `StatusBadge` markup | three screens (two routers, route group dropped, `[slug]`, alias and relative import), the unit test; four other screens excluded | folder is the route; test is not a caller |
| nx02-layout-fanout | shop | a line of `(shop)/layout.tsx` | `/cart`, `/products`, `/products/:slug` through the changed line, the line that renders `{children}`, the page's default export; must test | a layout reaches every page below its folder and no other |
| nx03-root-layout | shop | the root layout | eleven screens, sample (more than eight); one screen with a slot page, one with an intercepting page, one with `also_mounted`; `route_intercepted` | priority by count; slots and interception |
| nx04-handler-by-method | shop | `listOrders` in `lib/orders.ts` | `GET /api/orders` (not `POST`), `/cart`, the http edge and the client screen `/orders`; two importers excluded by narrowing | a handler file has a route per method; narrowing |
| nx05-direct-handler-change | shop | a line inside `GET` of `api/orders/[id]/route.ts` | `GET /api/orders/{id}` only, the http edge for `fetchOrder`, `/orders/:orderId`; `not_distinctive: GET` (4 definitions) | per-method reach; a generic name links nothing |
| nx06-module-level-handler | shop | `warmCache("orders")` at the top of a route file | `GET` and `POST /api/orders` | a statement that runs on load reaches every method |
| nx07-pages-router-app | shop | `_app.tsx` | the four Pages Router screens only | `_app` wraps the pages of its router |
| nx08-pages-api-items | shop | a handler in `pages/api` | `ANY /api/legacy/items/{id}` | a Pages API file answers every method |
| nx09-pages-api-status | shop | another one, which a client names | `ANY /api/legacy/status`, the http edge (a GET fits ANY exactly), `/status` | method matching for ANY |
| nx10-catch-all-pages | shop | `Markdown` | `/docs/*`, `/help` with `/help/*` also mounted | catch-all and optional catch-all |
| nx11-slot-and-intercepting | shop | `PhotoCard` and `Chart` | `/feed`, `/photos/:id` (page and intercepting page), `/dashboard` (page and slot page); `route_intercepted` | one screen per URL |
| nx12-not-a-route | shop | `components/layout.tsx`, `lib/route.ts`, `utils/middleware.ts`, `app/_private/page.tsx` | `/cart` and `GET /api/health` only (by import), the client screen; every page and route a name match would claim is excluded | Shape B: names that mean routing only in some places |
| nx13-removed-route | shop | delete `pages/api/legacy/status.ts` | `removed_route_still_called` (high risk, low confidence) with the client line and the removed line at the base | a removed thing is followed too |
| nx14-added-route | shop | add `api/invoices/route.ts` | `GET /api/invoices`, `added_route_uncalled`; `not_distinctive: GET` (5) | an added route nobody calls is said |
| nx15-removed-layout | shop | delete `(shop)/layout.tsx` | `/cart`, `/products`, `/products/:slug` through the layout's line at the base | a deleted wrapper is no chain of the walk; the hook finds it |
| nx16-unlexable-handler | shop | add a route file that does not lex | no route; `parse_degraded`, blocking | Shape A: an unreadable input is not an empty answer |
| nx17-middleware | shop | `src/middleware.ts` | every screen and route (23) and the three client screens, all listed by hand; sample | the matcher is not evaluated: undecidable means affected |
| nx18-client-change | mobile | `fetchOrder` | the http edge to the Next.js route `GET /api/orders/{id}`, `/orders/:orderId`; `OrdersScreen` excluded | a Next.js server answers a client change |

`explains` in each `expected.json` is the hand trace. The chain of a screen reached through a wrapper is: the changed line (or the
import line), the line of the wrapper that renders its children (`{children}`, `<Component />`, `<Main />`, the middleware function),
the page's default export. A removed wrapper's chain is the wrapper's line at the base commit and the page at the head.

## expected.json (schema 1, as in `tests/fixtures/README.md`)

Same rules as M1, with these usages: every chain item carries `repo`; `at: base` reads a locator at the scenario base (removed
files and lines, a removed route's evidence); a `route` entry's `evidence` is the one line of the method's export (`export async
function GET(`) or of a Pages API file's default export, and `ANY` is the method of a Pages API file; a `screen` entry's chain ends
at the page's default export (`export default function Name`); `also_mounted_evidence` also carries the other chains of a screen
whose pages share one URL (a slot page, an intercepting page), not only the chains of the routes in `also_mounted`.

### Incompleteness kinds used here

| kind | subject | meaning |
|---|---|---|
| `not_distinctive` | the name, with `definitions` (counted at the head) | as in M1 |
| `parse_degraded` | the path of the route file | a route file that does not lex: the methods it serves were not established; no route is claimed (blocking) |
| `route_intercepted` | the path of the intercepting page | new, not blocking: an intercepting route is shown in place of another one only on some navigations, so its route is partial and the run says so |
| `route_path_partial` | `<path>:<line>` | as in M7; for a folder name that is not a route part (`post-[id]`); unit tests only |

## What the tests recompute

`tests/test_nx_fixtures.py` checks expected.json against the repositories with `tests/fixtures/nx_claims.py`, a second reading
written apart from the adapter: the screens (label, also-mounted paths, files) and the routes (method, path, file) from the file
tree and the text of the route files, the pages a layout wraps, the importers of a changed file (the M1 oracle), that every import
hop of a chain imports the file before it and that the first hop imports a changed file, that every locator names a line of code (not a
comment) at the revision it is read at, that an http edge's client URL fits the route by segments and method, and that a
`not_distinctive` count is the definitions at the head.
