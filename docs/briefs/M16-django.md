# M16, Django and Django REST Framework (`dj`, branch `m16-django`): implementation notes

The brief is section 4 of `M16-adapters.md` (unchanged). This file says what was built, the choices, the limits and how it was tested.

## What was built

```text
reachmap/adapters/django/__init__.py   rules in the docstring; DjangoAdapter is imported when used (module __getattr__)
reachmap/adapters/django/adapter.py    DjangoAdapter (EntryPointAdapter): what a chain reaches, routes_changed, structural names, records
reachmap/adapters/django/table.py      Table: root URLconf, include chains, routes with evidence, views, viewsets, routers, unmounted units
reachmap/adapters/django/facts.py      what one file says about URLs, read with ast (assignments to urlpatterns, imports, routers, register calls, classes)
reachmap/adapters/django/known.py      tables: methods of the generic views, viewset actions, method decorators (names only, nothing is imported)
reachmap/adapters/django/urlpath.py    path() syntax and regexes as {param} / {rest:path} / <unknown:TEXT>
reachmap/adapters/django/options.py    config validator of `entry_points = { kind = "django", root_urlconf, settings }`
reachmap/adapters/third_party.py       registry used by the Python adapter (installed packages, conventional names)
tests/fixtures/repos/dj-portal         Django project: apps billing and cases, DRF API, unmounted URLconf, generic view names
tests/fixtures/repos/dj-portal-web     its React client (react-router screens, endpoints file)
tests/fixtures/scenarios/dj01..dj14    14 hand-traced scenarios
tests/fixtures/workspaces/django.toml  (and django.json, which the builder reads)
tests/fixtures/dj.py                   builds the two repositories and lists the scenarios (uses the M1 builder)
tests/fixtures/claims_dj.py            independent reader of the fixtures' URL shapes (oracle for the checks)
tests/support_django.py                case class and trees for the unit tests
tests/support_dj.py                    runs `reachmap analyse` on the Django fixtures and checks reports with the M1 harness
tests/test_django_{routes,views,reach,http,inherit,traps,config,urlpath,review}.py, test_route_change_guard.py, test_fixtures_dj.py, test_scenarios_dj.py
```

Registered through the existing registries: `entrypoints.register("django", ...)` and `config.register_kind("entry_points", "django", validator)`
(both in `reachmap/entrypoints.py`), the incompleteness kinds through `register_kind` in `adapter.py`. The adapter uses the Python adapter for imports,
module resolution, parsing and callers, and the walk (`reachmap.walk`) for everything between a change and an entry file.

## Shared-file edits (and why)

Core:

- `reachmap/entrypoints.py`: the `django` factory, `register("django", ...)`, `config.register_kind(...)`. One factory function, two registration calls.
- `reachmap/edges/http.py`, `_fits`, three small generic changes in one function. (1) A route whose method is `ANY` fits a client call of every method
  (a Django function view answers every method unless a decorator restricts it; Express `app.all` and Spring `@RequestMapping` with no method are the same
  shape). Before, a route with a method that differs from the call's was found only by the fallback and flagged `method_unknown`. (2) A route with
  `Route.partial` fits as a candidate (`RouteMatch.partial`), not only a route whose path holds an `<unknown:...>` segment: a `re_path` route is kept as a
  partial pattern whose path is an approximation (`/legacy/{year}`) and must not be a must-test match. For FastAPI nothing changes: a partial route there
  always has an unknown segment. (3) `Route.path_approximate` (a new field, default `False`) says the path is a reading of a pattern that was not evaluated,
  and a route that has it fits as a candidate even when `partial` is not set; before, this was inferred from `partial`.
- `reachmap/adapters/__init__.py`: `guard_route_changes(...)`, the one rule for what `routes_changed` says when a route table was not read completely, and the
  blocking kind `route_change_withheld`. See "Removed and added routes" below and the guard evidence at the end.
