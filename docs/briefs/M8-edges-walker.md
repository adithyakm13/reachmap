# M8 brief: edges and the cross-repository walker

Goal: follow a change across repositories along the configured edges, hop by hop, until
it reaches entry points in other repositories, with evidence at every hop, an
incompleteness record for every hop that could not be completed, and the propagation
rules of context 7.5 enforced in one place. Done when a contract field change in the
fixtures reaches the frontend screens through the service (s11), with evidence at every
hop. Read `docs/CONTEXT.md` sections 3, 4, 5 (all), 7.1, 7.4, 7.5; `tests/fixtures/README.md`
(`http_edge`, `consumer`, `contract_read`, `finding`, chains across repos, consumer
policy, subject formats for `hop_cap` and `not_distinctive`); the M3 to M7 briefs with
implementation notes (config edges, git layer and pins, adapters base, walk and entry
points, completeness on `EntryResult`).

Python 3.11+ stdlib only.

## Files

```text
reachmap/propagate.py              the cross-repo walker: hops, caps, pins, candidates, records
reachmap/versions.py               version parsing and ordering (PEP 440 subset, semver), tag containment
reachmap/edges/__init__.py         EdgeAdapter protocol, Hop type, registry by edge kind
reachmap/edges/http.py             http: client calls to server routes, both directions
reachmap/edges/package.py          package: producer tags to consumer manifests per branch, adoption
reachmap/edges/contract.py         contract: changed schema fields to consumer code reads
reachmap/adapters/http_client/endpoints_file.py   endpoints-file client adapter (JS/TS)
tests/test_versions.py, test_edge_http.py, test_edge_package.py, test_edge_contract.py, test_propagate.py
tests/test_scenarios_cross.py      cross-repo scenarios against expected.json
```

## The walker (`propagate.py`)

- Input: workspace config, the change set (M4) in repo R, R's language analysis (M5/M6)
  and entry result (M7). Output: a `Propagation` with hops, cross-repo areas (screens and
  routes in other repos with chains that span repositories, in the README chain shape),
  http edges, consumers, contract reads, findings inputs (removed route still called,
  added route uncalled), and records.
- A hop: edge kind, from repo, to repo, the target revision read (pinned sha per branch
  where branches matter), evidence at both ends, and the seeds handed to the target
  repo's own pass (files and lines, or names, or routes). The target repo's language
  adapter and entry-point adapter then run on those seeds exactly as for a change (the
  consumer lines of a contract read are the "changed lines" of the consumer's pass;
  context 7.4 "hand those lines to the consumer's own impact pass").
- Hop cap (`limits.hops`): edges past the cap are not followed; record `hop_cap` with
  subject `edge:<kind>:<from>:<to>` in the edge's declared direction (s21), non-blocking.
- Module cap per hop and hub rule: reuse M7's walk inside each repo; the walker never
  passes through a hub.
