# M11 brief: review layer (revisions, decisions, staleness)

Goal: a person records QA decisions against an analysis; a rerun on a new commit carries
each decision forward when the claim it was made on is unchanged, and marks it stale when
the claim changed; decisions whose target disappeared are said, not dropped. Done when a
rerun on a new commit marks exactly the right decisions stale (fixture-driven). Read
`docs/CONTEXT.md` sections 3 (Decision, fingerprint rules), 4 (rule 4: a decision is risk
acceptance, never evidence), 7.6, 11, 12 (stale); the M2 brief and notes (Decision,
`identity.fingerprint`, describe); M9 and M10 briefs and notes (report JSON, file naming,
HTML page).

Python 3.11+ stdlib only.

## Files

```text
reachmap/review/__init__.py
reachmap/review/store.py       decision store: JSON files beside reports, append-only log
reachmap/review/revisions.py   report revisions: which earlier analysis this one revises
reachmap/review/carry.py       carry decisions forward: current / stale / orphaned
reachmap/report/decisions.js   (or inside report.js) the HTML decision controls and export
tests/test_review_*.py
tests/fixtures/review/         scenario pairs for staleness (see Tests)
```

## Concepts

- Analysis key: (workspace, repo, change identity). Change identity: for a PR, host and
  number; for compare and range, the spec with refs as written (`release/3...main`), not
  shas. Two analyses with the same key are revisions of each other; revision numbers
  increase by the order they were made (stored, not inferred from time stamps).
- Decision (M2 model): target id (`a-...` area or `f-...` finding), verdict (areas: test,
  sample, skip; findings: accept, reject), reason (required for skip and reject), author
  (free text name given by the person, never inferred from the OS user), channel stamp
  (`cli`, `html-import`, `runner`, `mcp:<client>` later), the target's fingerprint at
  decision time, the revision number it was made on, created time.
- Fingerprint: `identity.fingerprint` (claim, excluding commit and line), unchanged.

## Store (`store.py`)

- Location: `<reports dir>/.reachmap/decisions/<analysis key slug>.jsonl`, one JSON object
  per line, append-only; a later decision for the same target supersedes the earlier one
  (both kept). Writes are atomic per line (open append, write, flush, fsync); a corrupt or
  partial last line is reported (record kind `decision_store_unreadable`, blocking for the
  decisions view only) and never silently skipped as "no decisions".
- No secrets, no machine paths in the store.

## Carry forward (`carry.py`)

For each latest decision on a key, against the new analysis:

- target id present and fingerprint equal: `current`.
- target id present and fingerprint different: `stale` (show both fingerprints' claims if
  available from the earlier report: what changed in the evidence set).
- target id absent: `orphaned` (the area or finding no longer exists in this revision):
  listed in its own section, never dropped silently.
- A decision never clears a gap and never changes priority, risk or confidence (rule 4.4);
  a `skip` is rendered as "risk accepted, not tested (reason from <author>: ...)" (M2
  describe).
- A stale or orphaned decision is never applied; the area shows "decision needs review".

## Reports

- The report JSON gains `revision` (number, previous report file name and its head sha) and
  `decisions` (current / stale / orphaned lists with the decision objects), computed at
  render time from the store, so the analysis itself stays a pure function of the code.
  Bump the report schema minor version; M10 renderers show a Decisions section and per-area
  decision badges.
- Markdown and HTML show decisions as text; a reason is content (escaped, never markup).

## Recording decisions

- CLI: `reachmap decide <report.json> --target <id> --verdict skip --reason "..." --author "Name"`
  writes to the store; validates target exists in that report and the verdict fits the
  target kind; prints the describe() text.
- HTML report: per area and per finding, controls to choose a verdict with a reason and an
  author name; decisions are kept in page memory (and `localStorage` as a convenience,
  try/catch), and an "Export decisions" button downloads a JSON file (Blob + object URL,
  no network) containing: report key, report head sha, revision, and for each decision the
  target id, verdict, reason, author, the fingerprint exported from the report (not
  recomputed by the page), created time. The page never sends anything anywhere (CSP keeps
  `connect-src` absent).
