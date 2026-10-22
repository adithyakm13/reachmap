# M15 brief: test sources

Goal: suggested test cases from test-management systems and from local end-to-end specs,
always labelled "suggested, not verified coverage", kept apart from mapped tests, never
counted as coverage, never written back. Done when suggestions are never counted as
coverage, tested. Read `docs/CONTEXT.md` sections 1 (item 6), 3 (Suggestion vs mapped
tests, required vs optional sources), 4 (rules 3 and 10), 7.2 (`[[test_sources]]`), 7.3
(TestSource), 13.3 (Playwright/Cypress, TestRail); M2 (Suggestion, MappedTest,
coverage_gaps), M3 (test_sources config, token_env, unset_vars, configured), M4 (host
transport injection, recorded responses, redaction), M9 (pipeline, records), M10/M13
(rendering) notes.

Python 3.11+ stdlib only (urllib for HTTP, injected transport, recorded responses in
tests; no network in default tests).

## Files

```text
reachmap/testsources/__init__.py   TestSource protocol, registry, run_all(workspace, analysis), returning the suggestions and the records
reachmap/testsources/qase.py       Qase (read only)
reachmap/testsources/testrail.py   TestRail (read only)
reachmap/testsources/specs.py      Playwright and Cypress specs in a configured repo: spec file to routes visited
tests/fixtures/testsources/        recorded Qase and TestRail responses (synthetic), spec files in a new fixture repo or the web repo (without changing existing shas: add a separate small repo)
tests/test_testsources_*.py
```

## Rules

- Required vs optional: a test source not configured (`configured=False`, unset token
  variable) is `source_unconfigured` (non-blocking). A configured source that fails to
  answer (auth, 5xx, rate limit, timeout, malformed JSON, a page that fails mid-pagination)
  is `source_unavailable` (blocking for suggestions only: the report says suggestions
  could not be established; it does not block area selection). Never "no suggestions".
- Read only: only GET requests; a test asserts every request method in every recorded
  transport is GET and no write endpoint is ever built (rule 10). Draft cases for a person
  to add may be shown as text (out of scope here beyond a stub that renders nothing).
- Pagination complete: Qase and TestRail lists are fully paginated; a cut (page cap) is a
  record and the suggestion list is marked incomplete (the truncated test-case list trap of context section 5).
- Matching suggestions to areas:
  - Qase/TestRail: by configured `[[test_sources.areas]]` (name, route_prefixes, modules,
    suites with descendants); a case is a suggestion for an area when the area matches the
    configured area AND the case shares at least two distinctive words with the area or
    finding. Words are lowercased tokens of length 4 or more from the case title and from
    the area's label, module, route segments and changed symbol names, minus a stop list,
    minus any word that appears in more than 10 percent of the fetched cases (a
    document-frequency cap: such a word is not distinctive). Record the matched words on
    the Suggestion.
  - Specs: a Playwright (`page.goto("...")`, `await page.goto(\`...\`)`) or Cypress
    (`cy.visit("...")`) call with a literal or template URL path is matched to screens by
    the M7 route table (a spec parameter matches a route parameter); a spec visiting a
    route is a suggestion for that screen with the spec line as evidence; non-literal URLs
    are recorded as partial (non-blocking) and not matched.
- Suggestions are `Suggestion` objects only; nothing creates `MappedTest` from a
  suggestion; `coverage_gaps` never reads suggestions (M2 already tests the counter; add an
  end-to-end test that a run with every area suggested still shows every gap).
- Tokens from `token_env` only, sent in headers, redacted everywhere (M4 redact), never in
  reports, records, logs or caches.

## Pipeline and rendering

- Pipeline step after findings: run the configured test sources; add suggestions and
  records to the Analysis; `inputs.test_source_read` true per source read.
- Rendering (M10): "Suggested, not verified coverage" section per area with source, case
  id, title (content), matched words; "None suggested." only when every source was read
  completely.

## Tests

- Qase: recorded responses for projects, suites tree (descendants), cases pages (2 pages),
  401, 429, 500, malformed JSON, page failing mid-way.
- TestRail: cases with pagination (`_links.next`), sections tree, auth failure.
- Specs: Playwright and Cypress fixture specs including template literals, params,
  non-literal URLs, comments containing goto (ignored).
- Distinctive words: two-word minimum, frequency cap, stop list, generic words dropped.
- Suggestions never counted as coverage end to end; read-only assertion; no token leak.

## Implementation notes

