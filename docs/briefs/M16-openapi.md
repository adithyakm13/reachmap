# M16, section 1: OpenAPI as an HTTP edge (`oa`, branch `m16-openapi`): implementation notes

The brief is section 1 of `docs/briefs/M16-adapters.md` (common rules included). This file is the notes of the adapter and the only
file of this branch that describes it. It says what was built, where it differs from or goes beyond the brief, which shared files
were touched and why, what every scenario proves, the acceptance bar item by item, and the limits.

## What was built

```text
reachmap/adapters/openapi/
  jsonpos.py     a strict JSON reader that keeps the line of every key and value (RFC 8259, a duplicate key and a nesting over 100 refused)
  spec.py        the document as operations, base paths, $ref closures with cycles ended, a signature per operation, the diff of two versions
  names.py       operationId to function name (snake_case), and the generic words that never link code
  options.py     the option validators of the two kinds (what `reachmap.config` calls at load)
  records.py     the incompleteness kinds of the adapter
  server.py      OpenApiServer, the entry-point adapter of `http_server = { kind = "openapi", ... }`
  client.py      OpenApiClient, the HTTP client adapter of `http_client = { kind = "openapi", ... }`
tests/fixtures/repos/oa-api, oa-web            the two synthetic repositories (data: history.json and one step folder each)
tests/fixtures/scenarios/oa01 ... oa10         ten scenarios, each with changes/, scenario.json and a hand-traced expected.json
tests/fixtures/workspaces/openapi.json, .toml  the workspace of the two repositories, and the same as a reachmap.toml
tests/fixtures/build_openapi.py                builder for these two repositories (it reuses the M1 builder: identity, dates, overlays)
tests/fixtures/claims_openapi.py               independent derivations the fixture tests check expected.json against (no `reachmap` import)
tests/support_openapi.py, support_openapi_world.py   small specs and repositories for the unit tests; the scenario world and its harness hooks
tests/test_oa_jsonpos.py, test_oa_spec.py, test_oa_config.py, test_oa_server.py, test_oa_client.py, test_oa_edges.py,
  test_oa_fixtures.py, test_oa_scenarios.py
```

## Configuration

```toml
[repos."acme/ledger-api"]                       # the repository that serves the API
language = "python"
http_server = { kind = "openapi", spec = "api/openapi.json", handlers = "app/handlers/*.py" }

[repos."acme/ledger-web"]                       # a repository that calls it through a generated client
http_client = { kind = "openapi", spec = "api/openapi.json", client = "src/api/generated" }

[[edges]]
kind = "http"
from = "acme/ledger-web"
to = "acme/ledger-api"
```

- `spec` is a path inside the repository (no leading `/`, no `..`). `handlers` (server, optional) and `client` (client, required) are a file, a
  folder, a glob (`*`, `**`, `?`, `{a,b}`) or a list of them: an entry without a glob character is a file or a folder prefix at a folder
  boundary (`app/handlers` is not `app/handlers2`), as the client's `client` is. Only code files count (`.py` for a Python repository, the
  JavaScript and TypeScript suffixes otherwise). A pattern that matches no code file is a `handlers_unmatched` record and an incomplete answer,
  never an empty handler tree. The validators say which key is wrong and never quote a value; a key they do not know is named with the nearest
  one (`handler` for `handlers`).
- `http_server` is a new sub-table of a repository (see "Shared files"), next to `entry_points`. A repository has one source of routes:
  `entry_points` of a kind that has routes, or `http_server`, and a config that has both is refused at load, naming the key (only a `celery`
  `entry_points`, which has no routes, goes with a spec, and then the spec is the entry-point adapter).
- The spec is JSON. YAML (`.yaml`, `.yml`) is not read: no safe subset parser is justified (anchors, merge keys, flow styles, multi-line
  scalars and indentation rules are most of YAML, and a half reader would report a route table that is not the spec). A YAML spec is a
  blocking `spec_unreadable` whose detail says "YAML" and "JSON only" (the first version of this brief said a non-blocking `file_not_analysed`;
  `docs/briefs/hardening-config-globs.md` changed it, and where the two disagree that text is the code), the routes it declares are not known,
  no route is claimed, and the answer is not complete (`EntryResult.complete` is False on the server side, a blocking `edge_incomplete` on the
  client side). A `spec` named `.yaml` or `.yml` is refused when the config loads.

