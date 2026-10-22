# M16, section 3: Express and NestJS (`ex`, branch `m16-express`): implementation notes

The brief is section 3 of `docs/briefs/M16-adapters.md` (not edited here, common rules included). This file is the notes of the adapter: what was built, where it
goes beyond or differs from the brief, the edits to shared files and why, what every scenario proves, the acceptance bar item by item, and the limits. Where the text and the code
disagree, the code is right; each point is covered by a test unless it says otherwise.

## What was built

```text
reachmap/adapters/express/
  source.py     token level reading of one file (arguments of a call, module level, require bindings, exports, strings, the Express path form)
  express.py    ExpressAdapter: apps, routers, route statements, mounts across files, the table, what a change reaches
  nest.py       NestAdapter: controllers, verbs, the global prefix, modules, what serves a controller, what a change reaches
  common.py     what the two share: `TableBase` (files read once per commit, holes, constants, the framework's own names through a re-export, Evidence), `Shared` (the grep,
                the areas, `routes_changed` with the route-change guard), and the record kind `routes_unread`
  __init__.py   the kinds `express` and `nestjs`, their option validators, the registration
tests/fixtures/express/        four repositories (data), 23 scenarios, README.md
tests/fixtures/workspaces/express.toml   the workspace of the fixtures
tests/fixtures/ex_build.py     builder (the M1 builder's functions, a tree of its own)
tests/fixtures/ex_claims.py    an independent reading of the repositories (shares no code with the adapters)
tests/support_ex.py            fixtures built once per process, `reachmap analyse` run on a scenario, the report read back
tests/test_ex_source.py, test_express.py, test_nest.py, test_express_review.py, test_nest_review.py, test_ex_config.py, test_ex_fixtures.py, test_ex_scenarios.py
```

## Configuration

```toml
[repos."acme/orders"]
language = "typescript"
roles = ["backend"]
aliases = { from = "tsconfig.json" }
entry_points = { kind = "express" }          # or { kind = "nestjs" }
service_marker = "package.json"              # optional: the folder that holds it is the `module` of a route (default package.json)
```

Neither kind has an option besides `kind` (a key is refused with its key path and without its value). The adapters are built on the M5 JavaScript adapter (aliases, importers, narrowing,
`Reach`) and refuse another language adapter with a `TypeError`. `[[edges]] kind = "http"` and the `endpoints-file` client work as for FastAPI.

## Rules as built

### Express (`express.py`)

1. What is Express is decided by what is imported, never by a name. An app is `express()` (the default or namespace import of `express`, `require("express")()`), a router is
   `express.Router(...)`, `Router(...)` of `import { Router } from "express"`, `new express.Router()`, or the router `express-promise-router` makes, declared at module level
   (`const app = express()`). A `router` made by `createRouter()`, an `app` that is a `Map`, a module of the repository called `express` and a `Router` of the repository claim nothing.
2. A route is a call on an app or a router at module level (inside an `if`, a loop, `try` or a bare block too: those run when the file loads; not inside a function, a class, an
   object or another call): `get post put delete patch options head` and `all` (method `ANY`, the name the Next.js adapter uses), `app.get(setting)` with one argument is not a route,
   and `router.route(path).get(...).post(...)`, which is one statement with one route per method call. The receiver may be declared in another file (`import { app } from "./app"`).
3. A mount is `X.use([path,] router...)`: the path is a string, a template, a regular expression, a list or a constant (or a sum with a string in it); each of the other arguments (a
   list is flattened one level) that resolves to a router or an app is mounted; the others are middleware. Resolution follows `import`, `require`, `export default`, `export const`, `export { a as b }`,
   `export { a } from`, `module.exports = r`, `module.exports = { r }`, `module.exports.r = r`, `exports.r = r`, `module.exports = require("./x")`, `require("./x").r`, an inline `require` in the mount,
   a namespace import and a member (`admin.router`), a folder with an index file, aliases from the tsconfig. A router mounted more than once, under a list of prefixes, or by a
   chain of routers gives a route for each way up; a cycle is cut. A sub application mounts like a router.