- Import: `reachmap analyse ... --decisions decisions.json` (and `reachmap decide --import
  decisions.json <report.json>`) validates the file (schema, key match, target kinds),
  stamps channel `html-import`, keeps the exported fingerprint (so a decision made on an
  older revision is judged against what the person saw), and appends to the store. A file
  for a different analysis key is refused with a message.

## Tests (the done-when)

- Fixture pairs: build a scenario head A and a second head B on the same scenario branch
  (extend the fixture builder with `revisions` steps, deterministic) where B (1) changes an
  unrelated line in a chain file (fingerprint must stay: current), (2) changes the detail
  of one evidence line of one finding (stale), (3) removes one screen from reach (orphaned),
  (4) adds a new screen (no decision, shown as undecided), (5) moves lines down by inserting
  blank lines (current: line numbers are not in the fingerprint). Decide on every area and
  finding of A, rerun on B, assert exactly the expected current / stale / orphaned sets.
- Revision numbering: three runs on one key give 1, 2, 3; a different key starts at 1.
- Import keeps the exported fingerprint (a decision exported from A imported after B is
  judged stale/current against A's fingerprint, not B's).
- Store: corrupt last line is a blocking record for decisions; supersede order; no secrets.
- Rule 4.4: a skip never removes an area from must test or changes counts; coverage_gaps
  unchanged by decisions.
- HTML: decision controls build DOM with textContent only; export produces the documented
  JSON; the CSP still has no connect-src; the hostile fixtures from M10 still cannot break
  out, including a hostile reason string round-tripped through import and render.
- Wording check on every new generated string.

## Implementation notes

Where the code differs from, fills in, or goes beyond the text above. Each point is covered by a test unless it says otherwise.

Files added beyond the list: `reachmap/review/exchange.py` (the decisions file the page exports and `--decisions` imports), `reachmap/review/attach.py`
(what `analyse`, `decide` and `report` put beside an analysis: the key, the revision, the decisions), `reachmap/review/formats.py` (the shapes and `StoreError`), `reachmap/review/lock.py`, `tests/test_review_{store,carry,exchange,cli,report,fixtures,js,lock}.py`, `tests/test_claims.py`,
`tests/support_review.py`, `tests/js/review_harness.js` (runs the decision controls against a small fake DOM in node; skipped when node is missing),
`tests/fixtures/review/` (see the fixture pair below). The decision controls are inside `report/assets/report.js` (the brief allowed it), not a fourth
asset: the page keeps one inline script, so the Content Security Policy and its hash are unchanged in kind. Changed: `reachmap/cli.py` (`decide`,
`--decisions`, the review keys), `reachmap/model.py` (one function: `verdict_text`, the words of a verdict that `describe` already used),
`reachmap/report/{__init__,model,layout,summary}.py`, `reachmap/report/assets/report.{js,css}`, `tests/fixtures/build.py` (`revision_steps`,
`apply_revision`; nothing `build_all` does changed), `tests/fixtures/README.md`.

### Report JSON

(Rewritten in the second round: see "Review fixes". This is the contract as it stands.)

Each area and each finding carries its own `id` and `fingerprint` (the last two keys of its JSON, like `is_exclusion` on a finding); `from_dict` recomputes both from the object it read and
refuses a mismatch, so a report that was edited cannot say a claim has the fingerprint of another. After `status` the report has `schema_minor: 2` (`schema` stays the major version, 1; a reader
accepts any minor, a later one shows what it knows) and one object:

```text
review: { key: {workspace, repo, change, slug}, revision: N | null, previous: {number, file, head_sha} | null, decisions: {...} | null, as_of: time | null }
decisions: { current, stale, orphaned: [decision objects], undecided: {areas: [ids], findings: [ids]}, records: [...], as_of: time }
```

