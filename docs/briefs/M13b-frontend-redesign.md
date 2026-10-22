# M13b brief: production-grade frontend redesign

The owner reviewed the M13 workspace and asked for a production-grade frontend. Owner
decisions (2026-10-21), which amend context section 13.4 ("no UI kit") and bootstrap ground
rule 4 for the frontend only:

- A full UI kit is allowed. Use **Mantine** (core, hooks, and the Mantine packages the
  features below need: notifications, spotlight, modals, dates only if used) with
  **Tabler icons** (`@tabler/icons-react`).
- Look: **Linear / Vercel**: quiet neutral greys, one accent colour, dense but airy,
  excellent dark mode, crisp typography, subtle borders instead of heavy shadows.
- Priorities, all four: visual polish; navigation and layout; triage workflow; evidence
  and graphs.
- For the graphs and code: **@xyflow/react** (React Flow) with **@dagrejs/dagre** for
  layout, and **prism-react-renderer** for syntax-highlighted evidence. Any other runtime
  package still needs the owner: stop and ask (report it instead of adding it).
- Python package stays standard-library only. The wheel still serves the built static
  files; users still need no npm.

Everything in the M13 brief and its Implementation notes that is not about dependencies
or look still holds: rules from context sections 4 and 8, the token handling (fragment,
replaceState, memory only, `X-Reachmap-Token` header), the API client as the only module
calling fetch, report content as text only (never HTML; no dangerouslySetInnerHTML,
innerHTML, eval, new Function, href/src from data), odd characters made visible like
M10, the wording check over every UI string (shared case table with Python), suggestions
labelled "Suggested, not verified coverage" and separate from mapped tests, a skip shown
as "risk accepted, not tested (reason from <author>: "...")", decisions never changing
priority or counts, failed and timed-out reports with no counts, own-property lookups for
report keys, the error page, light theme default with a toggle (dark must look designed,
not inverted), the runner's CSP (`connect-src 'self'`), and no external URL loaded at run
time (no fonts or icons from a CDN: everything bundled).

## Layout and navigation

- App shell (Mantine `AppShell`): left sidebar with workspace name, nav (Reports, Jobs),
  collapsible; top bar with breadcrumbs, a command palette (`Spotlight`, Cmd/Ctrl+K: jump
  to report, area, finding, page; run actions), theme toggle, keyboard shortcut help (`?`).
- Routes: `/` reports; `/reports/:name` report with tabs; `/reports/:name/areas/:id` and
  `/reports/:name/findings/:id` detail (as a side drawer on top of the list, with its own
  URL so links and reloads work); `/jobs`.
- Code-split per route (`React.lazy`), skeleton loaders, empty states with a sentence and
  an action, error boundaries per section with fixed words, toasts for actions
  (notifications), optimistic UI only where safe (never for decisions: show the runner's
  answer).
- Responsive down to tablet width; usable on a 13 inch laptop without horizontal scroll.

## Reports list

- Table: change label, repo, status badge, revision, head sha (short, copy button),
  created time (relative with exact on hover), areas to test, blocking records count.
- Search, sort, filter by repo and status; stale reports (newer revision exists) marked.
- Clicking a row opens the report.

## Report page

- Header card: repo, change, head and pre-change shas, status, revision, run time; a
  prominent banner when the run is incomplete (blocking records first, with a link to the
  Records tab); never "safe", "complete coverage" or "nothing to test".
- Tabs (URL-synced): Overview, Areas, Findings, Branches, Cross-repo, Records,
  For the author, Model notes (if present in the report).
- Overview: stat cards (must test, sample, candidates; findings by kind; risk and
  confidence as two separate distributions, never combined), decision progress (decided /
  undecided / stale / orphaned) as a segmented bar, a compact "what this change reaches"
  graph (repos as groups, screens and routes as nodes), top blocking records.
- Areas: data table grouped by priority (must test, sample, candidate) with filters
  (repo, kind, decision state), row selection, bulk decide (test / sample / skip with one
  reason and author for the selection, through the runner, each decision its own POST,
  results reported per row), keyboard triage (j/k to move, t/s/x to open the decide
  dialog prefilled, Enter to open the drawer), decision badges (current, stale with "what
  changed", orphaned listed separately).
- Area drawer: label, route lines, also mounted, priority, completeness of its chains; the
  chain as a left-to-right graph (React Flow, dagre layout, nodes per hop with file and
  line, swimlanes per repo for cross-repo chains, edges labelled import / route / http /
  contract / package), and below it each hop's evidence as a highlighted code block (the
  verbatim line, language by extension, line number, repo, path, short sha with copy).
- Findings: table with kind, title, risk badge and confidence badge in separate columns,
  confidence basis, evidence count; filters; exclusions in their own group "Not reached
  (with evidence)"; drawer with evidence code blocks.
- Branches: matrix grid (branches by files) with state chips (present, partial,
  pre_change, diverged, file_absent, unreadable, not compared) and the evidence lines on
  click; `branch-missing` findings linked.
- Cross-repo: graph of repos and edges with pins and adoption per branch, contract reads,
  candidates shown apart and dashed.
- Records: blocking first, kind, subject, what could not be established, detail.
- For the author: copyable unit test commands per repo, other branches, files beyond the
  diff.

## Visual design system

- Mantine theme: one accent (indigo or violet), neutral gray scale tuned for light and
  dark, radius small, Inter-like system font stack bundled? No external fonts: use the
  system UI stack (`-apple-system, Segoe UI, Roboto, ...`) and a system monospace stack;
  do not add a font package unless the owner asks.
