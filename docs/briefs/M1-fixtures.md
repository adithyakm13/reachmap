# M1 brief: synthetic fixtures

Goal: a script, `tests/fixtures/build.py`, that builds four small but real git
repositories on disk (history, tags, branches), plus a set of change scenarios, each with
a hand-written `expected.json` saying what the analyser must find, must exclude and must
report as not established. Every later milestone (M5 to M9) is accepted against these
files, so the expected answers are the contract. Read `docs/CONTEXT.md` sections 3, 4, 5,
7.4, 7.5 first.

Everything is synthetic and invented. Company "acme". No real product names.

## Layout

```text
tests/fixtures/
  build.py                 builder (stdlib only), importable and runnable
  README.md                what each repo and scenario is for, the expected.json schema
  workspace.json           workspace facts the scenarios assume (limits, repo roles, edges)
  repos/<slug>/            one folder per repo: the history, as data
    steps/NN-<slug>/       file overlay for one commit (written over the previous tree)
    steps/NN-<slug>/.delete    optional: paths to delete in that commit, one per line
    history.json           ordered steps: message, tags, which branch, branch creation
  scenarios/sNN-<slug>/
    scenario.json          repo, change kind, base/head (or range), extra notes
    changes/               file overlay applied on top of the base for the scenario head
    changes/.delete        optional
    expected.json          the hand-written answer
tests/test_fixtures.py     builds into a temp dir and validates every scenario
```

Data files, not Python strings, so a reviewer can read the fixture code as code. The
builder may also generate repetitive files (the hub importers) from a rule in
`history.json` or in `build.py`; say which.

Repo slugs on disk: `acme-web`, `acme-api`, `acme-contracts`, `acme-worker`. Workspace
names (used in every `expected.json`): `acme/web`, `acme/api`, `acme/contracts`,
`acme/worker`.

## Builder API

```python
from tests.fixtures.build import build_all
repos = build_all(dest: Path) -> dict[str, Path]   # workspace name -> repo path
```

- `python3 tests/fixtures/build.py <dest>` does the same from the shell and prints the
  paths.
- Deterministic: fixed author/committer name, email (`fixtures@example.invalid`) and
  dates (`GIT_AUTHOR_DATE`, `GIT_COMMITTER_DATE`, incrementing per commit), so commit
  shas are identical on every machine with the same git. Isolate from the user's git
  config: `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1`, `-c commit.gpgsign=false
  -c tag.gpgsign=false -c init.defaultBranch=main`. Annotated or lightweight tags: pick
  one, lightweight is fine.
- Whole build under 5 seconds on a laptop. Use `subprocess.run` with git; no network.
- Each scenario head is a branch `scenario/sNN-<slug>` in its repo, cut from the scenario's
  base. A range scenario may instead name two commits on `main` by tag or by step.
- Expose `scenarios(dest) -> list[dict]` that loads every `scenario.json` + `expected.json`
  with resolved repo paths, for later milestones' tests.
- Python 3.11+, standard library only.

## The four repositories

### acme/web (TypeScript, React Router)

- `tsconfig.json` with `compilerOptions.baseUrl = "."` and `paths`: `"@/*": ["src/*"]`,
  `"@components/*": ["src/components/*"]`, `"@api/*": ["src/api/*"]`. A file under
  `src/components/` is therefore importable by at least three specifiers (two aliases and
  relative). Also a stale editor config (for example `jsconfig.json`) pointing at a
  directory that does not exist (trap: read aliases from the build tool config).
- Routes files `src/modules/<module>/routes.tsx` for modules `billing`, `cases`,
  `reports`, written as React Router v6 `<Route path="..." element={<Page />} />`, full
  absolute paths. One page (`InvoiceDetailPage`) mounted on two routes:
  `/billing/invoices/:invoiceId` and `/cases/:caseId/invoices/:invoiceId` (one screen,
  also mounted). One page loaded through `React.lazy(() => import("./pages/..."))`.
