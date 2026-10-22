# M2 brief: core model

Goal: the data model every later milestone builds on (context section 3), with the rules
of context section 4 that a data model can enforce built into it, so no adapter can break
them. Read `docs/CONTEXT.md` sections 3, 4, 5 and 7.3 first.

Python 3.11+, standard library only. No I/O in this milestone except JSON
serialisation helpers.

## Files

```text
reachmap/model.py         dataclasses and enums below
reachmap/identity.py      stable ids and fingerprints (hashlib.sha256, canonical JSON)
reachmap/wording.py       banned wording check
reachmap/incomplete.py    Incompleteness kinds registry and the note-and-record helper
tests/test_model.py
tests/test_identity.py
tests/test_wording.py
tests/test_incomplete.py
```

## Types (frozen dataclasses where possible, `slots=True`)

- `Read`: string constants `FOUND`, `ABSENT`, `UNREADABLE` (context 7.3). Also a small
  frozen `ReadResult(state, text, detail)` with constructors `found(text)`,
  `absent()`, `unreadable(detail)`; `text` is None unless found; constructing a
  found result with None text, or an absent/unreadable one with text, raises.
- `Evidence(repo, path, line, ref, detail)`: `ref` is a full 40-hex commit sha (validate:
  reject branch names, short shas, empty), `line >= 1`, `detail` the verbatim line
  (no trailing newline). Property `locator` returns `f"{ref}:{path}:{line}"`. Paths are
  repo-relative POSIX, no leading slash, no `..`.
- `Risk` and `Confidence`: enums `high`, `medium`, `low`. Two separate fields, never
  combined. No function anywhere averages or merges them.
- `Finding(kind, title, workflow, risk, confidence, confidence_basis, evidence, identity,
  is_exclusion=False)`. `identity` is a tuple of strings that names the claim (kind, repo,
  path, symbol, route ...), not the title. Construction rules:
  - `evidence` non-empty, else `ModelError` (rule 4.1). There is no way to make a
    Finding without evidence: no default, no setter.
  - `kind` from a registry (`direct`, `cross-repo`, `duplicate`, `exclusion`, `release`,
    `contract-relaxed`, `contract-tightened`, `removed_route_still_called`,
    `added_route_uncalled`, `guard-excluded`, `branch-missing`); unknown kind raises.
    Registry extendable by adapters through one function that rejects duplicates.
  - `is_exclusion=True` requires evidence too (an exclusion cites why).
  - title and every human-readable field pass the banned wording check.
  - property `id`: `identity.finding_id(kind, identity)`, never using title, risk,
    confidence or evidence refs.
- `Incompleteness(kind, subject, not_established, detail, blocks_selection)`: `kind` from a
  registry of stable codes (start with the ones `tests/fixtures/README.md` documents:
  `hub_cutoff`, `router_unmounted`, `not_distinctive`, `pre_change_unavailable`,
  `hop_cap`, `module_cap`, plus `read_unreadable`, `search_truncated`,
  `diff_count_mismatch`, `timed_out`, `source_unconfigured`). Each kind registered with a
  default `blocks_selection` and a template for `not_established`; `read_unreadable`,
  `search_truncated`, `pre_change_unavailable` and `diff_count_mismatch` are blocking and
  cannot be created non-blocking (raise). `source_unconfigured` (optional source missing)
  is non-blocking (context 3: required-and-unavailable vs optional-and-absent).
- `incomplete.record(analysis_or_list, kind, subject, **kw) -> Incompleteness`: the one
  function that creates the record and the human note together, so they cannot drift.
  Returns the record; the note is derived from the record (`note()` method), never stored
  separately.
- `Area(label, kind, repo, module, route_line: Evidence, chains: list[list[Evidence]],
  also_mounted: list[str], priority, by_name: bool)`. `kind` in `screen`, `api_route`,
  `task`, `job`, `topic`. `priority` in `must_test`, `sample`, `candidate`. Rules:
  `by_name=True` forces `priority="candidate"` (never must-test, context 7.5); an area
  that is not by_name needs at least one chain with at least one Evidence. Id from
  `identity.area_id(kind, repo, label)`.
- `assign_priorities(areas)`: must_test for every import-backed screen when the change
  reaches 8 or fewer import-backed screens, otherwise sample; by_name stays candidate.
  Threshold a named constant.
- `BranchState(branch, commit, state, added_lines_found, evidence)`: state in `present`,
  `partial`, `pre_change`, `diverged`, `file_absent`, `unreadable`. `unreadable` must carry
  a detail and is never treated as `file_absent`.
- `Decision(target_id, verdict, reason, author, channel, fingerprint, revision)`. Verdict
  `test`, `sample`, `skip` for areas; `accept`, `reject` for findings. `skip` and `reject`
  need a non-empty reason. Helper `describe(decision)` renders skip as "risk accepted, not
  tested" (rule 4.4). A Decision is never Evidence: `Finding` and `Area` constructors
  reject a Decision in evidence (type check).
- `Suggestion(source, case_id, title, matched_words)`: always labelled; a module-level
  constant `SUGGESTION_LABEL = "suggested, not verified coverage"`. Kept in a separate
  field from mapped tests.
- `Analysis(change, ref, branches, findings, areas, propagations, backend_calls,
  suggestions, mapped_tests, incompleteness, notes, revision, status)`. `status` in
  `completed`, `timed_out`, `failed`. Rules: a `timed_out` or `failed` Analysis exposes no
  finding count (property raises or returns None, never 0); `coverage_gaps(analysis)` is
  the one function that counts gaps and it never counts suggestions; `blocking()` returns
  blocking incompleteness records.

## identity.py