- `review.revision` is the run's place among the runs on the key (the number a person means). The top-level `revision` is the model's own (`Analysis.revision`) and stays 1; nothing reads it as the review number.
- `review.as_of` and `review.decisions.as_of` are one time: when the decision store was read to make `decisions`. What is in a file is what the store said then; a consumer that wants the decisions of
  now recomputes them from the store (`reachmap report` does, and writes a new `as_of` only when the answer differs).
- A run that failed or timed out has the key and `revision`, `previous`, `decisions` and `as_of` null, and no entry in the revision log.
- Evidence has an optional `claim` (below); the key is in the JSON only when it is set.

### Analysis key, revision log, store, lock

- The key is (workspace name, repository, change identity): `pr:<host>:<number>` (host as the config names it: `github`, `gitlab`, `git` ...),
  `compare:<spec as written>` or `range:<spec as written>`. The file name is readable parts plus an 8 hex hash of the whole key (`a/b` and `a-b` do not share a file).
- `<reports dir>/.reachmap/revisions/<slug>.jsonl`: one line per completed run (number, report file name, head sha, time). The number is the next in the log, so deleting the report files does not renumber, and
  a clock set back does not either. A log with a gap, a bad line or an unfinished last line is `StoreError`, exit 2, before the run starts: a number is never guessed again. A completed report is
  `<repo>-<kind>-<change>-<hash>-<12 hex of the head>-r<N>.json`, so a rerun on the same commit is a file of its own and the file a log line names never changes; a run that did not complete is
  `...-<12 hex>-failed-<8 hex of the run's time>.json` and never replaces a completed report.
- `<reports dir>/.reachmap/decisions/<slug>.jsonl`: ASCII lines (every other character is a JSON escape, so U+2028 in a reason cannot split a line), one write (repeated until every byte is taken) and an
  `fsync` per line, opened with `O_NOFOLLOW` where the platform has it (a store that is a link is neither written nor read), `v: 1`. A reason is at most 2,000 characters and an author name at most 100.
  Any line that cannot be read, an unfinished last line, a file that is not UTF-8 or cannot be opened: no decision is returned at all (a later line may have replaced an intact one, so showing the intact ones
  would show a replaced decision as current), `review.decisions.records` holds a blocking `decision_store_unreadable` record (the shape of an incompleteness record, worded by the tool, with the line and a reason in fixed
  words, never a piece of the file), no area or finding says it has no decision, and nothing is appended to that store until it is repaired. The record is not in `incompleteness`: it blocks the decisions view only
  and `propagations[0].complete` and every list of the analysis are the same (tested). I did not register the kind in the model (that would change `model.kinds()`). A decisions folder that cannot be searched is the
  same unreadable store, not an absent one. `.reachmap` that is a file stops `analyse` (exit 2) before the run.
- No source line, no path and no machine user name is in either file (tested): the author is the `--author` the person gave (required; it is never taken from the OS user).
- **The lock** (`review/lock.py`): `<reports dir>/.reachmap/lock`, `fcntl.flock` (exclusive) on POSIX, `msvcrt.locking` on Windows, advisory, a take that waits more than 60 seconds gives up with a message, a thread that
  holds it takes it again without waiting for itself. `analyse` takes it once, after the analysis has finished, for one short critical section that assigns the revision number (just before the write), carries the
  decisions, writes the report and enters the log; `store.append` (so `decide` and an import) and `revisions.record` take it too. It does not protect NFS (flock may not be honoured there: one machine per reports
  directory), and the Windows branch was written, not run (no Windows here). Tested with six processes racing to take the next revision (1 to 6, a log that reads; the same race without the lock loses a number), four processes
  adding decisions (whole lines), threads with their own descriptors (one at a time), a hold that never ends (gives up with a message), and three real `analyse` processes at once.

### Carry