Where the code differs from, fills in, or goes beyond the text above. Each point is covered by a test unless it says otherwise. The
second half of this section ("Review fixes") records what an independent review changed; where the two disagree, the later text is the code.
The sources are a Qase reader, a TestRail reader and one spec reader for Playwright and Cypress; every one of them yields `Suggestion`
objects and records, nothing in `reachmap/testsources/` creates, imports or names a `MappedTest` (a test greps for it).

Files added beyond the list: `reachmap/testsources/{base,client,match,words}.py` (the shared types, the read-only web client, the tie
between a case and an area, the distinctive words), `reachmap/config_sources.py` (the option validators of the four kinds),
`tests/support_testsources.py` (the fake transport, the config tables, the spec repository builder), `tests/fixtures/testsources/`
(with a README) and `tests/test_testsources_{words,model,qase,testrail,match,specs,specs_git,run,report,e2e}.py`. Files changed:
`reachmap/model.py` (`Suggestion`), `incomplete.py` (two kinds, `SUGGESTION_ONLY_KINDS`), `config.py`, `hosts/__init__.py` (one alias),
`pipeline.py`, `cli.py`, `report/{model,layout,summary}.py`, `mcp/tools.py`, and two existing tests that pinned what M15 replaces:
`tests/test_config.py` (the example of an unknown test source kind was `testrail`, now it is `xray`) and `tests/test_pipeline.py` (M9
pinned the note "test sources are configured and were not read"; the note is now about context sources only and the test sources
are read). No M1 repository, sha or `expected.json` changed; the spec fixtures are a separate small repository
(`tests/fixtures/testsources/e2e`).

### Configuration

- Four kinds in `[[test_sources]]`: `qase`, `testrail`, `playwright`, `cypress` (`reachmap/config_sources.py`). Hosted tables take
  `token_env` (the variable's name), `base_url`, `page_size`, `max_pages` and `areas`; `qase` takes `project`; `testrail` takes
  `project_id`, `suite_id` and a required `base_url`. A spec table takes `repo` (required), `screens_repo` (the repository whose route
  table the visited paths are matched against, `repo` by default) and `globs`, and it takes no `areas`. Types and ranges are checked
  without quoting a value; a `base_url` with a user, a password, a query or a fragment is refused. A hosted table keeps keys it does
  not know (M3 pinned that for every test source table); a spec table is closed. A spec source naming a repository that is not under
  `[repos]` is a config problem (`config` checks `repo` and `screens_repo`).
- A key that is missing (`project`, `token_env`, `base_url`) is not a config problem: the source is not configured and the record names
  the key. An area with neither `route_prefixes` nor `modules` is allowed (M3 pinned an area with only a name) and matches no area of
  a run; the run says so in a note.
- `suites` in an area means Qase suite ids for `qase` and TestRail section ids for `testrail`, descendants included. A case with no
  suite is in scope only when the configured area names no suites.
- TestRail's credential is one variable holding `login:key`, sent as a Basic `Authorization` header (the same convention the host layer
  uses for Bitbucket); a value without a colon is "not configured", and the message does not show it.

### The four states of a source

- Not configured (`TestSource.configured` is False, an unset or empty token variable, a missing key, no areas, a spec source with no
  repository): `source_unconfigured`, non-blocking, naming the variable or key; nothing is requested.
- Configured and failing to answer (401, 403, 404 of the project, 429, 5xx, a redirect, a timeout, a transport that could not connect,
  text that is not JSON, an answer of the wrong shape, `status: false`, a page that fails or comes back empty before the total is
  reached, a next link that loops or leaves the host, a primary branch or a spec file that cannot be read): `source_unavailable`,
  blocking. No half list is returned: the first pages of a list that fails on its last page are dropped.
- A list cut by the page cap (`max_pages`: Qase 200 pages of 100, TestRail 80 of 250 by default): the suggestions found in what was read
  are listed and a blocking `suggestions_incomplete` (new kind) says how much of what there is was read (`cases: read 15 of 30`). A
  spec source makes the same record when its route table is not whole, when a route has a path that could not be evaluated, or when a
  spec file did not lex cleanly.