4. Paths. A path evaluates literals, templates, `+`, and constants of the file or imported by name (not assigned twice, a depth of six). `:id` is written `{id}` (the form the http edge
   matches), `:id?` is two routes, `:id(\\d+)` loses its pattern (the route fits more than it serves, so it is partial), `*` last is `{rest:path}`, `:name+` and `:name*` are `{name:path}` and partial (what they
   mean differs between Express 4 and 5), an Express 5 optional group `/users{/:id}` is two routes, a pattern in any other segment is an unknown segment, a regular expression path is an unknown segment, and a path that is unknown as a whole stands for a segment, `/<unknown:EXPR>` (as FastAPI). Such a route is `partial` and `route_path_partial` is recorded (non-blocking,
   subject `<path>:<line>` of the unknown part) when a change reaches it. Paths are joined without doubled or trailing slashes.
5. Evidence of a route: the line of its method call (claim `route:METHOD path:file`), for a chain also the `.route(` line, then each `use` line from the router outwards. A multi-line call is cited at the line
   of its method name. `Route.handler` is the last argument's last name (`ctrl.list` is `list`, an inline function `<anonymous>`, `wrap(x)` is `wrap(...)`), `Route.target` the file its first identifier is imported from,
   else the route's file.
6. A router no mount carries up to an app has no route: `router_unmounted` (non-blocking, `<path>:<variable>`), recorded when a change reaches it, and not while a mount of the repository could not be
   resolved (the blocking `mount_unresolved` says so; the table's `problems` too). A mount in a comment is not a mount.
7. What cannot be read is said, blocks, and keeps the answer from being complete (a record and a `problems` entry, never an empty table presented as complete): a router or an app built outside module
   level (a function, a class), an app or a router handed to a function (or `require("./x")(app)`) that calls `.get(`, `.use(` or `.route(` with arguments (`routes_unread`, `<path>:<line>`); an argument of
   `use` that does not resolve to a router but may be one (a call or a name that leads to a repository file or declaration that builds a router, or to a file that declares routers
   and not under that name) (`mount_unresolved`); a file that does not lex (`parse_degraded`, blocking); a file the tree lists that is absent; a search that was cut or unreadable; a file git grep does not search.
   `http.createServer(app)`, `serverless(app)` and a function that only listens are not registrars.
8. What a change reaches (`ExpressAdapter._reached`). The walk is M7's. A statement is a route statement, a mount, `listen`, or another statement. A name the change reaches (`Reach.locals`: an import
   binding, a declaration of the file that uses one) reaches the statements that spell it: a route statement its route (a chain: the call that spells it, the whole chain when the `.route(path)` call or the
   receiver does), a mount every route through it, `listen` nothing, any other statement every route in and through the file (undecidable means affected; a router level `use(mw)` is not narrowed to
   the routes after it). A namespace import is narrowed by the member the statement spells (`ctrl.get` is reached by `get` of the module, `ctrl.list` is not) when the names that arrive are known; a default
   import and CommonJS `require` are never narrowed (the adapter does not know what a CommonJS module exports). The module level of the file that is not a statement (a constant, a call that runs when it loads)
   that spells a reached name reaches every route.