## Rules as built

### Reading a spec (`jsonpos.py`, `spec.py`)

1. The reader counts a line by `\n` only (the way git and `str.split("\n")` do), keeps the line of every key, refuses a duplicate key in one
   object (which of the two a reader keeps differs between readers), a nesting over 100 levels and anything that is not strict JSON. Every failure
   is a `JsonError` with a line and a column. It scans 20,000 members in a fraction of a second (line of an offset by bisection).
2. A document is OpenAPI 3 (`openapi` starting with `3.`) or Swagger 2 (`swagger` equal to `2.0`); anything else is a `SpecError`, never an empty spec.
3. Operations are the eight methods of a path item (`get put post delete options head patch trace`), each with the line of its method key, the
   line of its path key and the lines of its object. A path item may be a local `$ref`. A path that does not start with `/`, a method whose value
   is not an object, a path item that cannot be resolved, a server with no `url` and a relative server URL are stated gaps (`Spec.problems`),
   not silent skips.
4. Base paths. OpenAPI 3: the path of each server URL, the operation's `servers` replacing the path item's, which replace the document's; none
   means the root. Server variables take their `default`; a variable with no default, or a relative URL, makes the base partial (`<unknown:NAME>`
   in the path, `Route.partial`), never a guess. Swagger 2: `basePath`. One route per distinct full path; two servers give two routes.
5. `$ref`. Local references only (`#/...`, with `~0`, `~1` and percent escapes, array indexes). The closure of the references an operation
   reaches is a breadth first search with a visited set: a schema that refers to itself, two schemas that refer to each other (the fixture's
   `Invoice` and `Customer`) and a graph in which every level refers to the next twice cost one visit each (tested: 3,000 levels deep, 61 levels
   wide with two references each). Cycles need no report: the visited set ends them (an earlier version also listed the references on a cycle; nothing used it, so it is gone). A reference to a parameter, a request body, a response
   or a path item is followed in place; a reference inside a schema is kept as the name it points at. A `$ref` that points outside the document
   or at nothing is kept on the operation (`unresolved`) and is a stated gap.
6. Signature of an operation: digests of its parameters (path level and operation level, the latter replacing by `(in, name)`), request body,
   responses by status code, and of every schema it reaches (each with its own digest), plus its operationId. Documentation (`description`,
   `summary`, `title`, `example`, `examples`, `externalDocs`) is left out, except where the word is a name (a property called `description`
   is a property). An operation whose signature could not be read whole (`known` False) is changed whenever the spec changes: undecidable
   means affected.

### The server (`server.py`)

7. A route is an operation under each base path. Evidence, in the order of the README: the method key line (with the claim
   `route:<METHOD> <path>:<spec path>`, so a decision survives a reformat), the path key line, and the line of the server URL when the path came
   from a server. `Route.handler` is the function found, `target` its file, `module` the first tag of the operation (else the stem of the
   handler file). Routes are sorted by path and method.
8. Handlers. The function of the handler files (`handlers`: a file, a folder or a glob) named like the operationId, as it is or in snake_case
   (`getInvoice`, `get_invoice`), defined at module level (Python: a `def`; JavaScript and TypeScript: a function or a variable). Exactly one, or the
   operation is unresolved: none, several, or no operationId. Nothing looser is tried, and a function of that name outside the handler tree is not
   the handler (tested). A file of the handler tree that defines a handler is an entry file; a handler file that does not parse is a blocking
   `parse_degraded` and the answer is incomplete. Whenever code changed (the walk asked whether a file is an entry file) and any operation is
   unresolved, each unresolved operation is said (`operation_handler_unresolved`) and the answer is incomplete: that code may be its handler under
   another name. The helpers that read a changed entry file (`module_changes`, `direct_hop`, `import_lines`) are in `adapters/base.py`, shared
   with the FastAPI adapter.