- Distinctiveness before any name-based match (`limits.distinct_max`, by definitions, via
  the language adapter's `definitions`), recorded as `not_distinctive` with the count
  (s12, s19 already language-level).
- By-name matches are candidates (`Area.by_name=True`, priority `candidate`), shown apart,
  never must test.
- A failed read on an edge is an incompleteness on that edge (`read_unreadable` or the
  edge's own kind), never "not adopted", "no caller" or "no consumer".
- Every hop pins the target repo's revision it read: the consumer's primary branch for
  unreleased changes, each configured branch where the edge needs it (package pins), the
  merge base or head for the changed repo.
- Cycles between repos terminate (a repo plus seed set is processed once).
- Order is deterministic (sorted by repo, path, line).

## http edge (`edges/http.py` + endpoints-file client)

- Client side (frontend repo): the endpoints-file adapter reads `http_client.path`
  (`src/api/endpoints.ts`): exported string constants (including a constant over two lines),
  template literal builders with parameters (`(id: string) => \`/api/billing/invoices/${id}\``),
  and string concatenation of constants where statically evaluable. A client call site is
  a line in client code that references an endpoint constant or calls a builder; the
  method comes from the call (`apiGet`, `apiPost`, `fetch(..., { method })`, `axios.get`):
  read the client wrapper names from the call sites, and when the method cannot be told,
  match any method and say so (`method_unknown` on the edge, not a record).
- Matching (context 7.4): split into segments; a client parameter segment matches only a
  route parameter segment; a client literal may match a route parameter (marked
  `literal_in_parameter`); the most specific route wins (more literal segments); a tie is
  ambiguous and every tied route is kept with a note. Query strings are ignored, trailing
  slashes normalised.
- Direction web to api (a frontend change reaches a client call): edge from the client
  line to the matched route; the route's handler modules are reported as backend calls
  (context section 1 item 5, up to three hops into the handler's module imports via the
  Python adapter's importers in reverse, i.e. the modules the handler imports).
- Direction api to web (a backend change reaches a route): find every client call whose
  URL matches the route, then hand the client file to the web pass (M5 callers and M7
  screens), producing screens with chains that start in the api repo and cross at the
  client line (s06, s11).
- Removed routes (`routes_changed` from M7): a removed route still matched by a client
  line on the frontend's primary branch is a finding input `removed_route_still_called`
  with evidence at the client line and the removed decorator line at the base (s10). An
  added route no client matches is `added_route_uncalled` (s22). Both carry the README
  risk/confidence values as data for M9.
- A client constant whose value cannot be evaluated statically: the call is a candidate
  (by name), recorded as `client_url_partial` (non-blocking), never matched silently.

## package edge (`edges/package.py`, `versions.py`)

- Producer versions: tags `tag_prefix` + version on the producer repo (`git ls-remote
  --tags` via M4). A placeholder version in the producer manifest (`0.0.0`) is ignored:
  real versions are tags (context 5).
- Consumer pins per configured branch of the consumer: read `manifest` at each branch
  head (M4 `heads` + `file_at`), apply `pin_pattern` (one group). Unreadable manifest:
  `read_unreadable` on that edge and branch, never "not adopted". Manifest absent at a
  branch or no pin line: say so as a consumer state `no_pin` with evidence of absence
  established (file read, pattern not found), not a record.
- Version ordering: parse versions (`1.10.0` > `1.9.0`), never compare strings; a version
  that does not parse is `unparsed`, recorded, and adoption is not decided.
- Adoption (README consumer policy): change not contained in any released tag
  (unreleased): each consumer once, from its primary branch, `on_adoption`. Change
  contained in a released tag: one entry per configured branch, `adopted` when the pinned
  tag contains the change commit (git ancestry: `merge-base --is-ancestor`), otherwise
  `on_adoption`; `contains_change` true/false. Version numbers never imply containment
  (context 5: read ancestry at the tag).
- `import_marker`: the consumer files that import the producer package (Python import of
  `acme_contracts`) are the consumer's entry for the contract edge.

## contract edge (`edges/contract.py`)

- Changed fields come from the producer's language analysis (`Class.field` symbols in
  `contract.root`), with the change kind: optional to required (`contract-tightened`),
  required to optional or removed default (`contract-relaxed`), removed, added, type
  changed. Classify by comparing the before and after field annotation and default via
  `ast` (Pydantic: `X | None`, `Optional[X]`, `= None`, `Field(default=...)`).
- Distinctiveness: a field name defined more than `distinct_max` times across the
  producer's models (definitions, not references) is not distinctive: record
  `not_distinctive` (subject the name, `definitions` count) and match nothing by that
  name (s12 `interval`). `id` likewise.
- Consumer reads: in each consumer (package edges from this producer), files that import
  the producer package (`import_marker`) are scanned for attribute reads `.<field>` and
  keyword uses `<field>=` and subscript `["<field>"]` in code (via `ast`, never comments or
  strings in comments; s11 comment excluded, s18 `limit` only in a comment). Each read is a
  `contract_read` with evidence: the import line and the read line. Reads in files that do
  not import the package are by-name candidates, shown apart (unless the name is
  distinctive and the file receives the model through a typed parameter: out of scope;
  keep them as candidates).
- Each consumer read line is handed to the consumer's own pass (M6 Python adapter changed
  symbols from those lines, then M7 routes, then http to web screens), with the hop cap
  applied across repos (s21 with `hops = 1`: api reads and route, no web screen, `hop_cap`).
- Consumer pins and adoption from the package edge are attached to each consumer.

## Scenario harness

`tests/test_scenarios_cross.py` checks `http_edge`, `consumer`, `contract_read`,
`finding` (kind, risk, confidence, evidence), cross-repo `screen` and `route` entries,
`hop_cap`, and the complete `not_established` lists, for s05, s06, s10, s11, s12, s17,
s18, s21, s22 (and any other scenario with those types). Remove the corresponding skips
explicitly from the JS, Python and entry skip tables. After M8 the only remaining skips
should be `branch_state` (M9) and anything M9 builds (findings risk wording). Cross-check
http matches with `claims.py`'s URL fit oracle.

## Tests beyond the scenarios

- versions: ordering, prerelease, `v` prefix, unparseable, `1.10.0` vs `1.9.0`.
- package: unreadable manifest is an incompleteness not "not adopted"; branch with slash;
  tag containment by ancestry, not number.
- http: two-line constant, builder with parameter, literal-in-parameter, ambiguous tie,
  method unknown, trailing slash, removed route still called, added route uncalled.
- contract: tightened vs relaxed classification; field in a comment; generic field;
  read via keyword argument; read in a file that does not import the package (candidate).
- walker: hop cap recorded with the right subject; cycle between repos terminates; a
  failed read on an edge is recorded on that edge and the propagation is incomplete;
  every Evidence in every chain resolves at its pinned sha (cross-repo chains included).

## Implementation notes

Where the code differs from, fills in, or goes beyond the text above. Each point is covered by a test unless it says
otherwise.

Files added beyond the list: `reachmap/adapters/http_client/__init__.py` (the client adapter protocol, `ClientUrl`, `ClientCall`,
the registry by `http_client.kind`), `tests/support_cross.py` (small multi-repository workspaces built with one `git
fast-import` per repository, a TOML text that names them, `propagate` of a change). Files changed: `reachmap/gitlayer.py`
(`Git.tags`, `Git.is_ancestor`), `reachmap/walk.py` (`WalkResult.visited`), `reachmap/adapters/__init__.py` (`Reached.sources`),
`reachmap/adapters/fastapi/__init__.py` (fills `sources`), `reachmap/adapters/base.py` (`analyze(..., distinct=False)`,
`not_distinctive_detail`), `tests/scenario_support.py`, `tests/test_scenarios_js.py`, `tests/test_scenarios_py.py`,
`tests/test_gitlayer.py`.

### The walker (`propagate.py`)

- `propagate(workspace, repo, change, git=, language=, entry=, analysis=, reach=, limits=)` returns a `Propagation`: `passes`, `hops`,
  `reached` (areas of every repository with their chains composed across repositories and one `assign_priorities` over all of them),
  `http_edges`, `backend_calls`, `consumers`, `contract_changes`, `contract_reads`, `contract_candidates`, `findings` (the inputs of
  `removed_route_still_called` and `added_route_uncalled`), `unit_tests`, `records`, `unfollowed`, `unread_entry_points`, `complete`
  and `reasons`. The caller may hand over the changed repository's own pass (the scenario harness does); otherwise it is made. The
  records go where the git layer's go (`git.target`), the changed repository's own records included.
- A pass is one repository's language analysis and entry points (`Pass`). The changed repository's pass is `depth` 0; a pass started
  by a hop is one deeper. An edge is followed from a pass only when `pass.depth < limits.hops`; the package and the contract edge
  between one pair are both depth 1, so `hops = 1` in s21 follows contracts to api and worker, and the http edge from the api (depth 2)
  is the one that is capped. `limits.hops` of 0 is not valid config (it must be positive); the walker still honours a `Limits` with 0
  that a caller passes (tests do).
- `hop_cap` is recorded (non-blocking, once per edge, subject `edge:<kind>:<from>:<to>` as declared) only when the pass had something to
  carry over that edge: an adapter's `applies` says so without reading another repository (a route reached, a changed field of a model,
  a client call to look for). An http edge declared web to api is named that way when the change travels from api back to web.