- `reachmap/edges/http.py`, `_changed_routes`: an adapter that does not guard its own `routes_changed` (`withholds_unread` is not true) has its removals and
  additions withheld by the core when it reports problems at a revision, or when `routes_complete(sha)` says the table was not whole. FastAPI, Next.js, OpenAPI
  (server) and Django guard themselves and say so; an adapter that is not changed is guarded by the core.
- `reachmap/edges/http.py`, `_removed_hop`: a removed (method, path) pair whose URL is still answered at the head by a route of the same path for the call's
  method (a route narrowed from `ANY` to `GET`, a method split from another) is an http edge that is `removed`, with the route that answers it, but not a
  `removed_route_still_called` finding.
- `reachmap/adapters/fastapi/__init__.py`, `reachmap/adapters/nextjs/__init__.py`, `reachmap/adapters/openapi/server.py`: `routes_changed` (and Next.js
  `screens_changed`) goes through `guard_route_changes`, `routes_complete(sha)` says whether the table was whole, `withholds_unread = True`. The failure these
  fix: a router file with an unclosed parenthesis made every route of it a removed route on main (a `removed_route_still_called` finding for a route nobody
  deleted).
- `reachmap/adapters/python/__init__.py`, `reachmap/adapters/base.py`: uses of the registry `reachmap/adapters/third_party.py` (about 50 lines, no dependency),
  which is per entry-point kind: `register(kind, installed=..., conventions=...)`, `names_for(kind)`. `PyAdapter(framework=kind)` is given the kind of the
  entry point that built it (`from_config`), so a repository that is not a Django one never gets Django's names. `_names_module` does not treat an unresolved
  import of a package registered as installed (`django`, `rest_framework`) as a miss, and `_distinct_name` does not count a name registered as a convention
  (`urlpatterns`, `app_name`). Why it is needed: the files of a Django project are named like the modules it
  imports. Without it, a change to any `urls.py` records a blocking `specifier_unresolved` for `django` (`from django.urls import path` spells `urls`), a change to
  a `models.py` the same for `from django.db import models`, a `views.py` for `from django.views import View`, a `settings.py` for `from django.conf import settings`,
  and every changed URLconf records `not_distinctive` for `urlpatterns`. The registry is generic: another framework registers its own names. The shadowing rule
  of M6 is kept (a module next to the importer that could shadow the package keeps the record).

Tests (existing files):

- `tests/fixtures/build.py`: `_build_scenarios` and `scenarios` skip a scenario whose repository is not in the M1 workspace (two `continue`s), and a helper
  `m1_scenario_dirs()` (the `sNN-` folders). Existing repositories, branches, shas and expected.json files are unchanged (checked: the M1 fixture tests
  pass and `test_fixtures_dj.test_the_m1_builder_does_not_build_or_list_these_scenarios`).
- `tests/test_fixtures.py` (three loops over the scenario folders), `tests/test_incomplete.py` and `tests/test_model.py` (one glob each) read the M1 scenarios
  only: they walked `scenarios/*`, which now holds the folders of the adapters of M16 too.
- `tests/scenario_support.py`: one condition. A blocking `edge_incomplete` among the expected records is the propagation's, not the entry-point result's
  (dj09 expects one); before, any blocking record other than `pre_change_unavailable` made the harness expect an incomplete in-repo result.
- `tests/test_scenarios_entry.py`: `entrypoints.KINDS[:2]` where it compared the whole tuple.

## Rules as built

1. Root. `ROOT_URLCONF` is read by `ast` from every non-test file that assigns it at module level (also inside `if`/`try`/`with` blocks). A literal that
   resolves to a module of the repository is a root; constants and f-strings of constants are evaluated; two assignments (a conditional) give two roots. The
   `root_urlconf` option replaces what the settings say, and `settings` (a file, a glob or a folder) limits which files are read. A root that is not a literal
   (a call, an environment lookup), that names a module that is not there, or no assignment at all: blocking `urlconf_root_unknown` (subject the repository),
   and no route is claimed. A reference to `ROOT_URLCONF` that is not an assignment (`settings.ROOT_URLCONF`) is not a root.
