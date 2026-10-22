# M10 brief: reports

Goal: render one analysis (the M9 JSON report, the contract) as Markdown, a single-file
HTML page, and a CLI summary, plus the "For the author" view, without ever turning report
content into markup or making a network call. Done when a fixture file containing a closing
script tag (and other markup) cannot break out of the HTML page, and every renderer is
driven only by the JSON report. Read `docs/CONTEXT.md` sections 1 (report contents and
order), 4 (rules 2, 3, 4, 8), 8 (HTML posture), 13.4; the M9 brief and notes (report
shape, `schema`, `counts`, null results for failed runs); `reachmap/wording.py`.

Python 3.11+ stdlib only. No templating library; no JS framework in the HTML report.

## Files

```text
reachmap/report/__init__.py     load(path) -> validated report dict (schema version check), render(report, fmt)
reachmap/report/model.py        view model: the report reordered into the reader's sections (one place)
reachmap/report/markdown.py
reachmap/report/html.py         builds the page from a static template + escaped JSON
reachmap/report/assets/report.html, report.css, report.js   the static template, inlined at build
reachmap/report/summary.py      CLI summary (moves out of cli.py)
reachmap/report/author.py       "For the author" view model
tests/test_report_*.py
tests/fixtures/reports/         hostile report JSON fixtures (see Security tests)
```

## The view model (one place, every renderer uses it)

Sections in the order a QA reader needs them (context section 1):

1. Header: repo, change label, pinned head and pre-change shas (short, full on hover or in
   a details block), run status, completeness (`complete` false is said at the top with
   the reasons count), tool version. Never "safe", "complete coverage", "nothing to test".
2. What the run could not establish: blocking records first, then non-blocking, each with
   kind, subject, `not_established`, detail. Shown before areas when any record blocks
   selection; after findings otherwise. A failed or timed-out run shows only header and
   this section, with "No findings are shown because the run did not complete." (passes
   the wording check).