9. What a code change reaches: the walk of M7 finds chains into entry files; a changed handler (or anything it uses: the in-file closure), or a
   handler that uses a changed service function through imports (named imports narrow, as in M6), reaches its route. A statement outside every
   declaration reaches every route of the file, except a changed import line, which reaches the handlers that spell a name it binds (read from the
   pre-change version for a removed one). The chain is the changed line (or the import and its use), then the route's evidence.
10. The spec as a change. A changed spec file is read at both versions: every operation whose signature differs, and every added operation, is
    an area, cited at the first changed line that falls in the operation or in a schema it reaches (an added line at the head, else a removed one at
    the pre-change version, else the route's own lines when the diff has no patch; the path item's own `parameters` and `servers` count as part of
    every operation of the path), then at the route evidence. Each changed operation is also a finding input of kind `operation-changed` (medium
    risk; high confidence, medium when a reference points outside the spec), with the method key line at both versions as evidence; the claim of
    each carries the signature digest, so a later edit of the operation is a new claim. The inputs come back with the areas, in
    `EntryResult.findings` (nothing is kept on the adapter), and the walker adds them to the run's findings. Removed and added routes are
    `routes_changed`'s, and the http edge turns them into `removed_route_still_called` and `added_route_uncalled` (same gating as M8: said only
    once every client edge was read completely and no client call fit nothing).
    A route whose path item or method the reader could not read at the other version (a path item split into another file with a `$ref`, a method
    that is no longer an object) is neither removed nor added: `Spec.unread` keeps those templates and methods, the route is cut from the diff, and
    that is a blocking `spec_unreadable` for it (at reach time too, so the entry result is incomplete). A deleted spec is not a removed route
    either: every operation of the base version is an area, cited at the base, the answer says it is not complete, and `spec_unreadable` blocks.
11. A change that handlers and the spec both touch is one area with two chains.

### The client (`client.py`)