2. Patterns. `urlpatterns` is read element by element: `path()`, `re_path()` and the old `url()`, with the route and the view as positional or keyword
   arguments; `include()` of a dotted string, a module object (`from cases import urls as cases_urls`; `import shop.urls`), a tuple `(module or string,
   app_name)`, an inline list, a list variable of the file or of another file, a module attribute (`shop_urls.urlpatterns`) and `router.urls`. `urlpatterns`
   built with `=`, `+`, `+=`, `.append`, `.extend`, `urlpatterns = urlpatterns + [...]`, inside module-level `if` blocks, from `list(...)`, `tuple(...)` and
   DRF's `format_suffix_patterns(...)` (the suffix routes are not made, see limits). `static()` and `staticfiles_urlpatterns()` are not routes and not unresolved.
   `path("admin/", admin.site.urls)` is not a route (URLs of a package outside the repository) and says nothing. Anything else in a pattern list (a comprehension, a
   function call, `i18n_patterns`, a name that is not a list) is not understood: blocking `mount_unresolved` at its line, and the table is incomplete.
3. Routes. A route is composed from the root down through every include, so a module included twice gives two routes with the evidence of their own include
   lines. A converter is a parameter (`<int:pk>` is `{pk}`), a `path` converter takes the rest (`{rest:path}`). A route string is joined to its include prefix by
   concatenation, the way Django does (it does not add a slash: `path("api", include(...))` over `path("items/", ...)` is `/apiitems/`; doubled slashes are
   collapsed and a trailing slash dropped, as for FastAPI). A prefix that is not a literal gives an `<unknown:...>` part, so the route is partial; it is never
   joined with an invented slash. The evidence is the line that says the route (the `path()` call, cited at its first line; for a router, the `router.register()` call, and for
   an `@action`, the decorator first), then each include line from the route outwards; the first item has the claim `route:<METHOD> <path>:<handler file>`.
   Routes are listed ordered by path and method.
4. Regexes. A `re_path` regex is kept as a partial pattern: literal segments stay, a named group that makes a whole segment becomes `{name}` (`{name:path}` when
   it can match a slash), an unnamed group, a literal mixed with a group, an alternation or any other syntax makes the segment `<unknown:TEXT>`. Every regex
   route is `partial`; a change that reaches it records `route_path_partial` (non-blocking) and an http match against it is a candidate, by name only.
   A prefix that is a variable or an f-string is evaluated when everything in it is a literal or a module constant (this file's or an imported one); a part
   that is not is `<unknown:EXPR>`, the route is partial and recorded the same way.
5. Namespaces and url names. An include's instance namespace is its `namespace=` argument or the `app_name` of the included module (or the tuple's second
   element); they nest. `DjangoAdapter.url_names(sha)` gives the routes by `ns:name` (a `path(..., name=...)`; a router's `<basename>-list`, `-detail`, the
   action's `url_name`, and `api-root`; the basename is the register argument, or the lowercase model of `queryset = Model.objects...`).
6. Views. A function view answers `ANY`; a decorator restricts it: `require_GET`, `require_POST`, `require_safe` (GET and HEAD), `require_http_methods([...])`,
   DRF's `api_view([...])` (default GET), as a decorator of the function or a wrapper in the URLconf (`require_POST(views.f)`, `require_http_methods([..])(views.f)`,
   any other wrapper such as `login_required(...)` is looked through). A class view (`X.as_view()`) answers the methods it defines (only lowercase names dispatch) and inherits from a base
   of the repository, `HEAD` when it has `get` and none of its own, and `OPTIONS` always, limited by a literal `http_method_names` (one that is not a literal is
   recorded as `route_methods_unknown`), and the methods of the generic views of Django and DRF (a table of names: `TemplateView` GET, `ListAPIView` GET, `CreateView`
   GET/POST/PUT, ...); `ViewSet.as_view({"get": "list"})` takes its mapping. A lambda is a route whose handler is `<lambda>`; a view of a package outside the
   repository (`LoginView.as_view()`) is a route whose handler is its name and whose target is the URLconf. A class with a base outside the repository that is
   not in the tables answers `ANY` when it defines no method, and records `route_methods_unknown` (non-blocking) when a change reaches it. Mixins are neutral.