- Seeds: the consumer line of a contract read and the client line of an http call are handed over as a synthetic `ChangeSet` (the same
  revision as before and after, the lines added; `kind="seed"`), so the other repository's language adapter places them in
  declarations by number and gives changed symbols, callers, narrowing, unit tests and entry points exactly as for a real change.
  A seed pass does not count definitions of its changed names (`analyze(distinct=False)`): it follows code, it does not link code by
  a name. One pass is made per hop (a contract read group, a client line); `(repo, sha, path, lines)` is processed once, which ends
  a cycle (tested with two repositories that hand each other seeds for ever: three passes, three hops).
- Order is deterministic: passes by depth, repository, path and line; edges by kind and name; results sorted.
- A declared edge of a kind with no adapter (`event`, `library`) is the non-blocking record `edge_unfollowed` (subject `edge:<kind>:<from>:<to>`),
  made once when a pass reached code at either end of it; a pass that reached code in a repository whose entry-point kind has no adapter
  (the Celery worker) is `entry_points_unread` (subject the repository). Both make `complete` False as `hop_cap` does. `Propagation.unfollowed`
  and `unread_entry_points` are derived views of the same events. (First version: fields only, no records; see "Review fixes".)
- A language with no adapter (`UnsupportedLanguage`) on an edge is a blocking `edge_incomplete`, not a crash and not an empty answer.
- `Propagation.complete` is False when a pass, a hop or a route table is incomplete, a hop cap or a hub or a module cap was hit, a
  stated limit was recorded, or a read on an edge failed; `reasons` says why. Any record that blocks selection makes it False, whatever
  its kind (`pre_change_unavailable` too: removals and narrowing were not computed). A short list is never "the" list then.
  By-name candidates do not make it incomplete.
