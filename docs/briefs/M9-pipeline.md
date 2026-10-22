# M9 brief: pipeline and findings

Goal: one deterministic pipeline, `reachmap analyse`, that takes a change in any
configured repository and produces an `Analysis` (M2 model): change set, pre-change
version, branch matrix, findings with risk and confidence kept separate, exclusions with
evidence, areas with priority, cross-repo propagation, and every incompleteness record.
Done when every fixture scenario passes end to end through `reachmap analyse` (the CLI,
not only the library), including `branch_state` (s13). Read `docs/CONTEXT.md` sections
1, 2, 3, 4, 5, 7, 11; `tests/fixtures/README.md`; and the M2 to M8 brief implementation
notes (model, records, config, change sets, adapters, walk, entry points, propagation).

Python 3.11+ stdlib only. No model calls.

## Files

```text
reachmap/pipeline.py       build(workspace, repo, change spec, options) -> Analysis; the only orchestrator
reachmap/branches.py       branch matrix
reachmap/findings.py       finding builders: one function per finding kind, each from evidence
reachmap/cli.py            argparse: `reachmap analyse`, `--version`; exit codes
reachmap/__main__.py       `python -m reachmap`
tests/test_pipeline.py, test_branches.py, test_findings.py, test_cli.py
tests/test_scenarios_e2e.py   every scenario through the CLI against expected.json
```

## Pipeline (`pipeline.build`)

Steps, each producing evidence or a record (context section 2), never a silent skip:

1. Resolve the change set (M4 `changeset.resolve`): `--pr N`, `--compare base...head`,
   `--range a..b`, optional `--as-of` (ISO time: pin every ref as of then). Refusals
   (`ChangeSetRefused`) end the run with status `failed` and the blocking record; the CLI
   exits non-zero and still writes the JSON with no findings count (rule 4.9).
2. Pre-change version from the change set; if unavailable, its blocking record is already
   there; steps that need it say they were skipped through that record (no second record).
3. Branch matrix (`branches.py`): for each configured branch pattern of the changed repo
   (`heads` via M4, slashes kept), read every changed file at the branch tip and classify
   per README `branch_state`: `present` (every added line found), `partial` (some),
   `pre_change` (none of the added lines and the removed lines still there), `diverged`
   (neither: the file changed differently), `file_absent` (git positively says the path is
   absent at that tip), `unreadable` (any failure, recorded, blocking for that branch only:
   it does not block area selection, but the matrix row says unreadable). Lines are
   compared by exact text after stripping trailing whitespace, per file, counting
   multiplicity. A branch with no changed file readable gets one row per file. The change's
   own head branch is excluded. Evidence: for `present`/`partial`, the found lines with
   their line numbers at the tip sha. This is how a fix that reached release branches and
   never `main` shows (s13).
   - A `branch-missing` finding: a change present on at least one release branch and
     `pre_change` on the primary branch (s13 shape), or the reverse for a merged change
     not on configured release branches; risk high, confidence by how many files agree.
4. Language analysis of the changed repo (M5/M6 via the adapter registry by `language`).
5. Entry points (M7) and propagation across edges (M8).
6. Findings (`findings.py`), each built by one function from evidence objects (no free
   text titles: the title is generated from the identity and counts, and passes the
   wording check):
   - `direct`: a changed symbol reaching an area in the same repo (one per area).
   - `cross-repo`: an area or route in another repo reached through edges (one per area).
   - `exclusion`: an importer or call site excluded by narrowing or a guard, with its
     evidence (`is_exclusion` derived from the kind; guard exclusions use `guard-excluded`).
   - `contract-relaxed` / `contract-tightened`: from M8 contract changes, per consumer.
   - `removed_route_still_called`, `added_route_uncalled`: from M8, with the README risk
     and confidence.
   - `release`: from the package edge: consumers that will be exposed on adoption, or are
     exposed (adopted), per branch.
   - `duplicate`: out of scope for now (needs a parallel-implementation detector); do not
     register a finding nobody builds.
   - Risk and confidence are separate fields set by documented rules per kind (a table in
     `findings.py` docstring), with `confidence_basis` saying why (import-backed, by name,
     guard proven, undecidable, partial read). Never averaged.
   - Identity: stable parts only (kind, repo, path, symbol, route, endpoint), never line
     numbers or counts (M8 review lesson).