3. Areas to test: must test, then sample, then candidates (by-name, labelled "candidate,
   matched by name, not import-backed"), each with priority, label, repo, also mounted,
   chains (collapsed by default in HTML), completeness of its chain.
4. Findings: grouped by kind, each with risk and confidence as two separate labelled
   fields, confidence basis, evidence (repo, path, line, verbatim detail, short ref).
   Exclusions in their own group: "Not reached (with evidence)".
5. Branch matrix: one row per branch and file, state, evidence lines; `branch-missing`
   findings linked.
6. Cross-repository reach: per edge (http, package, contract) what was looked at, pins per
   branch, adoption, contract reads, candidates apart.
7. Backend calls.
8. Suggested test cases: always under the heading "Suggested, not verified coverage",
   never merged with mapped tests (empty until M15; the section still renders "None
   suggested." only when the source was read; if not configured, say "No test source is
   configured." using the record).
9. For the author: other repositories reached, files beyond the diff, other branches
   (from the matrix), unit tests to run with the per-repo command from config (shell-quoted
   paths), each command copyable as text.

The view model takes only the report JSON (no repo access, no config), so a report can be
rendered anywhere. Every string that the view model creates (headings, labels, sentences)
passes `wording.require_clean`; strings copied from the report (source lines, paths,
subjects, details) are content and are never checked or altered, only escaped.

## Markdown

- GitHub-flavoured, readable as plain text. Source lines in fenced code blocks with a
  fence longer than any backtick run in the content; inline paths in backticks with the
  same rule. Escape Markdown-active characters in content placed outside code (`*`, `_`,
  `[`, `]`, `<`, `>`, `|` in tables, leading `#`, `-`, `+`, digits followed by `.`).
  Raw HTML from content must never be interpreted: escape `<` and `>` and `&`.
- Tables only where columns are short (branch matrix, counts); chains as nested lists.

## HTML (single file)

- One self-contained file: inline CSS and JS, no external URL of any kind (no fonts,
  images, scripts, styles, links that load anything). System font stack (`system-ui`,
  `-apple-system`, `Segoe UI`, `Roboto`, sans-serif; monospace equivalent) instead of
  bundled font files; record this choice (context says self-hosted fonts; system fonts
  need no hosting and make no network call).
- Content Security Policy meta tag: `default-src 'none'; style-src 'unsafe-inline';
  script-src 'sha256-<hash of the inline script>'; img-src data:; base-uri 'none';
  form-action 'none'`. Compute the hash at build. No `connect-src`, so the page cannot
  call out even if content tried.
- The report JSON is embedded in `<script type="application/json" id="report-data">`
  after escaping `<` as `<`, `>` as `>`, `&` as `&`, and U+2028/U+2029 as
  ` `/` `. The inline script parses it with `JSON.parse(textContent)` and builds
  the DOM with `createElement` and `textContent` only. No `innerHTML`, `outerHTML`,
  `insertAdjacentHTML`, `document.write`, `eval`, `Function`, `setTimeout(string)`, or
  `href`/`src` set from content (a test greps the template for these).
- A static server-side rendering of the same view model into the page body (escaped with
  `html.escape(quote=True)`) is the no-JS fallback; with JS on, the script builds the
  interactive view (collapsible chains, filters by repo, kind, priority). Both from the
  same view model JSON.
- Light theme by default, a theme toggle stored in `localStorage` wrapped in try/catch;
  the page works when storage throws.
- Print stylesheet: everything expanded.
- Accessible: headings in order, tables with headers, buttons are buttons, focus visible.

## CLI

- `reachmap analyse --format json,md,html` writes each requested format next to the JSON
  (same base name). `reachmap report <report.json> --format md|html --out FILE` renders an
  existing report. The summary printer moves to `report/summary.py` unchanged in
  behaviour (M9 tests keep passing).
- `reachmap report` validates the schema version and refuses an unknown major version
  with exit 2.

## Security tests (the done-when)

Hostile report fixtures (synthetic JSON in `tests/fixtures/reports/`), produced also from a
real fixture run whose changed source line contains these strings:
`</script><script>alert(1)</script>`, `<!--`, `<img src=x onerror=alert(1)>`,
`"><svg onload=alert(1)>`, `javascript:alert(1)` as a path, ` ` and ` `,
`]]>`, a backtick run of 10, Markdown link syntax `[x](javascript:alert(1))`,
`<details open>`, a 2 MB line, non-UTF-8 replaced characters, RTL override U+202E.

- The HTML parses (stdlib `html.parser`) to exactly one `<script type="application/json">`
  and one inline script, whose bodies contain no `</script` (case-insensitive) and no
  `<!--`; no element or attribute from content appears in the parsed tree; the JSON parses
  back equal to the input report.
- No URL in the page (regex for `http:`, `https:`, `//`, `url(` outside data: CSS)
  except in escaped content text.
- Markdown: content appears only inside code spans/blocks or escaped; no raw HTML tag
  from content survives; link syntax from content is inert.
- Renderers never call the network and never read files other than their assets.
- Wording: every renderer string passes the check; a report containing the word "safe" in
  a source line renders it verbatim (content) without failing.
- Failed and timed-out reports render header and records only, no counts.
- Golden tests: s11 and s13 reports rendered to Markdown match committed golden files
  (update with an env flag), so format changes are reviewed.

## Implementation notes

Where the code differs from, fills in, or goes beyond the text above. Each point is covered by a test unless it says otherwise.

Files added beyond the list: `report/text.py` (what is shown of a piece of content), `report/blocks.py` (the document: headings, lists, tables, code
blocks and details whose text is `Content` or something the tool says), `report/layout.py` (the view model as a document: every heading, label and
sentence the tool says, in one place), `report/assets.py` (reads the three assets), `report/read.py` (the tolerant readers of the deep payload, shared by the view model and the author view), `tests/support_report.py`, `tests/test_report_*.py` (load, model,
markdown, html, summary, cli, scenarios), `tests/fixtures/reports/` (`complete.json`, `hostile.json`, `failed.json`, `timed_out.json`), `tests/golden/`
(`s11.md`, `s13.md`). Files changed: `reachmap/cli.py`, `pyproject.toml` (the assets ship: `[tool.setuptools.package-data]`), `.gitignore` (the fixture folder
`tests/fixtures/reports/` was ignored by the later `reports/` line), `tests/test_cli.py` (one test: md and html are formats now, so the M9 test that refused
them refuses `pdf` and `docx` instead). No `expected.json` changed.

### Layers

`report.load/check` (the report is read and checked) then `report.model.build` (the report reordered into the sections: sorted, grouped, matched,
every copied value `Content`) then `report.layout.build` (the document; `blocks.require_clean` runs the wording check over every string that is not
`Content`, so a banned phrase in anything the tool says cannot be handed out) then `markdown.render` or `html.render`. The author view is
`report/author.py`, built by the model from the same JSON. Nothing below `report.load` reads a file but the three assets (a test patches `open` and
`socket`) and nothing reads a repository, a configuration or the environment.

### Choices (small ones I made; say if you want any of them the other way)

- **The page is the static body plus a script that enhances it**, not a second renderer. The brief has the script "build the interactive view" from the same
  view model JSON, but also wants exactly one JSON script holding the report itself. Building the sections again in JavaScript would be a second copy of the
  view model (ordering, grouping, wording) that could drift, so the Python view model is the only one: the script reads the embedded report, folds the
  chains, notes and file list, adds the filters (repository, area kind, priority, finding kind), the theme toggle and the print handling, and finds what
  to filter by the area and finding index each list item carries (`data-a`, `data-f`: numbers, never content). The filters read the report JSON, not
  the page text. Chains, notes, the commit ids and the file list are native `<details>` that start closed with or without a script; printing opens them
  all (the script's `beforeprint` handler, and `details::details-content` in the print stylesheet for a browser that has it, which is the whole job
  without a script).
- **System fonts** (`system-ui`, `ui-monospace` stacks) instead of self-hosted font files, as the brief allows: no file to host and no request. The context
  notes say "self-host fonts"; this keeps the posture (no network call) and needs nothing shipped.
- **No links at all in the page.** No `href`, `src` or `action` anywhere, not even `#` anchors: findings are numbered `F1`, `F2` (their place in the
  report) and the branch matrix and the areas name them in text. A pull request's `url` is not shown.
- **Content is made visible, not trusted**: control characters, bidi controls (U+202A to U+202E, U+2066 to U+2069), zero-width and line-separator
  characters and lone surrogates are written as `\xNN` or `\uNNNN` in every rendering (and in the CLI summary), so they can be read and cannot act. This
  is a presentation change of content and not a wording change; the embedded JSON keeps the original. Content longer than 2000 characters is cut with
  `[cut: N more characters in the report JSON]` (a 2 MB source line stays 2 MB in the JSON and is a few KB in Markdown and in the page body); both are
  tested.
- **Markdown**: short values (paths, labels, branches, shas, kinds, states) are code spans; titles, details and notes are escaped text, except that a text
  holding `://`, `www.` or `@` is put in a code span, because GitHub turns those into links whatever is escaped (backslash escapes do not stop an
  autolink). A newline in content is a space outside fences. Entities (`&lt;`, `&gt;`, `&amp;`) are used for `<`, `>`, `&` outside code. No CommonMark
  engine is installed here, so the tests read the output with a small scanner (`tests/test_report_markdown.py`: fences, code spans, escapes, table cells)
  and a real renderer was not run over it.
- **Counts** are the report's `counts`; if it has none (a hand-made report) they are counted from the lists. A run that did not complete has none, whatever
  the file holds (a failed report that carries counts or lists still renders header and records only).
- **A whole run says nothing about being complete**, as in M9: only `complete` false is said at the top (with the number of reasons). The header says
  "Run status: finished" for a completed run so that the word "complete" never appears in a whole run's report (a test checks all 23 scenarios).
- **Suggested test cases** has five states: suggestions listed; a `source_unavailable` record (so not "none"); a `source_unconfigured` record ("No test
  source is configured." with the record's text); the source was read and suggested nothing ("None suggested."), which needs an optional
  `inputs.test_source_read: true` that M15 will write (nothing writes it yet); and otherwise "No test source was read by this run." (every M9 report: it
  is neither none nor not configured, and the page does not say which). Mapped tests, when there are any, are a section of their own before it.
- **Branch matrix**: one table row per row of the report (a branch, and one per unreadable file, as M9 writes them), with the finding numbers of the
  `branch-missing` findings about that branch (matched on the finding's identity: the missing branch); the detail and evidence lines of each row follow the
  table. The brief's "one row per branch and file" is the report's own rows.
- **Which finding belongs to an area**: the `direct` or `cross-repo` finding whose identity is the area's repository, kind and label. The completeness of
  an area's chain is read from `propagations[0].reached` (matched on repository, kind, label; a pair reached twice is incomplete if either is).
- **Files beyond the diff** (author view): the files cited by area chains, route lines and the evidence of findings that are not exclusions, minus the
  files of the change in its repository. Excluded files are not reached and are not listed. **Other repositories reached** come from the hops, passes,
  consumers, http edges and contract reads, with the kinds of edge that led there. A repository with no unit test command lists the shell-quoted paths
  as the text to copy.
- **Order of areas** inside a priority is (repository, kind, label), the order of the CLI summary; findings keep their report number whatever the grouping.
  Finding groups follow `findings.TABLE_KINDS` order (a test holds the view's list to it); a kind the view does not know is kept, after the known ones.
- **`reachmap report`** takes `--view full|author` besides `--format md|html` and `--out FILE` (standard output without it; nothing is printed on success
  with `--out`). `analyse --format` takes `json`, `md`, `markdown` (as `md`) and `html`; the JSON is always written, the others beside it with the same base
  name, rendered from the file just written (so `analyse` and `report` give the same bytes). A format that cannot be written is exit 3 after the others
  and the summary; the summary lists `Markdown:` and `HTML:` lines after `Report:`.
- **The summary takes the report JSON** (`report.summary.lines`) and `cli.summary_lines(analysis, path, complete)` stays as a thin wrapper over it so that
  the M9 tests run unchanged. The only change of behaviour is that control characters in quoted code are written out.
- **Validation** (`report.check`) is strict about what the layout relies on (types and enumerations of findings, areas, branches, records; every finding
  has evidence; a failed or timed-out run carries its record and null results) and lenient about the deep payload of the propagation, where a missing or
  odd part is shown as absent. A fuzz test removes and retypes every key under the parts of the fixture and requires a render or a `ReportError`, never
  another exception.
- **The JS is ES2015 without a build**; `node --check` runs on it in the tests when node is installed (skipped otherwise). I also loaded the page for
  the fixture and for the real hostile run in the browser pane: the script ran under the policy (so the hash matches), the filter worked, no console error,
  and the hostile page had two scripts and no `img`, `svg` or `a` element.

### Known limits

- The filters and the theme toggle exist only with a script; the static page has no control. Printing without a script expands the folds only in a
  browser that supports `details::details-content` (recent Chrome and Edge, others by version); elsewhere the folds print closed.
- A report from a later minor version with fields these renderers do not know shows only what they know (the major version is the schema integer, so a
  schema 2 report is refused, never read partially).
- Content is cut at 2000 characters per piece, not per line of a multi-line piece, and not in the embedded JSON.
- The page does not search; use the filters or the browser.
- The tests run the real hostile run (a one-repository workspace built in a temporary folder) once per process; with a 2 MB added line it is still under a
  second.

### Review fixes

An independent review found no blocker (the page and the Markdown held against a broad attack set, with a CommonMark and GFM parser as oracle), one
major and several minors. Each is a test; no `expected.json` changed.

- **Major: an edge kind in a hop was report content shown as the tool's own text.** A hop kind `safe` made `reachmap report` fail with a wording error,
  `x\ud800` could not be written, and escape and bidi characters reached the page raw. The author view now maps the four kinds the tool follows
  (`http`, `package`, `contract`, `event`) to fixed words and shows any other value as content (a code span). The audit is permanent: a test marks
  every string of eight reports (the fixtures, three real runs and the real hostile run) with a suffix, spies on `wording.require_clean` and fails if a
  marked string is checked as the tool's own (`NothingFromTheReportIsTheToolsOwnText`); a second test makes every string that logic does not need
  verbatim hostile (`support_report.hostile_copy`: every `kind`, `state`, `adoption`, `basis`, edge kind, path, title and detail, for the fixtures and
  the real reports, failed and timed-out ones included) and renders all of it in both formats and views. `hostile.json` is now `hostile_copy` of
  `complete.json`, which has a pull request (`change_request`) too. A hostile `priority`, `risk`, `confidence` or `status` (the values the layout
  chooses by) is a `ReportError` and exit 2 through the command, never a traceback.
- **Markdown**: the delimiter of a leading number is escaped (`1\.`, `2\)`), a trailing `#` of a heading is escaped, `$` is escaped, and a text with
  an emoji shortcode (`:smile:`, not `edge:event:acme`) is put in code like a link is (a backslash does not stop either reading).
- **HTML**: `<link rel="icon" href="data:,">` so that a page served over http does not ask for `/favicon.ico` (the one `href` the tests allow, with that
  exact value); `th scope="row"` for the first cell of the counts and of the branch matrix; the script hides the heading and the note of a group when a
  filter hides every item of its list; the theme button has a fixed label and says its state with `aria-pressed` only; the print style forces white
  backgrounds for code, blocks and headers.
- **`reachmap report` to standard output** writes UTF-8 bytes to `sys.stdout.buffer` (tested in a subprocess with `PYTHONIOENCODING=ascii`).
- **The summary** shows odd characters through `text.visible` before the scrubbing, so the summary, the Markdown and the page show the same escapes
  (one test feeds the same string to all three); the findings line is quoted too (a hostile kind put a raw line separator in it).
- **Isolation test**: calls `summary.lines` as well as every renderer, with `open`, `io.open`, `Path.read_text`, `Path.read_bytes` and `os.open` all
  spied: anything but the three assets fails it. The JS check also forbids bracket access (`el["innerHTML"]`), `createContextualFragment`, `DOMParser`,
  `setHTMLUnsafe`, `parseFromString`, `srcdoc`, `outerHTML` and a name put together from two literals.
- **Raw bidi and invisible characters** in test sources (and a BOM in `reachmap/adapters/python/syntax.py` and `tests/test_py_syntax.py`, from M6) are
  `\u` escapes now; a test scans `reachmap/` and `tests/` for them.
- **Contract reads** name their consumer repository, branch, pin, adoption and form; `Header.how` is shown ("How the change was found"); the unused
  `BranchRow.commit` and `Ev.ref` are gone (the short commit is in the matrix and every evidence line). A run that failed or timed out keeps its notes
  and the change's file count in the header ("Files in the change: N"): the count is a fact about the change the host or git gave, not a count of what
  the analysis found, so it is not one of the counts a failed run must not show.
- **Non-finite numbers** (`1e999`, `NaN`, `Infinity`, also in a report held in memory) are a `ReportError`.
- `model.py` and `author.py` share `report/read.py`; the `Details` docstring says the folds start closed.

Not done: a real CommonMark engine was not run by me (the reviewer's oracle was); the check for a name built from pieces in the JS is the two-literal
pattern only.

### Tests, speed

2,088 tests (1,898 after M9; 190 new, one M9 test changed: see above). Measured with nothing else running, all passing:

| Python | serial (`unittest discover`) | parallel (`run_parallel.py`, 4 workers) |
|---|---|---|
| 3.11 | not run | 124.2 s |
| 3.12 | 268.0 s | 100.4 s |
| 3.13 | not run | 134.7 s |

The new modules cost, in a parallel run on 3.12: `test_report_model` about 13 s (the fuzz over the fixture's payload is most of it),
`test_report_scenarios` about 11 s (the 23 scenario reports through every renderer), `test_report_cli` about 4 s, `test_report_html` about 2 s,
`test_report_markdown` about 1 s, `test_report_summary` and `test_report_load` under 1 s. `tests/.timings.json` was refreshed with `--record` earlier so
that the workers stay balanced.
