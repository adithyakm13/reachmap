# Spring Boot fixtures (M16, adapter `sb`)

Two small git repositories and 20 change scenarios for the Java and Kotlin language adapter and the Spring Boot entry-point adapter. They are
invented (company "acme") and never built or run. They are a tree of their own, apart from the M1 fixtures: the M1 repositories, shas and expected
files are what they were, and nothing that walks the M1 scenarios meets an `sb` one.

```text
tests/fixtures/spring/
  repos/sb-ledger/      acme/ledger: a Spring Boot service, Java with one Kotlin controller (steps 01-build, 02-code, 03-tests)
  repos/sb-web/         acme/ledger-web: a React client (react-router screens, an endpoints file) of its API
  scenarios/sbNN-slug/  scenario.json, changes/ (an overlay over main, optional .delete), expected.json
tests/fixtures/sb_build.py            the builder (the M1 builder's replay: same identity, dates and overlay rules)
tests/fixtures/sb_claims.py           an independent reading of the repositories (shares no code with the adapters)
tests/fixtures/workspaces/spring.toml the workspace: the two repositories, one http edge from the client to the service, distinct_max = 3
tests/support_sb.py                   builds the fixtures once per process and runs `reachmap analyse` on a scenario
tests/test_sb_fixtures.py             every expected.json is true of the files (against the independent reading)
tests/test_scenarios_sb.py            every scenario through the CLI against expected.json, no type skipped
```

Build them: `python3.12 -c "from pathlib import Path; from tests.fixtures.sb_build import build_all; print(build_all(Path('/tmp/sb')))"` from the root.

## acme/ledger (language `java`, entry points `spring`)

Context path `/ledger` (`application.properties`, with `ledger.reports.base` for a placeholder in a mapping).

| controller | routes | what it is for |
|---|---|---|
| `api/InvoiceController.java` | `/ledger/v1/invoices`: list, `{id}`, `{id}/total`, `{id}/price`, POST, DELETE `{id}`, `{id}/lines` (`@RequestMapping(method = GET)`) | class path from the constant `Paths.INVOICES` (`BASE + "/invoices"`), constructor injection of `InvoiceService` and `Pricing`, a static import (`Money.format`) |
| `api/StatusController.java` | `/ledger/v1/status`, `/ledger/v1/status/flags` | constants of the same package with no import (`Paths`, `Helper`), `cache.get` against `flags.get` (a map's `get`) |
| `api/AuditController.java` | `/ledger/audit` | imports `InvoiceService` and spells only `list` (narrowing excludes it when `total` changes) |
| `api/ReportController.java` | `/ledger/reports/daily` | the placeholder `${ledger.reports.base}` |
| `api/CustomerController.kt` | `/ledger/customers`, `/ledger/customers/{id}` | Kotlin controller, constructor property, a Java service, a Kotlin top-level function imported by name |

Services: `InvoiceService` (a class), `Pricing` and `PricingImpl` (an interface and its implementation: the controller imports the interface),
`Cache` (`get`), `CustomerService`. `support/` holds `Registry`, `Settings`, `Lookup` and `Memo`, which define `get` too (six definitions of `get`
in non-test code in all, with the handler `InvoiceController.get`, more than `distinct_max = 3`), and `Docs`, which names `InvoiceService#total` in a
comment and in a string only. `config/WebConfig.java` is a `@Configuration` with a `@Bean` method. Tests: `InvoiceServiceTest` (same package, no
import) and `InvoiceControllerTest` (imports the service, uses the controller).

## Scenarios

| id | change | what it proves |
|---|---|---|
| sb01-service-member | `InvoiceService.total` | member narrowing: the handler that spells `total`, not its siblings; the importer that spells another member is excluded; a comment and a string are no use; unit tests; the screen through the http edge |
| sb02-handler-body | a line in `InvoiceController.get` | a change inside a controller reaches that handler's route only; `get` is recorded as not distinctive |
| sb03-constant-path | `Paths.STATUS` | a constant that is part of a path moves the routes that use it; the old route is removed and still called; `client_url_unmatched` and a blocking `edge_incomplete` |
| sb04-interface-impl | `PricingImpl.price` | a change in an implementation reaches the callers of its interface that spell the member |
| sb05-same-package | `Helper.describe` | a class of the same package, used with no import |
| sb06-generic-name | `Cache.get` | `get` is defined six times: recorded, and the handler that calls a map's `get` is not reached; four classes with their own `get` are no callers |
| sb07-class-header | `@CrossOrigin` on `InvoiceController` | a header line is the class itself: every route of the class |
| sb08-added-route | a new handler | an added route nobody calls (`added_route_uncalled`) |
| sb09-removed-route | a handler removed | `removed_route_still_called`, the URL now fits another method |
| sb10-context-path | `server.servlet.context-path` | the context path is part of every route (thirteen routes); four removed routes still called |
| sb11-unlexable-controller | a string never closed in the controller | no route is claimed and none is called removed; a blocking `parse_degraded` and the guard's blocking `route_change_withheld`; not complete |
| sb12-unlexable-importer | a changed service and a file that imports it and does not lex | the importer list is not whole and says so |
| sb13-wiring | a `@Configuration` class | not followed: a blocking `config_not_followed` |
| sb14-other-property | `server.port` | not followed: a blocking `config_not_followed` |
| sb15-placeholder | `ledger.reports.base` | a property that a mapping names is part of that one route |
| sb16-test-only | a test | a unit test, no area |
| sb17-java-to-kotlin | `CustomerService.fetch` | a Java service reaches the Kotlin handler that calls it |
| sb18-kotlin-function | `titleCase` in `Strings.kt` | a Kotlin top-level function, imported by name |
| sb19-kotlin-handler | `CustomerController.all` | a Kotlin handler's own route |
| sb20-client-change | `fetchTotal` in the web client | the other direction: a client line to the Spring route it calls, and the screen |

## What the tests recompute

`test_sb_fixtures.py` checks every expected answer against `sb_claims.py` (regular expressions over text with comments and strings blanked, not
tokens): the route table (method, full path, file) at main and at every head, that every `must_find` route exists and every `must_exclude` route
exists too (it is not reached, it is not absent), the importers of a changed file against the callers, exclusions and tests listed, the definitions
behind each `not_distinctive`, that each changed symbol is a member whose lines the diff touches, that every http edge's URL fits its route by
segments and method, that the unlexable files do not lex by a plain count of quotes, that every locator is code at the revision it is read at
(`at: base` for removed lines and routes). `test_scenarios_sb.py` compares the adapter's route table with the same reading, then every report with
expected.json by the harness of M9 (`scenario_support.check`), the evidence audit of the e2e harness (600 and more locators resolve), determinism,
a separate process, wording and no machine details. A blocking `edge_incomplete` is the walker's record: `support_sb.check` leaves it out of the
check the harness makes of the entry-point result of the changed repository and checks that result separately.