- Chains across repositories. A pass started by a hop has the hop's `chain` as its prefix and every area chain of the pass starts with
  it. For an http hop from a server area the chain is `crossing_prefix` (what leads from the original change to the end of the server's
  part) and then the client line; for a contract hop it is the import line and the read line (the consumer's entry for the edge).
  `crossing_prefix` shows the way inside the server repository without the route's own evidence: nothing for a change inside the
  entry file itself; the walk's imports for JavaScript; for Python the import line and the first line where the importer uses what
  arrives, and inside a file the lines where one declaration uses the next on the way from the seed to the name the importer takes
  (`python_segment`). That is the shape s11 expects: import, read, `references` line, import, handler line, client line, page import, routes
  import, `<Route>`. The in-file steps follow identifiers (a declaration that references the name), the same reading the narrowing uses.
  `Reached.sources` (the walk chains behind each chain of an area) is what makes this possible; only the first chain of an area is shown
  across an edge.
- Areas reached twice (by two seeds, or by the changed repository and a seed) are merged by identity, the chains kept in order of
  discovery, the first one primary. By-name areas are kept apart (`by_name=True`, priority `candidate`), after the import-backed ones.
- Pins: a hop lists the revisions it read (`Pin(repo, ref, sha, role)`): `primary` for a consumer's or a client's primary branch, `branch` for
  each configured branch the package edge reads (released changes), the changed repository is read at the head of the change and its
  removed routes at the pre-change commit.

### http edge

- `adapters/http_client/endpoints_file.py` reads `http_client.path` with the JavaScript tokenizer: exported constants, constants made of
  other constants with `+` or in template literals, builders (`(id) => ...`, arrow functions with an expression or a single `return`,
  `function` declarations with a single `return`), builders called inside other endpoints, `as const`, constants over several lines and
  template literals with nested templates. A part that cannot be evaluated is an unknown atom: the URL is partial (a leading unknown
  part stands for any run of segments, one inside a segment for one segment). `via` evidence is every non-blank line of the declaration
  and of every constant and builder it uses.
- Calls are found through the JavaScript adapter's importer search: a file that imports the constant or builder from the endpoints file
  (any alias, a namespace import; a type-only import is not a call) and refers to it, outside comments and strings. Test files are not
  client code. A cut or failed importer search, a file that does not lex cleanly, a re-export of the endpoints file (users past it are
  not searched), a dynamic import without bindings and an endpoints file that is absent, unreadable or does not parse make the answer
  incomplete, with the reason (`ClientCalls.complete`). The method comes from the enclosing call: a verb at the end of the callee name
  (`apiGet`, `api_delete`, `axios.post`, `http.put`), `fetch(url)` and `axios(url)` with no options are GET, a `method:` string in an
  options object is read, options that are a variable or hold a spread are unknown, any other wrapper is unknown.
- Matching is `edges/http.match_url` and `best_matches`: the route with the most literal segments equal to the client's wins (exact beats
  literal in a parameter because it has more), a tie keeps every tied route (`ambiguous_with`), `{p:path}` takes the rest of the URL, an
  unknown part of a route fits one segment. The README's oracle (`claims.best_routes`) takes every literal-in-parameter route when no
  route fits exactly; the matcher refines that by literal count (the brief's rule); they agree on every client URL of the fixtures at
  main and at every scenario head (checked). A client URL with an unknown part has no best route: every route it can fit is a candidate,
  its client line is handed over as a candidate (areas reached through it are `by_name`), and `client_url_partial` is recorded once per
  call site (non-blocking).
- A removed route that a client still names is an `HttpMatch(removed=True)` with the route's evidence at the pre-change commit (the
  whole removed route is pinned there) and, when no route fits the URL after the change, the `removed_route_still_called` input (high
  risk, low confidence, evidence: the client line and the removed decorator line). When the URL now fits another route (a literal in a
  parameter, or another method of the path), the edge says so (`rematched`) and the detail of the finding names the rematch: whatever
  answers the URL now was not written for the call. The client is followed to its screens either way.
  `added_route_uncalled` (low risk, medium confidence, evidence: the new decorator line) is said only when every http edge into the
  server was read completely and none matched the route.
