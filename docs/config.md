# Configuration: `reachmap.toml`

One file describes a workspace: the repositories, the links between them, and where test
cases and requirements can be read. Commands read it from `reachmap.toml` in the current
folder, or from the file named by `--config`. The file is TOML, with real comments.
`examples/reachmap.toml` is a complete, working file for four invented repositories; the
snippets below are cut from files like it.

Adding a repository is one `[repos."owner/name"]` table plus the edges it takes part in.
Nothing in the analyser names a repository.

An unknown key is refused, with an error that names the key, says which keys are allowed and
suggests the nearest one, in every table: the top level, `limits`, a repository's own keys,
`aliases`, `tests`, every kind of `entry_points`, `http_client`, `http_server` and `contract`,
each `[[edges]]` table, `[[test_sources]]` and their areas, and `[[context]]`. A value of the
wrong type or outside its range is refused too. So a mistyped `routes_glb` is an error at load,
not a default that is used without a word. One run of the loader reports every problem in the
file, not only the first. A message never repeats a value you configured (a remote, a path,
a token variable): it names the key and says what was expected.

Contents: [rules for values](#rules-for-values), [top-level keys](#top-level-keys),
[`limits`](#limits), [`repos`](#repos), [`edges`](#edges), [`test_sources`](#test_sources),
[`context`](#context).

## Rules for values

- **`${VAR}`** expands from the environment inside any string value. `$${` writes a literal
  `${`. A malformed reference (`${`, `${1X}`) is an error.
- **An unset or empty variable is "not configured", never the literal text and never an
  empty string.** Under `repos`, `edges`, `limits` and the top level that is an error that
  names the key and the variable, because a quietly missing remote would be an absence the
  tool made up. Only inside `test_sources` and `context` may a variable be left unset: the
  source is then kept as not configured, the run records `source_unconfigured` (it does not
  block the areas) and says which variable was missing.
- **Credentials are named, never written.** A variable whose name looks like a credential
  (a name containing `TOKEN`, `SECRET`, `PASSWORD`, `CREDENTIAL`, `API_KEY`, `PRIVATE_KEY`,
  `ACCESS_KEY` or a `PAT` part) is refused inside `${...}`. Put the variable's name in
  `token_env` instead: the value is a name such as `QASE_API_TOKEN` (capital letters,
  digits and underscores, at most 64 characters), it is never expanded, and the token is
  read from the environment only when a request is sent. A pasted token in `token_env` is
  refused without being shown; if you pasted one, remove it from the file and revoke it.
- **Remotes** are a URL, an scp-style `host:path`, or a path on this machine. A URL with a
  password, or with a user name other than for ssh (`git@host`), is refused: git's own login
  supplies credentials. A relative path resolves against the folder of the config file.
- **Paths inside a repository** (`path`, `routes_glob`, `spec`, `handlers`, `client`,
  `root`, `manifest`) are written relative to the repository root, with forward slashes.
  They are not machine paths and are not resolved.
- **Regular expressions** (`ticket_pattern`, `pin_pattern`) are Python regular expressions.
  Write them in single quotes: TOML single-quoted strings are literal, so no backslash needs
  doubling.
- A config file is TOML. A file ending in `.json` is refused, without being read, with the
  message "JSON configs are not supported; use a .toml file".

## Top-level keys

| Key | Required | What it is |
|---|---|---|
| `workspace` | yes | The workspace's name, a non-empty string. It is shown in reports and is part of the key that numbers revisions and carries decisions, so renaming it starts new histories. |
| `ticket_pattern` | no | A regular expression with exactly one group, the ticket key. It is validated, and the 0.1.0 analyser does not read it yet. |
| `limits` | no | The ceilings of a run. See [`limits`](#limits). |
| `repos` | yes | One table per repository. See [`repos`](#repos). |
| `edges` | no | The links between repositories. See [`edges`](#edges). |
| `test_sources` | no | Where suggested test cases are read. See [`test_sources`](#test_sources). |
| `context` | no | Where requirement context would be read. See [`context`](#context). |

## `limits`

Every limit is a whole number of 1 or more. Each is recorded in the report when it is
reached, so a short list is never taken for the whole list.

```toml
[limits]
hops = 3
hub_importers = 50
distinct_max = 10
timeout_s = 900
module_cap = 200
```

| Key | Default | What it does |
|---|---|---|
| `hops` | 3 | How many edges the change is followed across, past the repository it is in. An edge beyond it is not followed and the run records `hop_cap`. |
| `hub_importers` | 50 | A file imported by more files than this is a hub. The walk does not pass through it and records `hub_cutoff`, once per hub. |
| `distinct_max` | 10 | A name defined more often than this in non-test code is not distinctive. Links made only by that name are dropped and recorded as `not_distinctive`. The example workspace sets 3 because its repositories are tiny. |
| `timeout_s` | 900 | The ceiling of one run, in seconds. `reachmap analyse` checks it between the steps of a run (not in the middle of one), and a run that has reached it reports `timed_out` and no counts, never a partial list. Only `reachmap serve` kills a run that is stuck: it ends a job's process after this long. |
| `module_cap` | 200 | The most files followed at one hop. Files past it are not followed and the run records `module_cap` with the hop. |

## `repos`

A table per repository, named as you want it shown, usually `owner/name`. The name is what
`--repo` takes and what reports print.

```toml
[repos."acme/web"]
host = "github"
language = "typescript"
roles = ["frontend"]
branches = ["main", "release/*"]
aliases = { from = "tsconfig.json" }
entry_points = { kind = "react-router", routes_glob = "src/modules/*/routes.tsx" }
http_client = { kind = "endpoints-file", path = "src/api/endpoints.ts" }
tests = { markers = ["__tests__/", ".test.", ".spec."], command = "npx jest {paths}" }
```

### Keys of a repository

| Key | Required | What it is |
|---|---|---|
| `host` | yes | Where change requests live: `github`, `gitlab`, `bitbucket` or `git`. The plain `git` host has no pull requests: `--compare` and `--range` work, `--pr` is refused. |
| `remote` | for `host = "git"` | Where the repository is cloned from (a URL, `host:path` or a path). For the three hosted kinds it is optional: the host builds the clone URL from the repository name. See the rules above. |
| `language` | yes | `typescript`, `javascript`, `python`, `java` or `kotlin`. The language adapter reads code of that language (a repository with both Java and Kotlin sources is read by one adapter, whichever of the two you declare). |
| `roles` | yes | A non-empty list of `frontend`, `backend`, `contract`, `library` or `worker`. Roles say what the repository is for. |
| `branches` | no | The branches the branch matrix reads, as names or patterns (`release/*`; `*` also matches `/`). Default: `["main"]`, so a repository whose main branch is called `master` must say `branches = ["master", "release/*"]`. Reachmap reads committed work on the refs the remote has: uncommitted changes in a working folder are never seen. |
| `primary_branch` | no | The branch treated as the primary one. It must be a branch name (not a pattern) that `branches` lists or matches. Default: the first name in `branches` that is not a pattern, else `main`. |
| `aliases` | no | How import aliases are read. See [`aliases`](#aliases). |
| `entry_points` | no | What a person can test in this repository: screens, API routes. See [`entry_points`](#entry_points). |
| `http_client` | no | How this repository calls other repositories over HTTP. See [`http_client`](#http_client). |
| `http_server` | no | A committed spec that describes the routes this repository serves. See [`http_server`](#http_server). A repository takes `http_server` or a routing `entry_points`, not both (a `celery` entry point has no routes and may go with one). |
| `tests` | no | Which files are tests, and the command that runs them. See [`tests`](#tests). |
| `service_marker` | no | Python: a file name that marks a service folder in a monorepo, such as `pyproject.toml`. Every folder holding it is a source root of its own, so two services can both have a package called `app`. |
| `library_roots` | no | Python: a list of folders of a monorepo that hold shared libraries visible to every service, such as `["libs/common"]`. |
| `contract` | no | Marks a repository that publishes data contracts. See [`contract`](#contract). |

The values `host`, `language` and `roles` are checked against fixed lists: `host` is one of
`github`, `gitlab`, `bitbucket`, `git`; `language` is one of `java`, `javascript`, `kotlin`,
`python`, `typescript`; each role is one of `frontend`, `backend`, `contract`, `library`, `worker`.

### `aliases`

Not a registered kind: a plain table. It tells the JavaScript adapter where its import
aliases are.

```toml
aliases = { from = "tsconfig.json" }
```

| Key | What it does |
|---|---|
| `from` | The file the aliases are read from, `tsconfig.json` or `jsconfig.json`: `compilerOptions.paths`, `baseUrl` and `extends`. Without it the adapter uses `tsconfig.json`, then `jsconfig.json`, when the repository has one. A config that cannot be read completely is recorded as blocking, never read as "no aliases". |

### `entry_points`

The adapter that finds the places a person can test. `kind` is required and picks the
adapter. A repository with no `entry_points` has none: changes in it reach no screen or
route of its own.

#### `react-router`

Screens from React Router `<Route>` elements.

```toml
entry_points = { kind = "react-router", routes_glob = "src/modules/*/routes.tsx" }
```

| Key | What it does |
|---|---|
| `routes_glob` | The routes file or files, as one glob or a list of globs. Default: `src/**/routes.tsx`, `src/**/routes.jsx`, `src/**/routes.ts` and `src/**/routes.js`. Set it to your own routes files. A glob that matches no file is a blocking `routes_unmatched` record and an answer that says it is not complete, never an empty list of screens; when the change deleted or moved the routes files, the record says they matched before the change. |

#### `nextjs`

Screens and API routes from the folder tree of a Next.js project: the App Router (`app/`)
and the Pages Router (`pages/`).

```toml
entry_points = { kind = "nextjs", root = "apps/shop", base_path = "/docs" }
```

| Key | What it does |
|---|---|
| `root` | The folder of the Next.js project inside the repository. Default: the repository root. Several projects in one repository need one `[repos]` entry each. |
| `page_extensions` | The extensions that make a page, as a non-empty list of letters and digits. Default: `tsx`, `ts`, `jsx`, `js`. |
| `base_path` | The `basePath` of the Next.js config, put before every URL. It starts with a slash. The adapter does not read `next.config`, so say it here. |

#### `fastapi`

API routes from FastAPI decorators and `include_router` mounts, with full mounted paths. It
takes no option besides `kind`.

#### `django`

API routes from `urls.py` chains of Django and Django REST Framework.

```toml
entry_points = { kind = "django", root_urlconf = "config.urls" }
```

| Key | What it does |
|---|---|
| `root_urlconf` | The dotted name of the root URLconf, for when `ROOT_URLCONF` cannot be read from the settings (it comes from the environment, or several settings files disagree). It replaces what the settings say. |
| `settings` | Where `ROOT_URLCONF` is read from: a file, a glob or a folder of the repository. Default: every file that assigns it. |

#### `express`

API routes from Express apps and routers across files, with mounts and prefixes. It takes no
option besides `kind`. The framework is told apart by what a file imports, never by names.

#### `nestjs`

API routes from NestJS controllers, modules, global prefixes and versioning. It takes no
option besides `kind`.

#### `spring`

API routes from Spring MVC controllers in Java and Kotlin: `@RestController`, `@Controller`,
`@RequestMapping` on a class and the method mappings (`@GetMapping`, `@PostMapping`, and the
others), with constants in paths followed and the context path put first. The repository's
`language` is `java` or `kotlin`.

```toml
language = "java"
entry_points = { kind = "spring", context_path = "/api" }
```

| Key | What it does |
|---|---|
| `context_path` | A plain path that starts with a slash, put before every route. It replaces what `server.servlet.context-path` says in `application.properties` or `application.yml`. Without it the adapter reads that key from the module's configuration and, when it cannot, marks the prefix unknown. |

#### `celery`

Accepted so a worker repository can say what it is, and not read yet: no adapter follows
Celery tasks in 0.1.0. A change that reaches such a repository records
`entry_points_unread`, which says what the repository runs was not established.

### `http_client`

How a repository calls another over HTTP, so that a change to a server route is followed to
the client code that calls it.

#### `endpoints-file`

A frontend that keeps its backend URLs in one file as exported constants or builders.

```toml
http_client = { kind = "endpoints-file", path = "src/api/endpoints.ts" }
```

| Key | What it does |
|---|---|
| `path` | The endpoints file. Default: `src/api/endpoints.ts`. A URL part that cannot be evaluated statically (an imported base URL, a computed value) makes the URL partial, and it is kept and said, never dropped. |

#### `openapi`

A repository that calls the API through a generated client.

```toml
http_client = { kind = "openapi", spec = "api/openapi.json", client = "src/api/generated" }
```

| Key | What it does |
|---|---|
| `spec` | Required. The copy of the spec the client was generated from, a path inside the repository, JSON (a `.yaml` or `.yml` name is refused). |
| `client` | Required. The generated client files: a file, a folder or a glob, or a list of them. The code that defines the operations is not a call of them. |

### `http_server`

A committed spec that describes the routes a repository serves, for any language.

#### `openapi`

```toml
http_server = { kind = "openapi", spec = "api/openapi.json", handlers = "app/handlers/*.py" }
```

| Key | What it does |
|---|---|
| `spec` | Required. The committed spec, a path inside the repository. JSON is read. A name ending in `.yaml` or `.yml` is refused when the file loads (YAML is not read: commit a JSON copy of the spec). A spec that becomes YAML later is a blocking `spec_unreadable` record. |
| `handlers` | The files that hold the functions the operations are served by: a path, a glob or a list of them. Handlers are found by `operationId`. Without it no code is tied to an operation, a change to code reaches no route, and the run says so. |

### `tests`

Not a registered kind: a plain table.

```toml
tests = { markers = ["__tests__/", ".test.", ".spec."], command = "npx jest {paths}" }
```

| Key | What it does |
|---|---|
| `markers` | Which paths are tests, matched by path segment or file-name part, never as a substring. An empty list means no file is a test; leave the key out to use the defaults. `foo/` is a folder, `.test.` a part of the file name, `test_` a name prefix, `_test` a suffix before the extension. Defaults: JavaScript and TypeScript `__tests__/`, `test/`, `tests/`, `.test.`, `.spec.`; Python `tests/`, `test/`, `test_`, `_test`, `conftest.py`. |
| `command` | A command that runs tests, with `{paths}` replaced by the shell-quoted paths of the test files the change reaches. A command without `{paths}` is kept as written. Without a command the author view still lists the test files. |

### `contract`

Marks a repository whose models other repositories read (a schema or contracts package).

#### `pydantic`

```toml
contract = { kind = "pydantic", root = "contracts/models", package = "acme_contracts", versions = "git-tags", tag_prefix = "v" }
```

| Key | What it does |
|---|---|
| `root` | The folder of the models in the repository. Only changes under it are contract changes. Default: the whole repository. |
| `package` | The importable package name consumers use. A `contract` edge uses it to find the consumer's code that imports the package, unless a `package` edge between the same two repositories gives an `import_marker`, which wins. A `package` edge uses it only to tell that a manifest names the package when no line matches `pin_pattern`. |
| `versions` | `git-tags` is the only source of versions and the value to write: the real versions of the package are tags of the repository, never the version in a manifest. |
| `tag_prefix` | The text before the version in a tag, such as `v` for `v1.9.0`. A prefix that matches none of the repository's tags is recorded (`tag_prefix_unmatched`) and the change is read as unreleased. |

## `edges`

One `[[edges]]` table per link along which a change is followed. Each names two repositories
that exist under `[repos]`. Two edges that are the same (same kind, ends and manifest) are
refused, and so is an edge from a repository to itself, except `library`.

### Edge kinds

| Kind | Ends | Followed in 0.1.0 |
|---|---|---|
| `http` | `from` is the client, `to` the server | yes |
| `package` | `producer` and `consumer` | yes |
| `contract` | `from` and `to`, or `producer` and `consumer`, not both | yes |
| `event` | `from` and `to` | no: declared, recorded as `edge_unfollowed` |
| `library` | `from` and `to` | no: declared, recorded as `edge_unfollowed` |

An edge of a kind that is not followed is still useful as a statement: when a change reaches
code at one end, the report lists it under "Not followed" instead of staying silent.

#### `http`

A client repository calls routes of a server repository. Matching is by URL segments: a
client parameter matches only a route parameter, the most specific route wins, and a literal
in a parameter position says so.

```toml
[[edges]]
kind = "http"
from = "acme/web"
to = "acme/api"
```

| Key | What it does |
|---|---|
| `from` | The client repository. |
| `to` | The server repository. |

#### `package`

A repository that publishes a package (usually the `contract` repository) and a repository
that pins it in a manifest.

```toml
[[edges]]
kind = "package"
producer = "acme/contracts"
consumer = "acme/api"
manifest = "requirements.txt"
pin_pattern = '^acme-contracts==(.+)$'
import_marker = "acme_contracts"
```

| Key | What it does |
|---|---|
| `producer` | The repository that publishes the package. |
| `consumer` | The repository that pins it. |
| `manifest` | Required. The file in the consumer that holds the pin, such as `requirements.txt` or `package.json`. |
| `pin_pattern` | Required. A regular expression with exactly one group, the pinned version, matched against the lines of the manifest at the head of each of the consumer's branches. A manifest that names the package but has no line the pattern matches (a range, an extra) is recorded as `pin_unresolved`, and adoption is left undecided. |
| `import_marker` | Required. The name consumers import the package under; it finds the consumer's code that reads the package. |

#### `contract`

A change to one field of a shared model, followed to the consumer code that reads the field.
Only distinctive field names count; a generic name is dropped and recorded.

```toml
[[edges]]
kind = "contract"
producer = "acme/contracts"
consumer = "acme/api"
```

| Key | What it does |
|---|---|
| `producer` | The repository that holds the model (`from` is the same key). |
| `consumer` | The repository that reads it (`to` is the same key). |

#### `event`

A publisher and a subscriber. Declared, not followed in 0.1.0.

```toml
[[edges]]
kind = "event"
from = "acme/api"
to = "acme/worker"
topics_glob = "**/topics.py"
```

| Key | What it does |
|---|---|
| `from` | The publishing repository. |
| `to` | The subscribing repository. |
| `topics_glob` | A glob for the files that name the topics. |

#### `library`

A shared library inside a monorepo and the code that imports it. Declared, not followed in
0.1.0. It is the one kind that may connect a repository to itself.

| Key | What it does |
|---|---|
| `from` | The library repository. |
| `to` | The importing repository. |

## `test_sources`

Where suggested test cases come from. A suggestion is a place to look. It is never listed as
coverage and never closes a gap. Sources are read only, with GET requests, and a source that
is not configured or cannot be read costs only its own suggestions: the areas still come
from the code.

A hosted source (`qase`, `testrail`) with a missing `token_env`, `project` or `base_url`, or
with an unset variable, is not configured, which is not an error. The keys of a source are
checked for type and range, and an unknown key is refused with the nearest name.

```toml
[[test_sources]]
kind = "qase"
project = "ACME"
token_env = "QASE_API_TOKEN"

[[test_sources.areas]]
name = "Billing"
route_prefixes = ["/billing"]
modules = ["billing"]
suites = [12]
```

### Keys of every hosted source

| Key | What it does |
|---|---|
| `kind` | Required: `qase`, `testrail`, `playwright` or `cypress`. |
| `token_env` | The name of the environment variable that holds the credential. |
| `base_url` | The service address, `http` or `https`, with no user, password, query or fragment, so it cannot carry a token. Required to run a TestRail source. The credential is sent only over https. |
| `page_size` | Cases per request. At most 100 for Qase and 250 for TestRail. |
| `max_pages` | The most pages read. A list cut by it is listed as far as read, and the run records `suggestions_incomplete`. |
| `areas` | Which cases belong to which areas of a run. See [Areas](#areas). |

#### `qase`

| Key | What it does |
|---|---|
| `project` | The Qase project code. The credential is sent in the `Token` header. |

#### `testrail`

| Key | What it does |
|---|---|
| `project_id` | The TestRail project id, a whole number. |
| `suite_id` | The suite id, for a project in multi-suite mode. |

`token_env` holds one variable with `login:key`, sent as a Basic `Authorization` header.

#### `playwright`

Local end-to-end specs as a test source, with no test management system. A spec that calls
`page.goto("/path")` is suggested for the screen with that route. Spec sources take no
`areas`: the route table decides.

```toml
[[test_sources]]
kind = "playwright"
repo = "acme/web"
```

| Key | What it does |
|---|---|
| `repo` | Required. The repository the specs are in. It must be configured under `repos`. |
| `screens_repo` | The repository whose route table the visited paths are matched against. Default: `repo`. It needs `entry_points` of kind `react-router` or `nextjs`. |
| `globs` | Which files are specs. Default: `e2e/**` and `tests/**` with `.spec` or `.test` (Playwright). |

#### `cypress`

Local end-to-end specs, for `cy.visit(...)`. Like `playwright`, it takes no `areas`.

| Key | What it does |
|---|---|
| `repo` | Required. The repository the specs are in. It must be configured under `repos`. |
| `screens_repo` | The repository whose route table the visited paths are matched against. Default: `repo`. |
| `globs` | Which files are specs. Default: `cypress/e2e/**` and `cypress/integration/**` with `.cy` or `.spec`. |

### Areas

`[[test_sources.areas]]` ties cases of a hosted source to areas of a run. An area table
matches an area of a report when one of its `route_prefixes` covers the area's route on a
segment boundary, or the area's module is one of its `modules`. An area with neither matches
nothing, and a run says so.

| Key | What it does |
|---|---|
| `name` | Required. A label for the area. |
| `route_prefixes` | Route prefixes that start with a slash, such as `/billing`. |
| `modules` | Product modules (the folder a routes glob matches, or the service folder), case ignored. |
| `suites` | Qase suite ids, or TestRail section ids, descendants included. A case with no suite is in scope only for an area that names no suites. |

## `context`

Where requirement context would be read.

```toml
[[context]]
kind = "jira-snapshot"
path = "${ACME_JIRA_SNAPSHOT}"
```

### `jira-snapshot`

| Key | What it does |
|---|---|
| `path` | A path on this machine to an offline export. A relative path resolves against the folder of the config file. |

The file is validated and accepted, and 0.1.0 does not read requirement context yet: a run
that has a context source configured says in its notes how many were not read, so that no
context is never taken for no requirements.
