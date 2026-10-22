# Progress

Milestones from `docs/BOOTSTRAP.md` section 3. Updated at the end of every milestone.

Pull request numbers ("dev #N") refer to the private development repository where each
milestone was reviewed and merged; the public repository starts from a single commit of the
result, so those pull requests are not linked.

| # | Milestone | Status | PR | Notes |
|---|---|---|---|---|
| M0 | Setup | done | direct to main | Private repo, CI green on 3.11 to 3.13 |
| M1 | Synthetic fixtures | done | dev #1 | 4 repos, 23 scenarios, 54 tests; build about 1.7 s |
| M2 | Core model | done | dev #2 | 264 tests; review found 5 majors, fixed |
| M3 | Config | done | dev #3 | 404 tests on 3.11 to 3.13; review found 4 majors, fixed |
| M4 | Git layer and hosts | done | dev #4 | 599 tests on 3.11 to 3.13; review found 1 blocker, 7 majors, fixed |
| M5 | TypeScript / JavaScript language adapter | done | dev #5 | 985 tests on 3.11 to 3.13; 9 web scenarios pass at language level; review found 1 blocker, 7 majors, fixed |
| M6 | Python language adapter | done | dev #6 | 1,271 tests on 3.11 to 3.13; 14 api/contracts scenarios pass at language level; review: no blockers, 2 majors fixed |
| M7 | Entry points | done | dev #7 | 1,451 tests on 3.11 to 3.13; screens and routes for 12 scenarios; review: 2 blockers, 6 majors fixed |
| M8 | Edges and the walker | done | dev #8 | 1,623 tests on 3.11 to 3.13; all 23 scenarios pass cross-repo; review: 7 majors fixed |
| M9 | Pipeline and findings | done | dev #9 | 1,898 tests on 3.11 to 3.13; all 23 scenarios pass end to end via the CLI; review: 1 blocker, 5 majors fixed |
| M10 | Reports | done | dev #10 | 2,088 tests on 3.11 to 3.13; md, html, author view; review: no blocker, 1 major fixed |
| M11 | Review layer | done | dev #11 | 2,342 tests on 3.11 to 3.13; decisions carry forward exactly; review: 3 majors fixed, claim-based fingerprints |
| M12 | Runner | done | dev #12 | 2,467 tests on 3.11 to 3.13; every guard tested on a live server; review: process-tree major fixed |
| M13 | Frontend workspace | done | dev #13 | 158 frontend tests, tsc and build pass; 2,498 Python tests; review: no blocker, gaps fixed |
| M13b | Frontend redesign (owner request) | done | dev #16 | Mantine workspace; 312 frontend tests; review: 1 blocker, 5 majors fixed |
| M14 | MCP server and model notes | done | dev #14 | 2,784 tests on 3.11 to 3.13; stamp and fence held under review; minors fixed |
| M15 | Test sources | done | dev #15 | 3,131 tests on 3.11 to 3.13; suggestions never coverage; review: blocker and majors fixed |
| M16 | First-release adapters | done | #17 to #21 | Next.js, OpenAPI, Django/DRF, Express/NestJS, Spring Boot with Java/Kotlin |
| M16 nextjs | Next.js routes adapter | done | dev #17 | 18 scenarios; review: 1 major fixed |
| M16 openapi | OpenAPI adapter | done | dev #18 | 10 scenarios; review: 1 blocker, 2 majors fixed |
| M16 django | Django and DRF adapter | done | dev #19 | 14 scenarios; review: 1 blocker, 7 majors fixed; shared route-change guard |
| M16 express | Express and NestJS adapter | done | dev #20 | 25 scenarios; review: 1 blocker, 6 majors fixed |
| M16 spring | Spring Boot and JVM adapter | done | dev #21 | 20 scenarios; review: 8 majors fixed |
| M18 | Evaluation tools | done | dev #25 | `audit`, `witness` and `sheet`; audit passes on all six fixture workspaces (pinned in tests); review: 4 majors fixed |
| Hardening | Unmatched patterns, config keys, YAML specs | done | dev #24 | found by the docs review; review fixes applied |
| M19 docs | User docs (first part) | done | dev #23 | every command run; review fixed; the init and evaluation sections and the output snippets were finished in the release step |
| M14b notes | Workspace shows model notes | done | dev #22 | 385 frontend tests; review: test gaps fixed |
| M17 | `reachmap init` | done | dev #26 | generated configs analyse all six fixture workspaces after one documented confirmation; review fixed; also run on the four example repositories by the install check |
| M19 | Release prep | done, except the owner's two decisions | branch `m19-release` (the coordinator opens the PR) | version 0.1.0 in `reachmap/__init__.py`, wheel and sdist built and inspected, clean install check on 3.11 to 3.13, `release.yml`, `docs/releasing.md`; the repository is not public and nothing is published |

## Small decisions

- Local Python: system `python3` is 3.9, so Python 3.12.13 was installed with
  `uv python install 3.12`; run tests with `python3.12`.
- Build backend: setuptools (build time only, not a runtime dependency).
- CI frontend job detects `frontend/package.json` in a step and skips the rest until the
  frontend exists (`hashFiles` is not allowed in a job-level `if`).

- Briefs for each milestone live in `docs/briefs/`.
- M1 consumer policy: a change in no released tag lists each consumer once, from its
  primary branch, `on_adoption`; a change in a released tag lists one entry per
  configured branch, adopted or on_adoption by parsed-version containment.
- M1 caller definition: the first hop outside the changed file; transitive reach is in
  chains, not callers.
- M1 `added_route_uncalled` finding: risk low, confidence medium.

## M1 measurements

- Fixture build: about 1.7 s; two builds give identical shas; a hostile global git config
  does not change them.
- 54 tests, about 20 s. Mutation check: ten deliberately wrong `expected.json` edits (five
  from the review, five more) each fail the suite; one more tried in the M1 verification
  (an s04 exclusion flipped to affected) also fails.
- The test parsers understand only the shapes the fixtures use (single-line decorators
  and imports in chains, top-level functions). New fixture shapes need them extended.
