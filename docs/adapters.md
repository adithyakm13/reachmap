# Adapters

An adapter answers one narrow question about one repository at one pinned commit. The core
asks, keeps the rules of [docs/rules.md](rules.md), and turns the answers into areas,
findings and records of what could not be established. This page says what each adapter reads,
what it produces, and where it stops. The last part is the guide for writing a new one.

Contents: [what exists](#what-exists), [language adapters](#language-adapters),
[entry-point adapters](#entry-point-adapters), [HTTP clients and servers](#http-clients-and-servers),
[edges](#edges), [test sources](#test-sources), [hosts](#hosts),
[writing an adapter](#writing-an-adapter).

## What exists

| Role | Adapter | `reachmap.toml` | Reads |
|---|---|---|---|
| Language | JavaScript and TypeScript | `language = "typescript"` or `"javascript"` | `.ts .tsx .js .jsx .mjs .cjs .mts .cts` |
| Language | Python | `language = "python"` | `.py` |
| Language | Java and Kotlin | `language = "java"` or `"kotlin"` | `.java .kt` |
| Entry points | React Router | `entry_points = { kind = "react-router" }` | screens |
| Entry points | Next.js | `{ kind = "nextjs" }` | screens and API routes |
| Entry points | FastAPI | `{ kind = "fastapi" }` | API routes |
| Entry points | Django and Django REST Framework | `{ kind = "django" }` | API routes |
| Entry points | Express | `{ kind = "express" }` | API routes |
| Entry points | NestJS | `{ kind = "nestjs" }` | API routes |
| Entry points | Spring Boot | `{ kind = "spring" }` | API routes |
| Entry points | Celery | `{ kind = "celery" }` | accepted, not read |
| HTTP client | endpoints file | `http_client = { kind = "endpoints-file" }` | calls |
| HTTP client | OpenAPI generated client | `http_client = { kind = "openapi" }` | calls |
| HTTP server | OpenAPI spec | `http_server = { kind = "openapi" }` | routes |
| Edge | `http`, `package`, `contract` | `[[edges]]` | links between repositories |
| Edge | `event`, `library` | `[[edges]]` | declared, not followed |
| Test source | Qase, TestRail | `[[test_sources]]` | suggested cases |
| Test source | Playwright, Cypress specs | `[[test_sources]]` | suggested specs |
| Host | GitHub, GitLab, Bitbucket Cloud, plain git | `host = ...` | change requests, branch heads |

Every adapter keeps the same promises. Every read answers found, absent or unreadable, and an
unreadable read is a record, never an absence. Every claim cites a line at a commit. A name
match needs a distinctive name (one defined no more than `limits.distinct_max` times in
non-test code) or is dropped and recorded, and a match by name alone is a candidate, not a
finding of a reached area. Where a decision cannot be made statically, the answer is
"affected".

None of the adapters runs your code: no compiler, no JavaScript runtime, no Python import, no
Django settings loaded. They read text.

## Language adapters

### JavaScript and TypeScript

**What it reads.** Imports and exports of the files above, read with a tokenizer (not a line
pattern, so a statement written over several lines is found). It resolves relative specifiers
and the aliases of `tsconfig.json` or `jsconfig.json` (`paths`, `baseUrl`, `extends`), places a
change in the declaration it sits in by diff line number (never by text), finds the importers
of a changed file (aliased and relative imports both), and the call sites of a changed name.

**What it produces.**

- The changed symbols of each file, and the importers and callers of them.
- **Narrowing**: when a change is in a named export, importers that import only other names are
  excluded, with their import line as evidence. A change reaches inside the file too: an
  exported function that calls the changed one carries the change to its own importers.
- **Guards**: when the change sits under an `if` that compares a parameter with a literal, a
  call site whose argument is another literal is excluded, citing the guard and the call.
  A call with a variable argument is affected.
- Unit tests that import a changed file or a caller, one hop away.

**Records it makes**, by name: `parse_degraded` (a file that did not lex cleanly),
`specifier_unresolved` (an import that could not be resolved and may name the changed file;
blocking), `file_not_analysed` (a changed file of a kind it does not read, such as `.mdx` or
`.css`: its importers were not established), `alias_config_gap` (the alias configuration could not be read completely), `hub_cutoff`,
`not_distinctive`, and the git layer's `read_unreadable` and `search_truncated` (blocking).

**Known limits.**

- A `//` after a space in JSX text, and a `/*` closed by a later `*/`, can hide an import
  without a record.
- No `node_modules`, no workspace package names and no `package.json` `exports` resolution: an
  import of a package of the same monorepo by its package name is not followed.
- A changed `.json` file reaches the files that import it. A changed file of another non-code
  kind (a stylesheet) records `file_not_analysed` and reaches nothing.
- State shared between functions through a variable is not followed by narrowing.
- A folder called `test` or `tests` is a test folder by default markers: a page in it is routed
  but the walk never passes through it. Set `tests.markers` to change that.

### Python

**What it reads.** `.py` files with the standard library's parser. It builds a map of which
dotted names a file has under each source root: the repository root, each folder with the
`service_marker`, each of `library_roots`, `src/` folders, and the layout a `pyproject.toml`
declares (setuptools `package-dir` and `packages.find`, poetry, hatch, pdm). It finds changed
symbols (functions, classes, methods and model fields as `Class.member`), importers by import,
callers (with multi-line and aliased imports), and tests.

**Records it makes**, by name: `parse_degraded`, `specifier_unresolved`, `file_not_analysed`,
`module_map_gap` (a layout declared in `setup.py` or in a way not understood), `module_ambiguous`
(two files with one module name), `package_init_implicit` (importing a module inside a package
runs the package's `__init__`), `hub_cutoff`, `not_distinctive`, and the git layer's records.

**Known limits.**

- No cross-repository imports inside the language adapter: the `contract` edge handles a package
  such as `acme_contracts`.
- No `sys.path` edits, `.pth` files, editable installs or `setup.py` layouts.
- A class used as a base class or a metaclass of the changed one is not followed.
- Narrowing stops, and the whole module is reached, on import-time execution of a reached name
  (assignment values, decorators, defaults, class bodies) and on dynamic access
  (`sys.modules`, `globals()`).
- Guards are not read: no Python call site is excluded by a guard.

### Java and Kotlin

**What it reads.** `.java` and `.kt` files, with a lexer and a declaration reader written for
the purpose (comments, strings, text blocks, raw strings and Kotlin templates are handled, so a
class named in a comment or a string is not a use). No build file is read: the package of a file
comes from its own `package` line, and tests are the paths that hold a test marker
(`src/test/` among the defaults of the language). A repository with both Java and Kotlin sources
is read by one adapter.

**What it produces.**

- Changed symbols as `Class.member` (nested classes dotted); a line of a type header or its
  annotations is a change to the type itself.
- Importers by every form: import, static import, wildcard, the same package with no import,
  a fully qualified name in code, a Kotlin alias, Java importing Kotlin and the reverse, and a
  Kotlin top-level function through its file class.
- **Narrowing** by the names a file spells: an importer that spells only other members of the
  changed class is excluded, with the reason. A change to a class header, a constructor, an
  `init` block, a `@Bean` or `@PostConstruct` method, or a static initialiser that calls something
  reached makes the whole type reached.
- Dispatch through supertypes: a change in an implementation reaches the importers of its
  interface or base class that spell the member.

**Records it makes**, by name: `parse_degraded` (a file that does not lex), `type_ambiguous` (two
files declare one class name, both are kept), `hub_cutoff`, `not_distinctive`, and the git layer's
`read_unreadable` and `search_truncated`. An unreadable or unlexable candidate makes the importer
list not whole, with a blocking record, never an empty complete list.

**Known limits.**

- Bean wiring and dependency injection beyond static imports and interface dispatch are not
  followed.
- Reflection in a changed file stops narrowing (every importer is then a caller), and shadowing a
  name by a local or a parameter is not understood (a spelled name counts).
- A member reached through the result of a call, by a file that never imports its type
  (`order.getCustomer().total()`), is not found.
- A Kotlin extension function is called as a member of its receiver, and the receiver's type is
  not checked. Unicode escapes outside strings are not translated.
- Annotation processors, Lombok-generated members and generated sources are not seen.
- Gradle and Maven files are not read.

## Entry-point adapters

An entry-point adapter finds the places a person can test and the chain of imports or calls
that reaches them. A **screen** is a route of a frontend router, and an **API route** is an HTTP
route with its full mounted path. One screen per page file: a page mounted at two paths is one
screen with an "also mounted" list. A change reaches a route when its handler or page is a file
the chain arrives at; a change to a file the router wiring runs at load time (an app-level
dependency, a middleware, a prefix constant) reaches every route in or through the file,
because undecidable means affected.

An adapter that looks for its routing files (a `routes_glob`, the `app` and `pages` folders, a
search for apps, routers or controllers) and finds none says so: a blocking `routes_unmatched`
record, subject `<repository>:<what was looked for>`, and an answer that is not complete. It is
said for a change that has code, and when the files matched before the change (the change deleted
or moved them) the record says so and names a rename target. A tree or a search that could not be
read is not this record: it is the read's own incompleteness, never an absence.

Every entry-point adapter shares one guard for removed and added routes: a route counts as
removed only when the route table of the head was read whole, and as added only when the base
table was. Otherwise the run records a blocking `route_change_withheld` that says what was
withheld, so an unparsable router file never reads as "every route was removed".

### React Router

**Reads.** JSX `<Route>` elements (attributes in any order, nested children, `index`, `element`
and `Component`) and object routes (`path`, `index`, `element`, `Component`, `lazy`,
`children`, layout routes with no path) in the files `routes_glob` names. A page is an import
binding or a lazy import named in the element. Only leaf routes are screens.

**Records.** `route_path_partial` (a path that is not a literal is kept as `<unknown:EXPR>`),
a blocking `parse_degraded` for a routes file that does not lex cleanly, and a blocking
`routes_unmatched` when `routes_glob` (or the default globs) matches no file.

**Limits.** Descendant routes (`<Routes>` inside a component, a `*` splat) are composed only
inside the file that declares them, and a relative path with nothing above it is
`/<unknown:mount>/...` and partial. Routes outside `routes_glob` are not read. A changed
import statement reaches the routes that render any of its names.

### Next.js

**Reads.** The folder tree: the App Router (`page`, `layout`, `template`, route groups,
dynamic, catch-all and optional catch-all segments, private folders, parallel slots,
intercepting routes, `route` handlers per exported method) and the Pages Router (pages,
`pages/api` as `ANY`, `_app` and `_document` wrappers). A layout or a template reaches the
pages below it; middleware and instrumentation reach every route.

**Records.** `route_intercepted` (an intercepting route is partial, merged by URL),
`route_conflict` (two files answer one URL, a build error in Next.js: both are reached),
`route_path_partial`, and a blocking `routes_unmatched` when there is no routing file at all.

**Limits.**

- `next.config` is not read: `basePath` is an option, and rewrites, redirects, headers, i18n
  routing, middleware matchers, `generateStaticParams` and `dynamicParams` are not evaluated.
- `.mdx` pages are not read. `page_extensions` that are not letters and digits (such as
  `page.tsx`) are not supported.
- A Pages API handler is `ANY`: methods told apart by `req.method` are not read.
- `export *` is followed five files deep; a deeper chain is a blocking `specifier_unresolved`.
- Several Next.js projects in one repository need one `[repos]` entry each.
- A client-side `<Link href>` or `redirect()` is not an edge; a server action is reached by the
  import of its file, not as a route.

### FastAPI

**Reads.** Handlers decorated on a router or app, `include_router` and `mount` chains up to the
app, with prefixes taken from literals, `+`, f-strings and module constants. The evidence for a
route is the decorator, a router's constructor line when it has a `prefix=`, and each mount
line from the router outwards.

**Records.** `router_unmounted` (a router nothing mounts: no route is claimed),
`mount_unresolved` (a mount in a function or on something that does not resolve to a router;
blocking), `route_path_partial`, `route_change_withheld`, and a blocking `routes_unmatched` when
no file declares an app, a router or a mount.

**Limits.** Routers and apps must be module-level variables. `add_api_route`, Starlette's
`Mount` and `Route`, routers built by a factory function, class-based views and `self.router`
are not read. `root_path` and tags are ignored. The decorators of a handler give its routes
whether or not the function runs.

### Django and Django REST Framework

**Reads.** `urls.py` chains: `path`, `re_path` (approximate), `url`, `include()` (strings,
modules, lists, `router.urls`), namespaces, `ROOT_URLCONF` from the settings (also settings that
star-import other settings, and a literal `DJANGO_SETTINGS_MODULE`; `root_urlconf` and `settings`
override). Function views answer any method unless restricted, class views through `as_view`
with inherited methods, `HEAD`, `OPTIONS` and a literal `http_method_names`. DRF routers and
viewsets expand to list and detail routes and `@action` routes.

**Records.** `urlconf_root_unknown`, `view_unresolved`, `settings_not_followed` (all blocking),
`include_external`, `route_methods_unknown`, `urlconf_unmounted`, `route_path_partial`,
`route_change_withheld`.

**Limits.** Routes are read from module-level `urlpatterns`. Not read: `i18n_patterns`,
pattern lists built by functions or comprehensions, an aliased `path`, DRF format suffixes,
custom routers, nested routers, `reverse()` and url names as links between code,
URLconf-swapping middleware, `ROOT_URLCONF` set at run time, `admin.site.urls` and the routes
behind installed packages, views held in variables, `method_decorator`, `dispatch()` overrides.
A settings module chosen only by the environment is not detected: set `root_urlconf`.

### Express

**Reads.** Routes on apps and routers across files, ES modules and CommonJS (`app.get(...)`,
`router.route(path).get(...)`, chained calls), mounts and nested mounts with prefixes (constants
followed through re-exports), middleware reach (app-level reaches every route in and through the
file, route-level only its routes), and routers handed to helpers. A file is Express because it
imports `express`, never because of a variable's name.

**Records.** `routes_unread` (a router built in a function, an app handed to a function that
adds routes; blocking), `router_unmounted`, `mount_unresolved`, `route_path_partial`,
`parse_degraded`, and a blocking `routes_unmatched` when no file declares an app or a router.

**Limits.** A factory (`createApp()`) is said and blocks, not read. A router stored in an object
or a class (`this.router`) is not read. Middleware is not evaluated, and its order is not looked
at. No Koa, Fastify or Hapi. A path that is a call or an expression other than the listed ones
is an unknown segment.

### NestJS

**Reads.** `@Controller` with method decorators (`@Get` and the rest), the module graph
including dynamic `forRoot()` modules, global prefixes tied to their application, URI
versioning, and global guards and interceptors reaching every served route.

**Records.** As Express. A controller that no module registers has no route, and is said
(`router_unmounted`).

**Limits.** `defaultVersion` is not read. Microservice patterns (`@MessagePattern`), GraphQL and
WebSocket gateways are not routes. The global prefix `exclude` list is not evaluated. A change to
one method of an injected service reaches every method that uses the property.

### Spring Boot

**Reads.** Controllers (a non-abstract class or object with `@RestController` or `@Controller`
from `org.springframework`, or a class `@RequestMapping`; interfaces give their mappings to
implementors, and a base class's handlers are routes of the subclass) and their mappings:
`@GetMapping`, `@PostMapping`, `@PutMapping`, `@DeleteMapping`, `@PatchMapping` and
`@RequestMapping(method = ...)`. Class and method paths are joined. Constants in paths are
followed (a `static final String` or `const val`, by qualified name, static import or wildcard,
with the line that gives the value as evidence), as are `+` and Kotlin templates. `${key}`
placeholders and the context path are read from `application.properties` and a subset of
`application.yml`, from the controller's own module. A project annotation that carries a Spring
one counts, one level deep. The evidence of a route is the method mapping line, the constants,
the class mapping, and the configuration lines.

A change to a property reaches the routes whose path used the key (the context path reaches every
route). A `@ControllerAdvice` class reaches the routes it applies to, and an `@ExceptionHandler`,
`@InitBinder` or `@ModelAttribute` method reaches the whole class.

**Records.** `route_style_unread` (functional `RouterFunction` routes and Jakarta REST `@Path` are
found and not read), `config_not_followed` (a changed property that is not a path key, or a class
that wires the application: `@Configuration`, filters, interceptors, security classes, runners),
`config_unread` (a value that is set under a profile, in a part of a YAML file the reader does not
understand, or in a module that disagrees with its siblings: the prefix is marked unknown), and
`type_ambiguous` for a class declared in two places, and `routes_unmatched` when no file declares
a controller or a mapping (all blocking), plus `route_path_partial`, `route_change_withheld` and
`parse_degraded`. An unlexable controller claims no route and is
never reported as a removed one.

**Limits.**

- Every controller in the repository's source roots is taken as mounted: there is no component
  scan model.
- Request conditions (`params`, `headers`, `consumes`, `produces`) are not read: two handlers that
  differ only by them are two routes with the same method and path.
- Environment variables, `spring.config.import`, profile files beyond an equal-value check, and
  `@ConfigurationProperties` are not followed. YAML flow mappings, literal blocks, anchors and tabs
  are reported unread.
- A qualifier or a profile that selects one of several implementations is not understood.
- A meta annotation is read one level.
- `backend_calls` from a client list explicit imports only.

### Celery

Accepted as an `entry_points` kind so a worker can be described, and not read: a change that
reaches the repository records `entry_points_unread`. A repository that must contribute
areas needs a kind that has an adapter.

## HTTP clients and servers

These connect an HTTP edge: a client repository calls a server repository. See
[the `http` edge](config.md#http).

### Endpoints file

A frontend keeps its URLs in one file as exported constants or builders. The adapter evaluates
each URL statically from the file's tokens (a constant made of other constants, a builder with
parameters, a template literal, a string written over two lines). A part it cannot evaluate (an
imported base URL, a computed value) is marked unknown and the URL is partial: kept, said, never
matched quietly and never dropped. A client call is a line that imports the constant or builder
(under any alias, or through a namespace import) and uses it; the method comes from the call
that encloses it (`apiGet`, `axios.post`, `fetch` with `method`), else the call matches any.

**Records.** `client_url_partial`, `client_url_unmatched` (a URL that fits no route of the
server).

**Limits.** One file per repository. A client that builds a URL without the file is not seen.

### OpenAPI

Server side, `http_server = { kind = "openapi", spec, handlers }`: routes from a committed JSON
spec (`paths`, `servers` and `basePath`, `$ref` closure with cycles ended), with evidence at the
spec line, and handlers found by `operationId` in the configured files. A **change to the spec is
a change**: an operation removed, added or changed (method, path, parameters, request and
response schemas) is a finding (`operation-changed`, `removed_route_still_called`,
`added_route_uncalled`) with evidence at both spec versions. Client side, `http_client = { kind =
"openapi", spec, client }`: calls of a generated TypeScript client matched to operations by
their import, method and URL from the client's own copy of the spec.

**Records.** `spec_unreadable` (blocking), `spec_gap`, `operation_handler_unresolved`,
`handlers_unmatched`, `handlers_unconfigured`. A YAML spec is a blocking `spec_unreadable`.

**Limits.**

- JSON only. A `spec` named `.yaml` or `.yml` is refused when the configuration loads, and a spec
  that becomes YAML later (the file changes) is a blocking `spec_unreadable` and an answer that is
  not complete, so a removed operation in a YAML spec is never a quiet report: there is still no
  finding for it. A deleted JSON spec lists its operations as affected and blocks; a new one makes
  every operation affected.
- A `$ref` outside the document is not followed: the operation counts as changed whenever the
  spec changes, with a `spec_gap`.
- Operation identity is the method and the full path with its parameter names as written, so
  renaming a path parameter is a removed route and an added one.
- A generic operation name (`get`, `list`, `health`) is matched to calls only when they import it
  from the generated client; a call through a default import's member is a match by name and is
  dropped.
- Handlers are found by name in module-level functions, one per operation. A handler registered
  by a decorator, a class, a lookup table or a dotted `operationId` is unresolved and said.
- A repository takes `http_server` or a routing `entry_points`, not both. No generated spec
  (only a committed one is read), no `webhooks`, `callbacks` or `links`.

## Edges

An edge is a declared link along which a change is followed, one hop at a time, up to
`limits.hops`. Every hop pins the revision of the repository it reads, per branch where branches
matter, and a failed read on an edge is a record on that edge, never "not adopted" or "no caller".

### `http`

Client code to server routes. A client URL matches a route by segments: a client parameter
matches only a route parameter, the most specific route wins, and a literal in a parameter
position says so. A removed route is followed too: a route the change removes while a client
still names its URL is a `removed_route_still_called` finding, high risk and low confidence. A
route the change adds that no client calls yet is `added_route_uncalled`. An HTTP method that is
not known matches any method. A method a handler lists as `ANY` fits any client method.

**Limits.** The client side is the endpoints-file and OpenAPI-client adapters. The modules a
handler runs (backend calls) are listed for Python handlers only.

### `package`

A producer repository's tags and a consumer's manifest pin. The version a pin names is a real tag
of the producer (a placeholder `0.0.0` in a manifest is never read), and whether a tag contains
the change is ancestry read from git, never inferred from the number: release lines diverge. Each
consumer is listed per branch as `adopted`, `on_adoption` (it will be exposed when it takes the
change) or undecided. A change in no released tag lists each consumer once, from its primary
branch.

**Records.** `version_unparsed`, `pin_unresolved`, `tag_prefix_unmatched`, `edge_incomplete`.

**Limits.** A pin is read with one regular expression from one manifest file. A range such as
`>=1.2` is `pin_unresolved`. Tags are not filtered by `--as-of`: the report says so.

### `contract`

A change to one field of a shared model followed to the consumer code that reads the field, then
through that code's own routes, the `http` edge, and the frontend screens. Only distinctive field
names count, and a read needs a receiver whose type is proven, or a name that is distinctive in
both the producer and the consumer. Findings: `contract-tightened` (high risk: a field became
required, or its type narrower) and `contract-relaxed`.

**Records.** `contract_change_unscoped` (the change is not one field of a model; blocking),
`contract_construction_unseen` (only direct constructions of a model are seen, so where a new
required field must be supplied is not established), `not_distinctive`.

**Limits.** Contract reads are Python only (Pydantic models). An untyped `resp.status` whose name
only the producer defines can still be taken as a read.

### `event` and `library`

Declared, not followed. A change that reaches code at one end records `edge_unfollowed`, so the
report says what it did not follow instead of staying silent.

## Test sources

A test source yields suggestions, never mapped tests: a suggestion is a place to look. Every
request is a GET, a redirect is refused, and a credential is sent only in the source's own header
over https.

- **Qase** and **TestRail** read the cases of a project and tie a case to an area by the route
  prefixes and modules you configure, and by the distinctive words of the case's title and the
  area's route and changed symbols. A word shared by more than a tenth of the cases (with a floor
  of three), and words on a long list of generic ones, never count.
- **Playwright** and **Cypress** read local spec files: a literal `goto` or `visit` address
  matched to a screen's route is a suggestion for that screen. An address that is not a literal
  (`${BASE_URL}/cases`, a variable, an options object) is recorded as `spec_url_partial` and never
  matched.

**Records.** `source_unconfigured` (non-blocking), `source_unavailable` and
`suggestions_incomplete` (blocking for suggestions only), `spec_url_partial`,
`spec_visit_unmatched`. A source that fails costs only its own suggestions.

**Limits.** No source was run against the real service in the project's own tests: the responses
are invented, shaped like the public documentation says they are. There is no retry after a rate limit: the run says the
source did not answer. Only the first argument of `goto` and `visit` is read; a visit made
through a helper or page object is not seen. Word matching is on case titles only, in English.

## Hosts

The host layer holds only what git cannot know: a change request's title, state, base, head,
merge commit and file count, and the head of a branch by name. Diffs, file reads, searches and
merge bases come from git on a local clone, the same for every host.

| Host | Change request | Reached through |
|---|---|---|
| `github` | pull request | `gh api`, your own login |
| `gitlab` | merge request (`!N`, numbered per project) | `glab api` when installed, else `GITLAB_TOKEN` |
| `bitbucket` | pull request | `BITBUCKET_TOKEN` (a token or `user:app_password`) |
| `git` | none | `git` alone, for `--compare` and `--range` |

A merged request with one parent (a squash, rebase or fast-forward merge) is resolved by the
host's commit count, checked against the host's file count. If both or neither reading fits, the
run records `pre_change_unavailable` and refuses, rather than guess.

**Limits.** `gh` and `glab` were never run in the project's own tests (the argument lists and the
parsing are tested against fakes). An open pull request whose head cannot be fetched by commit
(a fork on a server that refuses it) is unreadable. A GitLab fast-forward merge that names no
merge commit is unreadable. Windows is untested.

## Writing an adapter

New adapters are welcome when they meet the bar below. Read `docs/CONTEXT.md`, sections 4 and 5
(the rules and the traps), and `CONTRIBUTING.md` first. The code is Python 3.11 or newer, standard
library only: an adapter parses text and never runs a compiler, a runtime or the framework.

### Protocols

The protocols live next to the code that uses them.

| Protocol | File | What it answers |
|---|---|---|
| `LanguageAdapter` | `reachmap/adapters/__init__.py` | `changed_symbols`, `importers`, `callers`, `guards`, `definitions`, `is_test_path`, `analyze_file`, `analyze` |
| `EntryPointAdapter` | `reachmap/adapters/__init__.py` | `entry_points_reached`, `is_entry_file`, `routes`, `routes_changed`, `structural_names` |
| `EdgeAdapter` | `reachmap/edges/__init__.py` | `applies` and `follow`, returning hops, each with its evidence or a record |
| test source | `reachmap/testsources/__init__.py` | suggestions and records for a configured source |
| host | `reachmap/hosts/__init__.py` | change requests and branch heads |

`reachmap/adapters/base.py` is the shell both language adapters share: reading at a pinned
commit, the importer search, definition counts, callers, unit tests, the hub rule and the record
helpers. A new language adapter fills in a few hooks (how to parse, which files are code, how a
specifier resolves) and inherits the rest, so a fix lands once.

Every read returns the model's three-way answer (`ReadResult`: found, absent, unreadable), and a
result that could not be completed carries `complete=False` with its reasons, so that a short
list is never taken for the whole list. Evidence is always pinned to a full commit.

### Registries

An adapter registers itself in one line each. It adds no adapter-specific code to the core.

| What | Call |
|---|---|
| A language | `config.register_language(name)`, and the dispatch in `propagate.language_adapter` |
| A kind of `entry_points`, `http_client`, `http_server`, `contract`, `test_sources` or `context` | `config.register_kind(section, kind, validator)` |
| An entry-point adapter | `entrypoints.register(kind, factory)` |
| An edge kind | `edges.register(kind, factory)`, and `config.register_edge_keys(kind, *keys)` for extra keys |
| A kind of incompleteness | `incomplete.register_kind(kind, template, blocking)` |
| A kind of finding | `model.register_finding_kind(kind, is_exclusion=...)` |

A config validator is called as `validator(options, describe)` and returns messages. It must never
put a configured value in a message: say what was expected, and use `describe(value)` (a type name)
for what was found. Name the allowed keys in the message for an unknown key, as `allowed keys: a,
b`: `tests/test_docs_config.py` reads them from there, and fails until `docs/config.md` documents
every key of every kind you register.

An incompleteness record's template starts with "What ... was not established" or "Which ... was
not established" and says why, and passes the wording check. Choose its default blocking value by
the rule: blocking when an input was needed and could not be read, non-blocking for a stated limit
or an optional source.

### The acceptance bar

An adapter ships when:

1. It reproduces a hand trace on a synthetic fixture and invents nothing beyond it. The trace is
   written by hand, in an `expected.json`, before the adapter, and the adapter's output is compared
   with it.
2. Every Shape A trap of `docs/CONTEXT.md` section 5 that applies has a fixture that makes the read
   fail, and asserts an incompleteness and not an absence: an unparsable file, a truncated search,
   an unreadable file, a mount that does not resolve.
3. Every name match has a fixture with a generic word and asserts that it is dropped.
4. Every citation it produces resolves at its pinned commit. The end-to-end evidence audit
   re-reads every citation of every scenario report.

Beyond the bar: a route-table adapter guards its removals and additions (the shared guard above);
undecidable means affected; a name match never decides alone; and the adapter does not decide how
to word a claim of safety or coverage, because none may be made.

### Fixture rules

- A fixture is a small, invented git repository, built by the fixture builder from data under
  `tests/fixtures/` (history steps with file overlays, fixed author and dates, a git environment cut
  off from your configuration, so two builds give the same commit ids). It is plausible code that is
  never executed. Company names, routes and data are invented.
- New repositories get their own folder and slug; existing repositories, branches and commit ids
  stay byte-identical. A new adapter has its own workspace file under `tests/fixtures/workspaces/`.
- Each scenario is a change (a branch with an overlay, or a range between refs) with a hand-written
  `expected.json`: `must_find` (with the `chain` of lines that reaches each item), `must_exclude`
  with the reason, `not_established` as the complete list of records expected, and `complete_for`
  naming the types for which `must_find` is the whole answer.
- Every locator in an `expected.json` (`path` and a `contains` substring) is checked against the
  repository at the revision it names, and facts such as routes, importers and consumers are
  recomputed by an independent parser in the test support, so the expectation is never trusted on
  its word.
- Every file under `tests/fixtures/` must be tracked by git (a test fails when one is ignored).
- No network in the default tests. A host or a test source is tested against invented responses shaped like the
  real APIs, served by a fake transport; a test fails when a default transport is built.
- End to end: new scenarios pass through `reachmap analyse`, with an explicit list of the types
  each scenario checks and no skip left. `tests/fixtures/README.md` describes the schema.
