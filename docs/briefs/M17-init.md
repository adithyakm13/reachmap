# M17 brief: `reachmap init`

Goal: `reachmap init <repo specs...>` reads each repository (local path, `file://`, or
`github:owner/name`, `gitlab:group/name`, `bitbucket:ws/name`, `git:<url>`) through the M4
git layer and writes a starter `reachmap.toml` from a text template, with every guess
marked by a comment for a person to confirm. Done when running it on the M1 fixtures
(and the M16 fixtures that have merged) produces a config that analyses them correctly
(the e2e scenarios pass with the generated config after only the documented
confirmations). Read `docs/CONTEXT.md` sections 6, 7.2, 10 (step 12), 11, 13.2 (tomllib
reads, init writes from a template), 13.5; the M3 config notes (keys, validation,
registries), M4 (git layer, hosts, auth), M5/M6/M7/M8/M16 notes (what each adapter needs
in config).

Python 3.11+ stdlib only. No network in default tests (fixtures as file:// remotes; host
specs resolved through recorded host responses or the plain git mode).

## Files

```text
reachmap/init/__init__.py     detect(spec) -> RepoGuess; render(guesses) -> str (TOML text)
reachmap/init/detect.py       language, roles, routes framework, http client, server framework, contract, tests, branches, aliases, service markers
reachmap/init/edges.py        edge guesses between repos
reachmap/init/template.py     the TOML text template and escaping
cli.py                        `reachmap init SPEC... [--out reachmap.toml] [--force]`
tests/test_init_*.py
```

## Detection (each guess carries evidence: file and line, and a confidence word)

- Host: from the spec prefix; plain paths and `file://` become `host = "git"` with
  `remote` (relative to the output file when possible).
- Default branch from `ls-remote --symref HEAD`; release branch patterns from existing
  heads (`release/*`, `release-*`, `hotfix/*`), commented as guesses.
- Language by file counts at the default branch (typescript, javascript, python, java,
  kotlin), ignoring vendored folders (node_modules, dist, build, .venv, vendor).
- Aliases: `tsconfig.json` (follow `extends`), `jsconfig.json`; prefer the build tool
  config when present (vite.config, webpack) and say so.
- Entry points, for the adapters on main at the time of writing: React Router (routes
  files with `<Route` or `createBrowserRouter`, glob guessed from where they are), Next.js
  (`app/` or `pages/` with `next` in package.json, kind `nextjs`), FastAPI
  (`APIRouter`/`FastAPI(`), Django (`urls.py` + settings, kind `django`), OpenAPI (a
  committed `openapi.json` with paths: `http_server = { kind = "openapi", ... }`), Celery
  (`@app.task`/`shared_task`) as `celery` with a comment that it is not followed yet.
  Express/NestJS (`express`, `nestjs`) and Spring (`spring`, languages `java`/`kotlin`) are
  being merged in parallel: build detection through a small detector registry (one
  function per kind, registered next to the adapter or in `reachmap/init/detect.py`) so
  their detectors are one registration each when those branches land; if they are on main
  when you finish, add them. Read each adapter's notes (docs/briefs/M16-*.md) for the
  config keys it needs.
- HTTP client: an endpoints file (a module exporting many URL-looking constants; pick the
  one with the most), OpenAPI spec files.
- Contract: Pydantic models folder (classes inheriting `BaseModel` concentrated in one
  folder), package name from pyproject, versions from tags with a common prefix.
- Tests: markers from the folders and file names present (`__tests__/`, `.test.`,
  `.spec.`, `tests/`, `test_`), command from package.json scripts (`jest`, `vitest`),
  pytest config, Maven/Gradle.
- Service marker for monorepos (several `pyproject.toml` / `package.json` below the root).
- Edges: `package` when a repo's manifest pins another repo's package name (requirements,
  pyproject dependencies, package.json), with `pin_pattern` generated and checked to have
  one group; `http` from a frontend repo to backend repos (guess, commented); `contract`
  alongside package edges from a contract repo.

## Output

- A TOML text built from a template with real comments; values escaped for TOML (strings
  with quotes, backslashes, control characters); regexes written as single-quoted literal
  strings unless they contain a single quote (then escaped basic strings).
- Every guessed value has a `# guess: <why>, evidence <path:line>; confirm` comment above
  it; values read directly (e.g. remote) have no comment.
- No secret ever written: token variables appear as names only (`token_env =
  "QASE_API_TOKEN"` commented out with an explanation).
- The output must load with `config.load` and validate (a test runs the loader on every
  generated file); `--force` needed to overwrite an existing file; writes atomically.
- Repos it cannot read: the spec is listed in a comment block "Could not read" with the
  reason; never silently skipped; exit code 3 when any spec failed, output still written
  for the rest.

## Tests

