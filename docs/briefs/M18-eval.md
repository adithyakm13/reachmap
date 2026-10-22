# M18 brief: evaluation tools

Goal: the tools that measure whether Reachmap's output is trustworthy and useful, from
context section 9: a citation audit (every cited locator re-read at its pinned commit), a
reach-beyond-the-diff harness, and judging sheets for people. Done when the audit passes
on all fixture reports. Read `docs/CONTEXT.md` section 9 (all of it: counts not
percentages, independence on both axes, exclusions are not predictions, reverts cannot
test reach, human judging sheets, recall needs linked defects), 4, 5; M4 (git layer, pins,
as_of), M9/M10/M11 notes (report JSON, evidence, claims, review keys).

Python 3.11+ stdlib only. No model calls anywhere in evaluation (judging sheets are filled
by people).

## Files

```text
reachmap/evaluate/__init__.py
reachmap/evaluate/audit.py      citation audit
reachmap/evaluate/witness.py    reach-beyond-the-diff harness
reachmap/evaluate/sheets.py     judging sheets (CSV and Markdown) and reading filled sheets back
cli.py                          `reachmap audit REPORT...`, `reachmap witness CASES.toml`, `reachmap sheet REPORT --out sheet.csv`, `reachmap sheet --score FILLED.csv`
tests/test_eval_*.py
tests/fixtures/eval/            witness cases on the fixtures
```

## Citation audit