- `src/api/endpoints.ts`: exported constants and builders, including one constant written
  over two lines (`export const X =\n  "/api/...";`), one builder with a parameter
  (`(invoiceId: string) => \`/api/billing/invoices/${invoiceId}\``), and one constant whose
  backend route will be removed in a scenario.
- `src/modules/<module>/api.ts` client functions that call endpoints; pages call those.
- A shared component (`src/components/StatusBadge.tsx`) imported by alias in some files,
  by the other alias in others, relatively in at least one, and from at least two modules.
- A hub: `src/lib/format.ts` imported by 55 files (generated under `src/widgets/`), above
  the default `hub_importers = 50`. Some real pages import it too.
- A guarded function: `formatAmount(value: number, mode: "legacy" | "modern")` with the
  body branching on `mode === "legacy"`; one call site passes the literal `"modern"`, one
  passes `"legacy"`, one passes a variable.
- Named-export narrowing: `src/lib/dates.ts` exports `formatDate` and `parseLegacyDate`;
  one importer imports only `formatDate`, another imports `parseLegacyDate`.
- Generic names: `mapStateToProps` (or a similar boilerplate name) defined in several
  files.
- Tests: `src/components/__tests__/StatusBadge.test.tsx`, `src/lib/dates.spec.ts`, and a
  non-test folder `src/contest/` with `engine.ts` (marker trap: `test/` must not match
  `contest/`).
- Branches: `main`, `release/2.0`, `release/2.1` (slashes on purpose), diverging after
  their cut.

### acme/api (Python, FastAPI)

- Service marker: `services/billing/pyproject.toml` and `services/cases/pyproject.toml`
  (a small monorepo), plus `libs/common/` shared code.
- `services/billing/app/main.py`: `app = FastAPI()`, `app.include_router(api_router,
  prefix="/api")`; `api_router` includes `billing_router` at `/billing`, which includes
  `invoices_router` at `/invoices` and `settlements_router` at `/settlements` (nested
  mounts, three levels). Handlers declared with `@router.get("/{invoice_id}")` etc.
- `services/billing/app/routers/legacy_exports.py`: a router with handlers that nothing
  mounts.
- Two handlers in different files each define a nested function with the same name (for
  example `_load`).
- Several handlers sharing an identical parameter line (`    request: Request,`) so text
  placement would be ambiguous.
- Module-level decorated functions (decorators at column 0), including a decorator with
  arguments over several lines.
- Imports: a multi-line parenthesised `from ... import (\n a,\n b,\n)`, an aliased module
  import (`import app.services.settlement as settlement_svc`), and an aliased name
  (`from x import y as z`).
- Uses the contracts package: `from acme_contracts.models.invoice import Invoice`, reads
  `invoice.settlement_reference` in `services/settlement.py`, and mentions
  `settlement_reference` in a comment in another file (code, not comments).
- `requirements.txt` pins `acme-contracts==1.10.0` on `main` and `acme-contracts==1.9.0`
  on `release/1.x`.
- Unit tests under `services/billing/tests/` importing the services (tests are tests, not
  callers).
- Branches: `main`, `release/1.x`, `release/1.2`.

### acme/contracts (Python, Pydantic models, versions as tags)

- `contracts/models/invoice.py`, `settlement.py`, `customer.py`, `schedule.py`,
  `common.py`. `pyproject.toml` version is a placeholder `0.0.0` forever; real versions
  are tags.
- Tags `v1.2.0`, `v1.9.0`, `v1.10.0` on successive commits (string comparison would order
  `1.10.0` before `1.9.0`: the trap).
- Fields: a distinctive one, `settlement_reference: str | None = None` (Optional, so
  nullable), defined in at most two models; a generic `id` defined in every model; a
  generic `interval` defined in at least four models; a field named `limit`, with the word
  "limit" also appearing in an English comment somewhere in the consumers.

### acme/worker (Python, consumes contracts)

