# Reachmap: context for building a generic, any-number-of-repos framework

This file is the starting context for a new project: a framework
that anyone can point at as many repositories as they have and get an evidence-backed
answer to "what does this change reach, and what should we test before we release it?"

The project is called **Reachmap** (decisions in section 13). It is written
for two readers: a person starting the project, and a coding agent (Claude
Code or similar) that will build it. Read it top to bottom once. Sections 4 and 5 are the
rules; breaking them is how a tool like this becomes dangerous rather than merely wrong.

The design grew out of an earlier internal tool the author built. This document describes
the design on its own merits, and the examples use an invented setup by role: a React
frontend, a Python backend monorepo, and a shared schema library.

---

## 1. What Reachmap is

A QA lead wants to release individual features without running the whole regression suite
for every change, and without missing indirect effects. Picking fewer tests is easy.
Defending every exclusion afterwards is not. The hard part is not the change but its reach:
a pull request labelled "Invoicing" can affect another module through a shared
component, a linked backend route, or a field in a shared data contract.

Reachmap takes a change (a pull request, a branch comparison, or a commit range), works
out what it actually reaches across repositories, and produces a test scope where **every
inclusion and every exclusion cites evidence that can be re-read at the revision it names**.
A human owns the release decision. **The tool never claims a release is safe.**

What a report contains, in the order a QA reader needs it:

1. **Areas to test**: the screens (frontend routes) and entry points (API routes, tasks,
   jobs) the change reaches, each with the chain of imports or calls that gets there, and a
   priority (must test when a change reaches eight or fewer screens; sample when more).
2. **Findings**: typed claims (`direct`, `cross-repo`, `duplicate`, `exclusion`,
   `release`, `contract-relaxed`, ...) each with risk, confidence, a confidence basis and
   pinned evidence.
3. **Branch matrix**: for each release branch, whether it carries the change (present,
   partial, pre-change, diverged, not on branch, unreadable). It is the check that catches,
   for example, a fix that lands on release branches but not on `main`.
4. **Cross-repository reach**: consumers of a changed schema field or package, which
   versions each consumer pins, and frontend code calling a changed backend route.
5. **Backend calls**: for a frontend change, the backend routes it calls and the modules
   each handler runs (up to three hops).
6. **Suggested test cases** from the test management system, read live, always labelled
   "suggested, not verified coverage".
7. **What the run could not establish**: structured incompleteness records, some marked
   blocking.
8. **For the author**: a developer view derived from the same data (other repositories,
   files beyond the diff, other branches, unit tests to run with a command per repo).

### Shape of the code

| Part | What | Rule |
|---|---|---|
| Analyser | Python 3, **standard library only**, no model calls | Deterministic. Every step finds evidence or records that it could not. |
| Reports | Markdown, JSON, one self-contained HTML file | HTML has no build, no server, no network. Report content is untrusted. |
| Runner | Localhost HTTP job queue (`127.0.0.1:8787`) | Loopback only, Host header checked, one job at a time, hard timeout. |
| MCP server | stdio, lets a model client read reports and record notes | Report content reaches the model fenced and labelled untrusted. Every write is stamped with the client name. |
| AI review | A skill a model client runs on demand | Notes kept apart from the report, never a finding, never coverage. |
| Frontend | React + TypeScript + Vite, the runtime deps of 13.4 (React, Mantine, React Flow and a few more) | Separate dependency rules. Reads reports, records decisions. |

### Parts of the design

| Part | Responsibility | Where it lives |
|---|---|---|
| Git layer | Shallow checkout of the analysed commit, change sets (PR, compare, range), diff parsing, file reads and search from disk, branch matrix | Core, written once for every host |
| Host adapters | Only what the host alone knows: a pull request's title, state, base, head and file count | One adapter per host |
| Data model | `Evidence`, `Finding`, `Incompleteness`, `Analysis`, and the finding builders | Core |
| Language adapters | Aliases, import resolution, enclosing symbols, callers, duplicates, test discovery | One adapter per language |
| Entry-point adapters | The walk up to the routed screen, route or job that a change reaches, with mounts | One adapter per framework |
| Edge adapters | Schema fields, version pins, propagation and consumers; HTTP calls in both directions | One adapter per edge kind |
| Test sources and context | Suggested cases from a test-management system or from local specs; requirement context | One adapter per source |
| Review | Report revisions, staleness, decisions, fingerprints | Core |
| Renderers | Markdown, JSON and the single-file HTML report | Core |
| Runner, MCP server, notes | The localhost job queue, the stdio server, model notes | Core |
| Evaluation | Label mining, citation audit, blind review, judging sheets | Core tooling |
| Pipeline | The linear walk of section 2 | Core; keep it small and drive it from the config |