7. DRF. `routers.DefaultRouter()` and `SimpleRouter()` (any other class that ends in `Router` is not understood: blocking `mount_unresolved`);
   `router.register(prefix, ViewSet, basename)` with positional or keyword arguments, in the file of the router or any other that imports it. A viewset
   gives the list route (GET `list`, POST `create`) and the detail route (GET `retrieve`, PUT `update`, PATCH `partial_update`, DELETE `destroy`) for the
   actions it has (its own, in a base of the repository, or from `ModelViewSet`, `ReadOnlyModelViewSet` and the model mixins), the `lookup_field` or
   `lookup_url_kwarg` (default `pk`) as the parameter, and one route per method of each `@action` (`detail`, `methods`, `url_path`; the name is the default
   `url_path`). `DefaultRouter` adds the API root view (`GET <mount>`, handler `APIRootView`). The handler of a route is `Class.action`.
   A viewset that cannot be resolved to a class of the repository is a blocking `view_unresolved`, and nothing is guessed for it.
8. Unmounted. A URLconf or router that no include carries up to the root has no route. The routes of such a unit are composed on their own (so a handler on it is
   known) and a change that reaches one records `urlconf_unmounted` (non-blocking, a wording of its own: nothing includes that URLconf or router; subject `<path>:urlpatterns` or `<path>:<router variable>`; the top of
   what nothing mounts, so a URLconf included only by an unmounted one is named through it). A commented-out include is not an include. The claim is not made
   while anything could not be read (a file that does not parse, an unreadable one, a cut search, an unresolved include or view or root): that file may be the
   one that mounts it, and `problems` says so.
9. What a change reaches. The files that are entry files: URLconfs, files with a router or `register` call, files with a view that a route runs or a class it
   inherits from, settings files that assign `ROOT_URLCONF`. A change inside a view reaches the routes of that view; inside a class view, the routes of the
   changed method and of the members that call it (private helpers; a public method that does not answer a method, such as `get_queryset`, may be called by
   the framework and reaches the whole class, as does a class attribute or a decorator or base expression of the class); a base class of the repository in
   another file reaches the routes of its methods in the subclasses, member by member. A change to a route line (any line of a multi-line call) reaches that
   route; to an include line, every route below it; to a `register` line, the routes of that registration; to a router's constructor, every route on it; a changed
   `app_name` every route of its URLconf; a statement outside every route and declaration, or a name called where the URLconf loads, every route of the file;
   an added route is reached by its own line; a removed line reaches the route that is still written the same, and none that is gone. A change that arrives by
   an import is matched with the references of what it brings (`views.Hello` of a module import, `Hi` of an aliased name); a route made from a view the
   walk reaches is reached in a viewset as the member that uses the name, or the whole class when a class attribute does (a serializer). A change that is an edit of an
   import statement reaches only the names whose binding was added, removed or re-targeted (the bindings before and after are compared), and the chain cites
   the statement that binds them. The routes of the views the routes name are structural names of their files (the walk does not go on to the URLconf that
   mounts them: the route table already knows), except a view that a module that is neither a URLconf nor a test imports by name (a view another routed view
   calls): the walk goes on past it, so the caller's route is reached. An importer list that is not whole cannot say a name is not imported: the walk goes on.
10. Settings. A settings module is a file that assigns `ROOT_URLCONF`, a module a script names with `DJANGO_SETTINGS_MODULE` (`os.environ.setdefault`,
    `os.environ[...] = ...`, `os.environ.update({...})`, `os.putenv`; a literal only), and a module that star-imports a settings module, to a fixed point
    (`settings_prod.py` that does `from .base import *`). A change that touches `ROOT_URLCONF` reaches every route of the new root. A change to anything else in a
    settings module is not followed (a middleware, an installed app or a flag may change what every route does): blocking `settings_not_followed`, and the answer
    is not complete.