- Not exercised by any scenario yet: `module_cap`; the quoted-word-on-an-added-line trap.
  Read-failure traps (rate limits, 5xx, truncated search) belong to M4.

## M2 measurements and choices

- 264 tests (210 new), about 18 s locally. Independent review tried to break every
  section 4 rule through constructors, `from_dict`, `replace` and post-construction
  mutation; its five majors (exclusion not in the id, ReadResult truthiness, timed-out
  findings still serialised, Analysis mutable without checks, lone surrogates) are fixed
  and tested.
- `ReadResult` is the one read shape (context 7.3's tuple replaced): truthiness raises,
  `.text` raises unless found, `text_or_none()` raises on unreadable.
- `is_exclusion` and `not_established` are derived from the kind registries.
- Analysis is an append-only builder (`add_*`, `finish`); non-completed runs serialise
  `findings` and `areas` as null.
- Priorities: the screen count (8 or fewer: must test) decides; the priority applies to
  every import-backed area.
- Not done: per-kind subject format validation.

## M3 measurements and choices

- 404 tests pass locally on Python 3.11, 3.12 and 3.13 (140 config tests).
- Review majors fixed: a token in a remote URL username reached repr (now any userinfo
  in a `scheme://` remote is refused, bare user allowed for ssh only); an unset `${VAR}`
  under `repos` or `edges` silently became None (now a ConfigError; "not configured"
  exists only for `test_sources` and `context`, with `unset_vars`); unknown edge keys
  (per-kind allowlist); Workspace not picklable (FrozenMap).
- Error messages never echo a configured value; parse errors carry no exception context.
- Contract edges accept `from`/`to` or `producer`/`consumer`.
- M3 first built a loader for a legacy JSON config. The owner decision of 2026-10-22 removed it
  (see "Owner decision 2026-10-22: design notes made generic" below): TOML is the only format.

## M4 measurements and choices

- 599 tests pass on Python 3.11, 3.12 and 3.13 (195 in the M4 modules, about 30 s).
- Review blocker fixed: a rebase-merged or fast-forwarded pull request lost all but its
  last commit. Merged requests with one parent are now read as squash or rebase using the
  host's commit count, checked against the host file count and the request's own diff;
  when neither or both readings fit, the run records `pre_change_unavailable` and refuses.
- Review majors fixed: grep with stderr and exit 0 read as complete; abbreviated sha on a
  fresh cache read as absent; global git config changing diff and grep output (now pinned);
  non-UTF-8 file names read as absent; `Authorization` followed redirects (all redirects
  now refused); `gh`/`glab` ignored a custom API host; case-colliding paths silently
  overwritten in a checkout.
- Every reviewer probe is now a test. The no-network guard is suite wide.
- Not done: open pull requests whose head is not fetchable by sha (`refs/pull/N/head` is
  not fetched); a GitLab fast-forward merge with no `merge_commit_sha` is unreadable;
  `gh` and `glab` themselves were never run (argv and parsing tested with mocks).

## M5 measurements and choices

- 985 tests pass on Python 3.11, 3.12 and 3.13 (about 110 s; the JS adapter tests are
  about 55 s). Web scenarios s01, s02, s03, s04, s05, s14, s16, s19, s20 pass for changed
  symbols, changed files, callers, unit tests and records; screens and http edges are
  explicitly skipped until M7 and M8. Importer sets are cross-checked against the
  independent fixture oracle.
- Review blocker fixed: narrowing ignored reach inside the changed file (exported `g`
  calling changed `f` let importers of `g` be excluded). Narrowing now uses the closure of
  declarations that reference a changed one, and stops on lexer problems or dynamic access.
- Review majors fixed: shadowed or rebound guard parameters (now undecidable, affected);
  mis-lexed changed files and candidates (`parse_degraded`); unresolvable specifiers that
  name the file (`specifier_unresolved`, blocking); non-JS changed files no longer
  "complete with no importers" (`file_not_analysed`; `.json` importers are searched).
- The JS adapter applies the hub cut-off itself for now (context 7.5 gives it to the
  core); revisit in M8.
- Known limits: a `//` after a space in JSX text and a `/*` closed by a later `*/` can hide
  imports undetected; no `node_modules`, workspace package names or `package.json`
  `exports` resolution; mutable state shared between functions is not followed by
  narrowing.

## M6 measurements and choices

- 1,271 tests pass on Python 3.11, 3.12 and 3.13 (about 150 s). Python scenarios s06,
  s07, s08, s09, s10, s13, s15, s22, s23 (api) and s11, s12, s17, s18, s21 (contracts)
  pass for changed symbols, changed files, callers, unit tests and records; importer sets
  agree with the fixture oracle. Routes, screens, edges, consumers and findings are
  explicitly skipped until M7 to M9.
- Independent review: no blockers, all 32 mutations caught. Its two majors fixed in both
  adapters: a changed class member reached through attribute access (`doc.total()`) was
  not followed by narrowing; a deleted or renamed file without a pre-change version read
  as complete with no importers.
- The JS and Python adapters now share `reachmap/adapters/base.py` (reading, importer
  search, definitions, callers, unit tests, hub rule, record helpers), so fixes land once.
- Narrowing also stops on import-time execution of a reached name (assignment values,
  decorators, defaults, class bodies), dynamic module access (`sys.modules`, `globals()`).
- Methods and model fields are named `Class.member`. Python guards are out of scope (no
  call site is excluded by a guard).
- Known limits: no cross-repo imports in the language adapter (the contract edge in M8
  handles `acme_contracts`); no `sys.path`, `.pth`, editable installs or `setup.py`
  layouts; a reached class used as a base or metaclass is not followed.

## M7 measurements and choices

- 1,451 tests pass on Python 3.11, 3.12 and 3.13 (about 175 s on 3.12; most of the cost
  is git subprocesses in tests). Screens pass for s01, s02, s03, s04, s05, s19, s20; api
  routes for s06, s07, s08, s10, s22; route tables equal the fixture oracle at every api
  scenario head. Cross-repo screens and routes stay skipped until M8.
- Review blockers fixed: the walk dropped a second parent bringing the same names; a
  change inside a FastAPI entry file (helper, prefix constant) reached no route while
  saying complete. Majors fixed: whole-module reach and routes-file locals now reach every
  route in or through the file; layout routes without a path; a relative top-level path is
  `/<unknown:mount>/...` with `route_path_partial`; `EntryResult` carries an aggregate
  `complete`; routers imported under another name are found through importers.
- Decision: one screen per page file; layouts do not split a screen; the smallest route
  is the label, the others are `also_mounted`.
- FastAPI app-level and router-level dependencies or middleware reach every route in or
  through the file (errs toward affected).
- Walk performance on a generated 2,002-file repo: 19.5 s and 2,000 git processes before
  the review, 0.43 s and 2 after (multi-pattern grep per hop, memoised trees and
  resolution, cached reach).
- Known limits: descendant `<Routes>` are not composed through the component's mount; a
  whole-module reach at an entry file is not followed past it; exclusions with evidence
  are stated for the first hop only; no Celery adapter yet.

## M8 measurements and choices

- 1,623 tests pass on Python 3.11, 3.12 and 3.13 (about 225 s on 3.12). All 23 scenarios
  pass for http edges, consumers, contract reads, findings, cross-repo screens and routes,
  hop cap and the full incompleteness lists; the only remaining skip is s13 `branch_state`
  (M9). s11: the contract field change reaches the web screen `/billing/settlements`
  through the api, with evidence at every hop.
- Review (no blockers, 7 majors) fixed: a blocking record beside `complete=True`
  (invariant now: any blocking record makes the run incomplete); a removed route whose URL
  fits another route was silent; a client wrapper name misread as an HTTP method produced
  a false `added_route_uncalled`; added required fields not followed (construction sites
  now count); reads on adopted release branches missed; generic names on unrelated
  receivers taken as contract reads (reads now need a typed receiver, or a name distinctive
  in producer and consumer); incomplete definition counts not recorded.
- Decisions: configured edges or entry points with no adapter yet are non-blocking records
  (`edge_unfollowed`, `entry_points_unread`) that make the run incomplete; the fixture
  expectations gained exactly those records. A removed route still called is always a
  finding. Released changes are searched on every adopted branch. `pre_change_unavailable`
  makes the run incomplete.
- Known limits: no event or library edge adapters, no Celery entry points; the http
  client side is the endpoints-file adapter only; contract reads are Python only; an
  untyped `resp.status` whose name only the producer defines is still taken as a read;
  pins are one regex over one manifest (ranges are `pin_unresolved`).

## M9 measurements and choices

- 1,898 tests pass on Python 3.11, 3.12 and 3.13: serial about 265 s, `tests/run_parallel.py`
  with 4 workers about 100 to 130 s. All 23 scenarios pass end to end through `reachmap
  analyse` (in process, and one as `python -m reachmap`), with no skipped type left,
  including s13 `branch_state`. Over 2,000 evidence items in the reports resolve at their
  pinned commits; reruns give byte-identical JSON apart from the `run` block.
- Review blocker fixed: the branch matrix counted an added line as found anywhere in the
  file, so common lines (`return None`) made a branch look `present` and suppressed
  `branch-missing`. Lines now count only above the copies the file had before the change;
  without a pre-change version a file is `not_compared` with a note.
- Review majors fixed: an added file absent at a tip is that file's pre-change state; the
  change's own head branch is left out only by tip sha, with a note; any exception in a
  step gives a `failed` report (exit 3), never a traceback without a report; unresolved
  runs have a null `ref` and cite the change spec, never an all-zero sha; `--as-of`
  wiring is tested between commits.
- Choices: confidence is the weakest link of the best chain; area findings cite the
  changed symbol's definition line, then the chain; reports contain no machine paths
  (local remotes shown by repo name); failed and timed-out reports have null results;
  `complete_for: finding` covers the listed kinds plus the mirror kinds (see the fixture
  README). Exit codes: 0 completed, 2 usage or config, 3 refused or failed, 4 timed out.
- Known limits: `--pr` exercised with a fake host only; tags are not time-filtered by
  `--as-of` (stated in the report notes); `duplicate` findings are not built; suggestions
  and mapped tests are empty until M15.

## M10 measurements and choices

- 2,088 tests pass on Python 3.11, 3.12 and 3.13 (parallel about 100 to 135 s; serial
  about 268 s on 3.12). Every one of the 23 scenario reports renders to Markdown, HTML
  and the author view; golden Markdown for s11 and s13 is committed.
- Independent review attacked the HTML with script-closing variants, comment and CDATA
  openers, U+2028/2029, NUL, surrogates, markup in keys, and checked Markdown against a
  real CommonMark and GFM parser with 90 adversarial payloads: no content became markup or
  loaded anything; in a real browser the page ran under its hashed CSP with no network use
  beyond the page. Its one major (an edge kind from the report treated as tool text, so a
  hostile report crashed rendering) is fixed, with a permanent test that no report string
  is ever treated as the tool's own words.
- Choices: system font stacks instead of bundled fonts (no file, no request); the page
  has no links (only a `data:` favicon); the inline script only enhances a server-rendered
  page (filters, theme, print); odd characters are shown as escapes in every renderer;
  content over 2000 characters is cut in the rendering and kept whole in the embedded JSON;
  a failed run's header keeps the change's file count (a fact about the change).
- Known limits: Markdown inertness is checked by the package's own scanner in tests (the
  real parser was only used in review); print and dark theme not checked visually.