7. Areas: from M7/M8 Reached/Area results; by-name candidates stay `candidate`;
   `assign_priorities` (8 or fewer screens: must test, else sample). Areas whose chain
   passes through an incomplete read carry that in `confidence_basis` or a field.
8. Unit tests per repo with a command per repo from config `tests.command` (`{paths}`
   placeholder), for the "For the author" view later.
9. Incompleteness: all records from every step, deduplicated by (kind, subject); the
   aggregate `complete` false if any record makes it so (M8 invariant).
10. `Analysis.finish(status)`; a run ceiling: `limits.timeout_s` enforced by M12's runner
    process; inside `build`, a cooperative deadline checked between steps returns
    `timed_out` with no count.

## CLI (`reachmap analyse`)

```text
reachmap analyse --config reachmap.toml --repo acme/api (--pr N | --compare A...B | --range A..B)
                 [--as-of ISO] [--out DIR] [--format json,md] [--quiet]
```

- Writes `<out>/<repo-slug>-<change-slug>-<short sha>.json` (the report contract,
  versioned `"schema": 1` at the top, produced by `Analysis.to_dict` plus metadata: tool
  version, config path hash not content, run time, inputs). Markdown and HTML come in M10;
  for now `--format json` only, other values rejected with a clear message.
- Prints a short summary to stdout: areas to test (count and the first few), findings by
  kind, blocking records first, never "safe" or "nothing to test" (wording check on the
  summary text too). With zero areas and no blocking record it says "No areas reached by
  this change were found; see the report for what was examined." (that sentence passes the
  wording check; keep it).
- Exit codes: 0 completed; 2 usage or config error (message names the key); 3 change set
  refused or run failed; 4 timed out. Completed-with-blocking-records is 0 but the summary
  starts with the blocking records.
- No secret in output: the config's token values never printed (M3 guarantees).

## End-to-end scenario harness