`carry.carry(report, loaded, key, earlier)` reads the id and fingerprint of each area and finding of the report (a report without them is a `ReviewError`: exit 2 for `decide`),
takes the last decision of each target and sorts it. `current`/`stale` follow the report's order (areas, then findings), `orphaned` the order the decisions were written.
A decision made on a later revision than the report at hand is still judged against this report's fingerprint. A stale or orphaned decision is never applied. "What changed"
is the multiset difference of the (repo, path, line) triples of the evidence, at most 20 a side, and is shown only when the report of the revision the decision was made on is still
beside the new one in the reports directory and is that report (its own `revision` and head are the log's, and its fingerprint for the target is the one the decision holds);
otherwise `changes` is null and the page says that what changed is not known. An orphan gets its label the same way. The module has no way to touch a priority, risk,
confidence, count or gap (a test greps it; a test compares every analysis key of a run with all decisions made to a run with none).

### CLI

- `reachmap analyse ... [--decisions FILE]`: the file is read and its key checked before the run (a file for another key, or one that is not valid, is exit 2 and nothing is run or written).
  The file must say it was exported from a revision and a commit that the reports directory's log for the key has (else exit 2 before the run: its fingerprints cannot be told to belong to this change).
  It is imported when the run completes, inside the critical section and before the report's decisions are made, so the report shows it; a run that does not complete says on stderr that nothing was imported.
  The summary says `Imported N decisions from FILE (channel html-import); M replaced a decision in force`, and for each imported decision older (by its own time, compared as times) than the decision in force
  for its target there is a warning on stderr (exit 0, nothing refused: the imported one is the last line and so the one in force).
  The summary gains `Revision N of this change; it revises revision M (file)` from the second run on, and `Decisions: 4 current, 2 need review, 2 orphaned` (or that they could not be read)
  only when there is something to say, so every summary that was printed before is the same.
- `reachmap decide REPORT.json --target ID --verdict V [--reason TEXT] --author NAME`: validates that the target is in the report and the verdict fits its kind (areas: test, sample, skip;
  findings: accept, reject), a reason for skip and reject, the limits above; records the report's own fingerprint and revision; channel `cli`; prints the `describe()` text with odd
  characters written out. `--list` prints `id  kind  label  state` for every target (the ids are not in the Markdown, so a person needs a way to find them; this is an addition).
  `--import FILE` imports the file the page exports. Exactly one of the three modes; everything wrong is exit 2 with nothing written.
- `reachmap report` makes `decisions` again from the store beside the report when there is one (so a decision made after the analysis shows in the rendering; the JSON file is not
  rewritten). Without a store the report's own `decisions` are shown, so a report moved away from its folder reads as it was made.

### The decisions file (HTML export, import)

`{"format": "reachmap-decisions", "version": 1, "report": {"key": {workspace, repo, change}, "head_sha", "revision"}, "exported_at", "decisions": [{target_id, verdict, reason, author, fingerprint, created_at}]}`.
The reader is exact about keys (a `channel` in a decision is not a key of the format: the import always stamps `html-import`), the key of the analysis it is read for, a target id of an area or a
finding with a verdict that fits it, a reason for skip and reject, a 64 hex fingerprint, times, no lone surrogate (the store cannot hold one: refused with a message), at most 5,000,000 bytes (checked on the bytes, before they are decoded: a file that is too big and not UTF-8 says too big) and 10,000 decisions. Messages name the decision and the rule and never repeat the file. An import may name a target the report no longer has: it is kept, and shows as orphaned. The same
file imported twice adds its lines twice and the last one is in force. The exported fingerprint is kept, and the revision of the file's `report` is the decision's revision (tested: a decision
exported from A and imported when B is the analysis at hand is judged against A's fingerprint, and only then is it stale or current).

### Reports

- The view model reads `review.decisions` and the ids on the areas and findings tolerantly (a retyped key is a render or a `ReportError`, never another exception; `report.check` refuses a wrong shape of the review keys by name).
  A report with no decision (the key missing, or every list empty and no record) has no section and no line: every report made before this milestone, and the Markdown goldens, read as they did.
- With any decision: a **Decisions** section after the findings (current, needs review with both short fingerprints and the lines that changed, orphaned, not decided, and the unreadable record),
  and a `Decision:` line on every area and finding (the decision, `decision needs review` with "It is not applied.", or `No decision recorded.`; none of these when the store was unreadable). A stale decision is worded against the revision the report is:
  `the claim on revision N differs from the claim Quinn decided on at revision M` when they differ (an older report is read too), `the claim differs from the claim ...` when the report is the revision decided on.
  A skip reads `risk accepted, not tested (reason from <author>: "<reason>")`: the verdict is `model.verdict_text` (a test holds the layout to it), the reason is JSON-quoted content, an unknown
  verdict is content in a code span. Areas stay under Must test or Sample, the counts table and every finding's risk and confidence are the same with and without decisions (tested).
- Every new heading and sentence is in `layout` and passes the wording check; a decision's strings are `Content` (the audit that marks every string of a report and spies on `wording.require_clean`
  covers reports with decisions, and a reason that says "it is safe" renders verbatim).

### HTML page

- Per area and finding (the `li` with `data-a` or `data-f`): a verdict (areas: test, sample, skip with its label saying risk accepted, not tested; findings: accept, reject), a reason, the person's
  name, Record and Clear buttons and a status line. Without `review` in the report, or an id and fingerprint on every area and finding (an older one), there is no control and no export button.
- State is in page memory and in `localStorage` under `reachmap-decisions:<slug>:<head sha>:<revision>` (a page of another commit or revision does not read it), always inside `try`; what it reads back
  is checked item by item (exact keys, a target the report has, the report's own fingerprint, a verdict that fits) and anything else is ignored.
- **Export decisions** writes the file above with the fingerprints read from the areas and findings of the report, as a Blob and an object URL on a link made for the click (`reachmap-decisions-r<revision>.json`),
  removed after it. This is the one link the script makes, so two M10 test rules were narrowed, each to exactly that: the forbidden-pattern test strips the two lines
  `var link = env.doc.createElement("a");` and `link.href = env.URL.createObjectURL(blob);` after asserting each occurs once (`EXPORT_LINK_LINES` in `test_report_html.py`), and the URL pattern's `url\(`
  is `\burl\(` (CSS), because `createObjectURL(` is not a URL. The Content Security Policy is unchanged (no `connect-src`), and the page makes no request: the node harness traps `fetch`, `XMLHttpRequest`
  and `WebSocket`. I loaded a generated page in the browser pane: the script ran under the policy, the controls were there, a hostile reason was recorded and exported as text, no violation event fired
  (`localStorage` was unavailable on the data URL the pane uses, which exercised the try/catch).

### Fixture pair (the done-when)

`tests/fixtures/review/s01-shared-component/` holds `r2`, a second commit on `scenario/s01-shared-component` (see `tests/fixtures/README.md`). `build.apply_revision` commits it in a copy of the built
repositories; the test analyses A, decides on every area and finding of A through `reachmap decide`, applies `r2`, analyses again in the same reports directory (same cache: the git layer fetches the new commit)
and asserts the sets. Existing repositories, branches and shas are unchanged (a test compares every ref of every repository between the shared build and the revised copy: only the scenario branch moved, and its
new head's parent is the old head). The result, areas and the findings about them alike (eight decisions on A, four areas and four findings):

| Target | Edit in `r2` | Result on B |
|---|---|---|
| `/billing/invoices/:invoiceId` | an unrelated import line in the page | current |
| `/cases/:caseId` | the alias import of the badge becomes a relative path to the same file, and three blank lines go above it (the cited line moves from 4 to 7) | current (the claim of the import is its resolved target and names) |
| `/billing/invoices` | the page imports the badge from `@/components`, a new re-export file | stale (shows the removed and the added import line) |
| `/cases` | its route is removed | orphaned (named from revision 1's report) |
| `/billing/settlements` | the page starts using the badge | undecided (no decision, said so) |

So current is 4 (two areas, two findings), stale 2, orphaned 2, undecided 2 (one area, one finding), and every decision of A is in exactly one list. (The first version of the pair had the alias import
as its stale case: see "Review fixes".)

### Existing tests changed (nothing about what they check)

- `test_cli.py` and `test_report_cli.py`: a listing of the reports directory ignores the dot folder (`.reachmap/` is the review layer's log; the tests meant "no temporary file is left"); the report name
  of a completed run ends `-r1.json` and of the unresolved failed one `-unresolved-failed-<hash>.json`; the M9 `--as-of` test and the running-again test leave out the `review` object (the second run is revision 2 of
  the same change, and read the store at another time).
- `test_scenarios_e2e.py`: `strip_run` also blanks `review.as_of` and `review.decisions.as_of`; the areas of the report are compared with `Area.to_dict()` (they carry an id and a fingerprint now).
- `support_cli.to_run` reads a report a test has edited with the id and fingerprint check off (`from_dict` verifies them otherwise).
- `test_model.py` (the key order of an Evidence and of an Area and a Finding), `support.py` (`ev(claim=)`).
- `test_report_html.py`: the two narrowings above.

### Choices (small ones I made; say if you want any of them the other way)

Report keys flat at the top level beside the analysis, not a nested `review` object; `schema_minor` as a separate integer; `decide` takes the report as a positional argument and a mode (`--list`, `--import`, or a single
decision); one author name and one reason typed per decision (no default author); an unreadable revision log stops the run (exit 2) where an unreadable decision store does not (it only blocks the decisions view); the
import is applied only for a run that completes; revision entries are written after the report file; a decision's text limits (2,000 and 100); `Decisions` shown only when there is something to show.

### Known limits

- Two `analyse` runs writing to one reports directory at the same moment can take the same revision number (the log is read, then appended; no lock). The store and the log are for one person at a time.
- The revision log and the store are beside the reports: a report moved to another folder carries its own decisions as they were but cannot be added to from there without its store.
- A rerun on the same commit replaces the report file of the earlier revision (the name is the commit's), so "what changed" for a decision made on that revision cannot be shown (and none is needed: the claim is the same).
- A pull request's key uses the host kind from the config (`github`, `gitlab`, ...), not the repository's URL; the repository name in the config is what tells two repositories apart.
- The page cannot tell the person which decisions of an earlier export were already imported; it holds only what was recorded in that page.

### Tests, speed

2,342 tests (2,088 after M10; 254 new: store 42, carry 21, exchange 17, cli 59, report 33, fixtures 20, js 25, lock 9, claims 9, and 19 in the identity, model and payload modules), all passing on 3.11, 3.12 and 3.13. The node tests run when node is installed (v25 here).

| Python | serial (`unittest discover`) | parallel (`run_parallel.py`, 4 workers) |
|---|---|---|
| 3.11 | not run | 163.2 s |
| 3.12 | 291.9 s | 113.5 s |
| 3.13 | not run | 156.8 s |

All four runs were made one after the other with nothing else running but me writing notes (light), the 3.12 parallel run first. The 3.11 and 3.13 times are higher than the first round's (139.8 and 144.9 s) for 150 more tests and a
heavier `test_review_cli`; the machine is shared and I did not repeat them. The M10 figures were 124.2, 100.4 and 134.7 s parallel and 268.0 s serial.
The new modules cost, in a parallel run on 3.12: `test_review_cli` 22.7 s (about thirty analyses of the fixtures, three at once in a subprocess race), `test_review_fixtures` 3.9 s, `test_review_report` 2.9 s,
`test_claims` 2.7 s (all 23 scenario reports), `test_review_js` 1.4 s, `test_review_lock` 0.8 s (processes), the rest under 0.1 s. `tests/.timings.json` was refreshed with `--record`.

Mutations that were tried and fail the tests: a fingerprint that includes the line number (the blank-lines area goes stale), orphans dropped, stale read as current, the revision always 1, an import that does not keep the exported fingerprint.
