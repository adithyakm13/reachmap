# M3 brief: config

Goal: load and validate `reachmap.toml` (context 7.2 and 13.2) into typed, immutable
config objects. TOML is the only format: a `.json` file is refused with a clear error
(owner decision of 2026-10-22; the brief first asked for a legacy JSON loader, since removed).
Read `docs/CONTEXT.md` sections 7.2, 7.4, 8 and 13.2 first.

Python 3.11+, standard library only (`tomllib`, `os`, `re`, `fnmatch`).

## Files

```text
reachmap/config.py            loader, dataclasses, validation, ConfigError
tests/test_config.py
tests/fixtures/configs/       good and bad example configs (TOML)
tests/fixtures/reachmap.toml  the fixture workspace from tests/fixtures/workspace.json
```

## API

```python
load(path: str | Path, env: Mapping[str, str] | None = None) -> Workspace
loads(text: str, *, fmt: "toml" = "toml", env=None, source="<string>") -> Workspace
```

`fmt="json"` is refused with a ConfigError that says to use TOML (owner decision of 2026-10-22).

`env` defaults to `os.environ`, passed in so tests never touch the real environment.

## Types (frozen dataclasses)

- `Workspace(name, ticket_pattern: re.Pattern | None, limits: Limits, repos: dict[str,
  Repo], edges: tuple[Edge, ...], test_sources, context, source)`.
- `Limits(hops=3, hub_importers=50, distinct_max=10, timeout_s=900, module_cap=200)`:
  positive ints; unknown keys rejected.
- `Repo(name, host, remote, language, roles, branches, aliases, entry_points,
  http_client, tests, service_marker, contract, primary_branch)`. `host` in `github`,
  `gitlab`, `bitbucket`, `git`; `git` requires `remote` (a URL or path); others may
  override it. `primary_branch` defaults to the first non-glob entry of `branches`, else
  `main`. `roles` subset of `frontend`, `backend`, `contract`, `library`, `worker`.
  `language` in `typescript`, `javascript`, `python` for now (registry so adapters can add
  `java`, `kotlin` in M16). Sub-tables (`entry_points`, `http_client`, `contract`,
  `aliases`, `tests`) keep `kind` plus their options in a frozen mapping; validate `kind`
  against a registry of known kinds per sub-table with a clear message, and leave option
  checking to the adapter (adapters register an option validator later).
- `Edge`: `kind` in `http`, `package`, `contract`, `event`, `library`. `http`, `event`,
  `contract`, `library` take `from`/`to`; `package` takes `producer`/`consumer`,
  `manifest`, `pin_pattern` (compiled, must have exactly one group), `import_marker`.
  Every repo an edge names must exist in `repos`.
- `TestSource(kind, options, areas)`, `ContextSource(kind, options)`. `token_env` holds a
  variable NAME; it must match `^[A-Z_][A-Z0-9_]{0,63}$`, otherwise it is probably a
  pasted token and is rejected: the error message names the key and never echoes the
  value.

## Rules

1. `${VAR}` expansion in string values only, from `env`. An unset or empty variable makes
   the whole value "not configured" (`None`), never the literal string and never an
   empty string; a source whose required path is not configured is kept with
   `configured=False` so the run records `source_unconfigured` (non-blocking) later.
   `$${` escapes a literal `${`. Expansion never applies to `token_env` names.
2. Errors: one `ConfigError` with every problem found (not just the first), each naming the
   dotted key path (`repos."acme/web".entry_points.kind`) and what is allowed. Unknown
   top-level keys, unknown repo keys, unknown edge kinds and unknown roles are errors.
3. No credential value is ever stored in the Workspace. A test asserts `repr(workspace)`
   and every error message contain no env value of a `*_TOKEN` variable set in the test
   env.
4. Machine-specific paths: a relative `remote` or `path` resolves against the config
   file's directory, recorded resolved.