- Backend calls (client change): the modules of the handler's file by its imports, up to three hops, in the server repository, from the
  Python adapter's module resolution (the package `__init__` of `from pkg import module` is listed too, as it runs). Cut at
  `limits.module_cap` per depth with `module_cap` recorded, subject `backend:<repo>:depth:<n>`.
- The calls a client change reaches are those in a changed declaration, in a declaration that uses one (`own_reach`), in a changed
  file whose statement at module level changed, and in the files the walk reached (`WalkResult.visited`: the declarations that carry the
  change, or the whole file when the whole module is reached). Only the changed repository's own pass looks for backend calls.

### package edge

- Tags are listed with `Git.tags` (`ls-remote`, peeled, by prefix, optionally fetched in one request), containment is
  `Git.is_ancestor` (`merge-base --is-ancestor`). The producer's `contract.tag_prefix` is exact when configured; without it a tag
  with or without a leading `v` is read. Every tag that parses as a version counts as a release (a pre-release tag too).
- A pin is the first line of the manifest that the pattern matches (more than one different line is said in the detail). It is read
  with `Git.file_at` (no language adapter needed). A manifest that is not on the branch, or has no line that matches, is `no_pin`
  (an established absence, no record). A pin that parses and that no tag names, or that names several, is `undecided` with
  `pin_unresolved` (non-blocking); one that does not parse is `undecided` with `version_unparsed` (non-blocking); unparsed beats
  unreleased: the brief says adoption is not decided. An unreadable manifest is `unreadable`/`undecided`, a blocking `edge_incomplete` and
  the git layer's `read_unreadable`, and the hop is incomplete. Listing the branches or pinning the primary branch can fail the same way.
- The change is "released" when some tag of the producer contains its head commit (the commit the range or compare ends at). If a tag's
  ancestry cannot be read and none is known to contain it, the consumers are listed from the primary branch as `undecided` and the edge is
  incomplete, never `unreleased`. Version ordering (`versions.py`) orders tags (lowest containing tag first) and is not used to decide
  adoption.
- Consumers are listed for a change that has source files in the producer; a change to a test alone has no consumers and no hop.

### contract edge

- `ContractChange` per member of a model under `contract.root`: `kinds` (the first is the main one: `contract-tightened`,
  `contract-relaxed`, `type_changed` (the annotation without its None alternatives), `default_changed`, `added`, `added_required`,
  `removed`, `member_changed`, `class_changed`, `unclassified`), `before` and `after` as `FieldShape`, `distinct` and `definitions`.
  Both directions at once (a default removed and an option of None added) lists both kinds.
- Distinctiveness uses the language adapter's `definitions` of the member's leaf name at the commit its symbol sits in; the record is made
  with the adapter's own wording (`not_distinctive_detail`), so the adapter's record and the edge's are one. A count that could not be
  made whole is not distinctive and the propagation says so.