## M11 measurements and choices

- 2,342 tests pass on Python 3.11, 3.12 and 3.13 (parallel 114 to 163 s; serial about
  292 s on 3.12). Fixture pair (s01, eight decisions, rerun on a second commit): current 4,
  stale 2, orphaned 2, undecided 2, exactly as designed; an alias import changed to a
  relative import of the same file stays current.
- Review (no blockers, 3 majors) fixed: a failed rerun no longer overwrites a completed
  report (completed reports are `...-r<N>.json`, failed ones `...-failed-<hash>.json`); a
  run that did not complete has a null review revision; an unreadable decisions folder is a
  record, not a traceback.
- Decisions: evidence carries an optional `claim` (import: resolved target and names;
  route: method, path and handler or page; definition: symbol) and fingerprints hash the
  claim, so reformatting or respelling an import is not staleness; review data sits in one
  `review` object; every area and finding carries its `id` and `fingerprint`, verified on
  read; an advisory lock (`flock` on POSIX, `msvcrt.locking` on Windows, not NFS) guards
  revision numbers, reports, the log and the decision store.
- Open question for the owner: a decision does not go stale when only the changed code's
  own body changes again, because fingerprints describe what the change reaches, not the
  change itself.
- Known issues: the Windows lock path was written but not run; a re-export hop is not
  cited as its own chain line; the React Router adapter attaches the first chain's hop to
  every route of a changed routes file (seen while building the fixture pair; to check).