5. JSON: not a config format. A `.json` file (or `fmt="json"`) is a ConfigError that says
   "JSON configs are not supported; use a .toml file", raised before the file is read.
6. TOML single-quoted strings are literal: regexes in the example (context 7.2) load
   without double escaping. Compile `ticket_pattern` and `pin_pattern` at load; a bad
   regex is a ConfigError naming the key.

## Tests

- The example config from context 7.2, verbatim, in `tests/fixtures/configs/example.toml`,
  loads (with `ACME_JIRA_SNAPSHOT` unset: jira context present but not configured; with it
  set: path expanded).
- `tests/fixtures/reachmap.toml` (the M1 fixture workspace, `host = "git"`, remotes as
  relative paths placeholders resolved by a `${REACHMAP_FIXTURES}` variable) loads and
  matches `workspace.json`: same repos, roles, branches, edges, limits.
- One bad config per rule, each asserting the message names the key: unknown role, unknown
  edge kind, edge naming a missing repo, pin_pattern without a group, bad regex, git host
  without remote, negative limit, unknown top-level key, token-looking token_env.
- Several problems in one file are all reported.
- Unset and empty `${VAR}` give not configured; `$${` stays literal.
- A `.json` config is refused with a message that says to use TOML.
- No secret value in repr or errors.

## Implementation notes

Where the code goes beyond or differs from the brief above.

Shape
- `contract` edges accept `from`/`to` (this brief) or `producer`/`consumer` (what
  `tests/fixtures/workspace.json` uses), never both on one edge. `package` takes only
  `producer`/`consumer`; `http`, `event` and `library` only `from`/`to`. `Edge` stores
  `from_repo` and `to_repo`, with `producer` and `consumer` as properties.
- Each edge kind has a key allowlist: `package` also takes `manifest`, `pin_pattern`,
  `import_marker`; `event` takes `topics_glob`; the others take nothing more. Adapters add
  keys with `register_edge_keys`. Any other key is a problem naming it.
- `Repo` has an extra optional `library_roots` (the fixture workspace needs it). `slug` in
  `workspace.json` is a fixture-builder detail and is not config.
- Sub-tables, `Workspace.repos` and options are `FrozenMap`, a read-only mapping that
  pickles, copies and hashes, so a Workspace can be sent to a spawned process.
- `TestSource` and `ContextSource` carry `unset_vars` (the variables that were unset or
  empty, areas included); `configured` is true when it is empty. A later
  `source_unconfigured` record names those variables.
- `fmt="json"` and a `.json` file are refused, unread. `loads` has an extra `base_dir`.

Rules
- An unset or empty variable is a problem (naming the key and the variable) everywhere
  except under `test_sources` and `context`, where the source is kept not configured. A
  quietly missing remote, marker or option would be an absence the tool manufactured.
- A variable whose name looks like a credential is refused, and `token_env` is checked
  wherever it appears and must be a plain name (never a `${...}` template).
- A remote with a user or password in a URL is refused, except a bare user for ssh and
  scp-style (`git@host:path`). `host:path` is a remote, not a relative path; URLs are never
  rewritten; only a relative path is resolved against the config file's directory.
- Messages never quote a configured value, only the key path, the expected type or the
  allowed values. Adapter option validators get a `describe` that gives a type name.
- Which option keys are machine paths is declared per kind (`register_kind(...,
  machine_paths=(...))`): `path` for `jira-snapshot`, none for test sources. Paths inside a
  repository stay as written.
- Also rejected: duplicate edges (same kind, endpoints and manifest), self-edges except
  `library`, empty or repeated `roles` and `branches`, an empty repo name, a
  `primary_branch` that is not in `branches`, and a `ticket_pattern` without exactly one
  group. `branches` defaults to `("main",)`; `host`, `language` and `roles` are required.
- Parse errors carry no chained exception (it would hold the whole file).
- The built-in kinds are only those the context example uses. Anything else is "unknown
  kind" until an adapter calls `register_kind`.