---

## 2. The pipeline

One change in, one report out. Linear and deterministic.

1. **Resolve the change set.** A PR, `base...head` (three dots: from the merge base), or a
   range. Pin every ref to a commit (never resolve a ref by name for anything that
   reaches a citation). Read the diff from a local checkout, check it against GitHub's own
   file count, refuse on mismatch.
2. **Find the pre-change version** (parent for a merged change, merge base for an open
   one). Without it, removed routes, removed calls and named-export narrowing cannot be
   computed, and the run must say so.
3. **Branch matrix**: read each changed file on every configured release branch and
   classify it.
4. **Per-language change impact**: which symbols the change touches (placed by diff line
   number, never by text), who calls them (confirmed by import, aliases and relative
   paths both), guards that exclude a call site.
5. **Entry points**: walk callers up to what a user or system actually invokes (a routed
   screen, an API route with its full mount path, a task, a job).
6. **Cross-repository edges**: schema field to consumers, backend route to frontend callers
   by URL, package to its pinned consumers, frontend call to backend handler.
7. **Context**: tickets from the change title, requirement snapshot, test management.
8. **Findings and incompleteness**, then **revision and decisions** (is this a new revision
   of an earlier analysis, which earlier QA decisions still attach).
9. **Render**: JSON (the contract), markdown, HTML, CLI summary.

---

## 3. Data model (keep this; it is the most reusable part)

```text
Evidence        repo, locator ("ref:path:line"), detail (the verbatim line), ref (commit sha)
Finding         kind, title, workflow, risk, confidence, confidence_basis,
                evidence[], identity (stable, hashed into the id), is_exclusion
Incompleteness  kind (stable code), subject, not_established (the conclusion the run
                cannot support), detail (prose), blocks_selection (bool)
Area            a screen or entry point: label, module, route line, chains[] of evidence,
                also_mounted[], priority, by_name (candidate, not import-backed)
BranchState     branch, commit, state (present | partial | pre_change | diverged |
                file_absent | unreadable), added lines found
Analysis        change set, ref, branch rows, findings, areas, propagations,
                backend calls, suggestions, incompleteness, notes, revision number
Decision        target (finding or area id), verdict, reason, author, channel stamp,
                evidence fingerprint at the time
```

Rules baked into the model:

- **Finding ids hash `identity`, never `title`.** Titles carry counts that move with the
  code; hashing them detaches QA decisions exactly when carrying them forward matters.
- **A decision's fingerprint hashes the claim, not the commit.** Exclude `Evidence.ref`
  from it, or every decision goes stale the moment a branch moves. False staleness costs as
  much as missed staleness.
- **Risk and confidence are separate fields.** A severe consequence on thin evidence is
  high risk, low confidence. Never average them.
- **Incompleteness is data, not prose.** A note in a list of strings is invisible to every
  machine reader. Write the note and the record together from one function so they cannot
  drift.
- **"Required and unavailable" is not "optional and absent".** A missing optional source
  (no test-management token) is recorded and non-blocking. Degraded search or an unreadable
  file is blocking, because an absence was never established.

---

## 4. Rules that must not be relaxed

These are the product. A generic framework must enforce them in its core, so that no
adapter can break them.

1. **Every finding needs evidence.** A finding with no citation should not exist.
   Plausible-sounding extras are failures, not bonus output; acceptance tests assert it.
2. **Never claim safety or coverage.** No output says a release is safe, a test covers a
   change, or nothing needs testing. Ban the wording in code (`covered by`, `already
   covers`, `no need to test`, `nothing to test`, `safe`).
3. **A suggested test case is a place to look, never coverage.** Suggestions and mapped
   tests live in different fields. One function counts coverage gaps.
4. **A human decision is risk acceptance, never evidence.** A "skip" reads as "risk
   accepted, not tested". Nothing a reviewer records clears a gap.
5. **An absence must be established, never inferred from a failure.** See the traps below;
   this one recurs more than any other.
6. **Undecidable means affected.** A call site is excluded only when the change sits under
   a guard the call provably cannot enter.
7. **The analyser makes no model calls.** A model can read reports and write notes through
   MCP, kept apart, stamped, tied to the report revision, and made stale by a rerun. The one
   model claim the framework checks is a proposed file: it must exist at a revision the
   report read and name the change by a distinctive name outside a comment.
8. **Report content is untrusted.** It quotes source lines from every repo read. Escape
   `<`, `>`, `&` when embedding JSON in HTML. Fence it when handing it to a model. Render
   model notes as text, never markup.