- Status colours used consistently: blocking (red), incomplete (orange), must test
  (accent), sample (blue-gray), candidate (dashed outline, gray), stale (yellow),
  orphaned (gray with strike), risk high/medium/low and confidence high/medium/low as two
  different badge styles so they are never read as one scale.
- Density: compact table rows, generous page padding, clear hierarchy; icons from Tabler.

## Engineering

- Keep TypeScript strict; vitest + Testing Library (update and extend the existing 158
  tests; keep the security and wording tests; add tests for bulk decide, keyboard triage,
  drawer routes, tabs URL sync, command palette, graphs render with hostile labels as
  text).
- CSP: the runner serves `style-src 'self'` today. Mantine and React Flow must work under
  the runner's CSP. Prefer static CSS files (import Mantine and React Flow CSS so Vite
  emits them) and avoid runtime `<style>` injection; if Mantine must inject CSS variables,
  use its nonce support with a per-response nonce from the runner (a small, tested runner
  change in `reachmap/runner/server.py`) rather than `'unsafe-inline'`. Style attributes
  set through the DOM by React are not affected by CSP; check there are no CSP violation
  events in the browser.
- Bundle: route-level splitting; report the gzipped size of each chunk; the graph library
  loads only on pages that show a graph.
- Dependencies: exact pins, each published at least two weeks before today (check with
  `npm view <pkg> time --json`); `npm install --before=<date>` for transitive ones;
  committed lock; record every package, version and publish date in the notes.
- Node 22 in CI (unchanged job: npm ci, tsc, test, build, wheel with static files).

## Verification (must be done, with screenshots)

- Start `reachmap serve` on a reports directory containing the fixture scenario reports
  (s01, s06, s11, s13, s16, s21) plus the hostile, failed and timed-out fixtures, open it
  in the browser pane, and check light and dark: reports list, each report tab, area and
  finding drawers with graphs and highlighted evidence, bulk decide through the real
  runner, keyboard triage, command palette, a hostile report (text only, no CSP violation
  events, no console errors), a failed report (no counts), the no-token page.
- Save screenshots of the main screens (light and dark) under
  `docs/screenshots/` (PNG, reasonable size) and list them in the notes.
- Python suite (parallel runner, 3.12 at least; all three versions if Python changed) and
  frontend tsc, tests, build all pass.

## Implementation notes

Where the code differs from, fills in, or goes beyond the text above. Each point is covered by a test unless it says otherwise. These notes replace the notes of
M13 on dependencies, styles ("plain CSS in two files"), the built output ("one script, one style sheet") and the page structure; everything else in the M13 notes holds.

### What was built

The workspace is rebuilt on Mantine 9 (`AppShell`, `Tabs`, `Table`, `Drawer`, `Modal`, `Spotlight`, notifications) with Tabler icons, React Flow with dagre for the graphs and
prism-react-renderer for the code. The data layer is unchanged: `api/client.ts` is still the only `fetch`, `report/{check,read,text,view,wording,words}.ts` keep their rules, and
the TypeScript view model still equals `fixtures/order.json`. New modules: `report/{graph,grid,stats,lang}.ts` (the three graphs, the branch grid, the overview counts and the
language of a cited line: plain data, tested without a browser), `graphs/{layout.ts,Graphs.tsx}` (dagre and React Flow, loaded only by a page that draws a graph),
`components/{badges,CodeLine,Evidence,Decisions,Header,Records,Boxes,...}.tsx`, `components/tabs/*` (one file per tab), `components/{AreaDrawer,FindingDrawer}.tsx`,
`pages/{Reports,ReportPage,Jobs}.tsx`, `palette.tsx` (command palette), `keys.ts`, `nonce.ts`, `providers.tsx`, `theme.ts`, `time.ts` and `styles/{app,code,graph}.css`.
The M13 components `Areas`, `Author`, `Findings`, `BranchMatrix`, `Chain`, `Suggested`, `ThemeToggle` and the pages `Report` and `Drill` are gone (replaced by the above).

- **Routes.** `/` the list; `/reports/:name` the report; `/reports/:name/<tab>` (`areas`, `findings`, `branches`, `cross-repo`, `tests`, `records`, `author`, `notes`);
  `/reports/:name/areas/:id` and `/reports/:name/findings/:id` the same list with the drawer open, so a link, a reload and the back button all work; `/jobs`. One route
  (`reports/:name/*`) reads the rest of the address (`ReportPage.parseTail`), so the page stays mounted while a drawer opens and closes and the filters and the selection survive.
  A run that did not complete has no tabs: any tab path goes to its header and records (no count, no list, no form).
- **Code split.** Each page (`Reports`, `ReportPage`, `Jobs`) is a chunk loaded when its route is first visited (lazy routes, a `hydrateFallbackElement` while the first one loads).
  React Flow, dagre and their styles are one chunk (`Graphs`) that a report page fetches when a graph is first drawn; the list and the jobs pages never load it (tested on the built
  output: the entry script holds none of it). Prism is in the chunk of the page that shows code. A section that fails to draw shows fixed words (`SectionBoundary`) and leaves the rest of the page
  alone; a route that fails shows the `Crash` page as before.
- **Reports list.** Change (the report's file name under it), repository, status, revision, head (short, the whole id in the title, a copy button), started (relative, the exact text in the
  tooltip), areas to test, blocking records; search, filter by repository and status, sort by six columns (`aria-sort`), "Newer revision" on a report whose change has a later revision in the
  list. The runner's list did not hold the head, the areas or the blocking records, so `_summary` in `reachmap/runner/api.py` now adds `head_sha`, `areas` and `blocking_records` (null for a run that
  did not complete: no count, rule 4.9; null when the file holds something that is not an integer or a text). A runner of an earlier version leaves them out and the cells are empty. Tested in Python and in the page.