11. Removed and added routes (`routes_changed`). Diffed by method and path, then guarded by `guard_route_changes`. The table keeps where it has holes: an
    include that could not be resolved, a URLconf that did not parse, a view that was not resolved, each with the prefix of what could not be read (a hole under
    a prefix may hide routes under that prefix only), and a hole with no prefix (an unknown root, a search that failed, a file that nothing readable includes) may
    hide routes anywhere. A removal is withheld when the head table has a hole that covers its path, an addition when the base table has one; the others stand.
    What was withheld is said once for the repository by the blocking `route_change_withheld`, with the counts. A route is never called removed because
    it was not read.
12. Records made here. New kinds: `urlconf_root_unknown`, `view_unresolved`, `settings_not_followed` (blocking), `include_external`,
    `route_methods_unknown`, `urlconf_unmounted` (non-blocking); `route_change_withheld` (blocking, shared: in `reachmap/adapters/__init__.py`). Used from earlier milestones: `parse_degraded` (blocking, a candidate that does not parse), `mount_unresolved`
    (blocking, an include or pattern element or router that is not understood, an include outside module level), `read_unreadable` (blocking, a symlinked file
    the search does not read; the git layer records unreadable files), `route_path_partial`, `search_truncated` (the git layer). An include of
    a dotted module is external (non-blocking, `include_external`) only when its first name is not a folder or a module anywhere in the repository (a package
    may be mounted from any folder: `apps/` on the path, a vendored copy); one whose first name is, but that does not resolve, is blocking.

## Fixtures and scenarios

`acme/portal` (Django 4 and DRF, never run): `config/` (settings with the root, `urls.py` with `admin.site.urls`, an include by string, an include of a module
object with a namespace, an include of the router's URLconf, a regex route), `billing/` (function views, one with `@require_http_methods`, a class view,
URLconf with a nested include, a report URLconf, a serializer module, a service module, a DRF API with a read-mostly viewset and a model viewset, a
commented-out include, a URLconf nothing includes), `cases/` (app_name, generic view names), a test. `acme/portal-web` is a small React client with an endpoints file
(one constant over two lines) and four screens. Workspace: one http edge from the client to the portal, `distinct_max = 3`.
The route table of the portal has 20 routes (`MAIN_ROUTES` in `test_fixtures_dj.py`, written by hand and checked against `claims_dj`).

| id | change | what it proves |
|---|---|---|
| dj01-view-body | body of a function view | route `ANY` with evidence, the caller, the module that imports the views module for another name is excluded, a unit test, the http edge and the screen |
| dj02-method-decorator | body of a view restricted to GET and PUT | one view, two routes; only the GET one is called by the client |
| dj03-class-view-method | `post` of an `as_view()` class | the route of that method only |
| dj04-service-helper | a service function two modules call | the route of the function view and the one action that calls it, not the other routes of the viewset; the importer of another name is excluded; the chain across the repositories |
| dj05-drf-action | body of one `@action` | the route of that action only (not list, detail, other actions, nor the API root) |
| dj06-serializer | a serializer a viewset names | every route of that viewset (six), none of the other viewset |
| dj07-unmounted-urls | a view of a URLconf nothing includes | no route, `urlconf_unmounted`; the commented include is not a mount |
| dj08-generic-name | `index` of one app, a name four modules define | only the route that imports it; `not_distinctive` |
| dj09-include-moved | an include moves under a new prefix | new routes reached, the old ones removed and still called by the client (high risk, low confidence), `client_url_unmatched`, an incomplete edge |
| dj10-route-added | a view and a route are added | the new route, `added_route_uncalled` |
| dj11-urlconf-syntax-error | a URLconf stops parsing | nothing claimed from it, no route called removed, blocking `parse_degraded`, `mount_unresolved` and `route_change_withheld`, not complete |
| dj12-root-urlconf-dynamic | `ROOT_URLCONF` stops being a literal | no route claimed, no route called removed, blocking `urlconf_root_unknown` and `route_change_withheld` |
| dj13-regex-route | body of a view a `re_path` names | a partial route, `route_path_partial` |
| dj14-settings-flag | a setting other than the root | nothing followed, blocking `settings_not_followed` |

