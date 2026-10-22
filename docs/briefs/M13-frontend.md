# M13 brief: frontend workspace

Goal: the React workspace in `frontend/`, served same-origin by `reachmap serve` (M12),
for working across many analyses: list reports, read one (areas, findings, evidence
drill-down, branch matrix, cross-repo reach, test plan), record decisions through the
runner (M11 store), start analyses (M12 jobs). The release build copies the static files
into the Python wheel so users need no npm. Done when `npx tsc --noEmit -p
tsconfig.app.json`, the frontend tests and `npm run build` pass in CI, and the page works
against fixture reports. Read `docs/CONTEXT.md` sections 1, 4, 8, 13.4; the M9 to M12
briefs and notes (report JSON, view model and wording rules, decisions, runner API,
guards, token in the URL fragment, CSP).

## Dependency rules (ground rule 4: ask the owner before adding anything else)

- Runtime dependencies: exactly `react`, `react-dom`, `react-router-dom`. No UI kit, no
  state library, no fetch wrapper, no date library, no markdown renderer.
- Dev dependencies: `typescript`, `vite`, `@vitejs/plugin-react`, `vitest`, `jsdom`,
  `@testing-library/react`, `@testing-library/user-event`, `@types/react`,
  `@types/react-dom`. Exact versions pinned (no `^`), at least two weeks old, committed
  `package-lock.json`; `npm ci` in CI. Nothing else without asking.