- **Report page.** A header card (status, revision, commits with a copy button, run time), a banner when a record blocks selection or the run did not read everything it set out to read (with a
  link to the records), a red or orange banner and no counts for a failed or timed-out run. Tabs are URL-synced; the counts on the tabs are the report's own.
- **Overview.** Four stat cards and the other counts in a line; findings by kind; risk and confidence as two separate bars in two different colour ramps (warm and indigo) with their own legends and notes
  ("a different scale from ..."); decisions as a segmented bar (decided, needs review, not decided; orphaned is a count apart, and nothing is said when the store of decisions could not be read); the first
  three blocking records; the reach graph (repositories as groups, screens and routes inside them, at most eight per repository and the rest counted); the files and how the change was found.
- **Areas.** One table per priority (Must test, Sample, Candidates with a dashed badge), filters (text, repository, kind, decision state), check boxes and select-all, a bar with "Decide for the selection",
  keys (`j` and `k`, Space, Enter, `t`, `s`, `x`; they are left to a text field, a list of choices, and (Space and Enter) to a button, link or check box that has the focus, and ignored while any dialog is open),
  decision badges (the verdict in the tool's words; "decision needs review" opens a popover with what changed; "Not decided" only when the store was read), orphaned decisions apart, and the whole Decisions
  section in an accordion. Mapped tests and suggestions are on their own tab (Tests) and never together (rule 4.3).
- **Deciding.** One form serves one area or finding and a selection. For a selection it posts one decision per area, one after the other, and shows the runner's answer for each (fixed words, the status and
  the runner's own code and field); nothing is optimistic, the page reads the report again and shows what the runner computed. The name is remembered as a convenience (storage in try/catch, as before).
- **Drawers.** The area drawer: facts, the route line, the chain as a graph left to right with one lane per repository (dagre gives the order, the lanes are bands), an edge labelled `import`, `route`, `http`,
  `contract` or `package` only when the claim the report attached to the step, or the edge the run followed between two repositories, says so (otherwise no label: none is made up), then every step as a block
  (repository, `path:line`, the line verbatim and coloured by token with the language taken from the extension, short sha with copy), the findings about it and the decision form. Choosing a step of the
  graph moves to its block. The finding drawer: kind, risk and confidence as two different badge styles, the basis, all the evidence, the areas it is about, the decision form.
- **Branches.** A grid of branches by changed files. A file's state is read from the exact words `branches.py` writes in the row's detail (`path: state`) and from nothing else; a cell the report does not itemise says
  "not itemised" (a dashed chip). A cell or a branch opens the lines the report cites; `branch-missing` findings are linked from the row. At most 30 files are columns, and the rest is said.
- **Cross-repo.** A graph of repositories and edges (edges between the same two repositories are one line named by all their kinds; candidates are dashed), the same edges as a table with the pin and adoption of
  each branch, then the M13 detail lists (edges followed, packages, http, contract reads, candidates apart, not followed) and the backend calls.
- **Palette and keys.** Ctrl or Cmd and K: pages, actions (start an analysis, toggle the theme, show the keys), the reports (asked for when the palette opens), and, on a report, its tabs, areas and findings.
  `?` opens the list of keys. Every entry is plain text, made visible and cut, and every jump is a path built by `paths.ts`.
- **Two tabs beyond the brief's list.** "Tests" (mapped tests and "Suggested, not verified coverage") and "Run notes". The report schema has no model notes yet (they come with M14), so the last tab shows the
  report's own `notes` and appears only when there are some; it will take model notes when the schema carries them.

### Dependencies (exact pins; ages as of 2026-10-21; the cutoff for "two weeks" is 2026-10-07)

| Package | Version | Published | Where |
|---|---|---|---|
| @mantine/core | 9.7.1 | 2026-10-06 | runtime (new) |
| @mantine/hooks | 9.7.1 | 2026-10-06 | runtime (new) |
| @mantine/notifications | 9.7.1 | 2026-10-06 | runtime (new) |
| @mantine/spotlight | 9.7.1 | 2026-10-06 | runtime (new) |
| @tabler/icons-react | 3.49.0 | 2026-10-05 | runtime (new) |
| @xyflow/react | 12.12.0 | 2026-09-24 | runtime (new) |
| @dagrejs/dagre | 3.1.1 | 2026-08-08 | runtime (new) |
| prism-react-renderer | 2.4.1 | 2024-12-11 | runtime (new) |
| react, react-dom | 19.3.0 | 2026-09-09 | runtime (as before) |
| react-router-dom | 7.18.4 | 2026-09-15 | runtime (as before) |
| the nine development packages of M13 | unchanged | see M13 | dev |

`@mantine/dates` and `@mantine/charts` are not used (the bars are Mantine's `Progress`). The lock holds 190 entries (135 before). Every one was checked against `npm view <pkg> time --json` and none is younger than
2026-10-07 (the newest is `@mantine/core` 9.7.1, 15 days old). It was made with `npm install --before=2026-10-07`. The lock gained 54 package names: the eight new direct packages above and 46 that they need: `@floating-ui/*` (5), `@mantine/store`,
`@tabler/icons`, `@xyflow/system`, `@dagrejs/graphlib`, `@types/prismjs`, the `@types/d3-*` (6), the `d3-*` (9), `classcat`, `clsx`, `zustand`, `react-remove-scroll` and `react-remove-scroll-bar`, `react-style-singleton`, `get-nonce`,
`use-callback-ref`, `use-sidecar`, `react-transition-group` with `dom-helpers`, `react-number-format`, `type-fest`, `tagged-tag`, `tabbable`, `tslib`, `prop-types`, `object-assign`, `loose-envify`, `detect-node-es`,
`use-sync-external-store`. Nothing else was added and nothing needed the owner. A source test holds `package.json` to the eleven runtime names, to exact versions and to the lock. Mantine 9 is a new major (its peer is
React 19.2 or later): it builds, runs and passes everything here with React 19.3.0.

### Design tokens (`src/theme.ts`, `src/styles/*.css`)

- One accent, an indigo (`accent`; shade 7 in light and 4 in dark; `#5e6ad2` at the middle), a cool neutral `gray` for light and a purpose-made `dark` scale (page `#0d0d10`, card `#18181c`, border `#2d2d34`, text `#ececef`); `slate` for "sample".
  Radius `sm` (a card is `md`), type scale 12 to 18 px, headings weight 600, the system sans and system mono stacks (no font file, no request).
- Status colours, set once in `components/badges.tsx`: blocking red (filled), incomplete orange, must test accent, sample slate, candidate a dashed grey outline, decision needs review yellow, orphaned grey with a line through,
  risk a filled light rectangle with a warning mark (high red, medium orange, low grey), confidence an outlined pill with a target mark.
- The dark theme is its own palette (tuned greys, softened banners, a quiet tint for the chosen row, code colours redefined), not an inversion. Light is the default; the choice is kept in `localStorage` as before
  (one `colorSchemeManager` in `storage.ts`).
- Layout: sidebar 232 px (60 px as an icon rail), top bar 52 px, content up to 1320 px, tables with `table-layout: fixed` so the lists fit a 13 inch screen with no horizontal scroll (a narrow window scrolls the table inside its card, never the page).

### CSP approach

The runner's policy for the workspace was `style-src 'self'`. Mantine writes its theme variables and a few media rules into style elements when it mounts, and the scroll lock of a drawer or a dialog makes one more
(`react-style-singleton`). "Prefer static CSS" was followed as far as it goes: Mantine's, React Flow's and the workspace's own sheets are static files that Vite emits (no `@import`, no `url()`, no font; a source test
says so), the code colours and the graph styles are classes, and the page's own code sets no style attribute (the scan fails on `style={`, `<style` and `createElement("style")`). What cannot be static is those few
style elements, so the runner now gives the index page a nonce:

- `reachmap/runner/server.py`: `workspace_policy(nonce=None)` is `style-src 'self' 'nonce-<n>'` when given a nonce and unchanged otherwise. The packaged `index.html` holds `<meta name="reachmap-style-nonce" content="__REACHMAP_STYLE_NONCE__">`
  and a response that holds the placeholder gets a fresh `secrets.token_urlsafe(18)` (24 characters of the URL-safe alphabet) in the body and in the header. This is the index page and the client routes that answer with it;
  scripts, styles, the minimal page and the API are unchanged, and `Cache-Control: no-store` was already on every response. Never `unsafe-inline` or `unsafe-eval`, and no nonce for scripts.
  Tests (`tests/test_runner_reports.py::StyleNonce`, six): the same value in the page and the policy, never used twice, the policy equals the workspace policy plus the nonce and nothing looser, other files are neither rewritten nor given
  one, the nonce is in no API answer and no log line, the minimal page is as before. `tests/test_frontend_static.py` serves the real `dist/` and checks the same.
- `src/nonce.ts` reads the meta element once at start (and takes it out of the page); a placeholder, or a value that is not a nonce, is "no nonce". `main.tsx` gives the nonce to Mantine (`getStyleNonce`) and to the libraries through the
  global that bundlers call `__webpack_nonce__`. Only `nonce.ts` names that global and only `providers.tsx` names `getStyleNonce` (source test).
- Style attributes that React sets through the DOM are not subject to the policy.
- Checked in the browser pane against the real runner, with a `securitypolicyviolation` listener and the console read: the overview, the areas, the area drawer with its graph, the decide dialog and a decision recorded for two areas;
  the hostile report, its area drawer and the command palette (Enter navigates); the finding drawer (the style element of the scroll lock carries the nonce), closing it, the shortcut list and a tab change. No violation event and no console
  message in any of them. The first tab I used logged two React Router warnings ("No HydrateFallback") from before the route fallback was added; a fresh tab after the fix logged none. The shortcut keys sent as synthetic events on `window`
  (the pane's `type` action does not send `?`) make Mantine's own hotkey hook throw in its handler, which a real key press (its target is an element) does not; those console errors are not the page's.

### Bundle (`npm run build`, Vite's gzip sizes)

| File | raw | gzip |
|---|---|---|
| `index-*.js` (React, router, Mantine core, floating-ui, notifications, spotlight, the frame) | 570.0 kB | 177.5 kB |
| `index-*.css` (Mantine's whole sheet and the workspace's) | 268.0 kB | 39.8 kB |
| `words-*.js` (the words of the page, pieces shared by the pages; preloaded) | 36.2 kB | 12.8 kB |
| `time-*.js`, `IconX-*.js` (shared pieces) | 23.6 and 8.6 kB | 8.6 and 3.4 kB |
| `Reports-*.js` | 7.3 kB | 2.6 kB |
| `Jobs-*.js` | 5.6 kB | 2.1 kB |
| `ReportPage-*.js` (tabs, drawers, decisions) | 120.8 kB | 32.9 kB |
| `Evidence-*.js` (prism-react-renderer and its languages, for code) | 87.9 kB | 27.6 kB |
| `Graphs-*.js` (React Flow, d3, dagre; only a page with a graph loads it) | 230.6 kB | 75.0 kB |
| `Graphs-*.css` (React Flow's sheet) | 15.4 kB | 2.6 kB |

The list of reports loads about 245 kB gzipped (the entry, its sheet, `words`, `time`, `IconX`, `Reports`); a report adds about 33 kB (its page), 28 kB (code) and 75 kB (graphs). The M13 build was one script of 384 kB raw for everything.
Vite warns that `index` is over 500 kB: it is React DOM (627 kB of source), React Router (409 kB), Mantine core (368 kB) and floating-ui (188 kB); the Tabler icons are tree-shaken to a few kB. Mantine's stylesheet is imported whole
(`@mantine/core/styles.css`): importing component sheets one by one would save something like 15 kB gzipped and risks a missing sheet for a component a later change uses, so it was left; the page is served from the same machine.
The built script holds the library strings of the M13 list plus `mantine.dev` (a message of a colour warning) and `reactflow.dev` (the attribution link, which the page does not draw, and error texts); `src/dist.test.ts` and
`tests/test_frontend_static.py` hold the scripts to that list of six hosts. React Flow's attribution is hidden (`proOptions.hideAttribution`) so that no link leaves the page; the shortcut list says in plain text that the graphs
are drawn with React Flow (MIT licence). Whether to keep it hidden is a question for the owner.

### Tests and what was run

- `npx tsc --noEmit -p tsconfig.app.json`, `npm test` (253 tests in 14 files: frame, list and jobs 28, report page 39, decisions and triage 35, hostile report 8, hostile keys 18, source scan 13, built page 4 (skipped before a build), session 3,
  crash 1, client 14, view 39, models 34, text 7, wording 10) and `npm run build` pass (Node 25.1.0, npm 11.6.2). A clean `npm ci` of the committed lock in a copy of the tree, then the same three commands, passes (249 tests and 4 skipped before the build, 253 after).
  Kept in spirit: the token, the wording check over every page for every fixture (the walker skips `[data-c]` content and checks the rest, drawers and dialogs included), hostile text as text on every tab and in the graphs (no element, attribute, link or
  style comes from content), failed and timed-out runs with no count, own-property lookups (six hostile keys through every tab, a drawer, the list and the jobs), suggestions apart from mapped tests, the skip wording, and the source scan.
  New: bulk decide (one request per area, an answer per row, one refused), keyboard triage and the focus rules, drawer routes, tab URL sync (click, address, back button), the command palette, the graph models and the laid out graphs, the counts,
  the grid, the languages, time, the nonce, the pinned dependencies. Mutations tried and caught: a plain lookup in `own()` (7 tests fail) and a bulk decide that sends one request (3 fail).
- Python: `python3.12 tests/run_parallel.py` 2,500 tests OK (1 skipped: `test_packaging` needs setuptools, which 3.12 and 3.13 here do not have), 274 s with other work running; `REACHMAP_REQUIRE_PACKAGING=1 python3.11 tests/run_parallel.py` 2,505 OK with no skip,
  197 s; `python3.13` 2,500 OK (1 skipped), 219 s. M13 had 2,493 and 2,498; the 7 new tests are in `test_runner_reports`, and `test_frontend_static` was changed in place. A checkout that holds a bundled `reachmap/runner/static/` (the gitignored product of
  `python -m reachmap.tools.bundle_frontend`) fails two tests of `test_packaging` that copy the tree (one first run on 3.11 did); with that folder moved away they pass. That was already so in M13.
- Browser pane, against a real `reachmap serve` on a folder with the reports of s01, s06, s11, s13 (two revisions), s16 and s21 made by `reachmap analyse` on the fixture workspace, the M10 `complete`, `hostile`, `failed` and `timed_out` fixtures and a real run whose
  source holds the hostile strings (`tests/support_report.hostile_run`): the list (light and dark), every tab of several reports, area and finding drawers with graphs and coloured evidence, a bulk decision for two areas through the real runner (a skip whose reason
  holds `<img src=x onerror=alert(1)>`, shown afterwards as text on the rows and in the drawer), keyboard triage (j, Space, x) and the dialog, the palette (a report, an area, a finding; Enter navigates), the shortcut list, the hostile report and its drawer (text only,
  escapes written out, long labels cut), the failed and timed-out runs (no counts), the page without a token, and a 768 px window (no horizontal scroll of the page; a table scrolls inside its card). The key presses were real (`computer key`) except `?`; a few
  screenshots were staged through the page's own DOM (scroll position, a grid cell).
- Not run: CI (nothing was pushed), Windows, a real browser other than the pane's, Node 22 (only Node 25.1.0 is here; every `engines` field of the lock accepts 22.13), a screen reader or a contrast tool (the structure is tested: one h1, headings never skip, landmarks,
  labelled controls, `aria-sort`, `aria-pressed`, `aria-selected`; focus is visible), and an analysis started from the Jobs page in this milestone (the form is tested with a fake client; its behaviour was not changed).

### Screenshots (`docs/screenshots/`, PNG, 800 px wide, from a 1200 px window)

Light: `light-reports-list`, `light-overview`, `light-overview-reach-graph`, `light-areas-triage` (an area in focus, two decided), `light-area-drawer`, `light-findings`, `light-branches-grid` (a cell chosen, its lines below), `light-cross-repo`, `light-records`, `light-jobs`,
`light-failed-run`, `light-no-token`.
Dark: `dark-reports-list`, `dark-overview`, `dark-areas-triage` (two selected, the bar), `dark-area-drawer`, `dark-area-drawer-evidence`, `dark-finding-drawer`, `dark-findings`, `dark-branches-grid`, `dark-cross-repo`, `dark-decide-dialog-for-a-selection`,
`dark-command-palette`, `dark-shortcuts-help`, `dark-timed-out-run`, `dark-hostile-report-areas`, `dark-hostile-report-area-drawer`, `dark-tablet-areas` (768 px wide). 28 files, 3.5 MB (no PNG optimiser is installed here).

### Choices (small ones I made; say if you want any the other way)

- **Mantine's own light and dark switch is used** (the provider with our manager), so the page sets `data-mantine-color-scheme` and not `data-theme`. The toggle keeps its one fixed label ("Dark theme") and `aria-pressed`.
- **Native selects** for the filters and the forms (`NativeSelect`), not Mantine's combobox: they work in every test environment, with a keyboard and with a screen reader, and look right.
- **The header card.** Repository and change are in the title; the card shows the head, the commit before, the base, the pinned time, the run (relative, exact in the title), the tool and, for a pull request, its number, title, state and branches.
- **A skeleton, not a spinner**, for every wait; an empty state has a sentence and, where there is something to do, the button.
- **The decision filter** appears only when decisions can be recorded; "Not decided" is shown only when the store was read (the page never says a target has no decision when it could not read the decisions).
- **Long content** is cut in lists, headings and graph boxes (`Clip`, `Code n=`) with the whole text in the title; the drawers and the evidence blocks keep the M13 limit (2,000 characters, then a note).
- **The palette asks for the report list when it opens**, so a page makes no extra request until it is used. The frame asks `GET /api/repos` once (the name of the workspace in the sidebar).
- **The text of the docs.** `CLAUDE.md`, `CONTRIBUTING.md`, `docs/BOOTSTRAP.md` and `docs/CONTEXT.md` (13.4) said "three runtime dependencies and no UI kit"; they now name the owner's amendment. `docs/PROGRESS.md` is untouched.

### Known limits, not done

- The filters and the selection are state of the page, not of the address (a reload clears them; the tab and the drawer are in the address).
- The graphs have no minimap and no way to pan to a chosen step. A chain is not a graph any more (see Review round 1): a list of steps, with a link between two steps named only when the report names the kind.
- React Flow draws edges only after it has measured the boxes, which jsdom cannot do: edge labels and lines are tested on the laid out data (`layoutReach`, `layoutCross`) and seen in the browser, not in the component tests.
- The Jobs page was restyled and not extended (no log, no per-job detail: the runner holds none).
- A cell of the branch grid has a state of its own only when the row's words say it; a row that lists its files in another way shows "not itemised".
- The `index` chunk (React, the router, Mantine core) loads on every page and is over Vite's 500 kB warning (see the bundle table of Review round 1).
- The model notes of M14 (`model_notes` in a report) were not drawn by the first build of this frontend; they are now (see "Model notes (M14b)" below).
- No run on a CI runner, no Windows run.

## Review round 1 (the independent review of M13b) and the port of M15

What changed after the review, and what the first notes above no longer say. Where this section and the notes above differ, this section is right (the chain graph, the colours, the bundle table, the palette).

### The blocker: report text as a key of an object

dagre keeps its nodes and edges in plain objects keyed by the id it is given, and the layout gave it the names of repositories from the report: a repository called `__proto__` or `constructor` wrote into `Object.prototype` (a scratch test showed the number of own properties of `Object.prototype` going from 12 to 14). `layoutReach` and `layoutCross` now give dagre only numbers made by the page (`r0`, `n3`) and keep the names in a `Map` and in the data of the boxes, so no text of a report is an id of anything. `src/pollution.test.tsx` lays out graphs of repositories called `__proto__`, `constructor`, `toString`, `hasOwnProperty`, `valueOf`, `prototype` and `__defineGetter__` (and renders the overview and the cross-repository tab of such reports) and checks that `Object.prototype` is what it was.

### What changed in the design

- **A chain is a list of steps**, not a graph: one block per step (the line verbatim and coloured, with its commit), a header where the chain enters another repository, and between two steps a link that is named (`import`, `route`, `http`, `contract`, `package`) only when the report says what kind of step it is. Several chains are folded, the first open. The graphs that remain are the reach graph (overview) and the cross-repository graph; both are fitted to the frame with a zoom floor, as tall as their boxes need (in steps of a class, never a computed style), and a repository with nothing to show says so. Repositories that call each other are one line with an arrow at each end, not two lines on one another.
- **One meaning per colour** (the table is the head of `components/badges.tsx`): teal is done or carries the change, red is a blocking record or a high risk, pink a medium risk, orange a run that did not establish everything, yellow something that needs a person's look, accent the verdict test, slate sample. The text of every badge, button and banner reads at 4.5:1 or more in both themes: checked in the browser on 12 pages in each theme (about 1050 text nodes a theme, computed from the colours actually painted, with the backgrounds composed). Two things needed a rule: Mantine takes the text of a filled button by the light shade of the accent, so in the dark theme (a lighter accent) the text of a filled button and the tick of a check box are the dark of the page (`app.css`), and the text of a light badge is darker than Mantine's sixth shade for teal, yellow and orange in the light theme. A segment of a bar has no number inside it any more (white or black on a colour that is any of several would not read on all): the legend under the bar has the numbers.
- **Triage by keyboard**: `j` and `k` put the focus itself on the title of the next or the previous row (the browser scrolls, a screen reader reads), Space selects the row in focus, `t`, `s` and `x` open the dialog for it or for the selection, Enter opens it, Escape clears the selection. The keys are off in a field, in a dialog and while the drawer is open. After a decision the focus goes to the next area with none. Rows are memoised and found by a ref, so a key touches two rows (a test counts the rows whose DOM changed on a key: at most two in a list of 500). In the browser, on a list of 500 areas (the production build): the handler 0.2 ms (median), render and commit 5.4 ms (median, 11.6 ms the worst of 60), and the next frame at the screen's rate; jsdom is several times slower and not a measure.
- **A decision from the keyboard is quiet and fast**: the dialog opens with the focus in the reason for a skip and in the name otherwise, Ctrl or Cmd and Enter records, a dialog for one area or the selection closes by itself when every decision was recorded, and a toast (bottom left, with a named close button) says so once. A drawer says it in its own status line and does not toast over its form. Partial failures stay in the dialog, row by row.
- **The header is two lines and the banner is a strip**, so the tabs are on the first screen at 900 px; the tab list scrolls on a narrow page and a tab keeps the room for its count.
- **Lighter first load**: the stylesheet of each Mantine component is imported by itself (the whole sheet was 268 kB, 39.8 kB gzipped; now 142.7 kB, 22.0 kB), the palette and the toasts are chunks that load when first used (Ctrl or Cmd and K or the Search button mount the palette already open), and the route asks for the list of reports as soon as it is matched, before the code of the page has arrived.
- **Smaller things**: the Copy button is its own module; the Kind column is only in the table of exclusions (a table of one kind would say the same on every row); the list of reports shows counts only for a run that completed and never for one that did not; a failed render logs one fixed line, never the message; the lookups of the page by a name from a report use `Object.hasOwn`; the names of the close buttons are fixed words.

### The bundle, before and after the review (gzip, Vite's sizes)

| File | before | after |
| --- | --- | --- |
| `index-*.js` (React, router, Mantine core, the frame) | 177.5 kB (570.0 kB raw) | 154.5 kB (498.6 kB raw) |
| `index-*.css` | 39.8 kB | 22.0 kB |
| `ReportPage-*.js` (tabs, drawers, decisions, code colouring) | 32.9 kB and 27.6 kB (`Evidence`) | 60.2 kB (one chunk) |
| `Graphs-*.js`, `Graphs-*.css` | 75.0 kB, 2.6 kB | 72.2 kB, 2.5 kB |
| `PaletteView-*.js` (palette, loaded on first use) | in `index` | 4.8 kB |
| `toast-*.js` (notifications, loaded on first use) | in `index` | 3.5 kB |

The list of reports loads about 216 kB (the entry, its sheet, the two preloaded pieces, `time`, `Reports`, `Input`, `esm`), where it was 245 kB. Vite still warns that `index` is over 500 kB (React DOM, the router, Mantine core and floating-ui).

### M15: the report shape

`main` was merged and `frontend/fixtures` regenerated. The view model is a port of `reachmap/report/model.py` and `read.py` (the same names and the same rules; `view.test.ts` holds it to them):

- every record has a `scope` (`selection` or `suggestions`), `blocks_selection` and `blocks_suggestions`; a record written before they existed is read from its kind, as `read.py` does. `counts.blocking_records` is the selection only and `counts.blocking_suggestion_records` counts the rest (made from the records when a report has no such count). The banner above the tabs, the tab count, the "records first" order and the blocking records of the overview use the records that block the selection only. A record of a test source is a blocking record for the suggestions: it is listed in Records first, with the chip "suggestions only" and an orange badge (the red one is for the selection), under its own sentence ("The areas come from the code and are not affected."), and on the Tests tab; the overview counts it on a line of its own, and only when there is one.
- suggestions carry `area_id` and `evidence`: the Tests tab groups them per area (in the order of the areas), with the spec line of a case that reads code, and lists the cases tied to no area apart. Under them it says what became of each source (did not answer, read in part, not configured) and what the spec sources could not use (addresses that are not literals, visits that match no screen route). The area drawer has the cases of its area. `_suggested` is ported as it is (states `listed`, `unavailable`, `incomplete`, `unconfigured`, `read_none`, `not_read`; the words are those of `layout.py`), and "None suggested." is said only when every source was read completely and nothing was set aside; for an area the drawer says "not known" when a source was not read completely.
- `frontend/fixtures/make_reports.py` makes five more fixtures from the Python model (`Incompleteness`, `Suggestion`, `Evidence`): `sources` (every kind of record and three suggestions, one tied to no area), `sources_unavailable`, `sources_incomplete`, `sources_unconfigured`, `sources_none` (read, nothing suggested, three records of what a spec source set aside); `order.json` holds what the Python view model makes of each (the suggestions, their records, the source states, the counts and the scopes of the records) and every one of them is compared in `view.test.ts`.

### Tests, after the review and the port

`npx tsc --noEmit -p tsconfig.app.json` clean; `npx vitest run`: 17 files, 312 tests (258 before the review round); `npm run build` clean. New tests cover the pollution, the stepper, the keys of the triage (roving focus, Escape, off while a dialog or a drawer is open, Ctrl or Cmd and Enter, the focus in the reason for a skip, the focus moving on after a decision), the cost of a key (rows touched, in lists of 50 and 500), the console (no call holds the token; no text of a report), the palette on the hostile report, a section that fails to draw, the counts of a report without them, the kind of an edge, repositories that call each other, and the suggestions (the Tests tab in every state, the records, the drawer, the report written before `scope`, hostile text in a case). The Python checks were run again after the merge and the regenerated fixtures (see the report of the round).

### Screenshots

Every screenshot above was taken again with the same name (the build of the review round, both themes), and these were added: `dark-decision-recorded-toast`, `dark-tests-suggestions-per-area`, `dark-tests-source-records`, `dark-records-suggestions`, `dark-overview-test-sources`, `dark-area-drawer-suggestions`, `light-tests-suggestions-per-area`, `light-tests-source-records` and `light-tests-none-suggested-with-partial`.

## Model notes (M14b)

The runner serves `model_notes` in `GET /api/reports/<name>` (`notes.attach`, M14). The workspace draws them as follows; the Python rules are ported, not invented.

- **What is read.** `report/notes.ts` ports `_note`, `_verification` and `_model_notes` of `report/model.py`, and `noteSection` ports the Model notes section of `layout.py` (the label, the sentence that says what a note is, the
  records of an unreadable store, then the groups by state: current, "Notes on an earlier revision or an older claim", "Notes about something no longer in the latest report", and a group for a state this version does not know).
  `report/notewords.ts` holds the words (`notewords.py` and the layout sentences). `view.modelNotes` is null when the report has no note and no record, and for a run that did not complete. Nothing about a note is counted:
  a test builds each fixture with and without `model_notes` and compares the whole view but for that field.
- **Where.** A "Model notes" tab (`/reports/<name>/model-notes`, after Records, with the number of notes) only when `view.modelNotes` is not null; any other report sends that path to its first page. It is not the "Run notes" tab (the report's
  own `notes`), which stays. The section is headed "Model notes, recorded via MCP; not evidence." (the label of `notewords.py`).
- **Per note.** A card: kind and state badges (current slate, stale yellow with the edge of "needs a look", orphaned grey and struck through) and the id, then labelled lines: Text (a block of its own), About (the label from the latest report
  when there is one, else the id, else "the report as a whole"), State (the tool's sentence), Made on ("revision N at commit <short>": the revision a stale note was made on), Stamp, Recorded, and for a proposed file the repository, path
  and symbol with Verification (verified or not verified and the reason in the tool's words), Read at, Where and Detail. The stamp is its own labelled line outside the text: "recorded via MCP, client reported as <name>" with "the name is
  the client's own claim" and the client's version (or that it gave none). It is built from the client fields of the note and never read from its text; a note whose text says the same is shown as text, in the Text block, beside its own stamp.
- **Tab for an unreadable store.** When `model_notes` holds a blocking record (the store of notes could not be read) the tab has an orange warning mark instead of a count.
- **In the drawers.** An area or a finding with notes gets an outlined dashed badge ("2 model notes", like a candidate: not backed by evidence), a yellow "N stale" badge when some are stale, and a "Model notes about this area (or
  finding)" section above the decision form with the same cards and a link to the tab. Only current and stale notes attach to a target (by its id); an orphaned note is attached to nothing (its target is not in the report), a note about
  the report as a whole has no drawer, and a state this version does not know is not attached. The runner puts the revision of the report a note was attached to in `viewed` (the Python view model does not read it; the page does,
  as it reads `viewed` of a decision): in a drawer of an earlier report, a note the runner calls current (made on the latest revision) is shown as "made on revision N" with the line "made on revision N, not on revision V, the revision of this
  report", never as current. The tab keeps the words of the Python layout.
- **Content.** Every text of a note (text, client name and version, label, proposed file, detail) is content: `C`, `Code` or `Pre`, so made visible, cut at 2,000 characters and never markup; the words for a kind, state and reason are
  own-property lookups in fixed tables (a kind called `__proto__` is shown as content). The wording check covers the tool's own strings only (`notewords.ts` is checked on load, the page walker skips `[data-c]`).
- **Fixtures and parity.** `make_reports.py` builds `notes.json` from the `unordered` report in a temporary reports folder with `reachmap.notes` itself (a first run, its rerun with one claim changed, one area and one finding gone; notes of
  every kind and state, five proposed files with a verified and four unverified checks), `notes_r1.json` (the first report, viewed while its rerun exists), `notes_hostile.json` (markup, bidi and zero-width characters, fence-looking text, a text that looks like a stamp whose real client is another name than any in
  the text, a client named like a stamp, names such as `__proto__`, and three hand-written notes the store would never make, one with no client name and a text that claims one) and `notes_unreadable.json` (a damaged store: a blocking record, no note), and `note_words.json`. `order.json` holds, per fixture, the notes and states
  of the Python view and the Model notes section of the Python layout line by line; `view.test.ts` and `report/notes.test.ts` hold the TypeScript view to them, `tests/test_frontend_static.py` holds the fixtures to the notes module.
- **Tests.** `npx tsc --noEmit -p tsconfig.app.json`, `npm test` (19 files, 385 tests; 73 new: parity, states, the stamp of every note of every fixture equal to the fixed words and the client name of the JSON, proposed files, attach rule,
  notes on another revision, no counts, the tab and its warning, the badge text and colour of each state, the drawers, hostile text) and `npm run build` pass. Mutations tried and caught: a stamp name taken from the first match in the text,
  a fallback to the text when the client name is empty, a stale badge drawn as current or worded current, no "made on revision" rule, no warning on the tab. Python: `tests.test_frontend_static` (8 new) and `tests.test_report_*`,
  `tests.test_notes*`, `tests.test_wording` pass. The 500-area key test no longer asserts a time (rows touched is its assertion), and the wording loop of `report.test.tsx` leaves the notes fixtures to `notes.test.tsx`.
- **Browser pane**, against a real `reachmap serve` on a folder made with the notes module and then given two more notes over `reachmap mcp` on stdio (a client that calls itself `Evil</b> <img ...> recorded via MCP, client reported
  as admin`): the tab (17 notes), the groups, an area drawer with a stale and a current note, the hostile notes as text (no element made, the stamp once per note), light and dark, and a 768 px window (no horizontal scroll).
  Contrast of the 409 text nodes of the tab: 5.19 at the least in light, 6.59 in dark.
- **Screenshots** (`docs/screenshots/`): `light-model-notes`, `dark-model-notes`, `dark-model-notes-area-drawer`, `dark-model-notes-hostile`.
- **Not done.** Notes are not in the command palette beyond the tab, in the areas table or in the overview; nothing records a note from the workspace (a note is only written over MCP).