12. Every operation with an operationId is a method name of the generated client. A call is a use of the name in a file that imports the generated
    code (the files of `client`, followed through barrels that re-export it, to a fixed point). A resolved one: a named import used (aliased
    too) or a member of a namespace import (`import * as api`, `api.getInvoice(`): the import says which function it is, whatever the name. A
    match by name alone: the name as a member of anything else in such a file (`new InvoicesApi().getInvoice(`, a default import's member; read with
    the JavaScript parser's public `member_uses`). Method and URL come from the spec, not the call:
    the base path of the spec's server, the path, parameters as parameter segments. Generated files and test files are not callers; a name in a comment or
    a string is not code; a type-only import is not a call. Evidence is the call line; the `via` lines are the method key, path key and server URL lines
    of the client's copy of the spec.
13. Distinctiveness (shape B) is for the match by name alone, not for a resolved import (decision of the review: an import-backed call of a generic
    operationId is a resolved edge, not a name match). A name is dropped from member matching and recorded (`not_distinctive`, non-blocking,
    subject the name, prefixed by the repository when the run is in another one, as a hub's subject is; the detail says that no member call is
    matched by it and that an import of it still is) when it is made only of generic words (`names.is_generic`: `get`, `list`, `getAll`,
    `health`, ...), or when the JavaScript adapter counts more definitions of it than `limits.distinct_max` in non-test code (definitions, not
    references). A name two operations carry is no name at all (no call, recorded). A count that cannot be made is never distinctive for member
    matching and makes the list incomplete.
14. The list says when it may be short (`ClientCalls.complete`, `reasons`), and the http edge turns every reason into a blocking
    `edge_incomplete`: a spec that is absent, unreadable, YAML or not OpenAPI; no generated file in the tree; an operation with no operationId; a
    stated gap that hides an operation (a `$ref` outside the document does not: it does not hide a call); an importer search that was cut; a
    candidate that does not lex; a dynamic import of the generated code with no binding; a re-export under another name.

### Records

| kind | blocking | subject | meaning |
|---|---|---|---|
| `spec_unreadable` | yes (always) | `<repo>:<spec path>` | the spec is not in the tree (a deleted spec too), could not be read, is not JSON (YAML included) or not OpenAPI, or has a duplicate key; or a path item or method could not be read at one version, so its routes are not said removed or added; no route is claimed from what was not read |
| `spec_gap` | no | `<repo>:<spec path>` | part of a spec that was read is not followed (a `$ref` outside the document, a path with no leading `/`, a server with no URL), or the spec is new in the change |
| `operation_handler_unresolved` | no | `<repo>:<METHOD> <path>` | no single function of the handler files carries the operationId, and code changed (or was reached) anywhere |
| `handlers_unmatched` | no | `<repo>:<pattern>` | a `handlers` pattern (file, folder or glob) matches no code file of the tree |
| `handlers_unconfigured` | no | `<repo>` | `http_server` has no `handlers` and code changed: no code is tied to any operation |
| `file_not_analysed` | no | the spec path | no longer made for a YAML spec (a YAML spec is a blocking `spec_unreadable`); the language adapter still notes a changed `.yaml` file with it |
| `not_distinctive`, `edge_incomplete`, `client_url_unmatched`, `client_url_partial`, `parse_degraded` | as before | as before | the existing kinds, used as M5 to M8 use them |

The new kinds live in `reachmap/adapters/openapi/records.py`, registered when the adapter package is imported (which `reachmap.config` does at its end, so every run and every report reader that loads the configuration has them). Every
record also lands in `problems` (server) or `reasons` (client), which is what makes `EntryResult.complete` and the propagation say they are not
complete.

## Shared files touched (each with the reason)

| file | edit | why |
|---|---|---|
| `reachmap/config.py` | the generic `http_server` sub-table (one key in `_REPO_KEYS`, an empty-kinds `_SECTIONS` entry, `Repo.http_server` with default None, the section read in `repo()`), the refusal of `http_server` beside an `entry_points` that has routes, and one import of `reachmap.adapters.openapi` at the end of the module | the brief names `http_server` and no sub-table of that name existed; the adapter registers its kinds itself with `config.register_kind` (`http_server` and `http_client` of kind `openapi`, with its validators) |
| `reachmap/entrypoints.py` | `register("openapi", ...)`; `entry_adapter` also looks at `http_server`; `reach` calls an optional `reached_by_files(change, reached)` of the entry adapter, which returns the areas and finding inputs; `EntryResult.findings` | a spec is a file no language adapter reads, so a change to it makes no chain; the hook is generic (any adapter whose routes live in such a file), and absent for the others |
| `reachmap/propagate.py` | `walk_edges` adds the origin pass's `reach.findings` to the run's findings | the findings an adapter makes of its own diff, without a side channel on the adapter |
| `reachmap/adapters/http_client/__init__.py` | `register("openapi", ...)` | a registration |
| `reachmap/adapters/base.py`, `adapters/fastapi/__init__.py` | `import_lines(parsed)` and `direct_hop(chain, inside, route_lines)` moved out of the FastAPI adapter into base (`module_changes(changed, after, before, import_spans)` is the one `main` already had from the Next.js adapter); FastAPI calls them | the OpenAPI adapter reads a changed entry file the same way |
| `reachmap/adapters/js/syntax.py` | the public `Parsed.member_uses(names)` | the client reads `x.name(` uses without the parser's private helpers |
| `reachmap/edges/http.py` | `BackendCall.handler` is `route.target or route.file`, and a route with no known handler says so in the reason | for FastAPI the two are the same file; for a spec the route's file is the spec and the handler is `target` |
| `reachmap/model.py`, `findings.py`, `report/model.py`, `report/layout.py` | the finding kind `operation-changed`: the literal in the model (a report must load without the adapter imported), `TABLE_KINDS`, `from_input` and its builder `operation_changed`, `KIND_ORDER`, `KIND_NOTES` | the brief asks for a new finding kind |
| `tests/fixtures/build.py` | two lines: `_build_scenarios` and `scenarios` skip a scenario whose repository is not one of the M1 workspace | the `oa` scenarios share the folder; the M1 build and listing are what they were (a test pins 4 repositories, 23 scenarios and no `oa` branch) |
| `tests/test_model.py`, `tests/test_incomplete.py` | their glob over `scenarios/*/expected.json` is `scenarios/s[0-9][0-9]-*/expected.json` | those tests pin the finding and record kinds of the M1 scenarios; the `oa` scenarios have kinds of their own |
| `tests/test_fixtures.py` | the three places that list the scenario folders use `glob("s[0-9][0-9]-*")` | the M1 tests compare the folders with the M1 scenario list and the README |
| `tests/test_scenarios_entry.py` | `entrypoints.KINDS[:2]` is `("react-router", "fastapi")` | the registry now also holds `openapi` (and the kinds of the other M16 adapters, when merged) |

Existing repositories, branches, shas and `expected.json` are byte for byte what they were (a diff from the branch point `a39d45e` over `tests/fixtures/repos/acme-*`, `workspace.json`, `reachmap.toml` and every `scenarios/s*` folder is empty).

## Fixtures and scenarios

Two repositories, one workspace (`ledger`, `limits.distinct_max = 3`, one `http` edge from the web app to the API).

- `acme/ledger-api` (Python, no web framework): `api/openapi.json` (OpenAPI 3.0.3, 325 lines, one server `https://ledger.example.invalid/v1`, eight
  operations, a path-level parameter by `$ref`, a response by `$ref`, an `Invoice` and a `Customer` that refer to each other), handlers in
  `app/handlers/*.py` (`invoices`, `customers`, `statements`, `system`), services in `app/services`, a test.
- `acme/ledger-web` (TypeScript, React Router): a copy of the spec, a generated client `src/api/generated/client.ts`, pages and routes files by
  module, `src/modules/invoices/api.ts` (a wrapper around two generated functions), `src/lib/cache.ts` (an unrelated `get`), `src/legacy/*` (three more
  definitions of `downloadStatement`), a test that names a generated function.

| scenario | change | what it proves |
|---|---|---|
| `oa01-handler-body` | `get_invoice` body | operationId to handler by name; the route has the base path and three spec lines; its client by the web copy of the spec; the DELETE of the same path is not an edge; generic names dropped |
| `oa02-service-function` | `invoice_total` in a service | a change reaches the handler that uses it by name and no other; the chain shows the import and the use; the test is a unit test, not a caller |
| `oa03-removed-operation` | operation and handler module deleted | `removed_route_still_called` (high, low) with the client line and the removed method key at the base; the removed route is no area |
| `oa04-added-operation` | operation and handler added, with an import | the new route; the import line does not reach the other handlers of the module; `added_route_uncalled` only because every client edge was read whole |
| `oa05-operation-changed` | a required parameter on one operation | the spec diff is a change: area, `operation-changed` (medium, high) cited at both versions; the other operations, DELETE included, are the same |
| `oa06-schema-ref-changed` | a schema reached through `$ref` and a cycle | four operations change, four routes, four findings, four screens; the one that reaches no schema does not |
| `oa07-spec-broken` | conflict markers in the spec, and a handler change | blocking `spec_unreadable`, no route, no claim of absence, not complete |
| `oa08-web-generated-call` | a wrapper around `getInvoice` in the web app | the routes its generated client calls, the handler file and the modules it runs, the whole page declaration reached (its other call, a DELETE, too), a test that names a generated function is not a caller |
| `oa09-web-generic-names` | pages that import `list` and `downloadStatement` and call `cache.get` | `list()` and `downloadStatement(...)` are resolved edges (imports) with their handlers' modules; `cache.get` is a member match by name and is dropped (`get` is generic); `get`, `list` and `downloadStatement` (4 definitions over the cap of 3) are recorded as not distinctive |
| `oa10-web-spec-copy-broken` | the web copy of the spec truncated, with a page change | blocking `spec_unreadable` and `edge_incomplete`; no call matched; what the web repository finds by itself stays |

Every `expected.json` lists its types explicitly (`tests/test_oa_fixtures.py` `TYPES`, pinned; every type is claimed by some scenario) and nothing is
skipped (`ss.skipped_types` is empty for all ten). Types added to the schema, both used only here: `backend_call` (the handler file and the
modules a client change reaches) and the `operation-changed` kind of `finding`; new `not_established` kinds are in the table above.
`not_distinctive` subjects of a pass in another repository carry it (`acme/ledger-web:get`), and their `definitions` are counted in that repository.

## Acceptance bar (context section 10)

| item | where it is shown |
|---|---|
| reproduces a hand trace on the fixture and invents nothing beyond it | `test_oa_scenarios.EndToEnd.test_the_report_of_every_scenario_matches_expected_json` (the M9 harness, `complete_for` on every scenario); `test_oa_fixtures` recomputes the claims independently (routes and their lines, changed operations by expanding every `$ref` in place, handlers by name, client URLs, best route by segments, definition counts); `test_the_harness_fails_when_the_report_is_wrong` and `test_the_harness_notices_an_extra_route_an_extra_edge_and_an_extra_screen` show that the check is not a rubber stamp |
| Shape A: a failed read is an incompleteness | spec absent, unreadable (the read failed, not absent), invalid, duplicate key, truncated, YAML, not OpenAPI: `test_oa_server.Unreadable`, `RoutesChanged`, `ReachBySpec`; client side: `test_oa_client.Incomplete`; scenarios `oa07` and `oa10`; an importer search that was cut, a candidate that does not lex, a dynamic import, a barrel, a missing generated folder, an operation with no operationId: `test_oa_client.Incomplete`; a handler file that does not parse: `test_oa_server.ReachByCode`; handlers not configured and unresolved operations: same; no pre-change version, a spec that first appears or is deleted, a diff with no patch: `test_oa_server.ReachBySpec`; a client URL that fits no route and a base path nobody knows: `test_oa_edges.BothDirections` |
| Shape B: a name match has a generic-word fixture and is dropped | `cache.get`, `get`, `list`, `downloadStatement` in `oa01`, `oa09` (and in every scenario that follows the edge); `test_oa_client.GenericNames` (a generic import resolved, a generic member dropped, over the cap with its count, at the cap still distinctive, a count that cannot be made, two operations with one operationId); names scoped to the handler tree and to import-backed files; names in comments and strings are not code |
| every citation resolves at its pinned commit | `test_oa_scenarios.test_every_evidence_in_every_report_resolves_at_its_pinned_sha` (the e2e audit, over all ten reports); every unit test of the server and the client audits its own evidence with `ss.evidence_problems` |
| scenarios pass end to end through `reachmap analyse`, explicit types, no skip | `test_oa_scenarios` (in process, and one run as a separate process); `TYPES` in `test_oa_fixtures` |
| undecidable means affected, never empty and complete on a failed read | no pre-change version, a spec that first appears, a spec that could not be read before, an operation whose signature has a `$ref` outside the document, a statement outside every declaration, a diff with no patch: every operation or route is affected and the answer says it is not complete; `test_oa_server.ReachBySpec` |

## Choices

- A deleted spec is not a removed route, and a spec that first appears does not make its routes new. A spec absent from the tree says nothing
  about what the handlers serve; claiming `removed_route_still_called` (high risk) or `added_route_uncalled` from it would infer a fact from an
  absence. Both are stated (`spec_unreadable` blocking for the deleted one, a non-blocking `spec_gap` for the new one); every operation of a
  new spec is an area, and so is every operation of a deleted one (cited at the base): undecidable means affected.
- A path item or method the reader could not read at one version is never "removed" or "added" (review, major 1): it is cut from the diff and said,
  blocking. Two operations with one method and path are a stated gap in the spec, and the first is read, in the diff as in the route table.
- The method of a generated call is the spec's, never the call's. `getInvoice` is a GET whatever wraps it.
- Generic words are decided by the words of the name, before any count (`get` defined once is still `get`). The stop list is `names.GENERIC_WORDS`;
  a name shorter than three characters is generic too. The cap applies to matches by name alone; an import from the generated client is
  resolved (coordinator's decision after the review).
- A name dropped from member matching does not make the client list incomplete (as M8's `not_distinctive` does not make a contract edge
  incomplete): it is a non-blocking record that says which name was not used for member calls. See the first known limit.
- Two sources of routes are refused at config load (an `entry_points` with routes beside an `http_server`): reading one and silently
  ignoring the other was the alternative the review refused.
- An absent `required` on a parameter is false, a path parameter is always required, the lists `required`, `enum` and `type` are sets, and `1` and
  `1.0` are one number, so a spec rewritten the same way is not a change.
- An operation with no operationId does make the list incomplete: the name of its generated method is not known, so a call to it cannot be found
  (a generator invents one from the path, which no reader here can reproduce).
- `module` of a route is its first tag; `handler` its function. A function is a handler by its name alone: decorators and registration by string
  are not read.
- YAML was `file_not_analysed` here (the brief's instruction; its template speaks of imports). It is now a blocking `spec_unreadable`, so a
  removed operation of a YAML spec is never a quiet, non-blocking note (see `docs/briefs/hardening-config-globs.md`).

## Known limits

- A generic-named operation (`get`, `list`, `health`) is matched to its callers only when they import it from the generated client (a named
  import, aliased or not, or a namespace member). A call of it through anything else (`new Api().get()`, a default import's member) is a match
  by name alone and is dropped, so a route that only such calls reach can be said to be uncalled (`added_route_uncalled`) or can lack its
  screens. The `not_distinctive` record names the name that was dropped from member matching; it is not blocking, as M8's is not.
- The client side is JavaScript and TypeScript, imports through the JavaScript adapter's resolver (aliases from `tsconfig.json`, relative paths);
  a client generated into `node_modules` or reached through a package name is not found (a missing generated folder is said). A client object
  that is built without an import of the generated code (a global, a dependency injected from elsewhere) is not found: a call is import backed, and a
  member name that is distinctive, on any object in such a file, counts as a call (more calls, never fewer). A base URL injected at run time that differs from the
  spec's server is a call that fits no route (`client_url_unmatched`).
- `$ref` outside the document (another file, a URL) is not followed: the operation is changed whenever the spec changes, with a `spec_gap`. A change to
  such a file is not a change to the spec file; only the configured spec path is watched. `allOf` composition is read as part of the schema (its
  references are in the closure), not interpreted; `security`, `deprecated` and `x-` extensions outside documentation keys count in the digest of
  the object that holds them only when that object is a parameter, request body, response or schema; `tags`, `summary` and `security` on the operation
  itself are not part of the signature.
- Operation identity across versions is the method and the full path with its parameter names as written: renaming a path parameter is a removed
  and an added route (the http edge says the URL still fits the new one).
- Handlers are found by name (operationId, snake_case) in module-level functions of the files `handlers` names, one per operation. A handler
  registered by a decorator, a class, a lookup table or a dotted `operationId` (Connexion style) is unresolved, and said. A statement outside every
  declaration in a non-Python handler file reaches every route of the file (the import refinement reads Python).
- YAML specs are not read (see Configuration): refused at load, and a blocking `spec_unreadable` when one appears later. OpenAPI 3.1 `webhooks`, `callbacks` and `links` are ignored; Swagger 2 `consumes` and `produces` too.
- A change to the web app's own copy of the spec is not followed as a change by itself (the generated client is a code change of its own); a
  spec the client was generated from that disagrees with the server's is what the http edge finds, as URLs that fit nothing or a removed route.
- The in-repository reach of a changed Python module import is narrowed by names the handler spells; a handler that uses an imported name only
  through a string or `getattr` is missed there as in M6.

## Not done

- No YAML reader. No `$ref` into other files. No generated server spec (the brief says "or a generated one"): only a committed spec is read. No
  operation-level `security` or `callbacks`. No client side for languages other than JavaScript and TypeScript. No frontend change: the frontend
  was not touched: it orders a finding kind it does not list last and shows it without a note (read in `frontend/src/report/view.ts` and `words.ts`, not run).

## Tests and speed

275 oa tests after the review fixes (`test_oa_server` 75, `test_oa_scenarios` 22, `test_oa_fixtures` 34, `test_oa_client` 30, `test_oa_edges` 20,
`test_oa_jsonpos` 18, `test_oa_spec` 61, `test_oa_config` 15); 243 before them. After the merge of `main` (M13b, the Next.js adapter) the whole suite is
3,610 tests and passes on the parallel runner (4 workers, Python 3.12, 308 s, load average about 3 at the start; the first full run of the branch, before the
review, passed on 3.12, 3.11 and 3.13 and in one serial `discover`). The oa modules and the core modules this branch touches (`test_fastapi`,
`test_scenarios_entry`, `test_model`, `test_incomplete`, `test_findings`, `test_config`) were also run alone after the merge: 729 tests, all pass.

Mutation check (each change made, the named modules run, the change reverted), first round: generic words never generic, the definition cap off by one
in either direction, an unreadable spec not recorded, `$ref`s not followed past the first level, documentation not left out of the signature, a deleted
spec read as removed routes, test files as callers, generated files as callers, snake_case names not tried, the method taken from nothing, the import
refinement off, the first changed line not cited, an unknown signature not read as a change: every one was killed; the review's mutation harness found
two survivors (a parameter `required` flip alone, a `$ref` loop among parameters, responses or path items), which now have tests
(`test_oa_spec.Equivalences`). One survivor is kept as defence in depth: the client's own "a file that does not lex" reason (the JavaScript adapter's
importer search already marks the list incomplete for the same file).

## Review fixes (second round)

The independent review found one blocker, two majors and twelve minors; each is fixed with a test.

- Blocker: `handlers` as a folder or a file is read as a prefix at a folder boundary (only code files); a pattern that matches no code file is a
  `handlers_unmatched` record and an incomplete answer; unresolved operations are said whenever code changed anywhere, not only inside the glob.
- Major 1: `Spec.unread` keeps the path items and methods the reader could not read; a route that is unread at the other version is neither
  removed nor added (blocking `spec_unreadable`), tested end to end on a path item split into another file.
- Major 2 (decision): an import of a generated function is a resolved edge; the generic and definition cap is for matches by name alone.
  oa09 changed for it (the only scenario that did): `list()` and `downloadStatement(...)` are now `http_edge` and `backend_call` entries, `cache.get`
  stays excluded, the three `not_distinctive` records stay, `complete_for` gains `backend_call`; the exact diff of `expected.json` is in the report.
- Minors: equivalent forms are not changes (sorted `required`, `enum` and `type`, an absent `required`, `1` and `1.0`); `http_server` beside an `entry_points`
  with routes is refused at config load; a deleted spec lists the base operations as affected areas, cited at the base; the path item's own `parameters` and
  `servers` count as a changed line of every operation of the path; a duplicate method and path is a stated gap in the spec; the adapter registers its config
  kinds with `config.register_kind` and its record kinds from its own package; `finding_inputs` travel in `EntryResult.findings`; the backend call of a route with
  no known handler says so; the test-only `Spec.cyclic`, `Operation.required`, `Node.keys` and `Node.to_python` are gone; `import_lines` and `direct_hop` are in
  `adapters/base.py` and `module_changes` is the one `main` already had (the Next.js adapter moved it first: signature `(changed, after, before, import_spans)`),
  and the client reads member uses through the new public `Parsed.member_uses` of the JavaScript parser; the fixture tests check the scenario texts for ASCII
  arrows and coverage or safety wording and that no scenario folder has a prefix nobody owns.