9. **A run has a ceiling, and a timeout is not a result.** Run analyses in a separate
   process so they can be killed. A timed-out run reports `timed_out`, never a finding
   count.
10. **Do not write to the test management system** from the analyser. Draft cases are
    shown for a person to add.
11. **Read at the revision you cite.** Code search reflects the default branch now; use it
    to propose candidates, never to conclude, and re-read the candidate at the pinned
    commit. Prefer a local checkout of the commit.

---

## 5. Traps that already cost time (general lessons)

Every one of these was a real bug in an earlier internal tool the author built. Most share
two shapes. A framework should have a test fixture for each.

### Shape A: an absence manufactured by the tool, reported as evidence about the code

- Code search was read one page deep (the API pages at 30 results), so a symbol used by
  hundreds of test files appeared to have none.
- A rate-limited search returned empty and read as "no callers".
- A search examined a small fraction of more than a thousand matches and listed that
  fraction as "the consumers". Record every cut centrally. Distinguish an **examination
  cap** (must be reported) from a **result cap** (stopping after N confirmed hits claims
  nothing absent, do not report it).
- Reverse alias lookup returned only the longest alias, so a file imported under several
  aliases appeared to have no importers. Resolution asks "which file does this specifier mean" (longest alias
  wins); reverse lookup asks "which specifiers mean this file" (all of them).
- Importer search covered alias imports only, and so missed every relative import.
- An endpoint constant written over two lines was invisible to a one-line regex.
- A decorator at column 0 was outside the function the walker attributed lines to.
- A failed file read (rate limit, 5xx, auth, timeout) returned the same `None` as a 404,
  and "file absent" turned into "the deletion landed". Detect 404 explicitly; everything
  else is `unreadable`, recorded, and blocking. Files over 1 MB come back empty from the
  contents API; read them through the blob API.
- An open PR silently had no pre-change version, so the passes that need it were skipped.
- A diff over 300 files is refused by GitHub; the fallback API pages at 30 and omits
  patches for large files. Paginate, mark `content_available=False`, refuse on a count
  mismatch.
- A list of test cases was fetched only up to a fixed limit, so a draft past the limit was
  never seen as a draft and read as real coverage.
- Branch names with slashes must be URL-encoded; unencoded, an existing branch is a 404.

**The rule:** any code path that can fail quietly will eventually present the failure as
"nothing found". Make every reader return found / not found / could not read, and make the
third one an incompleteness record.

### Shape B: widely-shared tokens masquerading as distinctive

- A removed import line's module path read as a defect fingerprint, producing a run of
  false findings.
- `mapStateToProps` read as a parallel implementation, producing dozens of false
  exclusions.
- The word `interval` read as a contract link to unrelated files.
- Any quoted word on an added line (`'react'`, `'string'`) read as a schema field read.
- A setting key's leaf `limit` matched an English word in a comment.
- An added line was placed in a function by its text, so every handler sharing a line such
  as `request: Request,` looked changed. Place lines by diff line number.
- A nested function name matched a sibling handler's own nested function of the same name.
- A test marker `test/` matched a folder such as `latest/` or `contest/`.

**The rule:** every similarity check needs a distinctiveness cap, and the cap counts
**definitions, not references** (a name is referenced everywhere it is imported and
defined in two places). Match code, not comments. Scope names to where they are defined.

### Other lessons

- **Version numbers do not imply containment.** Release lines diverge; read the file at the
  tag.
- **A placeholder version in a manifest** (`1.0.0` forever) means real versions are tags.
- **Read import aliases from the config the build actually uses** (for example
  `tsconfig.json`), not an editor config that may point at a directory that does not exist.
- **Shadowed loop variables** can silently replace one field of a report with another (a
  revision with a ticket key); a citation audit catches it.
- **A schema change matters before anyone adopts it.** List consumers from the consumer's
  primary branch and say the exposure comes "on adoption".
- **A route is composed on the router it is declared on**, following mounts; a router
  nothing mounts is said, not matched.
- **A walk must not pass through hubs** (a file with more than 50 importers, a Redux store)
  and must say that it stopped.
- **Subagents and scanners can be confidently wrong**, for example reporting "no secrets"
  when a token is split across two lines. Verify.
- **One job at a time** when the host API rate limits search; two concurrent runs make each
  other's searches fail, and a failed search looks like an empty one.

---

## 6. What a single setup would hard-code

Everything a generic framework has to turn into configuration or an adapter. Any one
team's setup tends to fix these without saying so.