The expected answers were traced by hand from the files (the generator that formats them is not in the repository: the facts are typed), then every locator is
checked against the files (`test_fixtures_dj.Locators`), the route table at main and at every head against the independent reader (`RouteTables`), the
http fits against the independent URL matcher (`Clients`), and the adapter's report against them (`test_scenarios_dj`).

## Acceptance bar

- Reproduces a hand trace and invents nothing beyond it: `test_scenarios_dj.test_the_report_of_every_scenario_matches_expected_json` (`complete_for` is set for
  every route, screen and http_edge list, so an extra item fails), `test_the_routes_reached_are_the_ones_traced_and_no_more`; `test_fixtures_dj` (the expected
  answers against the independent reader).
- Shape A traps, each with a failing read and an incompleteness: `test_scenarios_dj.test_a_trap_that_makes_a_read_fail_is_an_incompleteness...` (a URLconf that
  does not parse dj11, a root that is not a literal dj12, a setting that is not followed dj14), `test_a_file_that_cannot_be_read_does_not_turn_its_routes_into_removed_ones`;
  `test_django_traps` (a file that is not text, an unreadable settings file, a symlink git grep does not search, a failed search, a tree that cannot be read, a cut
  search, an unreadable or unparsable file that might mount a URLconf: not called unmounted, a base that cannot be read: nothing is new, a pattern element or a view
  that is not understood); `test_django_routes.Completeness`, `RootUrlconf`.
- Generic names dropped: dj08 (`index` is defined four times: `not_distinctive`, only the route that imports it), `test_django_views.GenericNames` (a view that does not
  resolve is never matched to a view of the same name elsewhere; two apps with `index` keep their routes), `test_django_reach.GenericNames`, `test_django_config.
  PythonAdapterHooks` (`urlpatterns` is never counted, another repeated name still is), and the http match is by segments (a client parameter fits only a route parameter).
- Every citation resolves: `test_scenarios_dj.test_every_evidence_in_every_report_resolves_at_its_pinned_sha` (the audit of the e2e harness over every report) and the
  `evidence_problems` call inside `Base.reach` of every unit test that builds areas.
- End to end through `reachmap analyse`: `test_scenarios_dj` runs the CLI in process for every scenario and as a separate process (`python -m reachmap`) for one;
  `TYPES` pins the types each scenario checks, `ss.skipped_types` is empty.

## Choices

- `ANY` as the method of a view that answers every method, and the generic hook in `edges/http.py` that lets it fit any call, rather than one route per HTTP
  verb (which invents routes) or `method_unknown` for every call.
- Evidence of a router route is the register line (and the action decorator first), not the class definition, because that is where the path comes from; the
  handler (`Class.action`) is in `Route.handler` and `Route.target`.
- `routes()` is ordered by path and method, not by discovery.
- A reached name called where a URLconf loads (`staff_only(views.f)` in `urlpatterns`) reaches every route of that URLconf: the Python adapter's rule for a call that
  runs at import time (the walk cannot tell which route the wrapper is on). Stated, tested, not refined.
- The settings are followed for `ROOT_URLCONF` only, and the rest says so (blocking) rather than silently reaching nothing.
- The record for an include of a package outside the repository is non-blocking; for one inside it that does not resolve, blocking.

## Known limits

- Routes are read from module-level `urlpatterns`. Not read: `i18n_patterns` (a pattern element that is not understood: blocking), pattern lists built by functions or
  comprehensions (blocking), `path()` with an alias of the function (`from django.urls import path as p`), DRF's format suffix routes (`.json`) and the
  `trailing_slash` of a router (paths are joined without trailing slashes, as for FastAPI), custom routers and `routes` lists (a router class that ends in
  `Router` and is not `DefaultRouter` or `SimpleRouter` is blocking), nested routers, `reverse()` and url names as links between code (names are listed, not followed),
  `request.urlconf` and middleware that swaps the URLconf, `ROOT_URLCONF` set at run time, `include()` of a callable, `admin.site.urls`, `django.contrib` URLconfs and other
  includes of installed packages (the routes behind them), views as variables (`view = views.f`), `View.as_view()` with an `http_method_names` that is not a literal (recorded), `method_decorator`,
  `dispatch()` overrides, `@api_view` on a class, `get_extra_actions` overrides, `lookup_value_regex` (the parameter is `{pk}`), and a viewset's actions that come from a
  base class outside the repository that is not in the tables (the route is `ANY`, the methods say `route_methods_unknown`).