- `requirements.txt` pins `acme-contracts==1.9.0` on `main` (it has not adopted 1.10.0).
- `worker/tasks/settlement_sync.py` reads `settlement_reference`.
- `worker/topics.py` with topic name constants (for the later event edge, unused now).

## Workspace facts (`workspace.json`)

Limits the scenarios assume: `hops = 3`, `hub_importers = 50`, `distinct_max = 3`,
`timeout_s = 900`. Repo roles, languages, branches and edges as in context 7.2 adapted to
these four repos (http web to api, package contracts to api and to worker, contract
contracts to api and worker). M3 will turn this into a `reachmap.toml`; keep it JSON now.

## Scenarios (one folder each)

Each is a change in one repo. For each, the hand trace goes in `expected.json`
`explains`, and the answer must follow from the fixture files alone.

| id | repo | change | must show |
|---|---|---|---|
| s01-shared-component | web | `StatusBadge.tsx` body change | every importer found by both aliases and relative; screens in billing and cases; the unit test listed as a test |
| s02-hub-cutoff | web | `src/lib/format.ts` change | walk stops at the hub and says so (not_established `hub_cutoff`, non-blocking); no widget listed as a screen |
| s03-guarded-call | web | change inside the `mode === "legacy"` branch | literal `"modern"` call site excluded with the guard as evidence; `"legacy"` and variable call sites affected |
| s04-named-export | web | `parseLegacyDate` changed | the importer of only `formatDate` excluded; importer of `parseLegacyDate` affected |
| s05-endpoint-multiline | web | the two-line endpoint constant's caller changes | http edge to the matching api route, with evidence at both ends |
| s06-nested-mount-handler | api | body of `GET /{invoice_id}` in invoices router | route `GET /api/billing/invoices/{invoice_id}` (full mount path); frontend screens calling it via endpoints; one screen with also_mounted |
| s07-unmounted-router | api | handler in `legacy_exports.py` | no route claimed; not_established `router_unmounted` names the router |
| s08-nested-same-name | api | body of nested `_load` in one handler | only that handler; the sibling with its own `_load` excluded |
| s09-decorator-line | api | arguments of a multi-line decorator at column 0 | attributed to the decorated function, not the previous one |
| s10-removed-route | api | delete a handler the web endpoints still call | finding: removed route still called, high risk, low confidence, evidence at the client line |
| s11-contract-field | contracts | `settlement_reference` changes from optional to required | reaches api `services/settlement.py` (code line, not the comment), its route, the web screen through http; worker listed as consumer pinning 1.9.0, exposure "on adoption"; evidence at every hop |
| s12-generic-field | contracts | change to `interval` in one model | dropped as not distinctive (not_established `not_distinctive`, non-blocking); no consumer line claimed |
| s13-branch-matrix | api | a fix that exists on `release/1.x` and `release/1.2` (cherry-picked) but not on `main` | branch states: present on the two release branches, pre_change on main |
| s14-test-marker | web | `src/contest/engine.ts` change | file is not a test file; `src/components/__tests__/...` is |
| s15-python-callers | api | `libs/common/` function changed | callers via multi-line and aliased imports confirmed; unit tests listed as tests, not callers |
| s16-no-merge-base | web | head on an orphan branch with unrelated history | not_established `pre_change_unavailable`, blocking; removed routes / narrowing not computed |
| s17-version-order | contracts | change between `v1.9.0` and `v1.10.0` (range scenario) | api on main pins 1.10.0 (contains it), api `release/1.x` pins 1.9.0 (does not); order by parsed version, never strings |

Add more if a trap in context section 5 that lives in fixture content has no scenario;
say which trap each covers in `tests/fixtures/README.md`. Traps that need injected read
failures (rate limits, 5xx, truncated search) belong to M4 with recorded responses, not
here; list them in the README as "covered in M4".

## expected.json schema (version 1)