- Read completely: suggestions, no record. "None suggested." is said only then, for every configured source.
- Both blocking kinds are `SUGGESTION_ONLY_KINDS`. The model's `source_unavailable` stays always-blocking (M2 pinned it); what the brief
  asks, "blocking for suggestions only", is done where the records are read: the pipeline runs the sources after the findings, so the
  propagation's `complete` does not include them; the summary does not say "none were established" for a source record; the report
  does not put the records section before the areas for one (`records_first`) and says "The areas come from the code and are not
  affected."; the counts still count them under `blocking_records` (they are blocking records). A mixed run says both sentences.
- Cut and failure details pass `redact.for_record` (scrubbed, one line, banned wording defused); a title passes `clean_title`
  (one line, control and bidirectional characters removed, a credential scrubbed, at most 300 characters).

### Reading (read only)

- One client (`testsources/client.py`) subclasses the host layer's `HostBase`, so it has what M4 tested: one injected transport, no
  redirect followed, rate limit told from 403 and 5xx, the credential registered with `redact` when it is read and never sent over a
  connection that is not https, and the guard that stops the default transport in the test suite. `Client.get` is the only method that
  sends and it names the verb itself; the fake transport of the tests fails the test on any other method, and a test reads the module
  text for write verbs and write endpoints. TestRail's `endpoint()` builds `get_project`, `get_sections` and `get_cases` and raises for
  anything else.