- Canonical JSON: `json.dumps(obj, sort_keys=True, separators=(",", ":"),
  ensure_ascii=False)`; sha256 hex, truncated to 16 chars for ids with a kind prefix
  (`f-`, `a-`).
- `finding_id(kind, identity)`: hashes kind and identity only.
- `fingerprint(finding_or_area)`: hashes the claim, excluding `Evidence.ref` (and
  excluding title counts): kind, identity, and per evidence (repo, path, detail stripped).
  Include path and stripped detail; exclude line and ref, so a line moving down because
  of an unrelated insertion does not make decisions stale. Record this in the module
  docstring.

## wording.py

- `BANNED = ("covered by", "already covers", "no need to test", "nothing to test", "safe")`
  plus close variants ("is safe", "safe to release", "fully covered", "no impact"). Match
  case-insensitively on word boundaries: `\bsafe\b` matches "safe" but not "unsafe",
  "safety" or "safely".
- `check(text) -> list[str]` returns the banned phrases found; `require_clean(text,
  field)` raises `WordingError` naming the field and the phrase.
- Applied to: Finding.title, workflow, confidence_basis; Incompleteness.detail and
  not_established; Area.label is exempt (it is a route path).
- Source code quoted in Evidence.detail is exempt (it is code, not our claim).

## Tests that prove it (one test per rule, named after the rule)

- Finding without evidence raises; exclusion without evidence raises.
- Two Findings with same kind and identity but different titles (counts) share an id.
- Fingerprint unchanged when only Evidence.ref changes, and when only the line number
  moves; changed when the detail line changes.
- Risk high with confidence low is representable and both survive a JSON round trip
  unchanged; no attribute or function combines them (grep test over `reachmap/`).
- Blocking kinds cannot be made non-blocking; `source_unconfigured` is non-blocking.
- ReadResult: unreadable is never equal to absent; constructors enforce text rules.
- Evidence rejects a branch name or short sha as ref.
- Banned wording raises for each phrase in a title; quoted code in Evidence.detail with
  the word "safe" does not.
- by_name area cannot be must_test; 8 screens all must_test, 9 all sample.
- Timed-out Analysis has no finding count.
- coverage_gaps ignores suggestions.
- Decision skip without reason raises; describe(skip) says "risk accepted, not tested".
- Decision passed as evidence raises.
- JSON round trip for every type (`to_dict` / `from_dict`), stable key order.

## Implementation notes

Where the code differs from, or fills in, the text above. Each is covered by a test.

- `by_name=True` with a priority other than `candidate` raises `ModelError`; it is not
  silently forced. `assign_priorities` counts import-backed screens but sets the priority
  of every import-backed area (routes, tasks, jobs, topics too).
- `ReadResult` has no truth value (`bool()` raises `TypeError`): test `.state`, or
  `is_found`, `is_absent`, `is_unreadable`. `.text` raises unless the read found something
  (`UnreadableError` for an unreadable read, which carries the detail). `text_or_none()`
  returns None only for an absent read. `record_if_unreadable(target, subject)` adds a
  blocking `read_unreadable` record and returns True. `ReadResult` is a small frozen class,
  not a dataclass, so that `text` can be a guarded property.
- `Finding.is_exclusion` is derived from the kind (`exclusion` and `guard-excluded` are
  exclusions; adapters register their own with `register_finding_kind(kind,
  is_exclusion=...)`). It is not a constructor argument. It stays in `to_dict`, and
  `from_dict` rejects a mismatch. Kind is hashed, so an affected and an excluded claim about
  the same place never share an id or fingerprint.
- `Risk` and `Confidence` are plain enums with a `rank` property; they never compare or
  mix.
- `Incompleteness.not_established` is derived from the kind template and the subject (a
  property, not a constructor argument), emitted by `to_dict`, and checked by `from_dict`.
  `detail` is wording-checked as written. New kinds `source_unavailable` (a configured
  source that did not answer, blocking) and `run_failed` (blocking); `timed_out` always
  blocks.
- `Analysis` is a builder, not a frozen dataclass: collections are read-only tuples, items
  go in through `add_finding`, `add_area`, `add_branch`, `add_suggestion`,
  `add_mapped_test`, `add_propagation`, `add_backend_call`, `add_note` and
  `add_incompleteness` (which `incomplete.record` uses), and `finish(status)` ends the run.
  Finding and area ids must be unique. `validate()` re-runs every check and `to_dict` calls
  it. A `timed_out` or `failed` analysis keeps no partial results: `to_dict` writes
  `findings` and `areas` as null and the analysis carries a blocking `timed_out` or
  `run_failed` record. `finish` never goes back to `completed`.
- `MappedTest(area_id, case_id, title, source, mapping)` is an addition: `coverage_gaps`
  needs to know which area a configured test case maps to, and `mapping` names the
  configuration entry, so a suggestion cannot pass for a mapped test. `coverage_gaps`
  returns the areas (`must_test` or `sample`) with no mapped test.
- `BranchState` has an extra `detail` field (required when unreadable). `present` needs
  `added_lines_found > 0`; `file_absent` and `pre_change` need 0.
- A `Decision` target id must match `^[af]-[0-9a-f]{16}$`. `describe` quotes and attributes
  the reason: `risk accepted, not tested (reason from <author>: "<reason>")`.
- Commit ids are 40 or 64 hex. Text with a lone surrogate (from `surrogateescape`) is
  refused; adapters decode with `errors="replace"`. `Evidence.detail` refuses a newline only;
  adapters strip the trailing `\r` of CRLF lines.
- The fingerprint keeps duplicate evidence and uses the fixed tags `finding` and `area`;
  golden tests pin two ids and two fingerprints.