- The method tables name generic views and mixins of Django and DRF; a project class with the same name as one of them is taken for the framework's when it is
  not defined in the repository.
- A viewset's detail route exists in DRF only when the viewset has one of the detail actions; the adapter follows that. The list route of a viewset with an
  empty prefix is `GET <mount>`, the same path as the API root view (both are listed).
- Narrowing by name in the Python adapter is by what the importer spells; a view reached through `getattr(views, "name")` is not followed (M6's limit).
- The walk goes past a view name only to a module that imports it and is neither a URLconf nor a test. A view that is called through another name (an alias,
  `getattr`) or that a decorator factory wraps is not followed there.
- A settings module is found by a literal `DJANGO_SETTINGS_MODULE` and by star imports of one that is known: a settings module chosen only by the environment
  (never written in the repository) and a settings module that is imported by name rather than with `import *` are not settings modules here.
- The Python route tables of Django and FastAPI each keep their own holes, and a route-table helper that both could share (a table with holes and the prefixes of
  them) is left for the brief: here Django has it, FastAPI withholds everything when its table is not whole, which is the safe side.

## Not done

- No change to `docs/PROGRESS.md`. No edit to `docs/briefs/M16-adapters.md`.
- Not covered: the frontend, `reachmap init` (no Django detection), a Celery or other entry-point kind for the same repository.

## Review fixes (second pass)

An independent review found a blocker, majors and minors. What changed:

- an include of a module of the repository was called external when the module map did not mount its folder: an include is external only when its first name is
  nowhere in the repository (not a folder, not a module); otherwise an include that does not resolve is blocking, and a URLconf in such a layout is never called
  unmounted. A vendored copy of a package named like an installed one is not external and keeps its record.
- the installed-package and convention names are per entry-point kind (a Django name is not given to a repository that is not a Django one).
- a settings module is more than the file with `ROOT_URLCONF` (rule 10).
- a view that another routed view calls reaches the caller's route (rule 9).
- an edited import statement reaches only the names whose binding changed, and the chain cites the statement (rule 9).
- route strings are concatenated, class views answer `HEAD` and `OPTIONS` and honor a literal `http_method_names` (rules 3 and 6).
- a route narrowed from `ANY` to some methods is not a `removed_route_still_called` for the calls that still fit (core, `_removed_hop`).
- what is withheld is scoped by holes, and an `@action` `detail` that is a module constant is evaluated and one that is not blocks (rule 11).
- Smaller: a `url_path` that is a regex is read as one and the route is approximate (`Route.path_approximate`, which is left out of the report JSON so the wire
  format of the reports does not change: `payload.LEFT_OUT`), the unmounted kind has its own wording (`urlconf_unmounted`), the arrow comments in the adapter
  were fixed (a test checks the adapter's comments and docstrings).

### Guard evidence for FastAPI and the core

The trap on main: a router file of a FastAPI service that stops parsing made every route of it a removed route, and each call of one a high-risk
`removed_route_still_called`. `tests/test_route_change_guard.py` (11 tests): `Guard` (the rule: a removal needs a whole head, an addition a whole base, and
one record says what was withheld; no record when nothing was), `FastApi` (an unclosed parenthesis in a router file gives no finding and an incomplete answer;
the adapter withholds the removals of a table it could not read; a base that could not be read makes no route of the head new; a removal between two readable
tables is still a finding), `NextJs` (a route file that cannot be read removes nothing; a readable deletion does), `CoreGuard` (an adapter that reports problems and
does not guard itself has its removals and additions withheld with a record; one that says which version is whole is asked; one that guards itself is left alone;
one with no problems is not touched). OpenAPI (server) says it guards itself (`withholds_unread`). Existing FastAPI, Next.js, OpenAPI and http edge modules pass
unchanged (`test_fastapi`, `test_nextjs*`, `test_edge_http`, `test_frontend_static`).

## Tests, speed

3,891 tests in the whole suite after the merge of main (3,366 before); the new ones of this branch are the Django modules (routes, views and DRF, reach, http,
inheritance, traps, config, path syntax, review 33, fixtures, scenarios end to end 19) and `test_route_change_guard` (11). All pass in the parallel runner (4 workers)
on Python 3.12 (3,891, 6 skipped, 341.4 s), 3.11 (3,896, 5 skipped, 421.5 s) and 3.13 (3,891, 6 skipped, 320.0 s). The machine was shared with other agents (load
average 11 to 21 at the start of these runs). The serial `unittest discover` was not run again after the review fixes. `tests/.timings.json` was not refreshed (a
busy machine would write the wrong weights).

Mutation check of the rules (each change made, the Django modules run, the change reverted; every one failed a test): `routes_changed` withholding nothing,
`incomplete` not marking the table unresolved, the installed-package hook off, the convention hook off, class members always whole, inherited members ignored,
`ANY` not fitting a call, a partial route fitting as exact, a regex route not partial, unmounted claimed beside an unresolved include, an unresolved view not blocking,
a non-literal root accepted, other settings silent, a commented include counted, namespaces ignored, an addition claimed against a base that is not whole, the
direct hop always the changed line, decorator methods ignored, an external include blocking, the include cycle guard off.

## Follow-up: shared Python route-table logic

The Django and FastAPI route tables now share `reachmap/adapters/python_routes.py` (unit tests in `tests/test_python_routes.py`). It holds the syntax helpers
(`statements`, `callee`, `kw`, `names_in`, `literal_strings`, `nearest_folder`), `FileFacts` (path, parse, import bindings, constants), `TableBase` (the search,
the facts per file with the `parse_degraded` rule, module resolution, constants evaluated to a path string), `route_evidence`, and `Gathered` and `Shared` (the
table per commit, `routes`, `routes_changed` through `guard_route_changes`, `routes_complete`, the areas of a change and the unmounted claim). No record kind,
message, evidence, ordering or report output changed. Both adapters were checked with the 37 fixture scenarios (23 of the M1 workspace, which includes the FastAPI
service `acme/api`, and the 14 Django ones): the reports before and after are identical except run timestamps, durations and the temporary directory.

What each adapter keeps, and why:

- FastAPI: routers, decorators (`api_route` methods), `include_router` and `mount`, `_Str` (a plain string with unknown parts), the way up through mounts and how
  a route is composed, the strays (mounts outside module level), the reach rules of a chain. Its words (`route table`, `routes and mounts in it were not read`) are
  the table's class attributes.
- Django: the URLconf and `include()` chains, regex routes, DRF routers and viewsets, namespaces, `ROOT_URLCONF` and settings, the `Str` with marker stand-ins for
  unknown parts and regex parts, star imports (`Facts.stars`, from `bind_imports`), `denote` (names to declarations and modules), the holes of the table (`incomplete`
  takes a scope, and `hole_covers` answers by path prefix, where the shared default says everywhere when the table is not whole), the `read_unreadable` record for a
  symlink (the `not_searched` hook), the files that did not read (the `unread` hook), and the chain evidence (`hops` not already in the route's evidence, where
  FastAPI uses `dedupe`).
- Equivalences checked rather than assumed: the helpers in `django/facts.py` and `fastapi/__init__.py` were the same as `python_routes`' (Django's `facts.py` still
  re-exports them in `__all__`); Django's constant check "not assigned in the file" (`name in facts.assigns`) is `name in facts.consts` because every assigned name has a
  `consts` entry; FastAPI's `routes_changed` (`base_ok=old.complete`, `head_ok=new.complete`) is the shared rule with `hole_covers` meaning "not complete"; the
  reasons in a withheld change are the head table's first for Django (`HEAD_REASONS_FIRST`) and the base's first for FastAPI, as each always said them.