| Assumption | Becomes |
|---|---|
| A fixed number of repos with fixed roles (frontend, backend, schema) | A repo registry of any size, roles per repo |
| JS import aliases read from one build tool's config file | JS/TS adapter option (`alias_config`, also `tsconfig paths`, webpack, vite) |
| Screens = React Router `<Route>` in one routes-file convention | Frontend entry-point adapter with a configurable routes pattern |
| A feature switch in the routes files that mounts a screen twice | Adapter-specific "mounted twice" rule, off by default |
| Frontend calls backend through one file of exported endpoint constants | HTTP client adapter: endpoints file, or an OpenAPI client, or a URL-literal scan |
| Backend routes = one framework's route decorators and router mounts | HTTP server adapter (FastAPI, Flask, Express, Spring, Django) |
| Service boundary = a directory holding a build tool's marker file | Configurable service marker (`pyproject.toml`, `package.json`, `BUILD`, `go.mod`) |
| Schema = Python models under one folder, versions as git tags | Contract adapter (Pydantic, JSON Schema, protobuf, OpenAPI, GraphQL) |
| Pin read by regex from a manifest | Config (`pin_pattern`); generalise to package edges |
| Unit test runner chosen by repo name | Per-repo `test_command` in config |
| Test management = one system, areas mapped to suite ids | Test-source adapter (Qase, TestRail, Xray, local specs) |
| Requirements = an offline export of one tracker | Context adapter |
| Ticket keys of one fixed shape in titles | Configurable ticket pattern |
| One host only | Host adapter (GitHub first; GitLab, Bitbucket later) |
| Frontend folder to product module map | Config |

Each of these is a per-repo setting in the framework, not a global one.

---

## 7. The generic framework design

### 7.1 Core idea

A **workspace** is a set of repositories and the **edges** between them. Each repository
has one or more **roles**, each role served by an **adapter**. A change in one repository
is analysed by its own adapters, then propagated along edges to other repositories, hop by
hop, with a cap, until it reaches **entry points** (screens, routes, jobs, topics). Every
hop produces evidence. Every hop that could not be completed produces an incompleteness
record. The core owns the rules in section 4; adapters only answer narrow questions.

```text
            change set (PR / compare / range) in repo R
                              |
        R's language adapter: changed symbols, callers, guards
                              |
        R's entry-point adapter: screens / routes / jobs reached
                              |
     edges out of R (http, package, contract, event), each an adapter
                              |
        next repo's adapters, same steps, until hop cap or no edge
                              |
     core: findings, incompleteness, areas, suggestions, report
```

### 7.2 Configuration: `reachmap.toml` (read with stdlib `tomllib`, Python 3.11+)

```toml
# Workspace for the Acme product. One [repos."..."] table per repository,
# one [[edges]] entry per link between them.
workspace = "acme"
ticket_pattern = '\[([A-Z][A-Z0-9]+-\d+)\]'

[limits]
hops = 3             # edges followed past the changed repo
hub_importers = 50   # never walk through a file imported more often than this
distinct_max = 10    # a name defined more often than this is not distinctive
timeout_s = 900

# ---------------------------------------------------------------- repositories

[repos."acme/web"]
host = "github"                      # github | gitlab | bitbucket | git
language = "typescript"
roles = ["frontend"]
branches = ["main", "release/*"]
aliases = { from = "tsconfig.json" }
entry_points = { kind = "react-router", routes_glob = "src/modules/*/routes.tsx" }
http_client = { kind = "endpoints-file", path = "src/api/endpoints.ts" }
tests = { markers = ["__tests__/", ".test.", ".spec."], command = "npx jest {paths}" }

[repos."acme/api"]
host = "gitlab"                      # hosts can differ per repo
language = "python"
roles = ["backend"]
branches = ["main", "release/*"]
service_marker = "pyproject.toml"
entry_points = { kind = "fastapi" }
tests = { markers = ["tests/", "test_"], command = "pytest {paths}" }

[repos."acme/worker"]
host = "gitlab"
language = "python"
roles = ["backend"]
entry_points = { kind = "celery" }

[repos."acme/contracts"]
host = "github"
language = "python"
roles = ["contract"]
contract = { kind = "pydantic", root = "contracts/models", versions = "git-tags", tag_prefix = "v" }

# ---------------------------------------------------------------- edges

[[edges]]
kind = "http"
from = "acme/web"
to = "acme/api"

[[edges]]
kind = "package"
producer = "acme/contracts"
consumer = "acme/api"
manifest = "requirements.txt"
pin_pattern = '^acme-contracts==(.+)$'
import_marker = "acme_contracts"

[[edges]]
kind = "package"
producer = "acme/contracts"
consumer = "acme/worker"
manifest = "requirements.txt"
pin_pattern = '^acme-contracts==(.+)$'
import_marker = "acme_contracts"

[[edges]]
kind = "event"
from = "acme/api"
to = "acme/worker"
topics_glob = "**/topics.py"

# ---------------------------------------------------------------- tests and context

[[test_sources]]
kind = "qase"
project = "ACME"
token_env = "QASE_API_TOKEN"         # the variable's name, never the token

[[test_sources.areas]]
name = "Billing"
route_prefixes = ["/billing"]
modules = ["billing"]
suites = [12]                        # Qase suite ids, descendants included

[[context]]
kind = "jira-snapshot"
path = "${ACME_JIRA_SNAPSHOT}"       # unset variable means "not configured"
```