- Qase: `project/<code>` (must exist), `suite/<code>` and `case/<code>`, `limit` and `offset` until the `filtered` total (the field
  Qase gives for the filter, else `total`) is read. The project code is one encoded URL segment. TestRail: `get_project`,
  `get_sections`, `get_cases`, each followed through `_links.next` (an older server's plain list is one page); a next link is taken
  only on the instance's own host, and a link seen before is a loop.
- Every case of the project is fetched, not only those of the configured suites, so that the document frequency cap counts all the
  fetched cases (the brief's wording); the suites only decide which are in scope.
- **Open point, Qase header.** The credential goes in `Authorization: Bearer <token>` because that is the one header this milestone's
  rules allow it in. Qase's documentation names its own header (`Token`) for the API token; this was not tried against the real service
  (no network here). If the service wants it, `Client.authorization` and the header name in `HostBase._headers` are the one place to change.
  Everything the tests assert (a header and nowhere else, redacted everywhere) holds for either name.
- Specs are read at the head of the change when the spec repository is the changed one (specs travel with the code), else at the head of
  the repository's primary branch as of `--as-of`; hosted sources are read as they are now and a note says `--as-of` does not move them.

### Matching

- `match.area_matches`: a configured area matches an import-backed area when one of its `route_prefixes` covers the area's route path (on a
  segment boundary, the method of an API label dropped, the other paths a screen is also mounted at included) or the area's module is one
  of its `modules` (case ignored). A candidate found by name is never suggested for.
- The words of an area are those of its label, module, also-mounted paths and the changed symbols that reach it. The symbols come from the
  area's `direct` or `cross-repo` finding: the definition lines it cites carry the claim `def:<symbol>` (M9), so `run_all(workspace,
  analysis)` needs nothing but the analysis. (A finding's own title is not used: it is generated text.)
- `words.py`: lowercased tokens of four letters or more (`MIN_LENGTH`), camel case, snake case and kebab case split (`formatAmount` is
  `format` and `amount`), a plural folded to its singular (`invoices` is `invoice`, `categories` is `category`; `status`, `address`,
  `analysis` stay), the length checked before and after the folding, a stop list of generic words (test-writing words, steps, UI
  furniture, CRUD verbs, place words, function words; the list is lowercase, in the singular, four letters or more), and the cap: a word
  in more than 10 percent of the fetched cases is dropped. Two choices beyond the brief, both tested: the folding, and a floor of three
  cases for the cap (`DF_FLOOR`): on a list under 30 cases ten percent is less than a case, so without a floor every word of a short
  project would be "in too many cases" and nothing could be suggested. From 100 cases the cap is exactly the 10 percent; 10 of 100 is
  kept, 11 is dropped. The matched words are recorded on the `Suggestion`, in the folded form, sorted.
- `Suggestion` gained `area_id` (the area it was suggested for; a case suggested for two areas is two suggestions) and `evidence` (the
  spec line, pinned). The JSON has both keys, in the field order before `label`; a suggestion written before them still loads. The
  frontend (`frontend/`, not touched here) reads `suggestions` by `source`, `case_id`, `title` and `matched_words` and ignores the new keys,
  so it shows the list flat until it is taught the grouping.

### Specs

- `specs.visits(text, framework)` tokenises with the JavaScript lexer of M5, so a `goto` or `visit` in a line comment, a block comment or
  a string is not a call. Playwright: `<anything>.goto(`; Cypress: `cy.visit(`. The first argument decides: a string, a template
  literal, or something else.
- A literal or template address is cut to its path (scheme, host, query and fragment dropped, a relative address read as if it started
  with a slash, `//` and a trailing slash dropped); a substitution is a parameter and so is a segment that holds one (`inv-${n}`).
- Matching to the route table (`match_routes`): a literal segment equals a route's static segment (case and percent encoding ignored) or
  fills a parameter; a spec parameter fills a route parameter and never a static segment (so `/cases/${id}` is not `/cases/new`); the number
  of segments agrees (a trailing `*` takes the rest); a static route beats a parameter route (the most static segments matched wins, equal
  ones are all kept). A route with a part that could not be evaluated is never matched and makes the list partial. The route table is
  the entry-point adapter's (`routes()`), at the same commit as the specs; the visit is a suggestion for an area of the run whose label
  or `also_mounted` is the matched route, in the repository the screens are read from. A visit to a route that is not an area of the run
  is not listed: it is not a place the change was shown to reach.
- Not literal, therefore recorded and never matched (`spec_url_partial`, non-blocking, subject `<repo> <path>:<line>`, detail the reason and
  the line): a variable, a concatenation, an options object (`cy.visit({ url })`), a template that starts with a substitution
  (`${BASE_URL}/cases`), a call or anything else. The title is the name of the nearest `test(` or `it(` before the line (`.only`, `.skip`
  and the like included), else the spec's path; `case_id` is `<path>:<line>`; `matched_words` is empty (the tie is the route); the spec
  line is the evidence. When an address is on a line of its own the evidence is that line.
- Default spec files: `e2e/**` and `tests/**` with `.spec` or `.test` (Playwright), `cypress/e2e/**` and `cypress/integration/**` with `.cy` or
  `.spec` (Cypress), extensions `ts tsx js jsx mjs cjs`; `globs` replaces them. A unit test that calls `goto` on a stub is not selected.
- Reads of the spec source go through `Git.quiet()`: a read that fails there is the source's answer (`source_unavailable`), not a blocking
  `read_unreadable` of the whole run. A route table that is not whole is a cut (the entry adapter's `problems`), not an unavailable source.

### Pipeline, CLI, report

- Step after the findings: `testsources.run_all(workspace, analysis, ...)` with the run's git, its `propagate.Run`, `(repo, head sha)` as the
  origin and the run's deadline (a check before every hosted request: a run past its limit is `timed_out` with no suggestions, never a source
  that failed). `Options` gained `test_transport` and `env`; `cli.main` gained `transport` (the default sends real requests; the suite's guard
  forbids that). `inputs.test_sources` (per source: `source`, `kind`, `state` of `read`, `incomplete`, `unconfigured` or `unavailable`, and `read`)
  and `inputs.test_source_read` (true only when every configured source was read completely) are written only when the workspace has a
  test source, so a report of a workspace without one is byte for byte what it was.
- `Outcome` unpacks as `suggestions, records`; it also carries `reads`, `notes` and `read_completely`. `drafts(analysis)` is the stub the
  brief allows: it returns nothing.
- Report: the section is "Suggested, not verified coverage" with a heading per area (must test, sample, candidates, in the order of the
  areas), each suggestion as source, case id, title (content), matched words and, for a spec, the line; suggestions tied to no area of the
  report go under "Not tied to an area of this report". Under the list, one sentence and the records per kind of source problem
  (did not answer, read in part, not configured). The states are `listed`, `unavailable`, `incomplete` (new: a cut and nothing listed),
  `unconfigured`, `read_none` and `not_read`; the records are kept in every state. "None suggested." needs `test_source_read` and no
  source record. The Markdown, HTML, summary (`Suggested cases (not coverage and not counted above): N`) and the MCP `suggestions` section
  (`area_id`, `evidence`, `source_state` also `incomplete`) follow.

### Known limits

- Nothing was run against a real Qase, TestRail or browser-test setup; the responses are synthetic, shaped from the public API descriptions
  (see the open point about Qase's header). There is no retry after a 429: the run says the source did not answer.
- A source with many cases reads them all at every run (up to the page cap); there is no cache (a cache would hold titles, never a token).
- Specs: only the first argument of `goto` and `visit` is read; `baseURL` and `Cypress.config` are not evaluated; a visit made through a
  helper or a page object (`this.open("/x")`, `loginAndVisit()`) is not seen; a route table is read for React Router screens only (the
  entry-point adapter of the repository decides; a repository whose entry points are not read makes the source unavailable, saying
  there is no route table); a visit to a route path that two routes share, with equal specificity, is a suggestion for each.
- The word match is on titles only (not on case steps or descriptions), and plural folding is English only.
- The frontend does not group suggestions by area yet (see above); the Markdown and HTML reports and the MCP section do.

### Tests, speed

3,027 tests (2,784 before M15; 243 new: words 23, model and config 25, Qase 31, TestRail 22, matching 23, spec text 26, spec repository 21, run_all 30,
report 19, end to end 23, plus the existing ones that were changed). All pass on 3.11 (3,032 counted there), 3.12 and 3.13 in the parallel
runner (4 workers) and in one serial `unittest discover` on 3.12. The machine was busy with another job during these runs (load average 13), so the
times are inflated and are not a measure of the change: parallel 3.12 179 s, 3.11 279 s, 3.13 273 s; serial 3.12 678 s (the M9 baseline was 106 s
parallel on a quiet machine). The new modules take under 14 s of the work together (the end to end module 10 s: five CLI runs on the small spec
repository; the spec repository tests 3 s; the rest run in well under a second, no git). `tests/.timings.json` was not refreshed (a busy machine
would write the wrong weights); the new modules are balanced by the runner's default.

Mutation check of the rules (each mutation made, the named tests run, the mutation reverted): the document frequency cap removed, its floor
removed, the two-word minimum lowered to one, the suites scope removed, candidates included, pagination stopped after the first page, the cut not
recorded, the loop guard and the same-host check of TestRail's next link removed, the `source_unavailable` record dropped, a 404 read as an
answer, a spec parameter matching a static segment, equally specific routes not kept, `coverage_gaps` reading suggestions, a POST sent, the
records not added to the run, the unavailable state not shown: every one is caught by a test.


### Review fixes

An independent review found one blocker and several majors; each is a test (`tests/test_fixtures_tracked.py`, `test_testsources_{scope,auth,guard,
specs_unseen,survivors,words,match,model,report,testrail,qase,e2e}.py`).

- **Blocker, two fixture files were never committed.** `.gitignore` ignores `reports/` (the output of `reachmap analyse`), and a folder called
  `reports` in the spec fixture (`src/modules/reports/`) was swallowed after the earlier exception for `tests/fixtures/reports/`. The exception is
  now `tests/fixtures/**/reports/`, both files are tracked, and `tests/test_fixtures_tracked.py` fails when any file under `tests/fixtures` is ignored.
  The M15 modules were run from a clean checkout in a fresh worktree (see "Tests, speed" below).
- **Qase's header.** Qase API v1 takes its token in a `Token` header. `HostBase.auth_header(token)` (default `("Authorization", ...)`) is the
  hook, `Client(header=...)` uses it and Qase sends `Token: <token>` and no `Authorization`; https only, `register_secret` and the redaction are
  as before. `redact` scrubs a bare `Token` header (plain, JSON and repr forms, anchored so `token_env`, `tokens` and `token-bucket` are left alone).
  `HostedCase.check_every_request` lets a secret appear only in the source's own header. A 401 no longer tells a person whose variable is set to set
  it: it says the token in the variable was refused. This replaces the "open point" above.
- **A failing source can no longer fail the run.** `run_all` catches any exception of a source (a reader that raises, JSON nested deeper than the
  parser allows: `RecursionError` is now one of `_get_json`'s failures too) and records `source_unavailable` with the exception's type name and not its
  message (a message may hold anything); the run goes on and the other sources are read. `StopRun` (what the deadline's check raises; `_TimedOut`
  is one) and the suite's `DefaultTransportForbidden` pass through. The CLI is shown to exit 0 with the areas unchanged on the nested-JSON answer.
- **A spec source never says "none suggested" when it established nothing.** Not configured (non-blocking `source_unconfigured` with the reason):
  no file matches the globs, the route table has no screen route, no area of the run is a screen of the screens repository, the screens repository's
  entry points are not read. Incomplete (`suggestions_incomplete`): spec files with `test(` or `it(` calls and no visit the reader could see at all
  (helpers, page objects), a route table that is not whole, a route whose path could not be evaluated, a file that does not lex. Either way
  `inputs.test_source_read` is false. "Non-blocking" for the invisible visits is read as "blocks no selection": the record is a suggestions record
  and blocks the claim "none suggested" (as every `suggestions_incomplete` does). Literal visits that match no screen route are counted in one
  non-blocking `spec_visit_unmatched` record per source (new kind).
- **"None suggested." next to what could not be read.** With `spec_url_partial` or `spec_visit_unmatched` records the section says "None suggested from
  what could be read; N visits have an address that is not a literal" and lists them; under a list of suggestions the same records follow it. When some
  sources are not configured and others were read it says "Some test sources are not configured, and the others were read and suggested nothing" (and
  the names are in the records under it); "No test source is configured." is for the case that none is.
- **Words.** The stop list is longer (about 260 words: the reviewer's inflections `created displayed shown correctly successful`, the code and UI words
  `handle change format fetch render detail setting default note report search filter login` and a reviewed set of common UI and test words; a domain
  word such as `dashboard` or `payment` is never on it, so that an area can name itself). Words are compared by `stem` (plural, `-ed`, `-ing`, `-ly`, final
  `e`), with a list of words that only look inflected (`series canvas alias family nothing ...`); a stem is never shown: a matched word is recorded as the
  case wrote it, in the order of the stems, so nothing like `sery` or `canva` can reach a report. At least one shared word has to come from the area's
  route (label and the paths it is also mounted at) and not only restate the configured prefix or module (when the route holds nothing but those words,
  they are what there is to go by). The cap's floor is `limit = max(0.1 * n, min(3, n / 3))` (the floor of 3 was too high under 9 cases), and a corpus under 30
  cases is said in a run note. `billing` and `bill` stem alike (a known cost of folding `-ing`; the fixtures avoid it).
- **TestRail's key alone.** The key part of `login:key` is registered with `redact` as well (the login is not a secret); a key alone in a title or an error is
  scrubbed. A next link has to name the same endpoint and project as the list being read, on the instance's own host (scheme and port too), and it is rebuilt
  from the configured address, so a link to a write endpoint or another project is refused before any request; the loop, the host check and the page
  loop are the host layer's (`HostBase._collect_pages`, which `_collect` now uses; the test sources ask it for a cap and a flag instead of a failure).
- **Qase.** `total` is preferred, `filtered` is used when only it is given, and a `filtered` that differs from `total` when no filter was sent is
  `source_unavailable`.
- **Scope of a record (the model rule).** A record has a `scope` (`selection` or `suggestions`, from the kind registry). The records of a test source
  (`source_unavailable`, `source_unconfigured`, `suggestions_incomplete`, `spec_url_partial`, `spec_visit_unmatched`) have `blocks_selection: false` always and
  `blocks_suggestions` (true for `source_unavailable` and `suggestions_incomplete`, which are always blocking for their scope). The M2 rule "blocking kinds
  cannot be made non-blocking" holds per scope; a suggestions record cannot be made to block selection. `to_dict` writes `scope` and `blocks_suggestions`
  after `blocks_selection`; a record written before them loads (a test source's record that said `blocks_selection: true` is read as blocking suggestions) and
  a record that contradicts its kind is refused. `Analysis.blocking()` is selection only, `blocking_suggestions()` the other. `counts.blocking_records` counts
  selection records and the new `counts.blocking_suggestion_records` the others (the row is shown only when it is not zero). The summary, the report
  (`records_first`, the sentences about blocking records) and the MCP records (`scope`, `blocks_suggestions`) use the fields, not a list of kinds;
  `SUGGESTION_ONLY_KINDS` is gone.
- **Config.** Every hosted table refuses a key it does not know and names the near miss (`max_page`, `tokn_env`); an area refuses `token_env`; the error of a
  spec table does not list `areas`. M3 pinned that a key `path` of a test source stays as written: that test now registers its own kind. A spec source whose
  screens repository (`screens_repo`, else `repo`) has no `entry_points` of a kind that makes screens (`react-router`) is a config problem at that key.
- **Specs.** A substitution in place of the host (`https://${host}/x`) is partial; `.` segments are dropped and `..` pops; titles go through `clean_title`.
- **Cleanups.** One `label_path`; `Corpus.complete` is derived from `cut`; `ids_in`, `Outcome.read_completely` (now `testsources.all_read(reads)`) and the
  duplicate tokenising of titles are gone.

Not done: the frontend does not group suggestions by area and does not read `scope` (not touched here; another branch rewrites it). A Qase title that stems
like another word (`billing` and `bill`) still matches it.
