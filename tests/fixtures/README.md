# Synthetic fixtures

Four small git repositories and 23 change scenarios, all invented (company "acme"). They
are the acceptance data for the analyser milestones M5 to M9: each scenario has a
hand-written `expected.json` saying what must be found, what must be excluded and what
must be reported as not established. The files in the repositories are plausible small
TypeScript, React, FastAPI and Pydantic code; they are never executed.

Build them:

```text
python3.12 tests/fixtures/build.py /tmp/acme-fixtures     # prints "acme/web <path>" lines
```

```python
from tests.fixtures.build import build_all, scenarios
repos = build_all(dest)        # workspace name ("acme/web") to repository path
for sc in scenarios(dest):     # scenario.json + expected.json with resolved repo paths
    ...
```

Run the tests from the repository root (the root must be on `sys.path`; `python -m` puts
the working directory there):

```text
python3.12 -m unittest discover -s tests -v
```

## Layout

```text
tests/fixtures/
  build.py                  builder (standard library only), importable and runnable
  workspace.json            limits, repo roles, languages, branches and edges (M3 turns it into reachmap.toml)
  repos/<slug>/history.json ordered steps: message, branch, branch creation, tags, generators
  repos/<slug>/steps/NN-x/  file overlay for one commit, written over the checked out branch
  repos/<slug>/steps/NN-x/.delete   optional: paths to remove in that commit, one per line
  scenarios/sNN-slug/       scenario.json, changes/ (overlay, optional .delete), expected.json
```

Slugs on disk are `acme-web`, `acme-api`, `acme-contracts`, `acme-worker`; workspace
names (used in every `expected.json`) are `acme/web`, `acme/api`, `acme/contracts`,
`acme/worker`.

### history.json

Each step has `dir` (the overlay folder), `branch`, `message`, and optionally `from` (the
ref to cut `branch` from when it does not exist yet), `tags` (lightweight), `overlay` (read
the files from another step folder, used for a cherry-pick) and `generate`. The builder
checks out `branch`, writes the overlay over it (after the `.delete` list), and commits.
Because the overlay is written over the checked out branch, branches diverge naturally.

### Generated files

One rule only: `hub_importers`, named in the `generate` list of acme/web step
`05-hub-and-contest` (parameters `dir`, `count`, `target`) and implemented in `build.py`.
It writes 55 components `src/widgets/Widget01.tsx` to `Widget55.tsx`, each importing
`src/lib/format.ts` (odd numbers by `@/lib/format`, even numbers by `../lib/format`). With
the two real pages that import it, the hub has 57 importers, above `hub_importers = 50`.
Everything else is plain files. Dotfiles and dot folders (`.DS_Store`, editor swap files)
and backup files ending in `~` under a step or scenario folder are not fixture content and
are skipped; the only dotfile with a meaning is the top-level `.delete` list.

### Reading files

`build.file_at(repo, rev, path)` answers three ways, like every read in the product:
`("found", text)`, `("absent", None)` only when git says the path does not exist at a
revision that does resolve, and `("unreadable", detail)` for anything else (a bad
revision, a corrupt object). An unreadable file is never an absence. The tests' `text_at`
helper returns the text or None for absent and raises on unreadable.

### Determinism and isolation

Fixed author and committer (`Fixture Author <fixtures@example.invalid>`), commit n of a
repository dated `1700000000 + n * 3600`, a git environment with `GIT_CONFIG_GLOBAL=/dev/null`,
`GIT_CONFIG_NOSYSTEM=1`, `HOME` and `XDG_CONFIG_HOME` pointed into the destination,
`core.excludesFile=/dev/null`, no hooks, no signing, `GIT_ALLOW_PROTOCOL=none` (git cannot
reach any remote). Two builds give identical shas for every ref on the same git version.
The build takes about 2 seconds (the target is under 5).

### Scenario branches

For a compare scenario the builder cuts `scenario/<id>` from `base` and commits the
`changes/` overlay on it. A range scenario has no `changes/` and names refs that exist in
the history (s17). The orphan scenario (s16) creates `scenario/s16-no-merge-base` as an
orphan branch holding only its overlay. `scenario.json` keys: `id`, `repo`, `change`
(`kind` compare or range, `base`, `head`), `commit_message`, `notes`, optional `orphan`,
optional `limits` (overrides of workspace limits, used by s21).

