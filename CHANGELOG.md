# Changelog

All notable changes to Reachmap are listed here, newest first. The version numbers follow
[Semantic Versioning](https://semver.org/). Until 1.0, a minor version may change the report
format or the configuration.

## 0.1.0 (2026-10-21)

The first release. It is early: it was built and tested against small invented repositories and
invented host responses shaped like the real APIs, and the known limits below are real. The milestone each item came from
is in parentheses.

### Milestones in this release

Every pull request that landed on `main` before 0.1.0, in order (the number is the pull request in the
private development repository; the public repository starts from a single commit of the result).

| Pull request | Milestone | Content |
|---|---|---|
| #1 | M1 | Synthetic fixtures: four invented repositories, 23 change scenarios with hand-written expected answers |
| #2 | M2 | Core model: findings, records, evidence, identities, claims |
| #3 | M3 | Configuration: `reachmap.toml`, validation, `${VAR}` expansion |
| #4 | M4 | Git layer and hosts: GitHub, GitLab, Bitbucket Cloud and plain git |
| #5 | M5 | TypeScript and JavaScript language adapter |
| #6 | M6 | Python language adapter |
| #7 | M7 | Entry points: React Router screens and FastAPI routes |
| #8 | M8 | Edges and the cross-repository walker: `http`, `package`, `contract` |
| #9 | M9 | Pipeline, findings and `reachmap analyse` |
| #10 | M10 | Reports: JSON, Markdown, HTML, the author view |
| #11 | M11 | Review layer: revisions, decisions carried forward by claim |
| #12 | M12 | The localhost runner, `reachmap serve` |
| #13 | M13 | The frontend workspace |
| #14 | M14 | MCP server and model notes, `reachmap mcp` |
| #15 | M15 | Test sources: Qase, TestRail, Playwright, Cypress |
| #16 | M13b | The workspace redesign (Mantine) |
| #17 | M16 | Next.js routes adapter |
| #18 | M16 | OpenAPI adapter |
| #19 | M16 | Django and Django REST Framework adapter, and the shared route-change guard |
| #20 | M16 | Express and NestJS adapter |
| #21 | M16 | Spring Boot adapter, with a Java and Kotlin language adapter |
| #22 | M14b | The workspace shows model notes |
| #23 | M19 | Documentation and examples (first part) |
| #24 | Hardening | Unmatched route patterns, unknown configuration keys, YAML specs |
| #25 | M18 | Evaluation tools: `reachmap audit`, `witness`, `sheet` |
| #26 | M17 | `reachmap init` |

The release step itself (version 0.1.0, packaging and the clean-install check, `docs/releasing.md`,
the release workflow) came after #26.

### What it does

- **Analyses one change** (a pull request, a comparison `base...head` or a range `a..b`) in one
  repository and follows it across the repositories you configure, hop by hop, to the screens and
  API routes a person can test (M9). Every inclusion and every exclusion cites a line pinned to a
  commit (M2, M9).
- **Never claims safety or coverage.** A suggested test case is a place to look and is never a
  mapped test. A read that fails is never reported as an absence: a record says what could not be
  established, and says whether it blocks (M2, M4, M9).
- **Reports** as JSON (the contract), Markdown and a single self-contained HTML file with no
  network use, plus an author view with the files beyond the diff, the other repositories and
  branches reached, and the unit tests to run (M10).
- **Revisions and decisions.** Each completed run of a change is the next revision of it. A
  decision (test, sample, skip with a reason; accept or reject a finding) is risk acceptance and is
  carried to the next revision by the claim it was about: current, needs review or orphaned. The
  HTML report exports decisions and `--decisions` imports them (M11).
- **A local runner and workspace**, `reachmap serve`: loopback only, a session token, one job at a
  time in a separate process with a hard time limit, and a workspace to browse reports, read the
  chains, triage areas with the keyboard, record decisions and read the notes a model client left.
  The workspace is built into the package, so installing it needs no npm (M12, M13, M13b, M14b). The licence notices of the third-party packages it
  bundles are in `reachmap/runner/static/THIRD_PARTY_LICENSES.txt`.
- **An MCP server**, `reachmap mcp`: a model client can read reports (fenced and labelled as
  untrusted) and add notes (stamped with the client's name, kept apart from the report, never
  findings), over standard input and output (M14).
- **Test sources.** Suggested cases from Qase and TestRail, and from Playwright and Cypress
  specs mapped to screens by route, read-only and never counted as coverage (M15).
- **Configuration** in `reachmap.toml`, validated in one pass with the key of every problem and
  never a configured value; an unknown key in any table is refused with the nearest allowed name;
  `${VAR}` expansion; credentials only by the name of an environment variable (M3).
- **A configuration that points at nothing is said.** A `routes_glob` (or a folder or search of
  another entry-point adapter) that matches no file is a blocking `routes_unmatched` record and an
  answer that says it is not complete, also when the change moved the routing files away.
- **Hosts.** GitHub (through `gh`), GitLab (through `glab` or a token), Bitbucket Cloud (a token)
  and plain git remotes for comparisons and ranges. Diffs, file reads, searches and merge bases
  come from git on a local clone, the same for every host (M4).

### Adapters

- **Languages**: TypeScript and JavaScript (M5), Python (M6), Java and Kotlin (M16).
- **Screens and routes**: React Router and FastAPI (M7), Next.js App Router and Pages Router
  (M16), Django and Django REST Framework (M16), Express and NestJS (M16), Spring Boot (M16),
  routes from a committed OpenAPI spec (M16).
- **Links between repositories**: `http` (an endpoints file, or an OpenAPI generated client, to
  server routes), `package` (a producer's tags and a consumer's pinned version, with adoption read
  from tag ancestry) and `contract` (one field of a shared model to the code that reads it)
  (M8, M16).
- A spec diff is a change: a removed, added or changed OpenAPI operation is a finding (M16).
- One guard for every route table: a route counts as removed only when the table of the new
  version was read whole, and as added only when the table of the old version was (M16).
- **A starter config**: `reachmap init SPEC...` reads repositories (a path, `file://`, `github:`, `gitlab:`, `bitbucket:` or `git:`)
  and writes a `reachmap.toml` with every guess marked: why, `path:line` evidence, a confidence word and "confirm". It detects the
  language, branches, aliases, entry points of every adapter, endpoints files and OpenAPI specs, contracts, tests, monorepo markers and
  the edges between repositories, never writes a secret, always writes a file that loads, and lists what it could not read (M17).

### Evaluation tools

- `reachmap audit` re-reads every line a report cites at its commit and says how many still match
  (`ok`, `mismatch`, `absent`, `unreadable`, counts only), with a structure check of ids,
  fingerprints and claims. A run that did not complete is never a pass (M18).
- `reachmap witness` counts, over independent cases written by a person, whether the report of an
  original change, analysed as of its merge time, cited a file that a later follow-up fix touched.
  It refuses cases that share a subject or a witness, and reverts, and gives counts, never
  percentages (M18).
- `reachmap sheet` writes a judging sheet (CSV, protected against formula injection, or Markdown)
  for people to fill in, and counts a filled one. A model does not fill a sheet (M18).

### Documentation and examples

- A README, a quickstart, the configuration reference, the adapters page with a guide for writing
  one, how to read a report, the security posture and how to measure whether the tool helps.
- `examples/`: four invented repositories, a configuration and a script that runs the quickstart
  on them.

### Known limits

Each adapter's page in [docs/adapters.md](docs/adapters.md) lists what it cannot read.

- The `event` and `library` edge kinds and Celery entry points are accepted in the configuration
  and not followed: a change that reaches them records that it did not follow them.
- `ticket_pattern` and `context` sources (an offline requirement export) are validated and not
  read.
- A route table is read from module-level declarations; factories, routers built in functions and
  framework settings chosen only by the environment are recorded as not read, not guessed.
  Descendant React Router `<Routes>` are not composed through the component's mount, Next.js
  middleware matchers, rewrites, redirects and i18n are not evaluated, Spring functional routes and
  JAX-RS are recorded and not read, and Django `i18n_patterns` and custom routers likewise.
- Imports are read from the code in the repository: no `node_modules`, workspace package names or
  `package.json` `exports`, no `sys.path`, editable installs or `setup.py` layouts, and no Gradle or
  Maven, Lombok or generated sources. Exclusions with evidence are stated for the first hop only.
- Links between repositories: the HTTP client side is the endpoints file (or an OpenAPI generated
  client) only, contract reads are Python only, and a package pin is one pattern over one manifest
  (a range is recorded as `pin_unresolved`).
- OpenAPI specs must be JSON, and a reference to another file is not followed.
- There is no YAML reader: an OpenAPI spec named `.yaml` or `.yml` is refused when the configuration
  loads, and one that becomes YAML later is a blocking `spec_unreadable`, with no finding for what
  changed in it.
- `gh` and `glab` were not run against real hosts in the project's tests, and no test source was
  run against a real service; `--pr` was exercised with a fake host only. There is no retry after a
  rate limit from a test source: the run says the source did not answer. Use a read-only
  credential for a first trial.
- A test source's Playwright or Cypress spec is mapped to a screen by the first argument of
  `goto` or `visit`: helpers and page objects are not followed.
- `reachmap init` reads the default branch only, by line patterns, and a guess is only a guess:
  confirm each one.
- `--as-of` does not move the commits a host reports for a pull request, the hosted test
  sources, or the tags of a producer, and the report says so.
- The advisory lock on a reports folder is untested on Windows and may not hold on a network file
  system. Keep one reports folder to one machine. On Windows a job's git processes are not swept
  when the job is stopped.
- A decision does not go stale when only the changed code's own body changes again: a decision is
  tied to what the change reaches, not to the change itself.
- Jobs of `reachmap serve` are kept in memory (the last 500): a restart forgets them. The reports
  on disk are the record.
- A model client's note that proposes a file is verified by finding the changed name anywhere in
  that file's code, not only where it is defined or imported from.
- Packaging: 0.1.0 is built as a wheel and an sdist and is not on PyPI; see
  [docs/releasing.md](docs/releasing.md).