`tests/test_scenarios_e2e.py`: for every scenario, write a fixture `reachmap.toml`
(M3 fixture config with `${REACHMAP_FIXTURES}` set to the built fixture dir), run the CLI
in-process (`cli.main([...])`) and also once as a subprocess (`python -m reachmap`) for
one scenario, read the JSON report, and check it against expected.json with the existing
`scenario_support.check` (map report JSON to the harness's shapes; do not weaken checks).
No skips remain, including `branch_state`. Every Evidence in every report resolves at its
pinned sha. Determinism: running the same scenario twice gives byte-identical JSON except
the run-time metadata field.

## Tests beyond the scenarios

- Branch matrix: present, partial, pre_change, diverged, file_absent, unreadable; branch
  names with slashes; a renamed file.
- Findings: identity stability (moving a line does not change the id), wording check on
  every generated title, risk/confidence table, exclusion findings carry evidence.
- CLI: each exit code; refusal still writes JSON without a count; summary wording;
  `--as-of`.
- Timed-out run: cooperative deadline returns `timed_out`, JSON has null findings.

## Implementation notes

Where the code differs from, fills in, or goes beyond the text above. Each point is covered by a test unless it says otherwise.

Files added beyond the list: `reachmap/payload.py` (the JSON form of the adapters' and edges' results, of a whole propagation, and a walk
for the evidence in a report), `reachmap/__main__.py`, `tests/test_payload.py`, `tests/support_cli.py` (runs the CLI on the fixtures and reads a
report back as an `ss.Run`). Files changed: `reachmap/changeset.py` (`resolve(as_of=)`), `reachmap/propagate.py` (`Run(as_of=)`,
`propagate(as_of=)`), `reachmap/gitlayer.py` (`Git.files_at`, and the zone in `--before`), `tests/scenario_support.py` (the branch matrix of every
run and the `branch_state` check), `tests/test_scenarios_py.py`, `test_scenarios_js.py`, `test_scenarios_cross.py` (the `branch_state` skip is gone:
no scenario skips any type now), `tests/support_cross.py` (`as_of`).

### The pipeline (`pipeline.py`)

- `build(workspace, repo, ChangeSpec, Options)` returns an `Analysis`. `ChangeSpec` is exactly one of `pr`, `compare` (`a...b`), `range` (`a..b`), checked
  when it is made; `Options` has `as_of`, `cache`, `git_env` (tests cut git off from the user's config), `host` (a host adapter for a pull
  request), `timeout_s` and `clock`. A usage error (an unknown repository, a bad time, a bad spec) is a `ValueError`; everything else that goes
  wrong is an `Analysis` with status `failed` or `timed_out`, which keeps no partial results (the model's rule): a refused change set, a failure
  in a step (an unsupported language, an unexpected exception: the record's detail names the exception type and a scrubbed message, never a
  traceback) and a missed deadline. The records made before the failure stay.
- A change set that was never resolved has no commit to cite, so the `ref` of such a run is None (the model allows it only for a run that did not complete), the subject of its record is what was asked
  for (`compare main...topic`) and `change["resolved"]`
  is False (the model needs a full sha).
- Steps: resolve, branch matrix, language analysis, entry points, edges (`propagate.walk_edges`, run by the pipeline so that a deadline can be
  checked between the steps), findings and areas, unit test commands. The deadline is cooperative (`limits.timeout_s` unless `Options.timeout_s`)
  and the record says which step it stopped after.
- The report content: `change` is the change set (spec, shas, how each was found, the pull request's number, title, state and refs, every file
  with its role, status and counts); `propagations` holds one object (`payload.propagation_to_dict`: every pass, hop, area route, http edge,
  consumer, contract change and read, unit test and unit test command, and `complete` with its `reasons`); `backend_calls` the handler modules a
  client change reaches; `branches`, `findings`, `areas`, `incompleteness` and `notes` are the model's lists. Whether the run is complete is
  `propagations[0].complete`: False when any record blocks selection or a stated limit was hit (M8's invariant, applied once over every record of
  the run, the branch matrix's included). A failed or timed out run has no propagation.
- Records: one per (kind, subject), the first wording stays, a blocking one is never lost to a non-blocking one of the same kind and subject, and
  the order is blocking first, then kind, then subject (the same whatever order the steps ran in).
- A candidate found by name that has the id of an area reached through imports is listed once, as the area reached for sure, and a note says so.
- Configured test and context sources are not read by this version: a note says how many, so that no suggestions is not read as none exist.
- `--as-of` pins the refs named in a compare or range, the branch tips of the matrix and the primary and branch heads read in other repositories.
  It does not move the commits a host reports for a pull request (a sha is a pin already; a note says so), and the producer's tags are listed as
  they are now (the package edge does not filter tags by time): both are limits.
- Unit tests: per repository the paths of the unit tests the change reaches, with `tests.command` (`{paths}` replaced by the shell-quoted paths; a
  command without the placeholder is kept as it is; a command that is not a string is ignored; a repository with no command still lists its tests).

### The branch matrix (`branches.py`)

- Per branch and file the states of the README plus `not_compared` (a file the change only removes lines from where they are gone, so there is no
  added line to find and `present` would be a claim without a line to cite; a name that is not valid text; a change with no text line; a tip with no
  line to cite). A `not_compared` file is left out of the branch's row and said in a note per branch, never dropped quietly.
- A branch is one row over its changed files: all the same state is that state; a mix is `partial` when an added line was found in some file and
  `diverged` when none was (old, absent or changed another way). The detail lists every file. An unreadable file is its own row, besides the
  row over the readable ones. A deleted file that is absent at a tip is `file_absent` (the deletion landed or the file never existed there: the row
  cannot tell which) and one that is there with its removed lines is `pre_change`.
- Evidence: the found lines at the tip (added lines for `present` and `partial`, removed lines for `pre_change` and `diverged`), verbatim, at most
  50 per file (the detail says when it was cut); with none to cite, the first line of the file as the tip has it. A renamed file is read at its new
  path and, when that is absent, at its old path. Blank lines are not compared (a blank line cannot be cited and matches anything).
- Rows are the primary branch first, then the others by name; the change's own head branch (the right side of a compare or range, the host's head
  ref of a pull request) is left out only when its tip is the change's head, and a note says so (see "Review fixes"). Branch heads that cannot be listed give one `unreadable` row per configured pattern. With `as_of`, a branch with
  no commit at or before the time has no row and a note names it.
- `Git.files_at` reads all changed files of a tip with one recursive `ls-tree` per 200 paths, one prefetch and one `cat-file --batch` (the first
  version read each file with `file_at`: 832 git processes for s16, 4.7 s; now 0.3 s). Each path gets the answer `file_at` gives it, tested path by path.
- Limit: another edit that adds or removes copies of the same text at a tip shifts the count (see "Review fixes": an added line is found only above
  the copies the file had before the change).

### Findings (`findings.py`)

One function per kind; the identity is `(kind, ...)` of stable parts (the areas: repository, kind, label; exclusions: repository, changed path,
importer path, reason; contract changes: producer, path, symbol, consumer; releases: producer, consumer, branch; branches: repository, the missing
branch, which way); titles are generated from the identity and counts and everything quoted from code passes `findings.clean` (a route called
`/safe` reads `/s_afe` in a title and is intact in the area and the identity). The confidence is the weakest of what the claim rests on, with every
reason in `confidence_basis`, the weakest first. Risk and confidence per kind:

| kind | risk | confidence |
|---|---|---|
| `direct` | medium | high: import-backed chain, read whole; medium: an undecidable call site (treated as affected) or a partial read; low: by name |
| `cross-repo` | medium | as `direct`; medium for an http match that is a literal in a parameter, a tie or of unknown method, and for a contract read by name; low by name or a partial client URL |
| `exclusion` | low | high (the importer's import line shows it names only other exports) |
| `guard-excluded` | low | high (the guard line and the call line) |
| `contract-tightened` | high | per consumer: high when every read is typed, medium when one is by name, low with no read found (the consumer's pin is cited) or a name that is not distinctive |
| `contract-relaxed` | medium | as `contract-tightened` |
| `removed_route_still_called` | high | low (M8's values) |
| `added_route_uncalled` | low | medium (M8's values) |
| `release` | high when adopted; medium on adoption or undecided | high when decided from the pin and the tag's ancestry; low when not decided |
| `branch-missing` | high | files that agree: all high, at least half medium, fewer low |

- Every area has exactly one `direct` (changed repository) or `cross-repo` finding (a candidate found by name too, with low confidence); the
  evidence of both is the area's best chain, after the definition line of the changed symbol (see "Review fixes"; a candidate with no chain cites its route line). The changed body lines are not in it: they would make a
  decision stale on any edit of the changed function.
- An exclusion's evidence is the adapter's. Exclusions of every non-candidate pass are findings (the web pass of an api change too).
- A contract finding exists per (changed member, consumer repository) for each of `contract-tightened` and `contract-relaxed` in its kinds; a member
  that is neither (a default change, an added optional field, s12's generic name) gives none. `release` findings are per consumer and branch with a pin
  line to cite (a consumer with no pin has none). `branch-missing` is made when the primary branch is `pre_change` and a release branch is `present` or
  `partial`, or when the primary is `present` and a release branch is `pre_change` (one finding per such branch); nothing is said when the primary has
  no readable row.
- `duplicate` is not built and nothing registers it anew (the model's registry already lists it from M2).
- The e2e mapping of `finding` entries: expected.json's `finding` entries are about `removed_route_still_called` and `added_route_uncalled` (README, "Kinds
  used"), so `complete_for: finding` (s10, s22) is checked over those two kinds; the report also has `direct`, `cross-repo`, `exclusion` and the other
  kinds, which the scenarios make no claim about. Stated here so that it is a decision and not a weakening: the alternative (no `direct` or `cross-repo`
  finding in s10 and s22) contradicts the brief.

### The CLI (`cli.py`)

- `reachmap analyse` as in the brief; `--config` defaults to `reachmap.toml`, `--out` to `reports` (made when missing), `--format` takes `json` and
  names `md`, `markdown`, `html` as not available yet (exit 2). The file name is `<repo>-<kind>-<change>-<hash>-<12 hex of the head>.json` with every character
  outside `A-Za-z0-9._` made a `-` (a run of dots too) and an 8 hex hash of the repository, the whole change and the time; a run that never resolved is
  `...-unresolved.json`. The write is a temporary file renamed
  into place.
- The report is flat: `schema`, `tool`, `run` (`started_at` and `elapsed_s`, the one thing that differs between two runs), `inputs` (repository,
  change, as-of, formats, workspace name and the sha256 of the resolved config path), `counts` (null unless completed: areas, must test, sample,
  candidates, findings, exclusions, records, blocking records), then everything `Analysis.to_dict` has.
- The summary lists at most 5 areas (`AREAS_SHOWN`), then findings by kind, the branches in one line, the number of non-blocking records, and, only when
  the run is not complete, that the lists may be incomplete; a complete run never says "complete" (it would read as coverage). With blocking records and
  no area it says none were established (not the "No areas reached ..." sentence). Failed and timed out runs print to stderr, with no count.
- `main` takes `env`, `stdout`, `stderr`, `git_env`, `clock`, `now` and `retain` so a test can run it in process.

### Fixes in earlier milestones' code (found while doing this one)

- `Git.pin` with `as_of` before March 1973 ignored the time: git reads `--before=@86400` as another kind of date and returned the newest commit. The
  argument now carries the zone (`@N +0000`); tested (`test_as_of_in_1970_...`).

### Known limits

- The e2e harness reads the origin repository's `ChangeAnalysis` and `EntryResult` from the first pass of the report; passes of other repositories are
  in the report and checked through the propagation only (as in M8).
- A change set of hundreds of files makes a large report (every importer of every changed file is in `propagations[0].passes[*].analysis`).
- `--pr` was exercised against a fake host only (no network in the suite); `gh` and `glab` are as M4 left them.
- Test sources and context sources are not read (M15); `suggestions` and `mapped_tests` are always empty.
- One run at a time on a git cache directory (M4's limit).


### Review fixes

An independent review of the first version found one blocker and five majors; each is a test (the probes of the review are cases in
`test_branches.py`, `test_pipeline.py`, `test_cli.py` and `test_scenarios_e2e.py`). No expected.json changed.

- **Blocker, the branch matrix counted a line anywhere in the file.** An added line is now found at a tip only above the copies the file had before the
  change: `found = clamp(tip_count - (pre_count - removed_count), 0, added_count)` per distinct added text, with one extra `files_at` at the pre-change
  commit (the pre-change file of a rename is read at its old path; a file the change adds had none). A text the change also removes cannot be told from
  what was there and is left out; a file whose every added text is like that is `not_compared` (a note says why), and so is every file of a change with no
  pre-change version (no row, one note). The cited lines are, among the tip's copies of a text, those nearest to the line numbers the change put its
  copies at (copies are interchangeable: the claim is the count, the cited line is where the extra copy most likely is). s14 and s22 (their own
  `naive_match` lines) are `pre_change` on every branch, a null guard backported to a release branch (its lines also occur in another function) no longer
  makes `main` present, and s13 is unchanged. Limit: another edit that adds or removes copies of the same text at a tip shifts the count.
- **Major, a fix that adds a file made the primary branch `diverged`.** A file the change adds that is absent at a tip is that file's `pre_change` (the
  change is not there). `file_absent` stays for a modified, renamed or deleted file whose path is not in the tree. The row of a branch where every changed
  file is a new one that is absent reads `file_absent` (nothing to cite), and the findings go by what the files say together (`Matrix.kind_of`), so
  `branch-missing` fires. `Matrix.state_of` is gone.
- **Major, the head branch was left out by name.** It is left out only when its tip is the change's head (a pull request from a fork's `main` keeps the
  primary row), and a note names every branch that is left out.
- **Major, exceptions after the steps escaped `build`.** `build` never raises for a run: making the report is guarded (it validates `to_dict`), and a failure
  there rebuilds a failed `Analysis` with a minimal change (`failed_analysis`, also what the CLI uses when `build` or the report fails). File names that are
  not UTF-8 are shown with their bytes escaped (`redact.display`: the change's files, the JSON codec, exclusion findings, the matrix notes); the CLI always
  writes a report and exits 3 for a failed run.
- **Major, the null id was cited.** `Analysis.ref` is None for a run that did not complete and never resolved its change (the model; `finish` takes the
  subject of the record, here `compare main...nope`); the all-zero id is refused as an `Evidence.ref`, a `BranchState.commit` and an `Analysis.ref`.
- **Major, `--as-of` was untested.** Tests: the s13 change by commit with a time between the two commits of `release/1.x` and `release/1.2` (the rows read
  the older tips, `branch-missing` follows the time), and the api's pin of the contracts package moving from 1.10.0 to 1.9.0 with a time just before its
  bump. Mutations that pass `None` to the matrix or the walker fail them.
- Minors: a time before 1970 is refused (exit 2); the failure record names the step in progress (`RuntimeError during the branch matrix: ...`), with home
  and temporary paths replaced by `~` and `<tmp>` (`redact.hide_machine_paths`); the record order is tested through the pipeline with duplicate and late
  blocking records; the e2e harness compares `report["areas"]` with the propagation's areas after `unique_areas`; no report or summary holds a home
  directory, a temporary folder, the working directory or a `file://` remote (a local remote is shown as the repository's name; records are rewritten the
  same way); a run that did not complete has null `branches`, `propagations`, `backend_calls`, `suggestions` and `mapped_tests` besides null findings and
  areas (the model clears them and refuses to add them); `--quiet` still prints blocking records on stderr; an `--out` that cannot be written is exit 2; an
  area is as sure as its best chain (the maximum over the chains of each chain's weakest link) and cites that chain; the tag limit of `--as-of` is said in
  the notes when a package edge is followed; `payload.decode` refuses a missing key even for a field with a default (only the fields left out of the JSON
  form are filled); the run state is a small dataclass, the identical `except` clauses are one, and a report name carries an 8 hex hash of the repository,
  the whole change and the time, so `a...b` and `a-b` do not share a file.
- **(a) What `complete_for: finding` means** is written in `tests/fixtures/README.md` (the listed kinds plus the mirror kinds `direct`, `cross-repo`,
  `exclusion` and `guard-excluded`, which must be exactly the reported areas and the exclusions stated) and the end to end harness enforces it for every
  scenario (`scenario_support._check_report_findings`, with mutation tests). This replaces the earlier reading in "Findings" above.
- **(d) Area findings** cite the definition line of the changed symbol (the `def`, `class` or `function` line at the head, or at the pre-change commit for a
  removed symbol; not a decorator above it) first, then the chain, and leave out the changed body lines, even the first hop of a change inside the entry
  file; so a later edit of the function's body does not change the fingerprint. Which symbol: those of the changed file the chain starts from (the importer's
  import of it, or the changed file itself), for a contract change those of the model fields the chain reads, otherwise the first changed symbols of the
  change (at most three). A change with only module-level symbols cites the chain alone.

### Tests, speed

1,898 tests (1,623 before M9; 275 new). Measured with nothing else running, all passing:

| Python | serial (`unittest discover`) | parallel (`run_parallel.py`, 4 workers) |
|---|---|---|
| 3.11 | 263.1 s | 129.2 s |
| 3.12 | 267.3 s (before the review fixes: 262.6 s for 1,817 tests; the reviewer measured 273 s) | 100.9 s (the reviewer's prototype: 131 s) |
| 3.13 | 266.9 s | 123.7 s |

The serial time did not fall although duplicate work was removed, because the review fixes added 81 tests; the baseline before M9 is 219.3 s on 3.12.
What was done about the time:

- `tests/run_parallel.py` runs the modules in worker processes (default 4), balanced by `tests/.timings.json` (`--record` refreshes it; it only balances),
  prints the time of every module and the failures with their text, and exits 1 on a failure or a worker that dies. `python3 -m unittest discover -s tests`
  still runs everything in one process (CI). Documented in `CLAUDE.md`. The long pole is `test_js_adapter` (about 55 s): modules are not split.
- One pipeline run per scenario for the whole suite: the pipeline keeps what its steps made on request (`Options.retain`, `cli.main(retain=)`), the end to
  end scenarios run it through the CLI, and `scenario_support.run_scenario` builds its `Run` from that run instead of making a second one.
- `test_fixtures` reuses the shared fixture build for its read-only classes (two builds fewer, and one build in the determinism test); the slow-git tests
  make the slow git do only the call they test (the checkout, the commit or the pin is made with a working git first), so their timeouts are 0.3 s and not 1 s
  (0.2 s was not tried: a working git call under four busy workers can take longer than that); the pipeline tests that need a run of their own use the
  one-repository scenario s13 and share builds.
- `Git.files_at` took the s16 pipeline run from 4.7 s to 0.3 s (M9, before the review).