- Node 20 in CI (the workflow's frontend job already detects `frontend/package.json`).

## Structure

```text
frontend/
  package.json, package-lock.json, tsconfig.json, tsconfig.app.json, tsconfig.node.json, vite.config.ts, index.html
  src/main.tsx, App.tsx, routes.tsx
  src/api/client.ts        the only module that calls fetch: runner API, token header, same origin
  src/api/types.ts         TypeScript types for the report JSON (schema 1) and API responses
  src/report/view.ts       view model ported from reachmap/report/model.py ordering rules (one place)
  src/report/wording.ts    banned wording check for every UI string (mirror of reachmap/wording.py)
  src/pages/Reports.tsx, Report.tsx, Jobs.tsx, NotFound.tsx
  src/components/...       Areas, Findings, Evidence, Chain, BranchMatrix, CrossRepo, Records, Decisions, ThemeToggle
  src/styles/*.css         plain CSS, system fonts, light theme default
  src/**/*.test.tsx
```

## Behaviour

- Routes: `/` reports list; `/reports/:name` one report with sections in the M10 order;
  `/reports/:name/areas/:id` and `/findings/:id` drill-down with every evidence line
  (repo, path, line, verbatim detail, short sha with the full sha copyable); `/jobs` queue
  and a form to start an analysis (repo from the config list the runner exposes; pr /
  compare / range; as-of).
- Token: read `#token=...` from the URL fragment once at start, keep it in memory (not
  storage), remove it from the address bar with `history.replaceState`; send as
  `X-Reachmap-Token` on every API call. Without a token, show a message telling the person
  to open the URL printed by `reachmap serve`.
- All report content rendered as React text (no `dangerouslySetInnerHTML` anywhere; a
  test greps for it and for `innerHTML`, `eval`, `new Function`, `href={` from data,
  `src={` from data). Odd characters made visible the same way as M10 (port
  `text.visible`).
- Decisions: per area and finding, verdict select, reason (required for skip and reject),
  author name (remembered in localStorage as a convenience, try/catch); POST to the
  runner; current / stale / orphaned shown as in M11; a skip shows "risk accepted, not
  tested (reason from <author>: ...)"; decisions never change priority or counts.
- Never claim safety or coverage: every UI string passes the TS wording check (unit test
  over all string literals rendered in tests plus a source scan of JSX text); suggestions
  labelled "Suggested, not verified coverage" and separate from mapped tests.
- Failed and timed-out reports show header and records only, no counts.
- Theme: light default, toggle stored in localStorage (try/catch), aria-pressed with a
  fixed label; system fonts; no external URL of any kind in the built output (test over
  `dist/`).
- Accessibility: headings in order, landmarks, tables with headers, focusable controls,
  visible focus.
- Loading and errors: API errors shown as text (status and a fixed message), never the
  server body as markup.

## Packaging and serving

- `npm run build` outputs `frontend/dist/`. A Python build step (`python -m
  reachmap.tools.bundle_frontend` or a setuptools hook in `pyproject.toml` that copies
  `frontend/dist/` into `reachmap/runner/static/` when present) puts it into the wheel;
  the sdist and wheel build without npm when `dist/` is absent (the runner then serves the
  minimal page). Document the release order in CONTRIBUTING.md.
- `reachmap serve` serves `reachmap/runner/static/` (M12 already does when present) with
  the M12 CSP (`connect-src 'self'`), hashed asset names, `index.html` for client routes.

## Tests

- Vitest + Testing Library over components and pages with fixture report JSON copied
  from the M10 fixtures (`complete`, `hostile`, `failed`, `timed_out`) and two real
  scenario reports (s11, s13) generated by a Python script committed under
  `frontend/fixtures/` (regenerate script documented): hostile strings render as text;
  section order; failed shows no counts; decisions flow with a mocked client; token
  handling; wording check; no forbidden APIs (source scan).
- A Python test (`tests/test_frontend_static.py`) builds nothing but checks, when
  `frontend/dist/` exists, that the files contain no external URL and that the runner
  serves them with the CSP; skipped otherwise.
- CI: frontend job runs `npm ci`, `npx tsc --noEmit -p tsconfig.app.json`, `npm test`,
  `npm run build`.

## Implementation notes

Where the code differs from, fills in, or goes beyond the text above. Each point is covered by a test unless it says otherwise.

### Files

Beyond the list above (`frontend/`): `src/report/{text,read,check,words}.ts` (the port of `reachmap/report/text.py`, the tolerant readers of `read.py`, the guard a body must pass before a view is made of it, and every sentence the page says itself), `src/pages/{Drill,useReport}.ts(x)` (the pages of an area and of a finding, and the one hook the three report pages share), `src/{context,hooks,paths,storage}.ts(x)`, `src/components/{Content,Fields,Header,Backend,Suggested,Author,Boxes}.tsx`, `src/test/{setup,fixtures,fakeClient,render}.ts(x)`, `src/*.test.ts(x)`, `frontend/fixtures/` (`make_reports.py`, the four M10 fixtures, `s11.json`, `s13.json`, `order.json`) and `frontend/.npmrc` (`save-exact`, no audit or fund noise).
Python: `reachmap/tools/{__init__,bundle_frontend}.py`, `setup.py`, `MANIFEST.in`, `tests/test_{bundle_frontend,frontend_static,packaging}.py`. Changed: `reachmap/runner/server.py` (one rule, below), `tests/support_runner.py` (`Live` points `static_dir` at a folder that does not exist unless a test gives one, so a checkout that holds a bundled build does not change what the runner tests mean), `tests/test_runner_reports.py` (five tests), `.gitignore` (`reachmap/runner/static/` is a build product), `.github/workflows/ci.yml`, `CONTRIBUTING.md`. No `docs/PROGRESS.md`, no `expected.json`, no fixture of M1 to M12 changed.

### Layers

`api/client.ts` (the only `fetch`) gives `report/check.ts` a body, which gives `report/view.ts` a report, which gives the pages and components a view. `view.ts` is the port of `model.py` and `author.py`: sections, sorting (by what is shown, as the Python sorts `Content`), grouping (`KIND_ORDER`), the matrix links, the carry states, the author view (`shlex.quote` included). `frontend/fixtures/order.json` is what the Python view model makes of each of the six fixtures, and `view.test.ts` holds the TypeScript view to it section by section; `tests/test_frontend_static.py` fails when the committed scenario reports or `order.json` no longer match what the analyser and the Python view model make today. So the ordering rules live in two places and a drift in either fails a test.
Everything copied out of a report is shown by `components/Content` (`C`, `Code`, `Pre`, `Quoted`): React text only, made visible and cut by `text.show`, marked `data-c`. Everything the page says itself is in `report/words.ts`, whose tables are wording-checked when the module is loaded (a banned phrase there stops the app and every test). The wording test removes everything marked `data-c` from a rendered page and checks what is left, for every fixture on every page (the report, an area, a finding, the jobs page).

### Choices (small ones I made; say if you want any of them the other way)

- **The client routes are served by the runner as the index page.** `reachmap serve` answered `/reports/x` with 404, so a reload or a bookmark of a report page failed. `RunnerServer.static_file` now answers a path whose first segment is `reports` or `jobs` (`server.CLIENT_ROUTES`; a Python test reads `frontend/src/routes.tsx` and holds the two to each other) with `index.html` when no file of that name exists and the packaged workspace is what is served; the minimal page has no client route, an unknown file under `/assets/` is still 404, `/api/` is never a route of the page, and every traversal test of M12 still passes (the other 404 and 400 cases are unchanged). This is the one change to runner code.
- **A reload needs the printed address again** (the token is in memory only, as in the minimal page): the page then says how to get one. Opening a route of the workspace without the token shows that message and calls nothing (tested, and seen in the browser pane).
- **The reports list tolerates what the runner can say.** For a report with no review data the list's `key.change` is the spec object (`{"compare": "..."}`), `revision` is null and any field can be null; the first version of the page broke on this against the real runner (not against fixtures) and the tests now include these shapes. `Content` also accepts a value that is not a string (a number or an object is shown as its text), so nothing a body can hold stops a render.
- **A decision form is in the list and on the drill-down page.** In the list it is folded (`<details>`), on the area and finding pages it is open. It is shown only for a report that completed and has a place among the revisions of its change (`review.revision` is a number), and only for an area or a finding that has an id: a report from before M11 has none, and the page shows no control, as the single-file page does. After a decision is recorded the page reads the report again (the runner computes `review.decisions`), so what is shown is the runner's answer and not a local guess.
- **Checks before the request, and nothing else:** an author is required (at most 100 characters), a reason is required for skip and reject (at most 2,000); the control-character rules of `review/formats.py` stay the runner's, and a refusal is shown as a fixed sentence for the status, the status itself and the runner's own code and field (`invalid_decision: reason`) only when they are plain lower-case words. The body of an answer is never shown.
- **The name of the person deciding** is remembered in `localStorage` (try/catch on every access, one store shared by every form of the page); the theme the same way. The token is never stored (a source scan holds `storage.ts` to those two keys).
- **No link is made from data.** `paths.ts` builds the three path shapes from a name and an id that passed a pattern (`[A-Za-z0-9][A-Za-z0-9._-]*`, `[af]-` and 16 hex), the only literal `href` in the sources is the skip link `#main`, and the source scan fails on `href={`, `src={`, `to={` with a template, `innerHTML`, `dangerouslySetInnerHTML`, `eval`, `new Function`, `fetch` outside `client.ts`, a style attribute, and an address of another host.
- **Sections follow M10:** header and counts, records first when one blocks selection, areas (must test, sample, candidates labelled as such), findings by kind with the exclusions under "Not reached (with evidence)", decisions (only when there is something to say), records, branch matrix, cross-repository reach, backend calls, mapped tests, "Suggested, not verified coverage", for the author. A failed or timed-out run shows the header and the records and nothing else (no counts, no table, no form).
- **Drill-down:** `/reports/:name/areas/:id` and `/findings/:id` use the ids of the report (`a-...`, `f-...`), show every chain step and every evidence line (repository, `path:line`, the line verbatim, the short commit with the whole one in the title and a "Copy commit id" button, `navigator.clipboard` inside try/catch with a fixed message when it is not there).
- **The jobs page polls every 3 s** while open (the runner keeps its jobs in memory); the reports page loads once. The job form sends `{repo, pr | compare | range, as_of?}` with the repository chosen from `/api/repos`; what the runner refuses is shown as its fixed code and field.
- **Styles are plain CSS in two files**, system font stacks, light by default, dark by variables under `data-theme`, no `style` attribute and no `url()` (the runner's policy has `style-src 'self'`), `:focus-visible` outlines, tables that are wider than the screen scroll by themselves. `index.html` carries `<link rel="icon" href="data:,">` as the M10 page does.
- **Versions chosen for Node 20, CI now on Node 22 (see Review fixes).** The brief says Node 20 in CI. `vitest@5` and `jsdom@30` need Node 22 or newer, so the set is `vitest@4.1.11` and `jsdom@29.1.1` (both accept Node 20.19). `typescript@6.0.3`, not 7 (the native port): the JavaScript compiler is what every tool here is tested with. `@types/node` is not a dependency, so the tests read sources, fixtures and the build with `import.meta.glob(..., { query: "?raw" })` and not `node:fs`. `@testing-library/jest-dom` is not used (plain assertions).
- **Tests install an in-memory `Storage`** (`src/test/setup.ts`): Node 25 has a `localStorage` of its own that shadows jsdom's and fails without a file, and the tests must not depend on which Node runs them. The report fixtures are read as text (`?raw`) and parsed, because the bundler's JSON loader refuses the lone surrogate (`\ud800`) of `hostile.json`.
- **Packaging.** `reachmap/tools/bundle_frontend.py` copies `frontend/dist/` into a folder called `static` (refuses any other name, a link, a hidden file, a kind of file the runner does not serve, a source map; replaces an earlier copy only with a whole one; does nothing and says so when there is no `dist/index.html`). `setup.py` runs it from `build_py` into the build folder (so the source tree is untouched), `MANIFEST.in` has `graft frontend/dist` so that the sdist carries a build when there is one and a wheel built from the sdist has it too, and `python3 -m reachmap.tools.bundle_frontend` puts the build into a checkout for `reachmap serve` (the folder is gitignored). `pyproject.toml` is unchanged (its package-data already named `static/*`).
- **CI.** The frontend job no longer detects `frontend/package.json` (it exists now): Node 20, npm cache keyed by the lock file, `npm ci`, `npx tsc --noEmit -p tsconfig.app.json`, `npm test`, `npm run build`; then it runs the built-output test of vitest and, with Python 3.12, `tests.test_frontend_static` and `tests.test_bundle_frontend` against the build it just made. Not run on a runner (nothing was pushed).

### Dependencies (exact, no `^` or `~`; ages as of 2026-10-21)

| Package | Version | Published | Where |
|---|---|---|---|
| react | 19.3.0 | 2026-09-09 | runtime |
| react-dom | 19.3.0 | 2026-09-09 | runtime |
| react-router-dom | 7.18.4 | 2026-09-15 | runtime |
| typescript | 6.0.3 | 2026-04-16 | dev |
| vite | 8.3.2 | 2026-10-01 | dev |
| @vitejs/plugin-react | 6.1.1 | 2026-08-28 | dev |
| vitest | 4.1.11 | 2026-08-18 | dev |
| jsdom | 29.1.1 | 2026-04-30 | dev |
| @testing-library/react | 16.3.3 | 2026-08-27 | dev |
| @testing-library/user-event | 14.6.7 | 2026-09-02 | dev |
| @types/react | 19.3.0 | 2026-09-09 | dev |
| @types/react-dom | 19.3.0 | 2026-09-09 | dev |

The newest is 20 days old (vite). The lock file holds 135 entries: the 12 above and what they need (`react-router` 7.18.4, `@testing-library/dom` 10.4.2, which `@testing-library/react` names as a peer dependency and npm 7 and later installs with it, `rolldown`, `lightningcss`, `postcss` and their platform packages). It was made with `npm install --before=2026-10-07`, and a check of every entry against the registry's publish times found none younger than that date (the first lock, made without `--before`, had 21 younger ones: `rolldown` 1.2.14 and its 15 platform binaries, `@oxc-project/types`, `postcss`, `tldts`, `tldts-core` and `undici`). One entry has an install script: `fsevents` (macOS file watching, optional, part of vite's tree). Nothing was added beyond the twelve; no other package was needed.

### The built output

One page, one script, one style sheet with hashed names (`assets/index-<hash>.js`, `assets/style-<hash>.css`, about 384 kB and 4 kB), no inline script or style, no source map, no font, no `url()`. The script holds some address strings that are never requested and that no build option removes: the XML namespaces of SVG, MathML and XLink (`www.w3.org`), the address in the text of a minified React production error (`react.dev`), one in a message of the router (`reactrouter.com`) and the base the router parses a path against (`http://localhost`). The tests (`src/dist.test.ts` and `tests/test_frontend_static.py`) therefore hold the script to that list of four hosts and fail on any other, and hold the page and the style sheet to no address at all. "No external URL of any kind in the built output" is true of what is loaded or called, and, for the script, of everything but those four library strings.

### What I ran

- `npm ci`, `npx tsc --noEmit -p tsconfig.app.json`, `npm test`, `npm run build` (Node 25.1.0, npm 11.6.2): all pass. 125 frontend tests in 8 files (121 before a build exists, because the 4 tests of the built page are skipped without `dist/`): view model 34 (the six fixtures against `order.json`, 4 checks each, and 10 others), pages 34, decisions 14, client and token 13, source scan 11, wording 8, text 7, built page 4. A clean `npm ci` from the committed lock file followed by the same commands passes.
- Python: `python3.12 tests/run_parallel.py` 2,495 tests, OK (4 skipped: `test_packaging` needs setuptools, which 3.12 and 3.13 here do not have), 114 s; 3.11 2,495 OK with no skip (setuptools 82 is there), 125 s; 3.13 2,495 OK, 4 skipped, 136 s (4 workers each, run one after the other; the M12 figures were 118, 170 and 167 s). 28 new tests: `test_bundle_frontend` 10, `test_frontend_static` 9 (5 of them run only when `frontend/dist/` exists, which it did), `test_packaging` 4, `test_runner_reports` 5.
- Wheels: `python -m build` in a copy of the tree with a build (the wheel has `reachmap/runner/static/index.html` and the two assets, the sdist has `frontend/dist/`) and without (both build, the wheel has no `static/`, the sdist no `frontend/`); the wheel installed in a clean virtual environment and `reachmap serve` there answered `/` with the page and the policy, `/reports/x` with the page, and a missing asset with 404.
- Browser pane, against the real runner (`reachmap serve` on the fixture configuration, six reports in the folder, a real job): the list, s13 (sections in order, counts, drill-down by clicking an area, a real decision recorded through the page: skip, a reason holding `<img src=x onerror=alert(1)> it is safe`, shown as quoted text under "Decision:", then in the Decisions section of the report), the hostile report (no `img`, `svg`, `iframe`, `style` or `object` element, no `on*` attribute, one script, odd characters written as escapes, the closing script tag as text), a timed-out report (header and records only), no token (the message, no call), a job started through the form on acme/api that ran to completion and linked its report, the dark theme (`aria-pressed`, stored), no console message at any point, the address cleared of the fragment, nothing in `localStorage` but the name and the theme, and every network request to the same origin (`/`, `/assets/...`, `/api/...`; the pane also asked for `/favicon.ico`, a 404 of the runner, though the page names a `data:` icon).

### Not done, or not covered

- Not run on Node 22 (the CI version after the review fixes; Node 20 before that): only Node 25.1.0 is on this machine (the Homebrew `node@24` here is the same binary). Every engine field of the 87 lock entries that have one accepts 22.13 and later (checked with `semver`), which is read from the lock, not tested. Not run on a CI runner.
- The stale and orphaned decision states are tested on the fixtures (decisions placed in the report by the tests) and by the Python side's M11 tests, not by a real rerun through the page.
- A real cancel of a running job was not tried in the browser (a queued and a running cancel are only covered by a mocked client).
- `form_input` of the browser tool sets a value without the events React listens to; the form was driven by typing instead. Nothing in the page depends on it.
- No end-to-end test starts the Vite dev server (`npm run dev` has no proxy to the runner; the working loop is `npm run build` and `reachmap serve`).
- The page does not search, sort or filter, does not poll the reports list, and shows times as the runner writes them (no date library).
- The accessibility checks are structural (headings never skip a level, one h1, landmarks, table headers and captions, labelled controls, `role="alert"` and `role="status"`) plus visible focus in the style sheet; no screen reader or contrast tool was run.

### Review fixes (an independent review found no blocker; these are its findings, each with a test)

1. **Suggestions and mapped tests (rule 4.3).** The page test now holds the Mapped tests section to its own list (it never holds a suggestion's title, one item for one mapped test) and the Suggested section to its own, and `build(report).mapped.length` equals the report's `mapped_tests`. Mutations tried and now failing: suggestions merged into `mapped` in the view model (M8a), suggestions drawn in the mapped section (M8b); both passed before.
2. **Plain-object lookups.** Every table of words is read through `own(table, key)` (`Object.hasOwn`), so a kind, state, adoption, basis, verdict, status or priority of `__proto__`, `constructor`, `toString`, `hasOwnProperty`, `valueOf` or `prototype` finds nothing instead of a member every object has (before, such a kind blanked the page). `hostilekeys.test.tsx` renders the report, an s13 with those words as priorities and identities, a decision with such a verdict and the lists of reports and jobs with them (six keys, three tests each; they fail with a plain lookup, tried). The routes have an `errorElement` (`pages/Crash.tsx`, fixed words, inside the frame, never the error), tested with a page that throws.
3. **A token typed into an open page.** `src/session.ts` takes the token at start and again on every `hashchange`: it is taken out of the address bar and the history at once and a new client replaces the old one in memory (the pages are loaded again with it, no reload of the document); a fragment with no token is ignored. `main.tsx` calls `startSession(window)` before anything renders. Tests: the message shows, the address is typed, the reports appear, the fragment is gone, nothing is stored; a second token replaces the first and the next call carries it; a fragment without a token changes nothing.
4. **The wording test no longer glues text.** `ownWords` walks the text nodes with a `TreeWalker`, skips every node inside `[data-c]` and joins the rest with a space (and each node is checked on its own as well). A banned word made up at run time in the chain summary (`"Sa" + "fe"`, M5g), a spaced one (M5h) and one in the header's files summary (M5i) now fail (11 to 16 tests each); they passed before because `textContent` ran the word into the text before it.
5. **Area order.** `make_reports.py` writes `unordered.json` (s13 with seven more areas, another repository, a sample pair and a candidate pair, each pair listed the wrong way round) and `order.json` has what Python's `model.build` makes of it; `view.test.ts` checks the page's order against it and that it differs from the report's, and a Python test checks the file is made from s13 and is out of order. Not sorting the areas (M14) now fails (it passed before).
6. **Packaging leak.** `setup.py` removes `build_lib/reachmap/runner/static` before it copies, and copies only from `frontend/dist/`; `static/*` is out of `pyproject.toml`'s package-data, so the hook is the only source (a `static/` left in a checkout by `bundle_frontend` is in neither the wheel nor the sdist). `test_packaging` no longer ignores `static` when it copies the project and has the stale-file case (no build: no `static/` in the wheel; with a build: the build's `index.html` and none of the stale files).
7. **Test gaps.** The source scan confines `writeStored(` to `storage.ts` and `ThemeToggle.tsx` and `rememberAuthor(` to `storage.ts` and `Decisions.tsx` (a token written from `main.tsx`, tried, fails). A refused job or decision is asked for exactly once (a retry in the client, M17, fails). An area matched by name is a candidate whatever priority the report gives it, on the list and on its own page (M24 fails).
8. **Wording parity.** The TypeScript check uses Python's whitespace class (U+001C to U+001F, U+0085, U+00A0, U+1680, U+2000 to U+200A, U+2028, U+2029, U+202F, U+205F, U+3000; not U+FEFF), tests word boundaries by hand next to the match (the `i` flag of the pattern made U+0345 look like a letter) and folds U+0130 and U+0131 to `i` as Python's case-insensitive match does. I compared Python and TypeScript for every code point next to `safe` and between the words of `no impact`, and for every character that case-folds or decomposes to a letter of a banned phrase: the only difference left is a code point that Python's Unicode tables (15.0) do not have yet and the browser's do (a letter added later counts as a word character in TypeScript only); it is documented in `wording.ts`. `frontend/fixtures/wording_cases.json` (82 cases) is checked by `tests/test_wording.py` and `wording.test.ts`.
9. **Author length** is counted in characters (`Array.from`), like the reason and the runner (a name of 100 emoji is accepted); the repository names of the job form go through `visible()`.
10. **CI.** The frontend job is on Node 22 (Node 20 reached end of life on 2026-04-30; this is a change from the brief's "Node 20"): every engine field of the 87 lock entries that have one accepts 22.13 and later, jsdom 29.1.1 being the one that needs 22.13, and `package.json` says `>=22.13`; the dependency versions are unchanged. Both Python jobs (and the frontend job) install `setuptools` and `build` (development tools only, never imported), and set `REACHMAP_REQUIRE_PACKAGING=1`, with which `test_packaging` fails and does not skip when setuptools is missing. The frontend job also builds the wheel with the fresh `frontend/dist/` (`python -m build --wheel`) and checks its listing for `reachmap/runner/static/index.html` and an asset script and style (the same commands, run here in a copy of the tree, pass).

After the review fixes: `npm ci`, `npx tsc --noEmit -p tsconfig.app.json`, `npm test` (158 tests in 11 files; 154 before a build exists) and `npm run build` pass (Node 25.1.0). `python3.12 tests/run_parallel.py` 2,493 tests OK (the packaging class is one skip: no setuptools in this 3.12), 112 s; 3.11 with `REACHMAP_REQUIRE_PACKAGING=1` 2,498 OK with no skip, 144 s; 3.13 2,493 OK (one skip), 144 s.