## The repositories

| Repo | What it is | Branches | Tags |
|---|---|---|---|
| acme/web | TypeScript, React Router v6, `tsconfig.json` aliases `@/*`, `@components/*`, `@api/*` (a stale `jsconfig.json` points at `app/`, which does not exist), modules billing, cases, reports, `src/api/endpoints.ts`, hub `src/lib/format.ts`, guarded `formatAmount` in `src/lib/money.ts`, `src/lib/dates.ts`, shared `StatusBadge`, four files defining `mapStateToProps`, `src/contest/engine.ts` | main, release/2.0, release/2.1 | v2.0.0, v2.1.0 |
| acme/api | FastAPI monorepo: `services/billing`, `services/cases` (each with its own `pyproject.toml` and a package called `app`), `libs/common` (`acme_common`), mount tree app > `/api` > `/billing` > `/invoices` and `/settlements`, an unmounted `legacy_exports.py`, `jobs.py` with a multi-line decorator at column 0 | main, release/1.x, release/1.2 | v1.2.0, v1.9.0 |
| acme/contracts | Pydantic models `acme_contracts` (source folder `contracts/`, mapped by `package-dir` in `pyproject.toml`), manifest version `0.0.0` forever, real versions are tags | main | v1.2.0, v1.9.0, v1.10.0 (successive commits) |
| acme/worker | Celery tasks reading the contracts, `worker/topics.py` for a later event edge | main | none |

Manifest pins (`requirements.txt`): api main `acme-contracts==1.10.0`, api release/1.x
`1.9.0`, api release/1.2 `1.2.0`, worker main `1.9.0`.

Routes in acme/api (full mounted paths): `GET /api/billing/invoices`,
`GET /api/billing/invoices/{invoice_id}`, `POST /api/billing/invoices/{invoice_id}/void`,
`GET /api/billing/settlements/summary`, `GET /api/billing/settlements/{settlement_id}`,
`GET /api/billing/audit-log`, `GET /api/cases`, `GET /api/cases/{case_id}`. The router in
`legacy_exports.py` is mounted by nothing (a commented-out `include_router` in `billing.py`
is not a mount).