Adding a repository is one `[repos."..."]` table plus the edges it takes part in. Nothing
in the core names a repository. Single-quoted TOML strings are literal, so regexes need no
double escaping.

Config rules carried over: `${VAR}` expands from the environment and an unset variable
becomes "not configured", never the literal string; no machine-specific path is committed;
the only credential is read from the environment by name and never logged, cached or
reported.

### 7.3 Adapter interfaces

Keep them small. Each method returns evidence or raises / records why it could not. Use
`typing.Protocol` so the core has no dependency.

```python
from typing import Protocol, Iterable

class Read:          # every read answers one of three ways
    FOUND, ABSENT, UNREADABLE = "found", "absent", "unreadable"

class Git(Protocol):    # written once, the same for every host (see 13.5)
    def checkout(self, remote: str, sha: str) -> "Checkout": ...               # shallow, cached
    def heads(self, remote: str, patterns: list[str]) -> dict[str, str]: ...   # git ls-remote
    def pin(self, remote: str, ref: str, as_of: str | None) -> str: ...        # ref to sha
    def diff(self, co: "Checkout", base: str, head: str) -> "ChangeSet": ...   # with renames
    def file_at(self, co: "Checkout", path: str, sha: str) -> tuple[str, str | None]: ...  # (Read.*, text)
    def grep(self, co: "Checkout", pattern: str) -> "SearchResult": ...        # complete, time-pinned

class Host(Protocol):   # only what the host alone knows; github, gitlab, bitbucket
    kind: str
    def remote(self, repo: str) -> str: ...                                    # clone URL
    def change_request(self, repo: str, number: int) -> "ChangeRequest": ...   # title, body, state, base, head, merge sha, file count
    def title_of(self, repo: str, number: int) -> str: ...                     # to follow "(#N)" in squash commits

class LanguageAdapter(Protocol):
    def changed_symbols(self, change: "FileChange", before: str | None, after: str) -> list["Symbol"]: ...
    def importers(self, repo: str, path: str, sha: str) -> list["Evidence"]: ...  # aliases AND relative
    def callers(self, symbol: "Symbol", sha: str) -> list["CallSite"]: ...
    def guards(self, symbol: "Symbol", call: "CallSite") -> str: ...            # "excluded" | "affected" | "undecidable"
    def is_test_path(self, path: str) -> bool: ...
    def definitions(self, name: str, sha: str) -> int: ...                     # for the distinctiveness cap

class EntryPointAdapter(Protocol):
    def entry_points_reached(self, chains: list["Chain"], sha: str) -> list["Area"]: ...
    def routes(self, repo: str, sha: str) -> list["Route"]: ...                 # full mounted paths
    def routes_changed(self, before: str | None, after: str) -> tuple[list["Route"], list["Route"]]: ...  # removed, added

class EdgeAdapter(Protocol):
    kind: str
    def propagate(self, finding_or_area, workspace) -> Iterable["Hop"]: ...    # each Hop: target repo, evidence, or an Incompleteness

class TestSource(Protocol):
    def suggest(self, findings, areas, change) -> dict: ...                    # always labelled suggestions

class ContextSource(Protocol):
    def lookup(self, ticket_keys: list[str]) -> list["Requirement"]: ...
```

### 7.4 Edge kinds

| Kind | From | To | How it is matched | Notes |
|---|---|---|---|---|
| `http` | client code (endpoints file, fetch calls) | server routes | URL segments; client parameter matches only a route parameter; most specific wins; literal-in-parameter matches say so | Both directions: client to server and server to client |
| `package` | producer repo (tags) | consumer manifests | pin regex per branch, import marker, adoption state | |
| `contract` | schema field change | consumer code reading the field | distinctive field names only (definition-count cap), code not comments, then hand those lines to the consumer's own impact pass | Chains: schema to backend code to routes to frontend screens |
| `event` | publisher | subscriber | topic or queue name, distinctive only | Tasks registered by class or by a framework registry are a known limit |
| `library` | shared lib in a monorepo | importers | imports, one hop past the library | |