## M12 measurements and choices

- 2,467 tests pass on Python 3.11, 3.12 and 3.13 (parallel 119 to 169 s; serial about
  323 s on 3.12). Every guard (loopback bind, exact Host, same-origin and JSON-only for
  state changes, session token in a header only, one job at a time, separate process with
  a hard timeout, forward-only job states, validated input, fixed security headers and
  CSP, size and connection caps, logs without tokens or bodies) has tests against a real
  server on 127.0.0.1; an end-to-end test runs s13, records a decision through the runner
  and sees it current on the next read.
- Independent review attacked a live server: the HTTP boundary held. Its major (a timeout,
  cancel or shutdown left the analysis's git processes running, overlapping the next job)
  is fixed: each job registers its git process groups, and the runner kills them all
  whatever ended the job; the child exits if the runner dies.
- Choices: jobs run `reachmap analyse` in a spawned child so a job and the command line
  write the same files; the minimal page offers reports as an HTML download (a page opened
  from a blob would lose the report's own CSP); decision author and reason refuse control
  and direction characters on every channel.
- Known limits: Windows does not sweep a job's git processes; a git started in the
  microseconds before a child is killed can be missed; jobs are kept in memory only (last
  500); the M13 workspace is not built yet (the minimal page stands in).

## M13 measurements and choices

- Frontend: `npx tsc --noEmit -p tsconfig.app.json`, 158 vitest tests and `npm run build`
  pass locally (Node 25); CI runs them on Node 22 and builds the wheel with the workspace
  inside. Python: 2,498 tests pass (packaging tests required in CI).
- Runtime dependencies exactly react 19.3.0, react-dom 19.3.0, react-router-dom 7.18.4;
  dev dependencies only the brief's list; exact pins; every lock entry published before
  2026-10-07. Built output: one page, one hashed script, one hashed style, no inline code,
  nothing loaded from anywhere but the runner.
- Checked in a browser against a real `reachmap serve`: reports list, report sections in
  the M10 order, drill-down, a decision through the runner shown as quoted text, a hostile
  report as text only, the token removed from the address bar, only same-origin requests.
- Independent review: no blocker; security held. Fixed: suggestions merged into mapped
  tests was not caught by tests; report keys such as `__proto__` blanked the page (lookups
  are own-property only, routes have an error page); a token pasted into an open tab was
  ignored; a stale local static folder could be packaged; the TypeScript wording check now
  matches Python's on a shared table of 82 cases.
- Choices: CI uses Node 22 (Node 20 reached end of life on 2026-04-30); the runner answers
  `/reports/*` and `/jobs` with the workspace page so reloads work; no dev proxy (build
  and `reachmap serve`); four never-requested library URL strings in the bundle are allowed
  by the tests.
- Known limits: Node 22 and CI unverified locally; no list search or sort; accessibility
  checked structurally only.

## M14 measurements and choices

- 2,784 tests pass on Python 3.11, 3.12 and 3.13 (parallel 114 to 143 s; serial about
  322 s on 3.12). `reachmap mcp --reports DIR` is a stdio MCP server (JSON-RPC 2.0 by
  hand, no SDK, protocol versions 2025-11-25, 2025-06-18, 2025-03-26, 2024-11-05) with a
  fixed tool list: list reports, summary, section, evidence, list notes, add note, propose
  file. No tool decides, runs analyses, reads arbitrary files or reads config.
- Every report-derived string in every result is fenced as untrusted with a random fence
  id per response and escaped fence markers; every note is stamped "recorded via MCP,
  client reported as <name>" by the server from `initialize`, never from arguments.
  Independent review found no blocker or major: stamp and fence held against every attack;
  its minors are fixed (forged notes in saved reports dropped on load, page sizing,
  duplicate JSON keys refused, blank client names refused, qualified proposals checked).
- Notes live beside reports, go stale on a rerun, are orphaned when their target is gone,
  and render as content under "Model notes, recorded via MCP; not evidence". A proposed
  file is verified only when it exists at a revision the report read and names a
  distinctive changed symbol in code.
- Open question for the owner: proposed-file verification finds the name anywhere in the
  file's code, not scoped to where it is defined or imported from (context 5, Shape B).
- Known limits: the workspace does not show notes yet (the API carries them); `ping`
  before `initialize` is refused (stricter than the MCP spec).

## M15 measurements and choices

- 3,131 tests pass on Python 3.11, 3.12 and 3.13 (parallel 122 to 204 s on a lightly
  loaded machine). Test sources: Qase (header `Token`), TestRail (Basic `login:key`), and
  Playwright and Cypress specs mapped to screens through the route table. Read only (GET
  everywhere, redirects and foreign next links refused); suggestions are never mapped tests
  and never close a gap: a run where every area has suggestions still shows every gap.
- Independent review found a blocker (two spec fixture files ignored by `.gitignore`; now
  tracked, with a test that every fixture file is tracked) and majors, all fixed: the Qase
  auth header; one failing source could fail the whole run (now only its suggestions);
  "None suggested." after nothing was established; generic words matching (longer stop
  list, stems, one shared word must come from the area's route); the TestRail key alone not
  redacted.
- Decision: every incompleteness record carries `scope` ("selection" or "suggestions"),
  `blocks_selection` and `blocks_suggestions`; test-source records never block area
  selection; `counts.blocking_records` counts selection only and
  `counts.blocking_suggestion_records` the rest. Old reports still load.
- Known limits: nothing was run against the real Qase or TestRail; no 429 retry; specs
  read only the first argument of `goto`/`visit` (helpers and page objects not followed);
  stemming joins words such as `billing` and `bill`; the redesigned frontend shows the new
  scope fields after its own port.

## M13b measurements and choices

- Owner decision (2026-10-21): a production-grade frontend with a full UI kit. Amends
  context 13.4 and ground rule 4 for the frontend: runtime dependencies are react,
  react-dom, react-router-dom, Mantine (core, hooks, notifications, spotlight),
  @tabler/icons-react, @xyflow/react, @dagrejs/dagre and prism-react-renderer, exact pins,
  all published at least two weeks before use. Anything else still needs the owner.
- Look: Linear / Vercel, light default, designed dark theme; one meaning per colour (map
  in `components/badges.tsx`); text contrast at least 4.5:1 measured in both themes.
- App shell, command palette (Ctrl/Cmd+K), report tabs with URL sync, overview cards and
  reach graph, areas triage with bulk decide (one request per area) and keyboard triage
  (j/k, Space, t/s/x, Enter, Esc, Ctrl/Cmd+Enter), chains as step lists with highlighted
  evidence, branch grid, cross-repo graph, suggestions per area with each source's state.
- CSP unchanged in spirit: the runner gives the workspace page a fresh style nonce per
  response for Mantine's runtime styles; never `unsafe-inline`.
- Independent review: a blocker (report repo names reached `Object.prototype` through the
  graph layout library; dagre now gets internal ids only, with a pollution test) and
  majors (illegible graphs, keyboard focus, badge contrast, test gaps, prism on the list
  page) fixed. 312 vitest tests; 500-area j/k render median 5.4 ms.
- Bundle (gzip): first load about 216 kB; report page chunk 60 kB; graphs 72 kB on demand.
- Known limits: model notes (M14) not shown in the workspace yet; filters and selection
  are page state, not in the address; Jobs page restyled only.

## M16 Next.js measurements and choices

- App Router (groups, dynamic and catch-all segments, private folders, slots, interception
  as partial, route handlers per exported method) and Pages Router (pages, `pages/api` as
  `ANY`, `_app`/`_document` wrappers); layouts and templates reach the pages below them;
  middleware and instrumentation reach every route. 18 hand-traced scenarios with an
  independent oracle; 3,335 tests pass on 3.12 after merging main.
- Review: no blocker; one major fixed (an `export *` chain deeper than five files was
  dropped while the answer said complete; now a blocking `specifier_unresolved`). Next.js
  build errors (page and route in one folder, the same URL in both routers) are recorded as
  non-blocking `route_conflict`.
- Choices: one screen per URL (a slot page and an intercepting page of one URL do not
  collide); fixtures in their own tree `tests/fixtures/nextjs/`; a route with method `ANY`
  fits any client method; shared helpers moved to `adapters/base.py`.
- Known limits: middleware matchers and `next.config` rewrites, redirects and i18n are not
  evaluated; `.mdx` pages are not read; `page_extensions` such as `page.tsx` unsupported.

## M16 OpenAPI measurements and choices

- Server side: routes from a committed JSON spec (`servers`/`basePath`, `$ref` closure with
  cycles ended, positions kept for every key), handlers found by operationId in the
  configured handler files or folders, a spec diff as a change (`operation-changed`,
  removed and added operations). Client side: generated TypeScript client calls matched by
  their import, method and URL taken from the client's copy of the spec. 10 hand-traced
  scenarios; 3,610 tests pass on 3.12 after merging main.
- Review: a blocker fixed (a handlers folder or a glob matching nothing gave an empty,
  complete answer; now `handlers_unmatched`), and two majors (a split-file path item read
  as removed routes; generic operation names dropping import-backed calls: an imported
  call is a resolved edge, only member calls by a generic name are dropped).
- Choices: YAML specs are `file_not_analysed` (JSON only); a deleted spec lists its base
  operations as affected and is blocking; a new spec makes every operation affected;
  `http_server` cannot be combined with a routing `entry_points` adapter in one repo.
- Known limits: no YAML; external `$ref` files are not read (operations treated as
  changed with a gap); handlers found by name only; client side TypeScript/JavaScript only.

## M16 Django measurements and choices

- `urls.py` with `path`, `re_path` (approximate), `url`, `include()` chains (strings,
  modules, lists, `router.urls`), namespaces, `ROOT_URLCONF` from settings (including
  settings that star-import settings and `DJANGO_SETTINGS_MODULE`), function views (`ANY`
  unless restricted), class views (`as_view`, inherited methods, HEAD and OPTIONS, literal
  `http_method_names`), DRF routers and viewsets with `@action`. 14 hand-traced scenarios;
  3,891 tests pass on 3.11, 3.12 and 3.13 after merging main.
- Review: a blocker fixed (an in-repo include under an `apps/` layout was treated as
  external, giving an empty, complete answer); majors fixed (framework names per
  entry-point kind, settings modules found, views called by routed views followed, import
  edits reach only re-bound names, `ANY` narrowed to some methods not a removal, regex
  paths marked with their own attribute).
- Shared guard (all adapters): a route counts as removed only when the head route table
  was read whole, and as added only when the base table was; otherwise a blocking
  `route_change_withheld` says what was withheld. This fixes a confirmed FastAPI trap on
  main (an unparsable router file produced false `removed_route_still_called` findings).
- Known limits: settings chosen only by the environment are not detected; Django and
  FastAPI route tables share logic that is not yet factored; `i18n_patterns`,
  function-built pattern lists and custom routers are recorded, not read.

## M16 Express and NestJS measurements and choices

- Express: routes on apps and routers across files (ESM and CommonJS), mounts and nested
  mounts with prefixes (constants followed through re-exports; a non-literal prefix is
  `<unknown:...>` and partial), `route()` and chained calls, middleware reach (app-level
  reaches all routes in and through the file, route-level only its routes), routers handed
  to helpers (`routes.mount(app)`) resolved or recorded. NestJS: controllers and method
  decorators, module graph including dynamic `forRoot()` modules, global prefixes paired
  with their application, URI versioning, global guards and interceptors reaching every
  served route. 25 hand-traced scenarios; 4,297 tests pass on 3.12 after merging main.
- Review: a blocker (routes registered through a helper call unseen, a false unmounted
  claim and an empty complete answer) and six majors fixed. The adapters guard route
  changes per file: an unreadable router or controller withholds only its own routes
  (ex24 and ex25 would otherwise give six false removed-route findings).
- Choices: Express and Nest are told apart by imports, never by names; `all` is `ANY`;
  CommonJS `require` is not narrowed by name.
- Known limits: a call as the first argument of `use` is treated as middleware; registrar
  lookup goes one level deep; Nest `defaultVersion` is not read; no Koa, Fastify, GraphQL or
  microservice entry points.

## M16 Spring measurements and choices

- Java and Kotlin language adapter (lexer for text blocks, char literals, unicode escapes in
  strings, Kotlin templates and raw strings; units and `Class.member` changed symbols;
  importers by every import form, same package, and the `package` line; member narrowing;
  interface and supertype dispatch). Spring routes: class and method mappings with path
  and method arrays, constants across files and Kotlin `const val`, `${key:default}`
  placeholders, context and servlet paths with profiles (YAML and `.properties`
  documents), interface and base-class mappings, project meta-annotations, controller
  advice. 20 hand-traced scenarios; 4,629 tests on 3.12 after merging main.
- Review: no blocker; eight majors fixed (placeholder defaults used for unknown values,
  sibling module context paths, ambiguous class names across modules, meta-annotation
  controllers and paths, controller-wide methods and advice, Kotlin package folders,
  `.properties` profile documents). The route-change guard is scoped to unread files.
- Known limits: meta-annotations read one level; advice selectors beyond basePackages,
  value and assignableTypes reach every route; functional routes and JAX-RS recorded, not
  read; no Gradle/Maven, Lombok or generated sources; nothing run on a JVM.

## M14b workspace model notes

- The workspace shows M14 model notes: a "Model notes" tab (only when a report has notes)
  labelled "Model notes, recorded via MCP; not evidence.", grouped current, stale (with the
  revision), orphaned; note badges and sections in the area and finding drawers. The stamp
  is built from the client's own fields and shown as its own line marked as the client's
  claim; note text is content, never markup. Notes change no counts (tested against the
  whole view). A note made on another revision than the one viewed says so. Parity with
  the Python view through generated fixtures; 385 frontend tests.
- Review: no blocker; test gaps on the stamp and the stale badge, the viewed-revision case
  and an unreadable store's tab badge fixed.

## M19 docs (first part)

- User docs: README, quickstart, config reference (every registered key, tested),
  adapters and contributor guide, rules for users (never claims safety or coverage,
  records, blocking and scope, risk and confidence, decisions, notes), security,
  evaluation (counts, independence, weak samples, no recall without linked defects),
  CHANGELOG draft for 0.1.0, and a runnable example. Every command in the docs was run.
- Independent review checked every command and claim against real runs; its findings are
  fixed (including a hidden direction control character in a doc, and "recorded" host
  responses that are invented). Tests block undocumented config keys, banned punctuation,
  coverage or safety wording, invisible characters and token shapes in the docs.
- The `reachmap init` and evaluation sections came with M17 and M18; the output snippets were
  regenerated after the version bump (see M19 release prep below).

## Hardening: unmatched patterns, config keys, YAML specs

- An entry-point pattern or search that matches nothing at the head is a blocking
  `routes_unmatched` (React Router, Next.js, FastAPI, Express, NestJS, Spring); when files
  matched before the change, the detail says which were renamed, deleted or are gone.
  Before, a misplaced or moved routes file gave an empty answer with no record.
- Every config table refuses unknown keys with the dotted path and a did-you-mean hint
  (validators for every kind, `tests` and `aliases`); `tests = { markers = [] }` means no
  file is a test.
- OpenAPI: a YAML `spec` is refused at config load, and a spec that becomes unreadable
  (YAML, invalid JSON, deleted) is a blocking `spec_unreadable`.
- Found by the docs review; reviewed by Opus (moved-routes gap and docs alignment fixed).

## M18 evaluation tools

- `reachmap audit` re-reads every cited line at its pinned commit (one `cat-file --batch`
  per commit) and reports ok, mismatch, absent, unreadable and structure problems as
  counts; a report from a run that did not complete is "not audited" (exit 1). Every
  fixture report passes: 432 / 246 / 239 / 166 / 359 / 296 distinct citations in the
  acme, nextjs, openapi, django, express and spring workspaces (pinned in tests).
- `reachmap witness` analyses historical changes as of their merge time and counts whether
  the report cited files a later follow-up touched (by findings, screens, any evidence;
  exclusions and release findings apart); refuses dependent or revert cases; prints counts
  only, the small-sample and no-recall sentences, and what `--as-of` does not pin.
- `reachmap sheet` writes judging sheets for people (CSV protected against formula
  injection, Markdown) and `--score` counts filled sheets; no tool lets a model fill them.
- Review: four majors fixed (failed-run reports passed, loosely loaded reports passed,
  unreadable note or decision stores ignored, release findings counted as witness hits).

## M17 measurements and choices

- `reachmap init SPEC...` (local paths, `file://`, `github:`, `gitlab:`, `bitbucket:`,
  `git:`, optionally `NAME=SPEC`) writes a starter `reachmap.toml` to standard output (or
  `--out FILE`, atomic, `--force` to replace) with every guess commented with its evidence
  `path:line`, a confidence and "confirm". Detects languages, aliases, entry-point kinds
  (React Router, Next.js, FastAPI, Django, Celery, Express, NestJS, Spring), HTTP clients,
  OpenAPI specs, Pydantic contracts, tests, service markers, library roots and edges.
- The generated configs analyse every scenario of all six fixture workspaces correctly
  after one documented confirmation (`distinct_max`, which no repository states). 5,132
  tests pass on 3.12 after merging main.
- Review: the core held (escaping fuzzed, output always loads, no secret written); fixed a
  crash on deeply nested files, silent skips (symlinks, YAML specs, unread files), false
  positives (non-React routers, URL constants, shared URL segments), pin patterns, and the
  `> reachmap.toml` usage. Any unread file makes exit 3 with the files named.
- Known limits: detection reads the default branch only, by line patterns; a `<Route>`
  file with no react-router dependency or import is read at low confidence with a note.

## M19 release prep

- **Version.** One place: `__version__ = "0.1.0"` in `reachmap/__init__.py`; `pyproject.toml` has
  `dynamic = ["version"]` with `attr = "reachmap.__version__"` (read from the source, the package is not
  imported). Tests hold the wheel's `Version` to it. The frontend's own `package.json` version stays
  `0.0.0` (it is private and never published). Classifiers: Development Status 3 - Alpha, Python 3.11 to
  3.13, topic, environment; project URLs point at github.com/adithyakm13/reachmap. The licence is the
  SPDX expression `Apache-2.0` with `license-files = ["LICENSE"]` (the build backend needs setuptools 77 or
  newer, a build-time tool only; the table form and the licence classifier are deprecated by setuptools).
  Runtime dependencies: none (the wheel's METADATA has no `Requires-Dist`).
- **The bump touched fixtures**: the Markdown goldens (`tests/golden/`), the generated report fixtures
  of the frontend (`frontend/fixtures/`, regenerated with `make_reports.py`) and one test that hard-coded
  `reachmap 0.0.0` now read the version from the package. Two test files held raw invisible or minus
  characters as hostile inputs; they are now escapes (`\u200b`, `\u202e`, `\u2212`) with the same values.
- **Build** (a throwaway venv with `build` 1.6.1 and `setuptools` 84, dev tools only): `npm ci`, `npm run build`
  (1.3 MB of `dist`: one HTML file, 14 scripts and 2 style sheets), then `python -m build`. Wheel
  `reachmap-0.1.0-py3-none-any.whl` 1,098,362 bytes (171 entries, 3.6 MB uncompressed, 142 Python files, 17 files
  of the workspace under `reachmap/runner/static/`); sdist `reachmap-0.1.0.tar.gz` 1,014,651 bytes (177 files).
  Neither holds tests, fixtures, `node_modules`, frontend sources, source maps, `.claude/`, `docs/briefs/`,
  screenshots or a secret; the wheel has `licenses/LICENSE`. The sdist first held `tests/test_*.py` (setuptools
  adds them by default): `MANIFEST.in` now prunes tests, docs, examples and frontend sources (a packaging test
  holds this), and the sdist keeps `frontend/dist/` so that a wheel built from it has the workspace.
- **Install check** (`tests/release/check_install.py`, standard library, not in the unit suite): clean venv,
  `pip install --no-index`, then `--version`, `init` on the four fixture repositories, `analyse`, `report`
  to md and html, `audit`, `serve --port 0` (`/api/health` without a token, the workspace page, an asset and `/api/reports` over
  loopback with the printed token, `/api/reports` without it refused with 401) and `mcp` (`initialize`, `tools/list`,
  `list_reports`). It passed on Python 3.11.15, 3.12.13 and 3.13.14. It was also run on a wheel built
  without the frontend, where it fails at the `serve` step, as it should.
- **CI.** `.github/workflows/release.yml` on tags `v*` and by hand: builds the frontend and the wheel and sdist,
  checks the tag against the version, checks the listings, keeps `dist/` as a workflow artifact, then runs the
  install check on 3.11, 3.12 and 3.13. No publishing step, no secret, `contents: read` only. It has not run on
  GitHub yet (not pushed, no tag): its commands were run locally, the workflow file itself was only parsed.
- **Final checks** on this branch: 5,141 Python tests pass on 3.12 (`tests/run_parallel.py`, and the same
  suite once in one process with `unittest discover`), with setuptools present so the packaging tests run, and
  `tests/run_parallel.py` passes on 3.11 (5,141 tests) and 3.13 (5,134 tests, 1 skipped);
  frontend `tsc --noEmit`, 385 tests and the build pass; `reachmap audit` passes on all fixture reports (the
  evaluation tests). Scans of tracked files: no token found (the shapes appear only in the redaction code, its
  tests and the docs that name them), no email address other than noreply or example ones (the other `@` strings
  are decorators, `git@github.com` URLs and `name@version` words), no dash, arrow, curly quote or invisible
  character in any tracked text file after the two escapes above.

- **Release review fixes** (after the first release review of this branch):
  - Third-party licence notices: `npm run build` now runs a `postbuild` step (`frontend/scripts/third-party-licenses.mjs`,
    Node built-ins only) that writes `frontend/dist/THIRD_PARTY_LICENSES.txt`: name, version, licence field and the full text of
    every licence file (LICENSE, LICENCE, COPYING, NOTICE, any case or extension) of each of the 67 production packages of
    `package-lock.json`. A production package with neither a licence file nor a licence field, or one that is not installed, fails the
    build. One package (`react-remove-scroll-bar`) ships no licence file; its entry says so and gives its licence field (MIT) and
    author. The file is in the wheel (`reachmap/runner/static/`) and the sdist (`frontend/dist/`); the runner serves it as
    `text/plain` with its usual policy; packaging tests, the install check, `release.yml` and `ci.yml` look for it.
  - The README is the long description on PyPI: its install paragraph is true before and after an upload, and every link is absolute at
    the tag `v0.1.0` (`tests/test_docs_text.py` resolves each to a file of the checkout and checks the heading of an anchor).
  - The install check asserts what it claims: the starter config goes through the installed `reachmap.config.load`, the audit says
    `ok` with a count above 0, `list_reports` counts 1, and the installed package holds the notices file.
  - `release.yml`: `timeout-minutes: 20` on both jobs, no `fetch-depth: 0` (nothing reads history).
  - Classifiers say POSIX Linux and macOS (Windows is not run); the sdist carries `CHANGELOG.md`, `SECURITY.md`, `CONTRIBUTING.md`.
  - Results: 5,147 Python tests pass on 3.12 (`tests/run_parallel.py`, with setuptools and `REACHMAP_REQUIRE_PACKAGING=1`) and on 3.11
    (same); 3.13 without setuptools runs 5,140 tests and 1 skipped; 385 frontend tests, `tsc` and the build pass; the new
    wheel (1,105,681 bytes) and sdist (1,029,337 bytes) pass `check_install.py` on 3.11.15, 3.12.13 and 3.13.14.
  - **Why 3.13 counted 5,134 tests and 1 skipped against 5,141 on 3.11 and 3.12.** All three Pythons collect the same tests (the same
    test ids on each: 5,147 today, 5,141 then). `tests/test_packaging.py` skips its whole `Packaging` class in `setUpClass` when
    `setuptools` cannot be imported (unless `REACHMAP_REQUIRE_PACKAGING=1`), and a class skipped that way counts as 1 skip and none of
    its 7 tests: 5,141 - 7 = 5,134, plus 1 skipped. The 3.13 interpreter here has no `setuptools` (checked), and the run today without
    it gives the same shape (5,147 - 7 = 5,140, 1 skipped). So the difference is a missing `setuptools`, not Python 3.13; with it
    installed, 3.13 runs the same tests as the others. This was inferred from the arithmetic and the local interpreters, not from the
    original 3.13 run.

## Owner decision 2026-10-22: design notes made generic

Before the repository goes public, the owner decided to strip every employer-specific detail from it.

- The design notes (`docs/CONTEXT.md`, `docs/BOOTSTRAP.md`) no longer name or describe the earlier internal tool the design grew out of.
  Gone: its name, the mapping of its internal modules and stack (context section 1 now has "Parts of the design" and section 6 is "What a
  single setup would hard-code", both generic), the example module name, the defect anecdote (now a hypothetical example), the figures
  quoted from that tool, and a record in 13.1 that does not belong in a public repository. Section numbers are unchanged; headings
  changed only where they named or described the earlier tool (the title, sections 1, 2, 6 and 10, and the table in section 1).
- The legacy config loader was removed from the package: `reachmap/config_legacy.py`, its fixtures and tests, and the text in
  `docs/config.md`. A `.json` config file is refused without being read, with "JSON configs are not supported; use a .toml file", and
  `loads(..., fmt="json")` says the same. `module_map`, which was accepted only so that the legacy config loaded and was never read, is no
  longer an accepted key.
- Context section 13 is amended where it listed legacy config support: 13.2 carries a dated note, and the M3 and hardening briefs and the
  M3 row of `docs/BOOTSTRAP.md` say that TOML is the only format.
- `tests/test_docs_text.py` now reads the design notes in its clean-room scan too, and a new test fails if the earlier tool's name appears
  in any tracked text file (found with `git ls-files`), so it cannot come back. The test holds only a SHA-256 of the name, so the name is
  not in the repository.
- Repository plan: a fresh public repository that starts from a single initial commit of the cleaned tree. PyPI is deferred by the owner.
  Nothing was pushed or tagged here.

## Release 0.1.0 readiness

**Ready.** The package, the docs (README, quickstart, config, adapters, rules, security, evaluation,
releasing), the changelog (dated 2026-10-21, every pull request #1 to #26, known limits), the example
workspace, the wheel with the workspace inside, the install check and the release workflow.

**Known limits** (full list in `CHANGELOG.md`): tested on small invented repositories and invented host
responses only, never on a large real one; `gh` and `glab` never run against a real host and `--pr` run
with a fake host only; no test source run against a real service; the `event` and `library` edge kinds and
Celery are not followed; no YAML OpenAPI; route tables read from module-level declarations only; Windows
paths (lock, process sweep) unrun; a decision does not go stale when only the changed body changes again;
the release workflow has not run on GitHub.

**Needs the owner** (nothing of this was done): make a fresh public repository and push a single initial commit of the cleaned tree to
it (decided 2026-10-22); push the tag `v0.1.0`; switch on private vulnerability reporting when the repository goes public. Publishing 0.1.0 to PyPI
is deferred by the owner (when it happens, the owner runs `twine upload` with their own token, or sets up trusted publishing; no token is
ever put in the repository, in CI or in a chat). Details in `docs/releasing.md`.

**Still needs the owner** (found by the release review; none of it was decided or done here):

- The public repository starts from a single initial commit of the cleaned tree; the per-milestone history and pull requests stay in the
  private development repository.
- Decided 2026-10-22: the design notes are made generic and the legacy config loader is removed (see "Owner decision 2026-10-22: design notes made generic" above).
- A trial on one real repository and one real host, with a read-only credential, before PyPI. Everything so far ran on invented
  repositories and invented host responses.
- Recheck that the PyPI name `reachmap` is free, and do a TestPyPI dry run with the files of the release run.
- Publish to PyPI only once the repository is public, so that the project URLs in the metadata resolve.
- The `m19-release` pull request merges before `v0.1.0` is tagged, and the upload uses the `dist` artifact of the release run, not a
  local build.
- The seven-day answer promise in `SECURITY.md`: confirm that the owner can keep it.
- The clean-room scan for the employer's names was done by hand by the release review over the whole history and found nothing.
  `REACHMAP_CLEAN_ROOM_TERMS` was not set, so the test that reads a list of terms did not run with one. The design notes are now read by
  that test too.

## Known not to work

See the Known limits in CHANGELOG.md and each milestone section above.