The evidence the FastAPI adapter reports for a route is the decorator, then each `include_router` line from the router
outwards, as `route` and `http_edge` entries list it. When a router or a parent router has its own `prefix=`, the adapter
also cites that constructor line right after the line it applies to (the route's path came from it); no router of these
repositories has a prefix, so `expected.json` never lists such a line and the independent parser in `claims.py` does not
derive it.

Screens in acme/web: `/billing/invoices`, `/billing/invoices/:invoiceId` (the same
`InvoiceDetailPage`, also mounted at `/cases/:caseId/invoices/:invoiceId`),
`/billing/settlements`, `/cases`, `/cases/:caseId`, `/reports`, `/reports/schedule` (lazy).

Contract fields: `settlement_reference: str | None = None` is defined in two models
(Invoice, Settlement) since v1.9.0; `interval` in five (Invoice, Settlement, Customer,
Schedule, common.Cadence); `id` in every model; `limit` once (Schedule), and the word
appears in consumer code only in a comment in `worker/tasks/settlement_sync.py`;
`due_date` (Invoice) arrives in v1.10.0.

## expected.json (schema 1)

Top level: `schema` (1), `id` (the folder name), `repo`, `change` (equal to
`scenario.json`), `explains` (the hand trace in plain prose), `complete_for`,
`must_find`, `must_exclude`, `not_established`.

- `must_find` entries have a `type`: `changed_symbol`, `changed_file`, `caller`,
  `contract_read`, `route`, `screen`, `http_edge`, `consumer`, `unit_test`, `finding`,
  `branch_state`. A milestone may check only the types it implements.
- `complete_for` lists the types for which `must_find` is the whole answer: the analyser
  must report no other item of that type (an empty list of a type means none). For a type
  not listed the scenario makes no claim about extras. `not_established` is always the
  complete list of incompleteness records expected.
- `must_exclude` entries with `evidence` are exclusions the analyser must state with that
  evidence; entries without are things that must simply not appear. Every entry has a `why`.
- Matching is exact on repo, path, route, method and kind.

### Locators and the revision they are read at

Every `{path, contains}` pair (an `evidence` item, a `chain` hop, `from`, `via`,
`removed_line`, `naive_match`) names a file and a substring that occurs in that file in code, not in a comment;
`contains` is always inside one line. `tests/test_fixtures.py` checks all of them with
`git show`. The revision is: `"at": "base"` reads the scenario base (used for removed
lines); a `branch` or `ref` key (on the item or on the enclosing entry, as for `consumer`)
reads that ref of that repo; otherwise the scenario head when the repo is the scenario's
repo, and `main` for any other repo (cross-repo reads use the other repo's primary
branch). `build.evidence_revision` and `build.file_at` implement this.

### Fields added to the brief's schema

- `chain`: ordered hops from the first hop the analyser reaches to the Route line. A hop
  that is an import statement really imports the previous hop's file by the specifier
  written (the test resolves aliases from `tsconfig.json`, relative paths and Python
  modules; see "What the tests recompute"). `screen.route` is the smallest of the screen's mounted routes in string order;
  the others are `also_mounted`, with `also_mounted_evidence` for them.
- `http_edge`: `from` is the client line, `via` optional constant definitions, `to` the
  route with its `evidence` (decorator, then each `include_router` line from the router
  outwards; the test derives the route and its mounts itself and compares). `removed: true` marks a route
  removed by the change.
- Import lines are module-level changes. A changed or removed `import` line sits outside every
  definition, so an adapter reports it as a module-level change of the file, not as a change of
  a function; s20 attaches its `removed_line` to `InvoiceListPage` for the reader's sake, and the
  analyser's harness (`tests/scenario_support.py`) accepts the line as evidence of either.
- `changed_symbol`: `symbol` is the outermost enclosing definition (dotted for a model
  field), `nested` names a nested function, `change` is `added` or `removed`;
  `removed_line` cites the removed line at the base.
- `caller`: the first hop outside the changed file, that is a direct importer of it or a
  call site of the changed symbol. Transitive reach (the route a caller serves, the screen
  that mounts a page) is shown in `chain`, `route` and `screen` entries, never as a caller.
  `reason` is `literal_legacy` (a literal that enters the guard) or
  `undecidable` (a variable argument); `path_prefix` / `path_suffix` stand in for `path`
  when a whole folder or a path tail is excluded.
- `changed_file`: `role` is `source` or `test`.
- `consumer`: `branch`, `pin`, `adoption`, `contains_change`. The policy:
  - A change not contained in any released tag (unreleased: s11, s21) lists each consumer
    once, from its primary branch (`main`), with adoption `on_adoption` ("list consumers
    from the consumer's primary branch", context section 5).
  - A change contained in a released tag (s17) lists one entry per configured branch of
    each consumer (the branches of the consumer's `branches` list that exist), with
    `contains_change` and an adoption of `adopted` (the pinned tag contains the change
    commit) or `on_adoption`, decided by tag ancestry and parsed version numbers, never by
    comparing version strings.
  The test enforces both rules.
- `contract_read`: a consumer code line that reads a contract field.
- `finding`: `kind`, `risk`, `confidence`, `evidence`. Kinds used:
  - `removed_route_still_called`: a route removed by the change that a client file on its
    primary branch still names (s10). Evidence is the client line and the removed
    decorator line at the base.
  - `added_route_uncalled`: a route added by the change that no client names yet (s22),
    from context 7.5. Evidence is the new decorator line.
  The risk and confidence values are the fixture's expectation, not a scale.
  `complete_for: finding` means the report has no finding of another kind than the listed ones, plus the mirror kinds that
  restate what the report already lists elsewhere: `direct` and `cross-repo` (one per area of the report, nothing else) and
  `exclusion` and `guard-excluded` (one per exclusion the analysis states). Those mirror findings must be exactly the reported
  areas and exclusions: one per area, one per exclusion, none for anything else. Every other kind (`release`,
  `contract-tightened`, `contract-relaxed`, `branch-missing`, a removed or added route that is not listed) is a claim of its
  own and is not allowed in a scenario that says `finding` is complete. A scenario that lists no `finding` makes no claim about
  kinds, but the mirror rule holds for every scenario. The end to end harness enforces both.
- `comment`: a locator that must be a comment. It documents what must not be counted.
- `naive_match`: a locator for a line a name-only matcher would wrongly claim. It is a
  substring locator like the others and may match several lines by design (for example
  `    request: Request,` occurs in many handlers); it only has to occur in code.
- `branch_state`: `state` is `present` (every added line is in the file at the branch
  tip) or `pre_change` (none of the added lines, the old lines still there).
- `not_established`: `kind`, `subject` (format per kind below), `blocking`, plus `definitions` for
  `not_distinctive` and `importers` for `hub_cutoff` (both are verified against the repos).

### What the tests recompute

`tests/test_fixtures.py` does not trust `expected.json`; it derives these facts from the
repositories (parsers in `tests/fixtures/claims.py`) and fails when a claim differs:

- `changed_symbol`: the span of each symbol (top-level TypeScript functions and arrow
  constants by bracket counting, Python defs and classes by indentation, decorators
  included) must contain a changed line of `git diff -U0 base...head`; every excluded
  symbol must not; when `complete_for` has `changed_symbol`, every top-level function that
  contains a changed line is listed.
- `chain`: hop 0 is the changed file, an import of it, or the client line of an http
  edge; each import hop resolves (tsconfig aliases, relative paths, Python modules) to the
  previous hop's file; a Route hop follows the import of the component it renders, that
  file defines the component, and the last Route path is the screen's `route`.
- `caller`: a cited import line resolves to a changed file; when `complete_for` has
  `caller`, every non-test direct importer of the changed files is a listed caller or a
  stated exclusion; when it has `screen` (web), the screens are exactly the routes that
  mount the changed files and the callers.
- guarded calls: the call sites of a guarded function are classified by their argument
  against the guard literal.
- `http_edge`: the client line's URL (constant, two-line constant or builder) fits the
  `to` route best (a client parameter fits only a route parameter, an exact fit beats a
  literal in a parameter); excluded edges are not the best fit.
- `route`: derived from the decorator's router variable through every `include_router`
  call up to the app, never from the listed lines; unmounted routers are found the same way.
- `contract_read`: the reads of a distinctive field (definitions at most `distinct_max`)
  in consumer code outside comments and tests are exactly the listed reads.
- `finding`: a removed route still called fits a base route and no head route; an added
  route no client URL fits.

### Incompleteness kinds

`blocking` is true only when a needed input is missing; a stated limit is not blocking.
The `subject` of each kind has a fixed format (the test checks it):

| kind | subject |
|---|---|
| `hub_cutoff` | the repository relative path of the hub file, for example `src/lib/format.ts` |
| `router_unmounted` | `<path>:<router variable>`, for example `services/billing/app/routers/legacy_exports.py:legacy_export_router` |
| `not_distinctive` | the name itself, for example `interval` |
| `pre_change_unavailable` | the head ref, for example `scenario/s16-no-merge-base` |
| `hop_cap` | `edge:<kind>:<from>:<to>` in the edge's declared direction in `workspace.json`, for example `edge:http:acme/web:acme/api` |
| `module_cap` | `hop:<n>`, the hop whose module cap was hit |
| `edge_unfollowed` | `edge:<kind>:<from>:<to>` as declared, of an edge kind that has no adapter yet (the `event` edge of the fixtures), for example `edge:event:acme/api:acme/worker` |
| `entry_points_unread` | the repository name, for example `acme/worker`, whose `entry_points` kind has no adapter yet (`celery`) |

In a propagation across repositories the subjects of the records made inside a repository other than the one the change is
in carry the repository: `hub_cutoff` is `<repo>:<path>` and `module_cap` is `<repo>:hop:<n>` there (the subjects above
are those of the scenario's own repository, which `expected.json` lists). The other kinds M8 makes have fixed formats too:
`client_url_partial` and `client_url_unmatched` are `<repo>:<path>:<line>` of the client call, `module_cap` for the list of
modules a handler runs is `backend:<repo>:depth:<n>`, `edge_incomplete` is `edge:<kind>:<from>:<to>`, `version_unparsed`
and `pin_unresolved` are `<consumer>:<branch>`, `tag_prefix_unmatched` is the producer repository, `contract_change_unscoped`
is the path of the file, and `contract_construction_unseen` is the consumer repository.

- `hub_cutoff`: the walk reached a file with more importers than `hub_importers` and did
  not pass through it.
- `router_unmounted`: a handler's router is mounted by nothing, so no route is claimed.
- `not_distinctive`: a name is defined more often than `distinct_max`, so it was not used
  to link code.
- `pre_change_unavailable`: no merge base, so no pre-change file; removals and narrowing
  are not computed (blocking).
- `hop_cap`: an edge past `limits.hops` was not followed.
- `module_cap`: a per-hop module cap was hit. No scenario exercises it: the workspace
  limits have no module cap yet.
- `edge_unfollowed`: the change reached code at one end of a declared edge whose kind has no adapter, so what
  lies past it was not followed (not blocking; the propagation is not complete, as with `hop_cap`). It fires in every api
  scenario whose change touches a declaration (the `event` edge api to worker), and in s11 and s21 through the api pass.
- `entry_points_unread`: a pass reached code in a repository whose entry-point kind has no adapter (the Celery worker
  in s11 and s21), so what that code runs was not read (not blocking, the propagation is not complete).

## Scenarios and the traps they cover

Trap numbers refer to the lists in `docs/CONTEXT.md` section 5.

| id | repo | change | key expected claims | traps covered |
|---|---|---|---|---|
| s01-shared-component | web | `StatusBadge` body | importers found by `@/`, `@components/` and relative specifiers; screens in billing and cases (one also mounted); the test is a unit test, not a caller; reports and settlements excluded | reverse alias lookup returned only the longest alias; importer search covered aliases only; stale editor config |
| s02-hub-cutoff | web | `src/lib/format.ts` | `hub_cutoff` (non-blocking, 57 importers); no widget listed | walk must not pass through hubs |
| s03-guarded-call | web | inside `mode === "legacy"` | `"legacy"` and variable call sites affected, `"modern"` call site excluded with the guard and call line as evidence | undecidable means affected |
| s04-named-export | web | `parseLegacyDate` | only the importer of `parseLegacyDate` and its screen `/cases`; importers of `formatDate` only excluded | named-export narrowing |
| s05-endpoint-multiline | web | `fetchSettlementSummary` | http edge to `GET /api/billing/settlements/summary` with evidence at both ends; the parameter route loses to the literal route | two-line endpoint constant invisible to a one-line regex; most specific route wins |
| s06-nested-mount-handler | api | body of `get_invoice` | `GET /api/billing/invoices/{invoice_id}` composed over three mounts; one screen with `also_mounted` | a route is composed on its router following mounts; client parameter matches only a route parameter |
| s07-unmounted-router | api | handler in `legacy_exports.py` | no route; `router_unmounted` naming the router | a router nothing mounts is said, not matched; commented-out mount is not a mount |
| s08-nested-same-name | api | nested `_load` in `get_settlement` | only `get_settlement`; `get_invoice` with its own `_load` excluded | nested function name matched a sibling's |
| s09-decorator-line | api | multi-line decorator argument | attributed to `run_nightly_settlement`, not `_log` | decorator at column 0 outside the function |
| s10-removed-route | api | `audit_log` handler deleted | `removed_route_still_called`, high risk, low confidence, evidence at the client line and the removed line at the base | a removed thing is followed too |
| s11-contract-field | contracts | `settlement_reference` becomes required | api read (code line, not the comment), its route, the web screen through http, worker read; both consumers `on_adoption` | schema change matters before adoption; code not comments; distinctive by definition count |
| s12-generic-field | contracts | `Settlement.interval` | `not_distinctive`; no consumer line claimed | the word `interval` read as a contract link |
| s13-branch-matrix | api | `round_total` fix, already on release branches | main `pre_change`, release/1.x and release/1.2 `present` | version numbers do not imply containment |
| s14-test-marker | web | `src/contest/engine.ts` and a `__tests__` file | engine is a source file, the `__tests__` file is a test | marker `test/` matched `contest/`: matching is by path segment, tested here on the web markers, none of which collides with `contest/`; the real collisions are in s23 |
| s15-python-callers | api | `format_cents` in `libs/common` | callers via multi-line, aliased-name and aliased-module imports; tests are tests; `jobs.py` imports only another name | multi-line and aliased imports; tests are not callers |
| s16-no-merge-base | web | orphan branch | `pre_change_unavailable` (blocking); no removed-route or narrowing claim | open PR silently had no pre-change version |
| s17-version-order | contracts | range v1.9.0..v1.10.0 | api main 1.10.0 contains the change, api release/1.x 1.9.0, api release/1.2 1.2.0 and worker 1.9.0 do not | versions compared as strings; placeholder manifest version; release lines diverge |
| s18-limit-comment | contracts | `Schedule.limit` | no consumer line: the word is only in a comment | a setting key's leaf `limit` matched an English word |
| s19-generic-name | web | `mapStateToProps` in `CaseTable.tsx` | `not_distinctive` (4 definitions); only `/cases`; the other three definers excluded | `mapStateToProps` read as a parallel implementation |
| s20-removed-import-line | web | removed `@/lib/format` import | page and its screen only; the 55 widgets and `CaseListPage` excluded | a removed import line's module path read as a defect fingerprint |
| s21-hop-cap | contracts | s11 change with `limits.hops = 1` | reads in api and worker, api route; web screen not listed, `hop_cap` | hop cap recorded when hit |
| s22-added-handler | api | new `get_invoice_pdf` handler | only the new handler and its route; `added_route_uncalled`; siblings with the same `request: Request,` line excluded | an added line placed in a function by its text instead of its diff line number; an added route nobody calls yet is said |
| s23-api-test-marker | api | files in `contests/` and `latest_rates.py`, plus a real test | the two source files are source (markers `tests/` and `test_` collide with their paths only as substrings), `tests/test_money.py` is a test | marker `tests/` matched `contests/`, marker `test_` matched `latest_rates.py` |

### Revisions (M11)

`review/<scenario id>/` holds later commits of a scenario branch, for the tests of the review layer. `revisions.json` lists them in order
(`name`, `dir`, `message`) and each `dir` is an overlay applied like a step (an optional `.delete` list first). Nothing in `build_all` reads
this folder, so every existing repository, branch and sha is what it was; `build.apply_revision(dest, scenario_id, name)` commits one on the
scenario's own branch in a repository that is already built (a copy of a build, for a test) and returns the new head sha. The commit is dated
one hour after the last commit made in that repository, so two copies given the same revisions have the same shas.

`s01-shared-component` has `r2`, a second commit on `scenario/s01-shared-component`. Against the first head (A) it:

| Edit | What it does to the claims | Decision made on A |
|---|---|---|
| an unrelated import line in `InvoiceDetailPage.tsx` | `/billing/invoices/:invoiceId` keeps its evidence: the fingerprint is the same | current |
| the alias import of the badge in `CaseDetailPage.tsx` becomes a relative path to the same file, and three blank lines go above it | the line reads differently and moves down 3, but an import's claim is its resolved target and the names it takes: the fingerprint is the same | current |
| `InvoiceListPage.tsx` imports the badge from `@/components`, a new re-export file (`src/components/index.ts`) | the import now resolves to another file: the claim changed | stale |
| the `/cases` route is removed from `cases/routes.tsx` | the screen is no longer reached | orphaned |
| `SettlementsPage.tsx` uses the badge | `/billing/settlements` is a new screen | none (undecided) |

### Covered in M4 (needs injected read failures, not fixture content; host responses are in `hosts/README.md`)

Code search paginated at 30 results; a rate-limited search read as "no callers"; an
examination cap on a search (a small fraction of the matches); a failed file read (rate limit, 5xx,
auth, timeout) read as "file absent" and files over 1 MB; a diff over 300 files and the
fallback API paging; a frontend list fetched in part; branch names with slashes
URL-encoded (the fixtures supply `release/2.0`, `release/2.1`, `release/1.x` and
`release/1.2` to test against); one job at a time under rate limits.

### Traps no M1 scenario exercises

Any quoted word on an added line read as a schema field read, and shadowed loop
variables (a code path trap, not fixture content). Both need analyser behaviour that
later milestones define. `module_cap` has no scenario for the same reason.