- Reads are looked for on the consumer's primary branch (also for a released change; the pins of the other branches are the package
  edge's), by `git grep` for the name and `ast` for the confirmation: attribute (also assignment and deletion), keyword, string
  subscript, and `getattr`/`hasattr`/`setattr`/`delattr` with a literal name. A class-level change (a removed or changed class)
  is read by identifier and by the import of its name; a statement outside every class in the contract root is a blocking
  `contract_change_unscoped` (which consumers it affects cannot be told). Test files of the consumer (its `tests.markers`) are not read.
  The import cited is the one of the class that holds the field when the file has several imports of the package.
- A group of reads in one top-level declaration is one hop with all its lines as the seed. `ContractRead.pin` and `adoption` come from the
  consumer of the same branch.
- A cut search (`search_truncated`, blocking, the git layer's), a failed search, a symlinked `.py` file, a consumer file that does not
  parse and a consumer that cannot be pinned each make the edge incomplete with a blocking `edge_incomplete`.

### New record kinds

Registered in `reachmap/edges/__init__.py`. Non-blocking: `client_url_partial`, `client_url_unmatched`, `version_unparsed`, `pin_unresolved`,
`tag_prefix_unmatched`, `edge_unfollowed`, `entry_points_unread`, `contract_construction_unseen`. Blocking: `edge_incomplete` (subject
`edge:<kind>:<from>:<to>`) and `contract_change_unscoped`. Subjects are in the README table of `tests/fixtures`.

### The harness

- `ss.run_scenario` runs the walker after the in-repo pass (with the changed repository's own pass, its git layer and so its record list
  handed over; `limits.hops` of a scenario is applied to the walker only) and keeps it in `Run.propagation`; `ss.check` checks, besides
  everything before, the entries about other repositories (`screen`, `route`, `unit_test`), `http_edge`, `consumer`, `contract_read`,
  `finding`, `hop_cap` among the records, and that `Propagation.complete` follows the expected records. A removed route's evidence
  is read at the base as a whole (the README reads a mount line at the head: the same line, another sha). `complete_for` compares across
  repositories. `skipped_types` no longer skips an entry because it names another repository.
- The skip tables of `test_scenarios_js.py` and `test_scenarios_py.py` are empty except for s13's `branch_state` (M9); the test of
  the entry-point scenarios has none of its own. s16's `must_exclude: finding` is checked (a removed route is not claimed without a pre-change
  version).
- Scenarios that pass for each type: `http_edge` s05, s06, s10 (and the exclusions of s05, s06, s08, s21); `consumer` s11, s17, s21;
  `contract_read` s11, s21 (and the exclusions of s11, s12, s18); `finding` s10, s22 (and s16); cross-repo `screen` s06, s10, s11
  and `route` s11, s21; `unit_test` of the api in s11; `hop_cap` s21; the complete `not_established` list of all 23 scenarios.
- `tests/test_scenarios_cross.py` cross-checks with `claims.py`: the URL fit of every client URL against every route at main and at every
  api scenario head, the client call lines, the contract reads of the five contract scenarios, the consumers (pins in the manifests, ancestry
  with `git merge-base --is-ancestor`, the listing policy), and that the harness fails when a read, a consumer, an edge, a screen, a chain
  part, a finding or the hop cap is wrong.

### Choices

- `Hop` and `Seed` are plain data; an edge adapter has `applies` and `follow` instead of the context's `propagate(...)`, so the walker can
  say `hop_cap` without reading the other repository.
- One hop per contract read group and per client line, and the first chain of a route area, keep the chains one-to-one with the evidence
  the scenarios expect; several routes tying for one client line give several hops that share one seed (one pass).
- A removed route's finding needs the route to fit the client's best match at the base and nothing at the head; a route that is
  merely renamed is a removal plus an addition, and a client that still names the old path is the finding.
- `Propagation.unfollowed` and `unread_entry_points` are derived from records now (see "Review fixes").

### Known limits

- Event and library edges have no adapter; Celery entry points are not read (a worker pass has no areas).
- The client side is the endpoints-file adapter only: URLs written inline in `fetch` calls, OpenAPI clients, a base URL or proxy prefix that the
  server's mounts do not contain (`/api` stripped by a dev proxy), and calls made through a function that takes the URL as an argument
  are not seen as calls to that URL. A re-export of the endpoints file hides its users (the answer says so).