Edges chain. A schema field change reaches backend code through `contract`, that code's
routes through the backend entry-point adapter, the frontend through `http`, and screens
through the frontend entry-point adapter. In the framework that chain
falls out of the edge list.

### 7.5 Propagation rules the core enforces for every edge

- **Hop cap** (`limits.hops`) and a **module cap** per hop, each recorded when hit.
- **Hubs are not passed through** (`limits.hub_importers`), recorded.
- **Distinctiveness** before any name-based match (`limits.distinct_max` definitions).
- **By-name matches are candidates**, shown apart, never must-test.
- **Every hop pins the target repo's revision** it read, per branch where branches matter.
- **A failed read on an edge is an incompleteness on that edge**, never "not adopted" or
  "no caller".
- **A removed thing is followed too**: a removed route still called by a client is high
  risk, low confidence; an added route nobody calls yet is said.

### 7.6 What the core keeps, unchanged

Revisions and staleness, decisions and fingerprints, incompleteness kinds, the report
contract and renderers, the localhost runner, the MCP server and its stamps, model notes,
the citation audit, the evaluation harness, the judging sheets. These are
repository-agnostic by design: nothing in them may name a repo.

---

## 8. Security and deployment posture

- **Credentials**: the host CLI's login (for example `gh auth`) and named environment
  variables. Nothing stored. A token never appears in a cache key, a cache file, a log or a
  report; test this.
- **The runner is a runner, not a service.** It acts as whoever is logged in. It binds
  `127.0.0.1` only, rejects any request whose `Host` is not a loopback name (binding alone
  does not stop DNS rebinding), and refuses cross-origin callers. Do not make it a shared
  service without deciding (a) whose credentials it acts with and (b) where report data is
  allowed to go. Reports carry internal architecture and live defect findings.
- **The HTML report makes no network calls.** Self-host fonts. A CDN link is a change of
  posture, not a convenience.
- **MCP over stdio**, no socket. Record which client connected. Stamp every write "recorded
  via MCP, client reported as <name>"; the stamp cannot be suppressed, doubled or forged by
  the caller, and it says the name is the client's own claim.
- **An AI review started by the runner gets a fixed tool list**, never a blanket permission
  skip: read and note tools only, no decision recording, no starting analyses.
- **Analysed repositories are an injection surface** the moment a model reads a report.

---

## 9. Measuring whether it helps

Do not claim the tool helps without these. Quote results as counts, never percentages, on
small samples.

- **Citation integrity**: re-read every cited locator at its pinned commit and require
  that every one resolves. Building and widening such an audit, in an earlier internal tool
  the author built, found defects no acceptance test could see. Run it after any change to
  citations.
- **Reach beyond the diff**: take historical changes whose later "witness" change (a
  follow-up fix) touched files outside the original diff, analyse the original **as of its
  merge time**, and count whether the report cited any witness file. Expect a weak result,
  and state it as weak.
- **Independence on both axes**: no two cases share a subject or a witness. One witness
  counted several times inflates a result.
- **Exclusions are not predictions**: score per finding, drop exclusions, match paths
  exactly or at a path boundary.
- **Reverts cannot test reach**: a revert undoes exactly the diff.
- **Human judging sheets**: one row per listed screen or suggested case, filled in by a
  person, never a model, before tuning any rule.
- **Recall against real regressions needs defects linked to the change that caused them.**
  Most organisations do not record this. Without it no honest recall number exists for
  this tool or any alternative, including the current process. Say so.

---

## 10. Building the framework: a plan

Ordered so each step keeps the tests green and ships something.

1. **Fixtures first.** Build two or three small synthetic repositories (a TS frontend with
   routes and an endpoints file, a Python FastAPI service, a contracts package with tags)
   as test fixtures, with known answers. A public framework cannot
   test against live private repos.
2. **`reachmap.toml` with a loader** (section 7.2) that builds `repos` + `edges`.
   Validate it; refuse unknown roles and edges with a clear message. TOML is the only
   config format (see 13.2).
3. **Write `Git` and `Host` as separate parts** (section 7.3): local-clone operations once, then
   GitHub, GitLab and Bitbucket host adapters, plus a host-less `git` mode for compare and
   range. Keep the three-way read result and the central failure and truncation records.
4. **Language adapters**: a JS/TS adapter (`adapters/js`, with test discovery) and a
   Python adapter (`adapters/python`).
5. **Entry-point adapters**: React Router (`adapters/react_router`) and FastAPI, with its
   route and mount code (`adapters/fastapi`).
6. **Edges**: `package` and `contract`, and `http` (one adapter, both directions).
7. **Drive the pipeline from an edge walk over the config**, not from hard-coded passes.
   Check: the reports of a fixture workspace come out identical before and after a
   refactor.