```json
{
  "schema": 1,
  "id": "s06-nested-mount-handler",
  "repo": "acme/api",
  "change": {"kind": "compare", "base": "main", "head": "scenario/s06-nested-mount-handler"},
  "explains": "hand trace in plain prose",
  "must_find": [
    {"type": "changed_symbol", "repo": "acme/api", "path": "...", "symbol": "get_invoice"},
    {"type": "caller", "repo": "...", "path": "...", "symbol": "...",
     "evidence": [{"path": "...", "contains": "verbatim substring of the cited line"}]},
    {"type": "route", "repo": "acme/api", "method": "GET", "path": "/api/billing/invoices/{invoice_id}",
     "evidence": [{"path": "...", "contains": "..."}]},
    {"type": "screen", "repo": "acme/web", "route": "/billing/invoices/:invoiceId",
     "also_mounted": ["/cases/:caseId/invoices/:invoiceId"],
     "chain": [{"repo": "...", "path": "...", "contains": "..."}]},
    {"type": "http_edge", "from": {"repo": "acme/web", "path": "...", "contains": "..."},
     "to": {"repo": "acme/api", "method": "GET", "path": "..."}},
    {"type": "consumer", "repo": "acme/worker", "branch": "main", "pin": "1.9.0", "adoption": "on_adoption",
     "evidence": [{"path": "requirements.txt", "contains": "acme-contracts==1.9.0"}]},
    {"type": "unit_test", "repo": "...", "path": "..."},
    {"type": "finding", "kind": "cross-repo", "risk": "high", "confidence": "low",
     "evidence": [{"repo": "...", "path": "...", "contains": "..."}]},
    {"type": "branch_state", "repo": "...", "branch": "release/1.x", "state": "present"}
  ],
  "must_exclude": [
    {"type": "screen", "repo": "acme/web", "route": "/reports", "why": "..."},
    {"type": "caller", "repo": "...", "path": "...", "why": "...",
     "evidence": [{"path": "...", "contains": "..."}]}
  ],
  "not_established": [
    {"kind": "hub_cutoff", "subject": "src/lib/format.ts", "blocking": false}
  ]
}
```

Rules for the schema:

- Every `evidence`, `chain`, `from` entry names a file that exists at the scenario head
  (or at the base when it says `"at": "base"`, used for removed lines) and a `contains`
  string that occurs in that file, in code, not in a comment.
- `must_exclude` entries with `evidence` are exclusions the analyser must state with that
  evidence (for example the guard line); entries without are things that must simply not
  appear.
- Matching is exact on repo, path, route, method and kind. Later milestones may only
  check the types they implement; a type nobody implements yet is not a failure.
- Incompleteness kinds used so far: `hub_cutoff`, `router_unmounted`, `not_distinctive`,
  `pre_change_unavailable`, `hop_cap`, `module_cap`. Add a kind only with a sentence in the
  README saying what it means.

## Tests that prove M1 (`tests/test_fixtures.py`)

- Build into a `tempfile.TemporaryDirectory`, once per test class; assert it takes under
  10 seconds.
- Every repo has the branches and tags listed above; tag `v1.10.0` exists; branch names
  with slashes exist.
- Build twice into two directories: identical commit shas (determinism).
- Every scenario: `scenario.json` and `expected.json` parse, `schema == 1`, the head
  branch or range refs exist, every `evidence` / `chain` / `from` path exists at the right
  revision and contains its `contains` string (use `git show <rev>:<path>`), the scenario
  diff against its base is non-empty (except s16 which has no merge base), and ids match
  folder names.
- The hub has more than 50 importers; `interval` is defined in more than 3 models;
  `settlement_reference` in at most 2.
- No network: nothing in the build or tests touches a remote.

## Not in scope

No analyser code. No reachmap.toml (M3). No recorded host responses (M4).

## Style

No em dashes, en dashes, arrows or curly quotes in any file (data, comments, README).
Plain hyphens and straight quotes. Commit with plain messages; git identity is already
set in the repo config, do not change it.