9. A change inside the file: added lines in a statement reach it (the call of a chain, or all of it on the `.route(` line), in an import line the statements that spell the names it binds, in a
   declaration the closure of the declarations that use it, in a receiver's declaration or a mount line every route on it or through it, in a statement that is not a route every route; a comment or a blank line
   changes nothing. Removed lines are read at the pre-change version: in a statement still there (the same receiver, method and path) it reaches that statement (that call), in a statement that is gone nothing (the
   removed route is `routes_changed`'s), in a removed middleware or setting every route; with no pre-change version only the added lines are known (as in M5).
10. The chain is the hops of the walk (the imports) or, for a change inside the file, the first changed line in the route's statement (else on its lines, else the first), then the route's own evidence.
    `structural_names` are the names that export an app or a router: the walk does not go past an entry file through them. (A route statement uses what reaches the file at module level, so the whole module is
    reached and the walk does not go past a routes file through the names it exports; M7's rule, noted in the limits.)

### NestJS (`nest.py`)

11. The decorators are the ones imported from `@nestjs/common` (by name, aliased, or through a namespace import): `Controller`, `Get Post Put Patch Delete Options Head All` (`All` is `ANY`, `Sse` is GET), `Module`,
    `Version`. A `Get` or `Controller` of another package or of a file of the repository is not Nest. `@Controller` takes nothing, a string, a constant, a template, a list or `{ path, version, host }`; a method decorator
    a path or a list. A path is `prefix + controller + method`, `:id` written `{id}`.
12. The global prefix is every `setGlobalPrefix(...)` call of the repository (wherever it is: the files with the call are found by a search for it), each paired with the application it is set on: the
    `NestFactory.create(X)` that the receiver variable keeps (`const app = await NestFactory.create(AppModule); app.setGlobalPrefix("api")`), else the only one in the same function; the prefix then applies to the
    controllers of the modules reachable from X only (a Nest CLI monorepo has an application per `main.ts`). A prefix that cannot be paired applies to every application, and, when the repository has more than one
    application, is partial and said. `createApplicationContext` and `createMicroservice` are not applications and set no prefix. A second argument (`exclude`) makes the routes partial and is said; a prefix that is not
    a literal or a constant is an unknown segment.
13. A controller is served when a `@Module({ controllers: [...] })` lists it and that module is reachable from the module the application is created with (`NestFactory.create(AppModule)`) through
    `imports: [...]` (a name, `X.forRoot(...)`, `forwardRef(() => X)`; a package module such as `TypeOrmModule.forRoot` is not a module of ours). With no `NestFactory.create` in the repository the modules nobody imports
    are the roots. One that is not served has no route and `router_unmounted` names it (`<path>:<Class>`) when a change reaches it. Reading the module lists is blocking when it cannot be done: a spread, an
    expression, an import of a name of the repository that is not a module, an application created with something that is not a module, `RouterModule.register` (`mount_unresolved`), a controller that extends a class that
    declares routes (`routes_unread`). The lists are read wherever in the module class the object is, so a dynamic module (`static forRoot() { return { module, controllers: [...] } }`) serves the controllers it
    lists; a `controllers: [...]` that is in no module class (a function that returns a dynamic module) is a blocking `mount_unresolved` and `router_unmounted` is not claimed while it exists.
14. A version (`version` in `@Controller`, `@Version`) puts an unknown segment after the prefix, `<unknown:version>`, and the route is partial (`route_path_partial` at the line that says the version), only when
    the repository calls `enableVersioning` with URI versioning (the default, or `type: VersioningType.URI`); without the call, or with header, media type or custom versioning, a version adds no segment.
15. Evidence: the method decorator, the controller decorator (when it has a path), the line of the module's `controllers` list that names the class, the line of the global prefix call.
16. What a change reaches, to the method (`NestAdapter._reached`): a changed line in a method reaches its routes; in a method decorator that method; in the class decorator or a line of the class that is in no member
    the whole controller; a name a change reaches (`Reach.locals` without the classes of the file) reaches the methods that spell it; a name that arrives in the constructor through a parameter property
    (`private readonly users: UsersService`) or a property reaches the methods that use `this.users` (a method that calls a reached method too); a constructor body or a parameter that is not a property is the whole class. A
    module file reaches every controller it lists and those of the modules it imports (to a fixed point); a name used by its decorator reaches nothing but a controller it lists. A file that creates the application
    (`NestFactory.create`, `setGlobalPrefix`) reaches every route that is served (global pipes, guards and the prefix are in it); the routes of controllers nothing serves are not reached by it. Removed lines are read at the
    pre-change version (a removed method is a removed route and reaches nothing; a removed line of a member reaches that member's twin).

## Records

New kind (registered by `common.py`): `routes_unread` (blocking, subject `<path>:<line>`): the routes of a place are made where this adapter does not read. Existing kinds used: `router_unmounted`, `mount_unresolved`,
`route_path_partial`, `parse_degraded`, `route_change_withheld` (by `guard_route_changes`, see the review fixes), `read_unreadable` (by the git layer), `not_distinctive` (by the JavaScript adapter: `get` defined in five files).

## Edits to shared files, and why

| File | Change | Why |
|---|---|---|
| `reachmap/entrypoints.py` | two lines: `from reachmap.adapters.express import register ...` and the call, after the two M7 kinds | the registry call the brief asks for (the same shape the Next.js branch uses; the config kinds are registered by the package through `config.register_kind`, so `reachmap/config.py` is not touched) |
| `reachmap/edges/http.py` | `_fits`: a route whose method is `ANY` fits a client call of any method | an `app.all(...)` and a Nest `@All()` answer every method; the same one line the Next.js branch adds for its Pages API routes (generic, an equal change) |
| `tests/test_scenarios_entry.py` | `entrypoints.KINDS` is compared by its first two entries | a registered adapter adds to the tuple; the same edit the Next.js branch makes |

Merged with `main` (Next.js #17, OpenAPI #18, Django #19): one conflict, in `reachmap/entrypoints.py`, resolved by appending the two registrations after `_register_nextjs(register)`; the `_fits` line and the
`test_scenarios_entry.py` edit are equal on `main` and merged without a change. The adapters use main's `guard_route_changes` and set `withholds_unread = True`. The per-kind registry of installed packages and conventions
(`reachmap.adapters.third_party`) is read by the Python adapter only, so nothing is registered for the two kinds.

No hook of the core was needed (`begin_change`, `reached_by_files`, `finding_inputs` of the other two branches were not): everything a change reaches comes from the walk's chains and the adapter's own reading of
the pre-change file. Nothing in M1's data (`tests/fixtures/repos`, `scenarios`, `workspace.json`, `README.md`, `build.py`, `claims.py`), `docs/PROGRESS.md` or `docs/briefs/M16-adapters.md` changed.

## Fixtures

`tests/fixtures/express/` (README there): `ex/orders` (an Express service: ES modules and CommonJS, a router mounted by constants and templates, a chain, an inline handler, middleware at three levels, an unmounted router,
a path that is a call, look-alikes), `ex/ledger` (NestJS: modules, controllers, a global prefix, an injected service and log, a versioned controller, a controller no module lists, one a module nobody imports lists, custom
decorators), `ex/web` and `ex/console` (React Router clients with an endpoints file, one per server). Twenty five scenarios `ex01` to `ex25`, hand-traced (`explains` in each `expected.json`), run through `reachmap analyse`
by `test_ex_scenarios.py` with `scenario_support.check` and no type skipped. The fixtures are in their own tree (not `tests/fixtures/scenarios`) for the reason the other two M16 branches give: the M1 builder, `build.scenarios` and about a dozen
test modules walk every folder there and assume the acme workspace. The clients are two repositories because an http edge reads every call of its client against the routes of its server: a client that also calls the other
server leaves calls that fit nothing, which is said (`client_url_unmatched`) and keeps `added_route_uncalled` from being claimed (ex11 would not have been what it is). After the review: the ledger's `main.ts`
enables URI versioning (so ex20 and ex21 keep their partial balances route for a reason the code states), `custom.controller.ts` imports `Param` from `@nestjs/common` (so ex22 is excluded by the import of the decorator
names and not by the file importing nothing from the package), and ex24 and ex25 are new (below).

## Acceptance bar (context section 10 and the M16 brief)

| Point | Tests |
|---|---|
| Reproduces a hand trace on the fixture and invents nothing beyond it | `test_ex_scenarios.EndToEnd.test_the_report_of_every_scenario_matches_expected_json` (23 scenarios; `complete_for` on changed symbols, routes, screens, http edges, findings and for callers and unit tests where the scenario says so, the complete list of records, `complete`), `test_ex_fixtures.ScenarioFiles.*` (the expected files against the repositories and the independent oracle: every route with its evidence in order, the unmounted ones, what a service, a middleware and a module reach, import hops, locators in code at their revision, URL fit, removed and added routes, definition counts), `test_ex_scenarios.EndToEnd.test_the_adapters_route_tables_are_the_ones_of_the_independent_reading_at_main_and_at_every_head`, `test_the_routes_reached_by_each_express_scenario`, `test_the_routes_reached_by_each_nest_scenario`, `test_the_screens_reached_across_the_http_edge`, `test_the_harness_fails_when_the_report_is_wrong` |
| Shape A: a failed read is an incompleteness, never an absence | `test_express.Unread.*` (a router or an app built in a function, a class; an app handed to a function that adds routes, by import, by `require(...)(app)`, local; a file that does not lex; a file absent; a search unreadable, cut, not searched, none; `unmounted` not claimed while a mount is unresolved), `test_express.Mounts.test_a_call_that_may_return_a_router_is_an_unresolved_mount_and_blocks`, `..._a_name_from_a_file_that_declares_routers_but_that_does_not_resolve_is_unresolved`, `test_express.ReachByEdit.test_a_change_inside_a_router_file_that_does_not_lex_is_incomplete_and_blocking`, `test_express.ReachByImport.test_an_unreadable_chain_makes_the_area_not_complete`, `test_nest.Unread.*`, `test_nest.Serving.test_a_list_with_a_spread_is_an_unresolved_mount_and_blocks`, `..._router_module_register_is_a_blocking_record`, `..._the_application_created_with_something_that_is_not_a_module_is_unresolved`, `..._a_controller_that_extends_a_class_with_routes_is_unread_and_blocks`, `test_nest.ReachByEdit.test_an_edit_in_a_file_that_does_not_lex_is_incomplete_and_blocking`; scenarios ex12 and ex23 (blocking, not complete, no area), ex05, ex17 and ex18 (unmounted said, complete), ex07 and ex21 (partial said); the cut-at-every-offset and token soup tests (`Robust`) |
| Every name match is dropped when the word is generic | `test_express.NotExpress.*` (a router not made by Express, an app that is a `Map`, a module called `express`, `cache.get(path, fn)` on a module of the repository, a client `axios.get(path, options)`, a `Router` of the repository, test files, declaration files and dependency folders, a handler name that links nothing), `test_express.ReachByImport.test_a_namespace_import_is_narrowed_by_the_member_the_statement_spells`, `test_nest.NotNest.*` (a `Get` of another file, of another package, an aliased import, the namespace import, decorators in comments and strings, a test file); scenarios ex13 (`get` defined five times: `not_distinctive`, only the route that spells `ctrl.get`), ex14 (a `router` and a `cache.get` that are not Express), ex22 (decorators called `Controller` and `Get` of the repository) |
| Every citation resolves at its pinned commit | `test_ex_scenarios.EndToEnd.test_every_evidence_in_every_report_resolves_at_its_pinned_sha` (the e2e audit, 23 reports), and `Base.reach` of `test_express` and `test_nest` asserts `scenario_support.evidence_problems` for every chain of every adapter test |
| Through `reachmap analyse`, an explicit list of types per scenario, no skip | `EndToEnd.test_no_type_is_skipped_in_any_scenario`, every `expected.json` lists its `must_find`, `must_exclude`, `complete_for` and `not_established` |
| Determinism, wording, nothing about the machine, a fresh process | `EndToEnd.test_two_runs_give_the_same_bytes_except_the_run_object`, `..._one_scenario_runs_as_a_separate_process_and_the_kinds_are_known_to_a_fresh_interpreter`, `..._every_human_readable_string_passes_the_wording_check`, `..._no_report_says_anything_about_the_machine_it_ran_on` |

Mutation check of the first version (each change made by hand, the named modules run, the change reverted): the namespace member narrowing off, an unresolved mount not recorded, a router built in a function not recorded, an app handed to a
registrar not recorded, `default` and `*cjs` not the same export, the prefixes joined in the wrong order, `get` with one argument a route, any name `Router` a router, middleware of the app reaching nothing, a chain statement
reaching all its calls, `router_unmounted` claimed while a mount is unresolved, `router_unmounted` not recorded, any decorator called `Get` a route, reachability from the application ignored (an equal change, which the check found: the
first version of the code decided it twice, and the redundant copy was removed), a bootstrap change reaching unserved controllers, the constructor property touch widened to the class, the prefix call not searched
for, the version not partial, inheritance not read, a removed line of a statement dropped, the type assertion not stripped, `ANY` not fitting, `listen` counted as a statement that reaches everything, a module not reaching
its controllers: every one is killed (five survived the first version of the tests and each now has a test of its own; two more were the redundant code above).

Mutation check of the review fixes (same method, the new modules and the two new scenarios): the guard never withholding a removal, a hole in the head table ignored, a cut search not withholding, an unknown callee root not flagged,
a member of an imported module not looked up, an unknown first argument of `use` read as middleware, a re-exported constant not followed, CommonJS literal members not read, barrels not followed, `new Router()` read twice, the
framework re-export hop off, chained route calls read only at the first, a pattern after a parameter not partial, the lists of a dynamic module not read, stray controller lists not said, global providers read as plain ones, the
body of a module reaching nothing, prefixes not paired with applications, versioning always a segment, an application context setting a prefix, a lost prefix file not withholding additions: every one is killed (one, the
callee that is a call result, survived until a test of its own was added).

## Choices (where the brief is open)

- Express routes use the FastAPI spelling for parameters (`{id}`, `{rest:path}`) so that M8's matcher works unchanged; `all` is `ANY`, as in the Next.js adapter (a one line edit of `_fits`, equal in both branches).
- The walk is carried by routes files' route statements at module level, which reach the whole module by the JavaScript adapter's rule (`Reach.whole`); the adapter therefore decides per statement itself from
  `Reach.locals` (the names that arrive), and counts a statement that is not a route as reaching everything in and through the file, as FastAPI counts a dependency or a middleware.
- `app.listen(...)` reaches nothing: what runs a server is not what a request does (a changed port reaches no route). Settings (`app.set`, `app.disable`) are statements that reach everything.
- The first argument of `use` that is a call (`app.use(auth(), router)`) is middleware, not a prefix; a first argument that is a member of an object (`cfg.prefix`), a name that is not a function and not a constant this can read is
  a prefix this cannot see, written as an unknown segment (partial, said). (A call in a mount's first place that really is a path gives the routes under it without it: a limit.)
- A Nest route is cited at its method decorator and not at the method name, as FastAPI cites a decorator.
- Nest versioning is read only as far as whether it adds a path segment (see rule 14); the version number is not evaluated, `defaultVersion` and `prefix` of the versioning options are not read. `RouterModule.register` is not
  evaluated: it is said and blocks.
- Two clients, one per server (see Fixtures).

## Known limits

- Routes are read where an app or a router is declared at module level. A factory (`createApp()`, `buildRouter()`) is said and blocks, not read; so is an app handed to a function that adds routes. Routes
  registered on an app that a function receives as a parameter are read only when that function is found as a registrar of an app of the repository (then the answer is blocking), and are not listed.
- A receiver that is assigned later (`let router; router = Router()`) is a router built outside a declaration: said, not read. A router stored in an object or a class (`this.router`) is not read.
- Middleware is not evaluated: `use(mw)` reaches every route in and through the file, a router level `use(mw)` too; the order of registration and a prefix of a middleware are not looked at. A route
  statement that is shadowed by an earlier one is still a route.
- A route statement uses what reaches its file at module level, so the whole module of a routes file is reached (by the JavaScript adapter's rule) and a name that a routes file exports and that something reached by a
  route statement is carried to is not followed past the file (a helper that only a declaration uses is). `structural_names` of an Express file are the names its apps and routers are exported under.
- CommonJS: `module.exports` forms listed in rule 3 only; `require` with an expression, `require.context` and `import x = require()` are not read as bindings; a `require` is never narrowed by name.
- A path that is a call, a member of an object (`cfg.base`), an enum or an expression other than the ones listed is an unknown segment (a segment that fits one segment only, where the real path may be several or none).
- `Route.target` for a Node handler is its file; the http edge's backend calls (the modules a handler runs) are listed by the Python adapter only: for a client change the edge says "the server's language adapter does not list imports" (incomplete).
- The JavaScript adapter does not name a decorated method in `Symbol.nested` (its parser does not see a method after a decorator and a parameter decorator): the changed symbol of a Nest method is the class. The reach is by the adapter's
  own member scan.
- Nest: a change to one method of an injected service reaches every method that uses the property (the walk's narrowing is by exported name, the class); `@Inject(TOKEN)` with a string token, custom providers and dynamic modules are
  followed only through the names that spell them; a module that is not imported by a repository module and is not named by `NestFactory.create` is unserved; microservice patterns (`@MessagePattern`) are not routes; the global
  prefix applies to every route (the `exclude` list is not evaluated: said as partial); guards, pipes and interceptors on a controller or a method are names that reach it, not evaluated.
- The importer search for a file called `index.ts` or `app.ts` uses the stem as the candidate filter (as for every JavaScript file): a cost, not an answer.

## Not done

- Router options such as `caseSensitive` and `strict` are read and ignored. No Koa, Fastify, Hapi or other Node framework. No Express `app.param` or `router.param`. No NestJS GraphQL, WebSocket gateway or
  microservice entry points. No `express.static` file routes. No evaluation against a real Express or NestJS repository (the fixtures are synthetic, shaped from the documented conventions; nothing was run against the frameworks).
- No frontend change (the kinds it does not know are shown last without a note; not run).

## Review fixes (the second version)

An independent review of the first version found a blocker and six majors, all of them a route that was wrong or invented while the answer said complete, and the false removal that `guard_route_changes` exists for.
What changed, each with tests (`test_express_review.py`, `test_nest_review.py`, and the scenarios ex24 and ex25):

- **The route-change guard.** Both adapters set `withholds_unread = True` and `Shared.routes_changed` calls `guard_route_changes`. A table keeps `holes`: the files in which something was not read (a file that did not lex or
  could not be read, a router built where routes are not read, an app handed to something that may add routes, a mount that did not resolve, a module list this cannot name) and `global_hole` for what may hide any route (a
  search that was cut or unreadable, a tree that could not be read, a file git grep does not search). A removal is withheld when the head table has a hole in one of the files the removed route is made of (its file, any file of its
  evidence: the mounts, the module, the file with the prefix), an addition when the base table has; every change when the table has a global hole; for NestJS also every addition when the file that carried the global
  prefix at the base is a hole at the head (the routes the head does show are then wrong, not merely missing). An unrelated problem withholds nothing (`test_an_unrelated_unreadable_file_does_not_withhold_a_real_removal`, both adapters),
  a real removal between two readable tables is still a removal (`test_a_real_removal_..._stand`). What is withheld is one blocking `route_change_withheld` for the repository. `routes_complete(sha)` is there too. Scenarios:
  ex24 (the customers router does not lex at the head: the web client calls two of its routes, no `removed_route_still_called` is made; `parse_degraded`, `mount_unresolved` for the mount of what could not be read, and
  `route_change_withheld`, all blocking) and ex25 (the ledger module does not lex: the four routes of its controller are not four removed routes). A mount of something that could not be read (a router file that did not
  lex) is an unresolved mount, so `router_unmounted` is not claimed beside it.
- **B1: an app handed to a member of something imported or declared.** `routes.mount(app)` (namespace, default, named import, CommonJS `require` and `require("./routes").mount(app)`), `registry.mount(app)` (a local object,
  a class, a function), a deeper chain (`routes.v1.mount(app)`) are looked up from the root of the callee: a file of the repository that calls `.get(`, `.use(` or `.route(` with arguments is a registrar (`routes_unread`,
  blocking, `router_unmounted` not claimed), a package or a global is not, and a root this cannot resolve (a call result, an index, an undeclared name, a constant that is the result of a call) is flagged (`routes_unread`:
  undecidable means affected). The receiver may be an imported app. The refactor that moves an inline `app.use` into `routes.mount(app)` is not a removed route (`test_the_refactor_that_moves_a_mount_into_a_function_is_not_a_removed_route`).
- **M1: a mount prefix that is not visible.** See Choices: a member of an object, `env.API_PREFIX`, a name that is not a function and not a constant this reads is `<unknown:EXPR>` (partial, `route_path_partial`); a constant
  is followed through `export { X } from`, `export * from` and CommonJS (`module.exports = { P: "/api" }`, `exports.P = ...`, `module.exports.P = ...`); a name declared twice is unknown (one rule for both adapters).
- **M2: dynamic modules** (rule 13). **M3: global guards and providers.** A name a change reaches that a module spells in a provider written as an object reaches the routes the module serves and those of the modules it
  imports; in an `APP_GUARD`, `APP_INTERCEPTOR`, `APP_PIPE` or `APP_FILTER` provider it reaches every served route of every module; in the class body (`configure(consumer)`) the module's own. A plain class provider reaches nothing.
  **M4: a prefix per application** (rule 12). **M5: barrels.** The importers of a file that only re-exports a router (`export { r } from`, `export * from`) are candidates, to a fixed point, so a file that adds routes to a
  router imported through a barrel is read (a barrel written as an import followed by an export is not followed). **M6:** `new Router()` and `new express.Router()` were read twice (a spurious blocking `routes_unread`): one
  read, asserted with no records and no problems.
- **Minors.** The framework's own names are followed through one re-export file (`export { Router } from "express"`, `export * from "@nestjs/common"`); chained route calls (`r.get(a, h).post(b, h2)`) are all routes, with a
  change reaching the call it is in; `:id(\\d+)`, `:p*` and `:p+` are partial and an Express 5 `{/:id}` group is two routes; Nest versioning is a segment only with `enableVersioning` (rule 14; ex20's explanation says so);
  ex22 imports `Param` from `@nestjs/common`; `_Table.imports` is initialised; one `const()` rule (`TableBase`); the dead code is removed (`source.joined`, `Src.requires`, `Src.args_braced`, `Recv.merge_params`,
  `Shared.with_claim`, the unused argument of `assemble`, the no-op in `extract`); `facts_of`, `incomplete`, `module_file`, `const`, the evaluation and the building of Evidence moved into `common.TableBase`.
- **`expected.json` changes.** ex20 (`explains`: versioning is enabled in `main.ts`, the unserved controllers are not reached), ex21 (`explains`: versioning), ex22 (`explains`: the `Param` import), and, because the fixture files
  changed, the changes overlays of ex20 (`src/main.ts` now has the `enableVersioning` line) and ex22 (`custom.controller.ts` has the import and a parameter). No expected answer (routes, evidence, records, findings, chains) of ex01 to ex23
  changed; ex24 and ex25 are new.
- **Limits that remain.** A first argument of `use` that is a call is middleware. A registrar is looked up one level (the file or the declaration the root names): a registrar that only calls another module's function that
  adds routes is not found. Nest `defaultVersion` and the versioning `prefix` are not read. The guard is per file: a hole that hides routes without a file in common with them (an unresolved mount in an app file whose
  routes are all in one router file the mount does not cite) withholds only through the files the removed route cites.

## Tests, speed

After the review fixes and the merge of `main` (Next.js, OpenAPI, Django): 406 tests of this branch (`test_express` 135, `test_nest` 93, `test_express_review` 38, `test_nest_review` 34, `test_ex_source` 41, `test_ex_config` 7,
`test_ex_fixtures` 32, `test_ex_scenarios` 26), 4,297 in the suite. `python3.12 tests/run_parallel.py` (4 workers): 4,297 tests, 6 skipped, 413 s, all passing, at a load average of about 3 (the machine is shared). The ex
modules and the touched core ones (`test_edge_http`, `test_scenarios_entry`, `test_incomplete`, `test_config`, `test_fixtures`, `test_nextjs`, `test_fastapi`) alone: 867 tests in 94 s at a load of 1.7. The first version
was also run on 3.11, 3.13 and serially (all passing; see the history of this file); the second was run on 3.12 in parallel only.

Performance of the adapters (a throwaway generator, not a test, run on the first version): an Express repository of 2,103 files (300 router files of three routes each mounted by `app.use`, 300 controllers, 1,500 other files),
a service function imported by 40 controllers: the entry-point step 1.2 s for 120 areas; a NestJS repository of 404 files (200 modules, 200 controllers of five methods each), a service injected into every controller: 1.4 s for 1,000
areas. (With 300 importers the shared service is a hub and the walk stops there, as for every language: `hub_cutoff`.) Not re-measured after the second version.