- The server side is whatever the entry-point adapter lists (FastAPI): routes it cannot compose are partial and only candidates.
- Method detection is by the name of the call and the options literal: a wrapper with an unusual name has no method (it matches every
  method), and `fetch(url)` is taken as GET.
- Contract reads are Python consumers read with `ast`, in files that import the package. Receivers are proven by annotations and bindings
  inside one function (no type flow across calls, attributes of `self` or fields of other classes); an unproven read of a distinctive name that the
  consumer defines nowhere is still a read, so `resp.status` on a response is read as the `status` of a model when the producer defines
  `status` once: the cap and the consumer's own definitions do not see it. Reads through `**payload`, dict keys, dynamic names and generated
  code are not seen. A candidate read is listed and not followed.
- Reads are looked for on the consumer's primary branch and, for a released change, on every other branch the package edge found
  adopted; a branch that has not adopted it is known by its pin, not by its code.
- A pin is one regular expression over one manifest; lock files, several manifests and a pin through a constraints file are not read.
  A manifest that names the package where no line matches (a range such as `>=1.9`, an extra, another spelling) is `unmatched`, with
  `pin_unresolved` and an incomplete edge, never an established `no_pin`.
- Every tag that parses is a release; pre-release tags count. A producer whose release line is on a branch with no tag for it has its change
  listed as unreleased until a tag contains it.
- The way inside a Python repository shown in a chain follows identifiers inside files; state shared between functions that is only
  mutated, and decorators other than the route's, are not followed (the same limits as the narrowing).
- The backend call list is Python only and follows `from`/`import` statements that resolve to files of the repository; imports inside
  functions count, imports by `importlib` do not.

### Review fixes

An independent review of the first version found no blocker and seven majors; each point below has a test (the probes of the review are
tests in `test_edge_http.py`, `test_edge_contract.py`, `test_edge_package.py`, `test_propagate.py` and `test_scenarios_cross.py`).

- **Stated limits are records.** `edge_unfollowed` (subject `edge:<kind>:<from>:<to>`) and `entry_points_unread` (the repository) are
  non-blocking, made once, and make `complete` False. They fire in every api scenario whose change touches a declaration (the `event` edge
  api to worker) and, with the worker, in s11 and s21. The `not_established` lists of s06, s07, s08, s09, s10, s11, s13, s15, s21, s22 and
  s23 gained exactly those records (the diff is in the report of the change); the README table has both kinds, and the other kinds M8
  makes have their subject formats there too.
- **A blocking record is never beside `complete=True`.** The walker checks every record of the run at the end: a blocking one adds a
  reason and `complete` is False, `pre_change_unavailable` included (decided: removals and narrowing were not computed, so the answer is
  not whole; the harness expects it for s16). Tested over every registered kind. An adoption that could not be decided is a blocking
  `edge_incomplete` (the consumer stays `undecided`, never `on_adoption`); a manifest that names the package where no line matches is
  `unmatched` with `pin_unresolved`; an incomplete definition count is an `edge_incomplete` on each contract edge of the producer.
- **Methods.** A verb comes only from a known client shape (`axios.get`, `http.post`, `api.put`, `client.delete`: a member of a client object;
  `apiGet`, `http_post`: a wrapper whose last word is exactly get, post, put, patch or delete behind another word; `fetch`/`axios(config)` with
  a `method` option). Never HEAD or OPTIONS from a bare name, never the end of any other name (`fetchWithOptions`). A method that fits no
  route of the path falls back to the routes of the other methods, flagged (`fallback`, `method_unknown`) with a note. A reached client
  call that fits no route is `client_url_unmatched` (non-blocking, `<repo>:<path>:<line>`); while any client call into a server fits no route,
  or a client scan was incomplete, no added route is called uncalled (mutation M13 is a test now).
- **Removed routes.** A removed route that was a client's best match at the base is always `removed_route_still_called` (high risk, low
  confidence), with the rematch (`GET /x/{id}` as `literal_in_parameter`, or another method) in the detail. The finding's identity holds
  the client's path, endpoint name and method, never a line number.