8. **Make report sections generic**: "Other repositories" lists whatever repos were
   reached, with what was looked at per edge, instead of three fixed descriptions.
9. **Generic test sources and context** (Qase first, then Playwright / Cypress specs as a
   local source, then TestRail).
10. **First-release adapters** in the order of section 13.3: OpenAPI, Next.js,
    Express / NestJS, Django, Spring Boot.
11. **Frontend in the wheel** (section 13.4): build the React app at release time, ship the
    static files inside the package, serve them from `reachmap serve`.
12. **Packaging**: `pip install reachmap` with no dependencies, Python 3.11+, and
    `reachmap init` that writes a starter `reachmap.toml` by reading the repos (detect
    host, language, test runner, route framework) and marks every guess with a comment for
    a person to confirm.

What to keep out of scope until asked: a hosted multi-user service, writing to test
management, CI gating that runs a subset instead of the full suite.

### Acceptance bar for each adapter

- Reproduces a hand trace on a fixture, and invents nothing beyond it.
- Every Shape A trap above has a fixture that makes the read fail and asserts an
  incompleteness, not an absence.
- Every name match has a fixture with a generic word and asserts it is dropped.
- Every citation it produces resolves at its pinned commit.

---

## 11. How a user would use it

```bash
pip install reachmap
gh auth login          # or glab auth login, or a Bitbucket token in the environment
reachmap init github:acme/web gitlab:acme/api gitlab:acme/worker github:acme/contracts > reachmap.toml
# review reachmap.toml: confirm roles, routes, endpoints file, edges (guesses are commented)
reachmap analyse --repo acme/api --pr 812
reachmap analyse --repo acme/web --compare release/3...main
reachmap serve         # the workspace on 127.0.0.1, no npm needed
```

Then: decide test / sample / skip per screen with a reason in the workspace (or in the
single-file HTML report, exported and fed back with `--decisions`), rerun on the next
commit and see which decisions went stale. Optionally connect a model client over MCP for
an AI review that adds notes beside the report.

---

## 12. Glossary

- **Change set**: the files and lines a PR, comparison or range changes, pinned.
- **Finding**: one evidence-backed claim about reach, with risk and confidence.
- **Exclusion**: a finding that says an area is not reached, with the evidence why.
- **Area / screen**: an entry point a person can test, with the chain that reaches it.
- **Edge**: a declared link between repositories along which impact propagates.
- **Incompleteness**: something the run could not establish, and the conclusion it
  therefore cannot support.
- **Blocking**: an incompleteness that means an absence was never established.
- **Witness**: a later change used in evaluation to see what the original really reached.
- **Pinned**: tied to a commit sha, so it can be re-read and checked.
- **Stale**: a decision or note whose evidence fingerprint or revision no longer matches.

## 13. Decisions

Recorded 19 Oct 2026.

### 13.1 Name, licence, visibility

- **Name: Reachmap** (working name, recommended). It says what the tool produces: a map of
  what a change reaches. Checked 19 Oct: free on PyPI; only empty, unstarred GitHub repos
  use it. Alternatives, also free on PyPI: `testreach`, `reachlens`, `ripplescope`.
- **Licence: Apache-2.0.** Permissive like MIT, plus an explicit patent grant and a
  contribution clause, which companies' legal teams prefer when they adopt a tool that
  reads their code.
- **Visibility: public.**
- **Clean-room.** Write Reachmap from this document and synthetic fixtures, and commit no
  company data, repository names, reports, tickets, test cases or tokens.

### 13.2 Config format: TOML

TOML, read with `tomllib` (standard library since Python 3.11), so the core keeps zero
dependencies. **Minimum Python: 3.11.**

Why TOML over JSON:
- **Comments.** JSON has none; a workspace file that people edit by hand needs real ones
  (why a suite id is mapped, which edge is a guess).
- **Readable tables for many repos**: `[repos."acme/web"]` and `[[edges]]` read better than
  nested braces at twenty repositories.
- **Familiar** to anyone who has seen `pyproject.toml` or `Cargo.toml`.

Costs, accepted: `tomllib` reads but does not write, so `reachmap init` writes the file
from a text template; and reports, the decision store and every machine interface stay
JSON.

*Amended 2026-10-22 (owner decision):* an earlier draft had the loader also accept a legacy
JSON config and map it to the new shape. That was removed before release. TOML is the only
config format, and a `.json` config is refused with a message that says to use TOML.

### 13.3 Adapters to build first

Ordered by how much of the real world each one unlocks. The base set: TS/JS, Python,
React Router, FastAPI, endpoints-file HTTP client, Pydantic contracts, pip/npm pins, Qase.