- For every Evidence anywhere in a report (findings, areas' chains and route lines, branch
  rows, propagation hops, contract reads, http edges, consumers, decisions' "what changed",
  model notes' targets), re-read `repo` at `ref` through the git layer and check line
  `line` equals `detail` exactly (after the documented CR handling). Results per
  locator: `ok`, `mismatch` (shows both), `absent` (path not in the commit), `unreadable`
  (recorded; never counted as ok or as mismatch).
- Also check structural claims cheaply verifiable from the report alone: every finding has
  evidence; ids and fingerprints recompute; claims (import, route, def) agree with the
  cited line where checkable.
- Output: counts (ok, mismatch, absent, unreadable), the first N failures with locators,
  exit code 0 only when mismatch and absent are 0 and unreadable is 0 (or `--allow
  unreadable` reports them separately). Never percentages.
- Done when: every report produced by the 23 + M16 scenarios passes with 0 mismatches,
  0 absent, 0 unreadable; a mutated report (a detail edited, a line shifted, a ref
  changed, a path changed) fails with the right category.

## Witness harness (reach beyond the diff)

- A case: repo, the original change (pr / compare / range), its merge time, and a later
  witness change (a follow-up fix) whose touched files are outside the original diff. The
  harness analyses the original as of its merge time (`--as-of`), and counts whether the
  report cited any witness file: by findings alone, by screens and routes, by any
  evidence. Paths match exactly or at a path boundary.
- Independence on both axes: refuse a case set where two cases share a subject (the same
  changed file set or the same PR) or a witness (the same witness change or file set); say
  which.
- Exclusions are not predictions: exclusion findings never count as a hit.
- Reverts cannot test reach: a case whose witness is a revert of the original (same file
  set, inverse diff) is refused.
- Output: counts per axis ("hits: 3 of 9 by findings; 5 of 9 by screens"), never
  percentages, with a sentence that small samples are weak and that no recall number
  exists without defects linked to the changes that caused them (context 9, last bullet).
- Fixture cases: build three or four witness cases on the M1 fixtures (extend the builder
  with follow-up commits on new branches, existing shas unchanged), including one that a
  dependency-sharing case would double count (refused), one revert (refused), one hit by
  screens only, one miss.

## Judging sheets

- `reachmap sheet REPORT` writes one row per listed screen or route and per suggested
  case: report key, revision, area id, label, priority, chain summary (first and last
  hop), columns for a person: `relevant (yes / no / unsure)`, `would have tested (yes /
  no)`, `notes`, `judge`. CSV (RFC 4180, formula-injection safe: cells starting with `=`,
  `+`, `-`, `@`, tab or CR prefixed with a quote) and Markdown.
- `reachmap sheet --score FILLED.csv` reads filled sheets back and reports counts per
  column; refuses sheets missing the judge column or with unknown values; never fills
  anything itself. Document that sheets are for people: the MCP server exposes no tool to
  fill or score them, and the evaluation docs say a model must not fill them.

## Tests

- Audit: all fixture scenario reports pass; mutation categories; unreadable separated;
  performance on a large report (batch reads per commit with `cat-file --batch`).
- Witness: independence and revert refusals; counts on fixture cases; as-of honoured.
- Sheets: CSV injection safety, round trip, score counts, refusal cases.

## Implementation notes

Where the code differs from, fills in, or goes beyond the text above. Each point is covered by a test unless it says otherwise.

Files added: `reachmap/evaluate/{__init__,audit,witness,sheets}.py`, `docs/evaluation.md` (the three tools, and that a model must not fill a sheet),
`tests/support_eval.py` (the reports of every fixture world), `tests/support_eval_mini.py` (a small report and a reader with no git),
`tests/support_witness.py`, `tests/test_eval_audit{,_acme,_m16a,_m16b,_m16c,_perf}.py`, `tests/test_eval_witness{,_fixtures}.py`, `tests/test_eval_sheets.py`,
`tests/fixtures/eval/` (`witnesses.json`, `build.py`, `cases/{valid,refused-shared-witness,refused-shared-subject,refused-revert}.toml`). Changed:
`reachmap/cli.py` (the commands `audit`, `witness` and `sheet`; the work is in `reachmap.evaluate`, `cli._evaluate_command` is the glue, a usage error is exit 2).
No fixture, `expected.json`, existing sha or other milestone's module changed; `docs/PROGRESS.md` is not touched.

### Citation audit (`audit.py`, `reachmap audit REPORT... [--config] [--show N] [--allow unreadable]`)

- **What is audited.** Every piece of evidence of the report, found by shape (an object with `repo`, `path`, `line`, `ref` and `detail`), wherever it is:
  findings, areas, branch rows, every pass, hop, call site, consumer, contract read, http edge, suggestion. A walker by shape (not by place) means a later
  kind of evidence is covered without a change here; an object that has those keys and is not valid `Evidence` (a ref that is not a commit, a path that
  leaves the repository, a line of 0) is a structure problem and is never read. Model notes: the note store beside the report is read through
  `notes.attach` (a `model_notes` the file itself holds is never trusted, as everywhere else) and each verified proposed file is read again at the
  commit it was verified at, its lines checked for the symbol as a name; a `current` note about a target the report lacks is a structure problem
  (an `orphaned` note is a state, not a failure).
- **Reads.** Per (repository, commit) one `Git.files_at` call (M4: one `ls-tree -r` per 200 paths and one `cat-file --batch`; chunks of 500 paths bound
  what is held): a test with 5,000 citations over 150 files at two commits counts the git processes and asserts one `cat-file --batch` per commit, no blob
  read on its own and fewer than 25 processes in all (a first version of it, with 400 files, measured 17: the fetches, the listings and the batches). The repository to remote map is the config's (`--config`, default `reachmap.toml`; the report holds no remote), through the same
  `get_host(...).remote(name)` the pipeline uses; a repository the config lacks, a remote that cannot be named, a commit that cannot be fetched and any
  exception are `unreadable` (the reason is shown), never absent and never a mismatch.
- **Counts.** Per report: distinct locators `(repo, commit, path, line, text)` read once, and how many places cite them (`occurrences`); the answers are
  `ok`, `mismatch` (a text that differs, or a line past the end: both shown), `absent` (the path is not in the commit), `unreadable`. A line is a piece
  between newlines (never `str.splitlines`, which also splits on U+2028, U+0085 and the vertical tab); the empty piece after a final newline is no line;
  a trailing carriage return is not part of the text (the adapters cut it with `rstrip("\r")`, the git layer with `removesuffix`: either equals).
- **Structure** (a fifth failing count of its own, not folded into the four): every finding has evidence; every finding and area reads back through
  `from_dict`, which recomputes its id and fingerprint; no repeated id; claims: the role is one of `import`, `route`, `def`, `wraps`, `operation` (an
  unknown role fails: a new adapter role is a change to citations and should come with a change here), a `def:` claim names a symbol (or, for a field
  claimed as `Model.field` and cited at the class line, the model; Java and Kotlin cite a declaration at its first annotation line, which names nothing) on its line, an `import:` line is an import that names the claimed names (or, for Java's same-package use, a line that uses the name) unless the
  statement goes on below (the cited first line ends in `(`, `{`, `,` or `\`; `*` and `default` name nothing), a `wraps:` claim names the file its line
  is in, an `operation:` claim names the method its line holds, an area's route line carries the route claim of its own label and the last hop of each of
  its chains one of its routes (the label and the `also_mounted` paths); a `stale` decision's `changes.added` must be evidence the report holds.
  `changes.removed` cannot be checked from the report alone (it is what the earlier report held) and is not.
- **Exit codes.** 0 only when mismatch, absent, unreadable and structure problems are all zero; 1 when any is not (`--allow unreadable` reports the
  unreadable ones on a line of their own and does not fail on them, never a mismatch, an absence or a structure problem); 2 for a file that is not a
  report of schema 1, a missing file or config, a bad `--show`. The output ends with a sentence that a pass is about citations and says nothing of what the
  analysis found. Counts, no percentages.
- **Results** (the reports of the scenarios as `main` at 035de64 builds them (merged into the branch), audited through `reachmap audit` with each workspace's config; each of
  these is also a test that fails otherwise):

  | workspace | reports | distinct citations | ok | mismatch | absent | unreadable | structure problems |
  |---|---|---|---|---|---|---|---|
  | acme (M1, s01 to s23) | 23 | 432 | 432 | 0 | 0 | 0 | 0 |
  | nextjs | 18 | 246 | 246 | 0 | 0 | 0 | 0 |
  | openapi | 10 | 239 | 239 | 0 | 0 | 0 | 0 |
  | django | 14 | 166 | 166 | 0 | 0 | 0 | 0 |
  | express | 25 | 359 | 359 | 0 | 0 | 0 | 0 |
  | spring | 20 | 296 | 296 | 0 | 0 | 0 | 0 |

  s16 (no merge base) has no citation at all and passes with 0. The first run of the claims check flagged two honest shapes (a field cited at the line of
  its class; an import whose names are on the lines below the first); both are now what the check says it is, not exemptions of a failing report.
- **Mutations** (tests on the real fixtures, `test_eval_audit_acme`): an edited detail is a mismatch (both texts shown), a shifted line and a line past the
  end are mismatches, a ref moved to another commit is a mismatch where the file has another text and absent where the file is not there, a ref no one
  can fetch is unreadable, a path moved to another file is a mismatch and to a missing file absent, a repository the config lacks is unreadable, a finding
  with no evidence, an edited id and an edited fingerprint are structure problems. Mutations of the audit itself that the tests catch: absent read as ok,
  unreadable read as a mismatch, the text comparison always true, structure left out of the verdict, `splitlines`, the phantom last line.

### Witness harness (`witness.py`, `reachmap witness CASES.toml [--config]`)

- **Case file** (TOML, `tomllib`): optional `config` (a path relative to the file; `--config` wins) and `[[case]]` tables with `id`, `repo`, `merged_at`
  (an ISO time, or a TOML datetime; naive is UTC), `original` and `witness` (each exactly one of `pr`, `compare`, `range`, and for the witness `repo`,
  default the case's: a screen can be in another repository than the change, as in the fixture case c02). Closed keys, a repeated id, a bad time are exit 2.
- **Analysis.** The original goes through `changeset.resolve(as_of=merged_at)` for its files and `pipeline.build(Options(as_of=merged_at))` for the report:
  the same code as `reachmap analyse --as-of`, so a pull request needs the host as `analyse` does (not exercised here: no network in the suite). The
  witness is resolved as of now. Files outside the original diff are the witness's files minus the original's changed paths and the paths its renames came
  from (same repository only).
- **Axes.** by findings: a finding that is not an exclusion cites a witness file; by screens: an import-backed area cites one in a chain or route line
  (a candidate found by name is not a screen); by any evidence: a non-exclusion finding or any area, a candidate included. An exclusion never counts on any
  axis and a witness file only an exclusion cites is said apart. The propagation payload is not read for hits: it also holds the excluded importers.
  Paths match exactly or at a `/` boundary; the shorter side needs a `/` in it, so `index.ts` is only `index.ts` (Shape B), and `ab/c.ts` is not `b/c.ts`.
- **Refusals** (the whole set, every reason listed, nothing analysed, exit 2): a witness that is a revert (same file set, per file the added lines of the
  one equal the removed lines of the other as multisets; files with no text are not compared and at least one file must have been; same repository only);
  a witness with no file outside the original diff; a witness not later than the merge time (`Git.pin_read(witness head, as_of=merged_at)` finds the head
  itself: the first-parent line from it has no commit after the time) or one that cannot be dated; a change that cannot be read; two cases that share a
  subject (the same pull request, the same head commit, the same set of changed files in a repository) or a witness (the same, for the witness change).
  Overlapping but different file sets are independent; the harness does not try to be cleverer than the brief.
- **Output.** `hits: 1 of 4 by findings; 2 of 4 by screens; 2 of 4 by any evidence` and the sentence that small samples are weak and that no recall number
  exists without defects linked to the changes that caused them. A run that failed or timed out is listed as not analysed and in no count (a failed run is not
  a miss); a run with blocking records is counted and said to be incomplete. No percentage appears (tested).
- **Fixture cases** (`tests/fixtures/eval/`): `witnesses.json` and `build.py` add six commits on new `eval/...` branches of a build of the M1 repositories
  (dated after the head they follow; `merged_at` in the case files is the date of that head, and a test compares them). A test builds the M1 repositories
  twice and shows every existing ref has the same sha and the only new refs are the six branches. They are built in a copy of their own
  (`support_witness`), not in the shared M1 build, so no other test sees an `eval/` branch. `valid.toml`: c01 (s01, the follow-up fix touches a page cited by
  a finding and a screen: all three axes), c02 (s06 in acme/api, the follow-up in acme/web's `cases/routes.tsx`, cited only by the second chain of a screen:
  screens and any evidence, not findings), c03 (s19, a file nothing cites: a miss), c04 (s04, a file only an exclusion cites: a miss, said apart). Result:
  `hits: 1 of 4 by findings; 2 of 4 by screens; 2 of 4 by any evidence`. Refused: `refused-shared-witness.toml` (two originals, one witness),
  `refused-shared-subject.toml` (one original, two witnesses), `refused-revert.toml` (c07, the witness reverts s01). Also tested on the fixtures: a witness
  inside the original diff, one not later than the merge, one that cannot be read, a repository not in the config, a malformed file, a missing config, and
  as-of honoured (at the merge time the s01 change is analysed and hits, one second earlier it is not on the branch yet and the case is a miss; before any
  commit of the repository the change cannot be read and the set is refused). Mutations the tests catch: independence off, the revert check off, substring
  path matching, a failed run counted as a miss, exclusions counted, candidates counted as screens, `as_of` dropped from the options.

### Judging sheets (`sheets.py`, `reachmap sheet REPORT [--out] [--format csv|md]`, `reachmap sheet --score FILLED.csv`)

- **Rows and columns**: `report key`, `revision`, `kind` (the area's kind, or `suggested case`), `area id`, `label`, `priority`, `chain (first and last hop)`,
  then `relevant (yes / no / unsure)`, `would have tested (yes / no)`, `notes`, `judge`, empty. One row per area (every priority, candidates too) in the
  report's order, then one per suggestion (its area id and priority when it was suggested for an area; the spec line or the matched words as its chain).
  The key is the review key's slug (the report's fallback is its repository and change). Everything the report quotes passes `text.show`, and whitespace is
  one space (a label never holds a newline or a bidi control in the sheet).
- **CSV**: RFC 4180 (`csv`, CRLF), UTF-8 with a byte order mark so a spreadsheet opens it as UTF-8 (reading accepts it with or without, and LF). A cell starting
  with `=`, `+`, `-`, `@`, tab or carriage return gets a leading `'`; a cell that already starts with quotes and then one of those gets one more, so
  `unprotect` removes exactly one and every cell comes back as written (tested over every string of up to three characters from an awkward alphabet).
- **Markdown**: the same rows as a table with the notice that it is for people; report content in code spans with `|` escaped (the M10 helpers).
- **Score**: counts of `relevant`, `would have tested`, notes and judges, and the relevance counts per group (screens and routes, suggested cases) and
  priority; answers are read case-insensitively and trimmed. Refused (exit 2, the file named and no path): no `judge` column, a header that is not the sheet's
  (unknown column, a missing one, another order), a value that is not an answer for its column, an answer with no judge name, a repeated row, a row of the wrong
  width, a file with no rows, text that is not CSV. A judge name alone is not an answer. Nothing is filled, inferred or rounded.
- **For people**: `docs/evaluation.md` says a model must not fill a sheet; a test asserts that no MCP tool name contains `sheet`, `score`, `judge`, `fill`,
  `audit`, `witness` or `evaluat`, that the docs say it, and that nothing under `reachmap/evaluate/` imports a network or model module. No tool of the product
  can tell who filled a sheet: the judge column is a person's own word.

### Known limits

- The audit shows that citations resolve and that claims agree with their lines where a line can say so. It does not re-run the analysis, so a consistent but
  wrong claim is not found, and it cannot check `changes.removed` of a stale decision (it needs the earlier report).
- With no network and a commit that is not in the cache, every citation of that commit is `unreadable`: use `--allow unreadable` for a run that knows that.
- The witness harness cannot tell a revert from a follow-up that restores the same lines in other words, and a witness that reverts the original and adds other
  files is counted (its extra files are outside the diff). Independence is exact equality of sets, not overlap. A pull request case was not run (the suite has
  no host); its resolution is `analyse`'s. The `--as-of` limits of M9 carry over (tags of a producer, the commits a host reports for a pull request).
- The Markdown sheet was checked with the repository's own scanner, not with a CommonMark engine.
- The audit counts per report, so two reports that cite one line count it twice in the total; the per-report counts are the ones to quote.
- The fixture worlds are built in each test module's own process (about 25 to 50 s for acme or a pair of the M16 workspaces on a busy machine), which is
  why the audit tests are in five modules: the parallel runner balances modules, not tests.

### Tests, speed

The Spring merge (`main` at 035de64) added the `spring` world and two Java conventions to the claim check (a declaration cited at its annotation line, an `import:`
claim cited at a same-package use of the name): the first run on the 20 Spring reports flagged both as structure problems, and each is now a test of its own.

Before the merge, on 3.12, 3.11 and 3.13 (parallel runner, 4 workers, a shared machine at load 5 to 9): 4,456 / 4,461 / 4,456 tests, all passing (6 / 5 / 6 skipped), 463 s /
455 s / 447 s. After the merge, on 3.12 (load 10 to 25: other agents were running suites): 4,800 tests, all passing (6 skipped), 771 s; 3.11 and 3.13 were not re-run after the merge.
The new modules: `test_eval_audit` 45 tests (no repository, under a second), `test_eval_audit_acme` 17 (35 to 55 s, nearly all of it building the acme world and running its 23
scenarios), `test_eval_audit_m16a` 2 (about 37 s), `test_eval_audit_m16b` 1 (about 53 s), `test_eval_audit_m16c` 1 (about 20 s), `test_eval_audit_perf` 2 (about 36 s: building a
repository of 150 files at two commits), `test_eval_witness` 39, `test_eval_witness_fixtures` 16 (about 26 s), `test_eval_sheets` 40 (both under a second). `tests/.timings.json` was not refreshed (a busy
machine would write the wrong weights).

### Review fixes

An independent review found the core sound, four majors and a set of minors. Each is a test. Where this section and the notes above disagree, this section is the code.

Majors:

- **A run that did not complete is not a pass.** A report with status `failed` or `timed_out` is `NOT AUDITED` (its own verdict, counted among the failed reports as `(N not
  audited)`), the exit code is 1, and `--allow unreadable` does not change that. Before, it had no citation and "passed".
- **Loading.** `audit.load` runs `report.check` as `sheet` does: a file that is not a report, or a completed one without its result lists (`{"schema":1,"status":"completed"}`),
  is exit 2. A completed report held in memory that lacks `change`, `ref`, `incompleteness`, `notes`, `findings`, `areas`, `branches`, `propagations`, `backend_calls` or
  `suggestions` is a structure problem.
- **An unreadable store is not an absence of notes.** A `note_store_unreadable` record in `model_notes.records` (and a `decision_store_unreadable` one in
  `review.decisions.records`) is counted as one `unreadable` with its detail, so the report is not a pass and does not read as one with no notes (`--allow unreadable` is the only way past it).
- **Witness and `--as-of`.** `release` findings are left out of every axis and a witness file that only a release finding cites is listed apart, like an exclusion. The
  output always says what `--as-of` does not pin (the tags of a producer, the commits a host reports for a pull request, the hosted test sources), and
  `docs/evaluation.md` says it.

Minors:

- `original` may not have a `repo` (it is read in the case's repository; it was resolved there and analysed there but could be named elsewhere).
- An object with `repo`, `path`, `line` and `ref` or `detail` but not all five evidence keys is a structure problem (call sites and contract reads have a `repo`, `path` and `line`
  and neither, and are not evidence).
- The two Java conventions of the claim check apply to `.java`, `.kt` and `.kts` files only; for a definition cited at its annotation line the file is read and a declaration
  below it (within 12 lines) must name the symbol; an operation claim's method is matched as a word.
- `GitReader` no longer chunks: `files_at` lists in chunks of its own and reads each commit with one `cat-file --batch` (a test with 520 paths at one commit counts one batch).
- One evidence walker: `payload.evidence_walk` (new, beside `evidence_in`, which is built on it) is what `collect` and the claim check use; `evaluate.remote_of` is the one remote
  lookup of the audit and the witness harness; `sheets` prints through `emit` too. The old `GitReader.remote`/`_find`, `_evidence_objects` and `witness._remote` are gone.
- A witness file that counts as a hit in two cases is listed ("counted for more than one case"); it is not refused (the sets of two different witnesses may overlap).
- **Paths are compared exactly** in one repository (`matches`): the boundary match is gone (a suffix match made `index.ts` a hit for every `index.ts`, and the two sides are
  paths of one repository). `docs/CONTEXT.md` says "exactly or at a path boundary"; exactly is the stricter half of it.
- A witness that holds the inverse of the original's diff for every file of the original is refused whether or not it touches other files (a revert and a fix beside it). Limit: an
  original of a line or two of very common text can be contained by a witness that is no revert.
- `witness` exits 1 when a case was not analysed (the counts are still printed).
- Tests: the distinct citations and the places by part of the report of each world are pinned (`support_eval.PINNED`: 432 / 246 / 239 / 166 / 359 / 296 distinct citations in 23 / 18 / 10 /
  14 / 25 / 20 reports), evidence under `backend_calls`, `suggestions` and contract reads is read, a reader that gives no answer is never `ok`, the verdict of a failed run is tested through
  the command, and a witness with a revert and a fix has its own fixture (`w07`, `refused-revert-and-fix.toml`).
- `sheet`'s error says what is wrong (`areas[1] has no 'id'`, `areas[0] is not an area as a report holds one`), not "the areas have no ids" for every `KeyError` and `TypeError`.
- Wording: "protected against formula injection" and "audited" in what people read.
- Merged with `main` at b5c23a2 (the hardening PR: unknown config keys refused, the blocking `routes_unmatched` record): the audit and witness fixture configs load as they are, and the audit
  treats `routes_unmatched` like any record (a test); the full suite passes after the merge (4,920 tests, 3.12, 336 s, load 3 to 5).
- The evaluation text moved into `docs/evaluation.md` (the file `docs/EVALUATION.md` is deleted: the two names are one file on a case-insensitive filesystem), into the three
  slots, and `CHANGELOG.md` has the M18 entry. The sentence about mining history for candidates is gone: a person writes the case file.
