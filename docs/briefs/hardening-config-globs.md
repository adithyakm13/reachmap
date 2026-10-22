# Hardening: config keys, unmatched routing files, unreadable specs (`hardening-config-globs`): implementation notes

Three gaps a documentation review found. Each broke the rule that an absence must be established (context 4, rule 5) in the same way: a
configuration that points at nothing, or a spec that cannot be read, gave an empty answer or a note that blocks nothing.

## 1. A routing glob or search that matches nothing is a record

A React Router `routes_glob` (explicit, or the default `src/**/routes.{tsx,jsx,ts,js}`) that matched no file of the tree gave "No areas reached"
with no record. It is now the record `routes_unmatched` (registered in `reachmap/adapters/__init__.py`): scope selection, blocking, always
blocking, subject `<repo>:<what was looked for>`, and the entry result is incomplete (`EntryResult.complete` False, a reason that names what
matched nothing).

The mechanism is one function, `reachmap.entrypoints.check_unmatched`, called by `reach` after the walk. An adapter opts in with
`unmatched(sha)`, which lists `(what, detail)` for what matches nothing in the tree at `sha`. The rules, all pinned in
`tests/test_routes_unmatched.py`:

- Said only for a change with code in it (a changed source file with symbols, not a test). A docs-only change reaches nothing, and that is true.
- Said whenever nothing matches at the head. When files matched at the pre-change commit (`routing_files(sha, what)`), the detail says "it
  matched N files before this change and the change deleted or moved them" and quotes each file with what became of it (`renamed to X`,
  `deleted`): routes moved out of a glob, or routing files deleted, are never an empty, complete answer. With no pre-change version (an
  orphan branch) the head alone decides and the detail does not claim a move.
- A tree or a search that could not be read is never an unmatched pattern: `unmatched` returns nothing and the read's own incompleteness
  stands (for React Router a problem says the tree could not be listed). A search that was cut or failed makes the adapter's table
  incomplete and `unmatched` stays silent.
- One record per subject however often the result is asked for (`record_quoted(..., once=True)`).

What each adapter says:

| Adapter | When | Subject |
| --- | --- | --- |
| React Router | an explicit pattern (each entry of a list on its own) matches no file; the default globs as one | the pattern, or `src/**/routes.{tsx,jsx,ts,js}` |
| Next.js | no routing file at all: no `app` or `pages` folder (also under `src/` and `root`) with a page, handler or wrapper | `{app,pages}`, or `<root>/{app,pages}` |
| FastAPI | the candidate search came back whole and no file declares an app, router or mount | `FastAPI or APIRouter` |
| Express | the same, no app or router | `express() or Router()` |
| NestJS | the same, no controller, module or bootstrap | `@Controller or @Module` |
| Spring | the same, no controller or mapping | `@Controller or a request mapping` |

Checked and already right: Django. A repository with no literal `ROOT_URLCONF` that names a module, and a `settings` option that matches no file,
are the blocking `urlconf_root_unknown` and an incomplete result; two tests in `test_routes_unmatched.py` pin it. OpenAPI already had
`handlers_unmatched` for its handler globs (non-blocking, by its own brief; unchanged). The Celery kind has no adapter (`entry_points_unread`).

Fixture change: `tests/fixtures/scenarios/s16-no-merge-base/expected.json` gains `routes_unmatched` for `acme/web:src/modules/*/routes.tsx`
(the orphan branch rewrites the tree and nothing matches the configured glob; there is no pre-change version to say a file was deleted). The
harness tables `JS_NOT_ESTABLISHED` (`tests/scenario_support.py`) and `NOT_ESTABLISHED_KINDS` and its subject patterns
(`tests/test_fixtures.py`) know the new kind, and the fixture test checks the claim independently (the head tree has no file the glob matches).

## 2. Option validators for every kind and for `tests` and `aliases`

A misspelled `routes_glb` was ignored (the default glob was used, which caused the empty answer above). Every kind that had no validator has one
now, and so do the two tables with no kind, in `reachmap/config_options.py` (installed in the section registry of `config.py`, so they hold
whether or not an adapter module was imported; `register_kind` still accepts a replacement). A table takes only the keys its adapter reads; any
other key is a problem at the table's dotted path, `<key>: not a key of ...; allowed keys: ...` with `(did you mean 'x'?)` when one is close. A
message never quotes a configured value.

| Table | Keys | Read by |
| --- | --- | --- |
| `entry_points` kind `react-router` | `routes_glob` (a path or glob inside the repository, or a list) | `ReactRouterAdapter.from_config` (`module_map`, once accepted and never read, was removed on 2026-10-22) |
| `entry_points` kinds `fastapi`, `celery` | none | the adapters take no option |
| `http_client` kind `endpoints-file` | `path` | `EndpointsFileClient.from_config` |
| `contract` kind `pydantic` | `root`, `package`, `versions` (`git-tags`), `tag_prefix` | `edges.contract`, `edges.package` |
| `context` kind `jira-snapshot` | `path` | the context source |
| `tests` (no kind) | `markers` (checked with `testpaths.split_marker`; an empty list is allowed and means no file is a test, the key left out means the defaults), `command` | the language adapters, the pipeline |
| `aliases` (no kind) | `from` (a path inside the repository) | `JsAdapter.from_config` |

The kinds registered by adapters (`django`, `nextjs`, `spring`, `express`, `nestjs`, `openapi`, the test sources) already had validators. A test
fails for any registered kind without one. `tests/test_config_options.py` also loads every config under `tests/fixtures` (the workspaces and the
example; the bad ones still fail), under `examples/` (`examples/reachmap.toml`), and the TOML examples of
`docs/CONTEXT.md` and `docs/BOOTSTRAP.md`. No fixture config needed a change.

## 3. A configured spec that cannot be read blocks

A changed OpenAPI spec (the configured one) that is YAML was the non-blocking `file_not_analysed`, so a removed operation in it gave no finding and nothing that
blocks. YAML is now the blocking, always blocking `spec_unreadable` (subject `<repo>:<spec path>`, the detail says YAML and JSON only), on the
server side and on the client side, and the entry result is incomplete. A `spec` named `.yaml` or `.yml` is also refused when the config loads
(`openapi/options.py`: "YAML is not read; commit a JSON copy of the spec"); the run-time record is for a spec that becomes YAML after the config
was written. The record of a spec that cannot be read is made once per version and cause (no `once`), so two unreadable versions are two records (the adapter's problem was already there). It is one record for one cause:
the adapter no longer adds its own `file_not_analysed` (the language adapter still notes a changed `.yaml` file as not analysed, as for any file
of that kind). Every other reason a configured spec cannot be read (deleted, read failure, not JSON, not OpenAPI, duplicate key) already made a
blocking `spec_unreadable` and an incomplete result; tests now pin that for a changed spec in all those cases.

Next.js reads its table before it checks the tree, so an unreadable tree adds its own problem. Limits: there is still no YAML reader, so a YAML spec gives no routes and no operation finding, only the blocking record. The brief
`M16-openapi.md` says YAML is `file_not_analysed`; where the two disagree, this text is the code.