**First release (beyond the base set):**

1. **OpenAPI as an HTTP edge.** The single biggest unlock: one spec describes the server's
   routes and can drive the client match, whatever language either side is written in.
   Read routes from a committed spec (or a generated one), match client calls against it,
   and treat a spec diff itself as a change (removed or changed operation).
2. **Next.js routes** (`app/` and `pages/` file-system routing): the most common React
   setup that React Router does not cover; screens come from the folder tree, not a routes
   file.
3. **Express and NestJS** servers: the common Node backends (`app.get(...)`,
   `router.use(prefix, ...)`, NestJS `@Controller` + `@Get` decorators).
4. **Django and Django REST Framework**: `urls.py` with `include()` chains is the mount
   graph; views and viewsets are the handlers.
5. **Spring Boot** (Java and Kotlin): `@RestController`, `@RequestMapping` on class and
   method; needs a Java/Kotlin language adapter (imports and call sites, no build).
6. **Playwright and Cypress specs as a local test source**: map spec files to the routes
   they visit (`page.goto`, `cy.visit`) so suggestions work with no test-management system.
7. **TestRail** as a second hosted test source.

**Next, by demand:**

| Area | Adapters |
|---|---|
| Languages | Go, C#, Ruby |
| Frontend routing | Vue Router / Nuxt, Angular router, SvelteKit, Remix |
| Backend routing | Flask, Go (`net/http`, chi, gin), ASP.NET Core, Rails |
| Contracts | JSON Schema, protobuf / gRPC, GraphQL SDL (and operations as the client side), Avro |
| Event edges | Kafka topics, Celery tasks, SQS / SNS, Temporal |
| Package edges | Maven / Gradle, Go modules, NuGet |
| Test sources | Xray / Zephyr (Jira), Azure Test Plans |
| Context | live Jira, Linear, GitHub Issues |

Every adapter meets the acceptance bar in section 10 before it ships.

### 13.4 The frontend ships with the framework

Yes. The workspace (screens, findings, evidence drill-down, test plan, decisions, AI
review notes) is part of Reachmap, not an extra.

- **Repository layout:** one repository, `reachmap/` (the Python package) and `frontend/`
  (the React app) side by side, each with its own dependency rules. The Python package
  keeps zero runtime dependencies. The frontend started with three (react, react-dom,
  react-router-dom) and no UI kit; the owner amended that for M13b (2026-10-21): Mantine,
  Tabler icons, React Flow with dagre, and prism-react-renderer, nothing else.
- **No npm for users.** The release build compiles the frontend and puts the static files
  inside the Python wheel. `reachmap serve` starts the localhost runner and serves the
  workspace from it on `127.0.0.1`, same origin, so no proxy and no CORS.
- **The single-file HTML report stays.** It is the artifact to attach to a pull request or
  a ticket, readable with nothing installed, no network. The workspace is for working
  across many analyses.
- **Same rules as today:** self-hosted fonts, report content rendered as text, light theme
  by default, decisions go through the runner, and the loopback guard applies to the
  workspace too.

### 13.5 More hosts than GitHub in the first release

Yes: **GitHub, GitLab and Bitbucket Cloud** in the first release; Azure DevOps and
self-hosted GitHub Enterprise / GitLab next (the same adapters with a base URL).

The design that makes this cheap: **git does most of the work, the host API does little.**

- Everything that can be answered from a local clone is: diffs, file reads at a commit,
  `git grep` for search, branch heads (`git ls-remote`), merge bases, commit lists,
  rename detection. That code is written once and is identical for every host.
- The host adapter is left with what only the host knows: a pull/merge request's title,
  description, state, base and head, its merged commit, and its file count (to check the
  local diff against). That is a few calls per analysis.
- **A plain git remote works with no host at all** for `--compare` and ranges, so anyone
  can try Reachmap on any repository.
- Auth through each host's own CLI where one exists (`gh`, `glab`) or a token from a named
  environment variable (Bitbucket app password, GitLab token). Nothing stored; the same
  "never logged, never cached, never in a report" test for each.
- Host differences to pin with fixtures: GitLab says "merge request" and numbers them per
  project (`!123`); Bitbucket's diff and pagination differ; each host has its own file and
  rate limits; branch names with slashes need encoding everywhere.
- Code search through a host API is dropped entirely for the analysed repositories
  (local `git grep` is complete and time-pinned); a repository that is not cloned is
  cloned shallowly at the pinned commit rather than searched remotely.

### 13.6 Still open

1. Whether a CI integration (comment the report on a pull request, recommendation only)
   is in the first release or the second.