- **Contract reads, backing.** A read is `typed` when its receiver is provably the model (`reachmap/edges/receivers.py`: annotated parameters
  and variables in every spelling, variables bound from the model or its constructors, loop and comprehension variables over
  `list[Model]` and the like, `isinstance`, a keyword on the model, import aliases followed, scopes that do not leak); otherwise it is `name`
  based and followed only when the name is distinctive and the consumer defines it nowhere (class fields, assignments, `self.x =`, defs,
  classes: `defines_name`); otherwise a candidate, shown apart; a string subscript on an unknown receiver is always a candidate; a name that is
  not distinctive is followed by its typed reads only. s11, s17 and s21 are unchanged; no fixture read became a candidate.
- **Constructions.** An added required field is followed to the calls of the model class (through the file's import of it), to
  `Model.model_validate(...)` and the other constructors, and to `Model(**data)`: reads of the form `construct`. The constructions that
  cannot be seen (a factory, a call through another name) are the non-blocking `contract_construction_unseen` and the run is not complete.
- **Released changes** are searched on the primary branch and on every consumer branch whose adoption is `adopted`, in that order, identical
  (path, line text) reads dedupe to the first, and each read's evidence and seed are pinned at the branch it was read on (the package edge
  runs before the contract edge for that reason: the edge order is package, contract, http).
- **Minor.** Pure import changes in a models file are not `contract_change_unscoped`. A candidate pass never takes the place of a backed one on
  the same seed (backed hops first, a later backed hop replaces a candidate pass). Exports of the endpoints file that are not URLs (numbers,
  objects, arrays, labels, helpers with a body, a builder whose result is its first argument) are skipped. A client repository with no
  `http_client` on a client change is a blocking `edge_incomplete`. `hub_cutoff` and `module_cap` subjects are `<repo>:<path>` and
  `<repo>:hop:<n>` in a repository other than the changed one (the changed repository's own stay bare: s02's), `client_url_partial` is
  `<repo>:<path>:<line>`, the backend-call cap is `backend:<repo>:depth:<n>` counted per depth. A tag prefix that matches none of the producer's
  tags while it has others is `tag_prefix_unmatched` (non-blocking, the run is not complete); a consumer's detail names the first tag that
  contains the change and says when only pre-release tags do.
- **Readability.** One `edge_subject`, one `incomplete_hop`, `records_of` once (`reachmap/incomplete.py`), `reachmap/chains.py` holds the chain
  composition (no more import cycle between `edges.http` and `propagate`), one bracket matcher in `endpoints_file.py`, `READ_FORMS`,
  `versions.newest` and `Tags.unparsed` are gone, and `Tags.containing` asks one `git for-each-ref --contains` over the tags the cache holds
  (`Git.tags_containing`), asking each tag alone only for those the cache does not hold at the commit the remote names (unreadable stays
  distinct from "none").
- **Harness.** An exclusion also rejects candidates and by-name areas that match it; the determinism test reorders the declared edges and
  repositories; the URL oracle also checks POST and DELETE (and the fallback) and partial URLs against an independent reference matcher.
  Mutations M13 (added route called uncalled after a failed client read), M18 (tag ancestry unreadable read as unreleased) and M19 (a cut
  contract search with matches taken as complete) are killed by tests.
- **Speed.** `ss._matches` keeps the fixture refs per (repository, revision), the oracle `text_at` is kept per (repository, revision, path), and
  the http, contract and propagate tests share one repository per name and one git cache per class (`CrossCase.SHARED`, the
  `support_universe` pattern; the package tests and the released-change tests keep a repository each because they need tags and branches).

### Tests, speed

Every case is a handful of small repositories written with one `git fast-import` each (`tests/support_cross.py`), and the fixture runs are
shared with the other scenario modules (`ss.run_scenario` caches them per process, the walker included). A test is mostly git
processes (a fetch and a few reads per repository, about 55 to 70 per propagation).

Measured on this machine with the full suite after the review fixes (1,623 tests; 1,451 before M8, so 172 are new): Python 3.11 218.3 s,
3.12 224.9 s, 3.13 222.2 s, all passing. The suite took 170.6 s on 3.12 before M8 and 217.7 s (1,581 tests) at the first version of M8:
the review fixes added 42 tests and about 7 s net, after the memoised fixture refs and oracle reads and the shared repositories
per test class took about 13 s off the edge tests (http, contract and propagate together 52 s, now 39 s with 14 more tests). The target
of about 175 s is not met: the suite is about 54 s over what it was before M8.