- On the M1 fixtures (four repos as file:// remotes): the generated config, after a
  documented minimal set of confirmations applied by the test as text edits (ideally none),
  equals the fixture `reachmap.toml` in meaning (repos, roles, kinds, edges, limits
  defaults) and the e2e scenarios pass with it.
- Each detector on small generated repos (positive and negative cases, generic decoys:
  a file named `routes.ts` that is not a router, a `models/` folder without BaseModel).
- Template escaping fuzz: values with quotes, newlines, `'''`, unicode; the output parses
  and round-trips the values.
- Unreadable repo: listed, exit 3, others written.

## Implementation notes

Where the code differs from, fills in, or goes beyond the brief. Each point is covered by a test unless it says otherwise.

### Files

```text
reachmap/init/__init__.py   detect, render, build, generate (specs in, text and failures out)
reachmap/init/source.py     spec parsing (`NAME=SPEC`), reading a repository through the git layer, names
reachmap/init/tree.py       a repository at one commit: file list, three-state reads, scans with a file budget and a size limit, JSON with comments
reachmap/init/guess.py      Note, Guess, RepoGuess, EdgeGuess, Facts, SpecError
reachmap/init/registry.py   the detector registry (`register(section, kind, fn, languages=...)`), Ctx, Found
reachmap/init/detect.py     the orchestrator and the generic detectors: language, branches, aliases, tests, service marker, library roots, roles, end to end specs
reachmap/init/entries.py    entry points: react-router, nextjs, fastapi, django, celery, express, nestjs
reachmap/init/clients.py    endpoints file, OpenAPI specs (server handlers, generated client)
reachmap/init/contract.py   Pydantic contract: models folder, package name, tag prefix
reachmap/init/facts.py      what a repository provides and depends on (manifests), what its code imports
reachmap/init/edges.py      package, contract, http and event edge guesses, pin patterns
reachmap/init/template.py   the TOML text, escaping, per-repository check with the loader
reachmap/cli.py             `reachmap init SPEC... [--out FILE | --out -] [--force]`
reachmap/gitlayer.py        `Git.default_branch` (ls-remote --symref HEAD; found, absent or unreadable, recorded)
reachmap/config.py          `languages()` and `kinds(section)` (what the registries accept), read by the detectors
tests/support_init.py, test_init_detect.py, test_init_edges.py, test_init_template.py, test_init_source.py, test_init_cli.py, test_init_e2e.py
```

Another adapter's detector is one `registry.register(...)` call (see the docstring of `registry.py`). Express, NestJS and Spring (merged into this branch from main) are
detectors in `entries.py`; Spring is registered for the languages `java` and `kotlin` and the language is counted by file suffix like the others (`config.languages()` says which
are accepted).

### Choices

- Names. A spec is `github:owner/name`, `gitlab:group/name`, `bitbucket:ws/name`, `git:<url>`, a local path or `file://`; `NAME=SPEC` is an addition: the config needs
  names like `acme/web` and a folder is called `acme-web`. Without it a hosted repository is `owner/name`, a `git:` URL its last two path parts, a path its folder name.
  Two specs that get one name are exit 2 (nothing is read). `workspace` is the owner every name starts with (a low confidence guess), else `workspace`.
- Remotes. A path or `file://` becomes a path relative to the output file (`./web`, `../repos/web`), absolute with a comment when it is more than four levels away. A
  host spec writes no `remote` (the host derives it). A URL has its credentials removed (`redact.transport_url`) with a comment. Reading a host spec needs no API call: the
  clone URL is the host's web address; tests redirect it to a local repository with git's own `url.<base>.insteadOf`.
- Default branch: read from `ls-remote --symref HEAD`, no comment. When HEAD names no branch (detached, old server) the first of main, master, trunk, develop that exists is used
  and the branch is marked a low confidence guess. Release patterns (`release/*`, `release-*`, `hotfix/*`) are guesses from existing heads, evidence `refs/heads/...`.
- Evidence is `path:line` of the file read, with line 1 for a whole file. Where the evidence is not a file (a branch, the tags a prefix was read from, a workspace name from
  repository names) it is the ref, `tags ...`, or absent; those are the only guesses without a `path:line`.
- Aliases. The brief says to prefer the build tool config. The JavaScript adapter reads only JSON (a `babel.config.js` or webpack config named in `aliases.from` is a blocking
  unreadable), so `from` is always `tsconfig.json` or `jsconfig.json`; a vite or webpack config that sets `alias` is said in the comment (path:line) and never pointed at.
  Of several configs the one whose `paths` targets exist in the tree wins (`extends` followed when relative): the M1 web repository has a stale `jsconfig.json` whose
  `app/*` targets do not exist, and using it makes 15 scenarios fail; the comment says why it was not used.
- One table per repository: of several entry-point candidates the best by evidence wins (Next.js over React Router in one repository, a server over Celery) and the others
  are named in a comment. A committed OpenAPI spec is a `http_server` only when no code framework was found. A module of URL constants in a repository that serves routes
  (a `config.ts` with a prefix) is not an `http_client`; it is said.
- Edges. `package`: a pinned dependency (`==` in requirements and pyproject, a number in package.json and poetry) of a package another repository declares, one manifest
  per pair (requirements.txt, then pyproject.toml, then package.json); the pattern is written for that line and checked to compile, have one group and read a version;
  without a readable pin the dependency is only said. `contract` goes with a package edge from a repository that has a contract table to a Python consumer. `http`: client
  repository to server repository; a URL segment on both sides makes it medium confidence, none and exactly one server low, none and several writes no edge and says so.
  `event`: a Celery worker with a topics module (two or more dotted string constants in `topics.py`) and a repository that serves routes, low confidence, comment says no
  publisher was found and no adapter follows events. The fixtures expect it (`edge_unfollowed edge:event:acme/api:acme/worker`).
- Roles: from what was found (frontend for screens, backend for routes or tasks, contract for concentrated models in a repository that serves nothing, `library` with a low
  confidence when nothing was found). A repository with Next.js is `frontend` and, with route handlers, `backend`.
- A table the loader refuses (a kind that is not registered) is written as `# not written: ... the loader refused it: <message>`; a refused required key leaves the repository out
  and lists it under "Could not read". Each repository is checked on its own with `config.loads`, the whole file at the end (a failure there is a bug, not output).
- When no repository can be read no file is written (a file with no `[repos]` does not load); the reasons go to standard error, exit 3. With some readable, the file is written
  and the exit code is 3.
- `Git.default_branch` and `config.languages()` / `config.kinds()` are the only edits outside `reachmap/init` and `cli.py`.

### The documented confirmations

For every fixture workspace (M1 acme, Next.js, OpenAPI, Django, Express, NestJS and Spring) the generated config passes every scenario with exactly one edit, `distinct_max = 10` to
`distinct_max = 3` (the scenarios define "a generic name" with it; nothing in a repository says what a team's limit is, so the default stays). No edit for names (given with
`NAME=SPEC`), roles, kinds, aliases, library roots, the event edge or the edges. Without the edit s12 and s19 fail (tested), so the check is not vacuous. The generated config
means the fixture config for repositories, roles, kinds and options and edges; only `tests` differs: the markers present in the repository at its default branch (a fixture
config lists `.spec.` where no spec file is) and a command (`npx jest {paths}` from the package.json test script, `pytest {paths}` from the style of the tests).

### Known limits

- Detection reads the default branch only. A repository whose routes live on another branch, or only in generated code, is not seen.
- Source scans are regular expressions over lines, not parses: a `<Route` in a string, a `class X(BaseModel)` spelled across lines or a decorator built by a helper can be missed
  or, rarely, matched. A comment line is skipped, a docstring only when it starts on its own line. Each scan reads at most 20,000 files and no file over 1 MB (listed as not read).
- One language per repository (the dominant one; a second language of a quarter or more of the files is said). One `entry_points` table, one `http_client` per repository.
- `routes_glob` is exact paths, a `*` over one folder name, or a brace list of up to 12 files; more files that do not fit one glob leave `routes_glob` out (said).
- `package` edges: Pipfile, `setup.py` and `Cargo` style manifests are not read; poetry dependencies given as inline tables, `>=` and `~=` ranges are not pins (said). The import
  name of a Python package is `package-dir`, the first poetry `packages`, or the project name when a folder of that name is a package; otherwise the project name with dashes as
  underscores (medium confidence).
- `http` edges compare URL segments, not routes: an edge is a guess for a person to confirm, the analysis does the matching. Next.js route hints come from folder names, FastAPI,
  Django and Express from string literals, Nest from literals and constants of the repository.
- `service_marker` is written for JavaScript monorepos as the brief asks, but only the Python adapter reads it today (the comment says so).
- Spring: a controller is a file with an `org.springframework` import and a controller or request mapping annotation outside `src/test/`; a build file naming Spring only
  raises the confidence (the fixture's pom.xml names nothing). Test markers for JVM repositories are `src/test/`; the command is `mvn test` or `./gradlew test` from the build file.
  Paths written as constants are not read for the http edge hints (an edge is still written when one server is the only candidate).
- Playwright and Cypress: a `[[test_sources]]` table is written when the framework is configured, a spec visits a literal URL and a repository with screens exists; otherwise a
  comment says what is missing. Qase, TestRail and Jira blocks are commented examples with `token_env` named in a comment only.
- Not run against a real hosted repository (no network in the suite and none used); host specs are tested through git's `insteadOf`, the same code path as a real clone URL.

### Tests, speed

New modules (all pass on 3.11, 3.12 and 3.13): `test_init_detect` 75 (detectors, decoys, always-loads fuzz of 150 random file mixes), `test_init_edges` 17, `test_init_template` 17 (escaping
fuzz of 300 strings, hostile names), `test_init_source` 15, `test_init_cli` 11, `test_init_e2e` 48 (the six fixture workspaces: the scenarios of M1 23, Next.js 18, Django 14, OpenAPI 10,
Express and NestJS 25 and Spring through `reachmap analyse` with the generated config, plus meaning checks). The whole suite is 4,820 tests on 3.12 and 3.13 (4,825 on 3.11, which
skips one fewer). Parallel runner (4 workers) on a shared machine: 3.12 851 s (load 2 to 14), 3.11 484 s, 3.13 407 s; one serial `unittest discover` on 3.12 857 s (load 3 to 12). The
only failures in every run are the four tests of `test_frontend_static.BuiltFrontend`: they read the untracked `frontend/dist` build of the main checkout, which is stale (they fail
the same on a clean checkout of main with that folder copied in, and are skipped without it); nothing of this branch touches them. `tests/.timings.json` was not refreshed.

### Review fixes

Where this section and the text above disagree, this section is the code.

- **Nothing a repository holds crashes a run.** A file nested past the parser (package.json, tsconfig.json, an OpenAPI spec, pyproject.toml: `RecursionError`) is unread for
  that file ("nested too deeply") and listed. Aliases are read with the JavaScript adapter's own loader (`load_aliases`, JSON with comments to a depth limit, `extends`
  followed), so `tree.jsonc` is gone. Manifest tables are read through `common.dig` (a table that is not a table is empty). `generate` catches anything else a repository
  raises and lists it under "Could not read" as `could not be analysed: <type>: <message>` (scrubbed), and writes the rest.
- **What the checkout did not write** (symlinks, submodules, names that are not UTF-8, unsafe paths) is listed in the repository's block, and a skipped tsconfig.json or
  jsconfig.json is an alias candidate with an error note. A spec file that does not parse is listed. **Exit code 3 is also for any file that could not be read**; the summary
  line says how many (`N files could not be read`), standard error names the first ten, the file lists up to 25 per repository. Absence notes are conditional: "no entry
  points ... found in the files that could be read (N could not be read)", and the same for "No edges were found".
- **Standard output by default.** `reachmap init SPEC... > reachmap.toml` (context section 11) works as written: without `--out` the text goes to standard output and the
  summary to standard error; `--out FILE` writes atomically and needs `--force` to replace; `--out -` is the same as no `--out`.
- **YAML specs and JSON files named like a spec but with no version key or no paths** each get a note ("YAML is not read; convert the spec to JSON"). A vite or webpack alias
  with no tsconfig/jsconfig is still said. `RepoGuess.spec` is the scrubbed form of the spec and the "Could not read" block scrubs again where it writes.
- **React Router** needs a react-router dependency or an import from `react-router*` in that file for the table to be high confidence. A `<Route path element>` JSX element with
  neither is still read, with a low confidence and a note saying so (the M16 Next.js client fixture has such a routes file and its scenarios need the screens), unless the file
  imports another router (Solid, Vue, wouter, TanStack, ...), in which case it is a note and no table. Router creation calls (`createBrowserRouter`, `useRoutes`) count only
  where react-router is imported; a `<Route` inside a template literal is nothing.
- **Endpoints file** needs an HTTP call site (a file that imports it, uses one of its constants and calls fetch, axios, a client or an `api...` wrapper) or API-looking paths
  (`/api`, `/rest`, `/graphql`, `/v1`); three constants at least unless a call site exists. Otherwise a note says the constants were not taken.
- **http edges** ignore a URL segment that more than one candidate server has; with several servers and no segment only one has, no edge is written and a note says why.
  **Event edges**: one edge, from the first backend, and a note naming the others. **Package edges**: the dependency's own spelling is used (a one line array holds several), an
  indented requirement line matches, a `^` or `~` range is a medium confidence edge that says "a range; its lower bound is read". Edge evidence names its repository
  (`acme/api:requirements.txt:3`).
- **Next.js** is classified with the adapter's own routing table (`adapters/nextjs/routes.classify`: the same files, the same URLs, route hints from its API paths). The
  endpoint constant reader is not shared with the http client adapter (that one evaluates constants for matching; init only needs to count and place them).
- `registry.register` refuses a kind the config does not accept; `Tree` bounds the text it keeps (64 MB, then it re-reads from the cache folder); "1 python file".
- `NAME=SPEC`: NAME is letters, digits, `.`, `_`, `-` and `/` with no colon, and the part after the first `=` is the spec; a relative path that holds an `=` and looks like a
  name (`a/b=c`) is read as NAME=SPEC, so write `./a/b=c`.
