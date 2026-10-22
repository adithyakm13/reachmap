# M16 notes: Spring Boot and the Java / Kotlin language adapter (`sb`, branch `m16-spring`)

Section 5 of `M16-adapters.md`. Python 3.11+ stdlib only: no JVM, no build tool, no Spring. The text of Java and Kotlin files is read by a lexer and a
declaration reader written for this. Nothing is compiled or run.

## Files

```text
reachmap/adapters/jvm/
  lexer.py      comments (Kotlin's nest), strings, text blocks, raw strings, Kotlin templates (the inner tokens), char literals; problems for
                anything left open (string, comment, text block, a bracket that does not match)
  syntax.py     parse_java / parse_kotlin: package, imports (import, static, wildcard, static wildcard, Kotlin aliases), the unit tree (class,
                interface, enum, record, annotation, object, companion, fun, property, constructor, init), spans by brackets, annotations,
                supertypes (Kotlin `by` delegates too), uses of names and members, calls, chains
  closure.py    the closure of a changed file by spelled names, with a distinctiveness gate; "whole" units (constructor, init, bean methods,
                static initialisers that call something reached)
  __init__.py   JvmAdapter(AdapterBase): changed symbols by line, importers by every form, narrowing by member, dispatch through supertypes,
                definitions for distinctiveness; registers the languages java and kotlin and the record kind type_ambiguous
reachmap/adapters/spring/
  props.py      application.properties and a yaml subset (nested and flat keys, profiles recorded), placeholders
  expr.py       the value of an annotation argument: strings, constants, `+`, arrays, arrayOf, Kotlin templates, placeholders
  table.py      the route table: controllers, mappings, class and method paths, constants, context path, inherited mappings, meta-annotations
  adapter.py    SpringAdapter(EntryPointAdapter): routes, routes_changed (guarded), entry_points_reached, reached_by_files (configuration and
                wiring changes)
  records.py    route_style_unread, config_not_followed, config_unread (all blocking)
tests/fixtures/sb_build.py, sb_claims.py, workspaces/spring.toml, spring/ (repos, scenarios, README)
tests/support_sb.py
tests/test_jvm_lexer.py test_jvm_syntax.py test_jvm_closure.py test_jvm_adapter.py test_spring_props.py test_spring_routes.py
tests/test_spring_reach.py test_spring_traps.py test_sb_fixtures.py test_scenarios_sb.py
```

Config: `language = "java"` or `"kotlin"` on the repository, `entry_points = { kind = "spring" }`, optionally `context_path = "/api"` (replaces what
`server.servlet.context-path` says). A repository with both Java and Kotlin sources is read by one adapter whichever the declared language is
(both are read, a Kotlin file by the Kotlin rules).

## How it reads

JVM adapter.
- A changed symbol is a member (`Class.member`, nested classes dotted) whose lines the diff touches. A line of a type header or its annotations is
  the type itself, and so is an added or removed member's class (a modification of the class). An import line is a module-level change. A file
  that does not lex is one module-level change and a parse problem; its importers are searched from the path-derived identity.
- Importers: by import, static import, wildcard (counts when the importer spells a name of the package), same package with no import, fully
  qualified name in code, Kotlin alias, Java importing Kotlin and the reverse, a Kotlin top-level function by name through the file class
  (`FooKt`). Tests (`src/test/`) are unit tests, never callers. A class name declared twice keeps both files and says so (`type_ambiguous`).
- Narrowing: the changed file is closed by the names it spells (a member that calls a changed member widens the set). A class-level change, a
  constructor, an init block, a `@PostConstruct` or `@Bean` method or a static initialiser that calls something reached makes the whole type
  reached. An importer is a caller when it spells a name of the reached set; one that spells only other members is excluded (with the symbol and
  the reason). A name defined more than `limits.distinct_max` times in non-test code is not linked by name: recorded as `not_distinctive`, and only
  a use on its own (a static import, a Kotlin top-level function) links.
- Dispatch: a change in a class reaches the importers of its interface or base class that spell the member; a member the supertype does not
  declare reaches nobody through it.
- Reads answer found, absent or unreadable. An unreadable candidate, a candidate that does not lex, a cut search, a symlink or a name that cannot
  be cited makes the importer list not whole with a blocking record. Never an empty complete list.

Spring.
- A controller is a non-abstract class or object with `@RestController`, `@Controller` (Spring import required: `org.springframework...`, wildcard
  or qualified use accepted) or a class `@RequestMapping`; interfaces give their mappings to implementors; a base class's handlers are routes of
  the subclass; a method mapping on the subclass wins. Project annotations that carry a stereotype or a mapping count (one level).
- Mappings: `@GetMapping`, `@PostMapping`, `@PutMapping`, `@DeleteMapping`, `@PatchMapping`, `@RequestMapping(method = ...)` (several methods,
  none means ANY), `value`/`path`/array/`arrayOf`, a multi-line annotation cited at its first line, path variables including regex with braces,
  wildcards, trailing slash.
- Paths: the class path and the method path are joined; constants (`static final String` in the same project, a `const val`, in an `object` or
  companion, by qualified name, static import, wildcard) are followed with the line that gives the value as evidence; `+` and Kotlin templates are
  evaluated; a call or a constant outside the project makes the route partial (a record when a change reaches it); a cycle ends; `${key}`
  placeholders are read from the configuration and the key is part of the route's evidence; the context path and the servlet path come first.
- Evidence of a route, in order: the method mapping line, constants used, the class mapping, constants, the configuration lines.
- `routes_changed(base, head)`: the shared `guard_route_changes` (`reachmap/adapters/__init__.py`) decides, scoped by `unsure_removed` and
  `unsure_added`: a route is withheld when the table that does not show it has a hole anywhere (a search that failed, a package that could not be
  listed, a configuration that could not be read), when its file or a file its evidence cites was not read, or when a file that was not read
  names its controller; the others stand. One blocking `route_change_withheld` says what was left out; without a base version nothing is computed. The adapter says it guards itself
  (`withholds_unread`) and answers `routes_complete(sha)`, as the Django adapter does.
- `entry_points_reached`: the reach of a change by files, then the routes whose handler (or class header, constructor, field it uses) is in the
  reached set; evidence is the chain from the change to the handler, ending in the mapping line. A change in `application.properties` or yaml
  reaches the routes whose path used the key (the context path: every route). A `@ControllerAdvice` class reaches the routes it applies to:
  every route with no scope, the controllers of `basePackages` (or `value`) and of `assignableTypes` when it names them, every route for any
  other selector. A method of a controller that handles exceptions or binding (`@ExceptionHandler`, `@InitBinder`, `@ModelAttribute`) reaches
  the whole class. Any other key, and a class that wires the application (`@Configuration`, filters, interceptors, security classes, runners,
  `@AutoConfiguration`) is not followed: a blocking `config_not_followed`, never an empty answer.
- The configuration that applies to a controller: its own module's (the nearest build file above it). A module with none does not take a
  sibling's (the prefix is `<unknown:key>` and `config_unread` says why); several modules that disagree are the same. A key that is set
  under a profile, in a profile document (`#---` in `.properties`, `---` in yaml), or in a part of a file this reader does not understand is
  unknown, and a placeholder default is never used for it (`<unknown:${key}>`). The last value of a key in a file wins.
- A name that more than one file declares (copy-pasted services, the same class in two modules) is the one of the controller's own file, module
  or source root; when nothing tells, its constant is unknown and its supertype is not followed, the table is not whole and a blocking
  `type_ambiguous` says so (the importer search keeps the non-blocking record it always had).
- Files are listed by package from the `package` line each one holds (one search for the word), as well as by folder: a Kotlin file does not
  have to sit in the folders of its package. A search that could not be made makes the table not whole.
- A project annotation that is meta-annotated with a Spring one makes a controller or a handler; the files that use one are searched for by its
  name (they may spell neither `Controller` nor `Mapping`). The usage's path wins; with none, the path and the `method` the project annotation
  was declared with are used, and both lines are evidence. An attribute of the usage that may be an `@AliasFor` of the path makes the route partial.

## Shared-file edits (and why)

| file | edit | reason |
|---|---|---|
| `reachmap/entrypoints.py` | two lines after the nextjs registration: import `register` of the spring package and call it | register the kind `spring` (registry entry only) |
| `reachmap/propagate.py` `language_adapter` | a `java` / `kotlin` branch returning `JvmAdapter.from_config(repo, git, limits=limits, remote=remote)` | the pipeline builds language adapters here; the other languages have a branch of their own |
| `tests/test_config.py` `test_a_registered_language_becomes_valid`, `tests/test_edge_contract.py` `test_a_consumer_in_a_language_without_an_adapter_is_an_incomplete_edge_never_no_reads` | the example language `java` became `ruby` | both used `java` as the language that has no adapter and is not registered; with this branch it is registered, so they would fail for a reason that is not a regression |

Merged with `main` after the Next.js, OpenAPI and Django adapters (conflict free). The shared pieces that main now has are used and none is copied:
`guard_route_changes` (Spring's `routes_changed` calls it), `third_party.register("spring", installed=("org.springframework",))` (the per-kind
registry: the Spring table asks it which packages are the framework, and `JvmAdapter` takes `framework=` from `entry_points.kind` like the Python
adapter does, for `installed` and `conventions`; the JVM adapter makes no record for an unresolved import, so an `org.springframework` import was never a
miss), `ANY` method fitting in `edges/http.py` and the config kind registry. Nothing else is outside `reachmap/adapters/jvm`,
`reachmap/adapters/spring` and the new test and fixture files; no edit to `config.py`, `edges/`, the core model, `tests/test_fixtures.py` or the M1 builder and fixtures. The record kinds are
registered at import of the spring package (`records.py`) and of `reachmap.adapters.jvm` (`type_ambiguous`) so a fresh interpreter that only reads
a report knows them (test: `test_a_fresh_interpreter_that_only_reads_reports_knows_the_record_kinds_the_adapters_make`).

## Fixtures and scenarios

Own tree: `tests/fixtures/spring/` (two repositories, 20 scenarios, README), own builder `sb_build.py` (the M1 replay: same identity, dates, overlay
rules), own independent oracle `sb_claims.py` (regular expressions over text with comments and strings blanked; it imports no adapter code),
workspace `workspaces/spring.toml`. The M1 files are untouched (test: `test_the_m1_fixtures_are_untouched_by_the_sb_ones`). The README there has the
tables of controllers and scenarios; in short:

- acme/ledger: five controllers (four Java, one Kotlin) over `InvoiceService`, `Pricing`/`PricingImpl`, `Cache`, `CustomerService`; a constant
  based path (`Paths.INVOICES = BASE + "/invoices"`); context path `/ledger`; a placeholder path; `get` defined six times in non-test code
  (`distinct_max = 3`); a class that names `InvoiceService#total` in a comment and a string only; a `@Configuration` with a `@Bean`; two tests.
- acme/ledger-web: a client (react-router screens, an endpoints file) joined to the service by one http edge.

| scenario | proves |
|---|---|
| sb01 service member | member narrowing, a comment and a string are no use, unit tests, the screen through the http edge |
| sb02 handler body | a change inside a controller reaches that handler's route only; `get` recorded as not distinctive |
| sb03 constant path | a constant that is part of a path moves its routes; old route removed and still called |
| sb04 interface implementation | a change in an implementation reaches callers of the interface that spell the member |
| sb05 same package | a class used with no import |
| sb06 generic name | `Cache.get` (six definitions) links nothing by name; four classes with their own `get` are no callers |
| sb07 class header | `@CrossOrigin` on the class: every route of the class |
| sb08 added route | an added route nobody calls |
| sb09 removed route | a removed handler whose URL now fits another method |
| sb10 context path | the context path moves thirteen routes; four removed routes still called |
| sb11 unlexable controller | no route claimed, none called removed, blocking `parse_degraded` and `route_change_withheld`, not complete |
| sb12 unlexable importer | the importer list is not whole and says so |
| sb13 wiring | `@Configuration` change: blocking `config_not_followed` |
| sb14 other property | `server.port`: blocking `config_not_followed` |
| sb15 placeholder | a property a mapping names moves that one route only |
| sb16 test only | a unit test, no area |
| sb17 java to kotlin | a Java service reaches the Kotlin handler that calls it |
| sb18 kotlin function | a top-level function imported by name |
| sb19 kotlin handler | a Kotlin handler's own route |
| sb20 client change | the other direction: a client line to the route it calls, and the screen |

Every `expected.json` was hand traced from the files (not copied from the adapter's output), then checked against the independent reading by
`test_sb_fixtures.py` and against the adapter through `reachmap analyse` by `test_scenarios_sb.py`.

## Acceptance bar (context 10)

| bar | tests |
|---|---|
| reproduces a hand trace, invents nothing beyond it | `test_scenarios_sb`: `test_the_report_of_every_scenario_matches_expected_json`, `test_the_route_table_of_the_adapter_is_the_independent_reading_at_main_and_at_every_head_that_lexes`; `test_sb_fixtures`: `test_the_importers_are_the_files_the_independent_reading_finds_and_the_expected_answer_lists_each_as_caller_exclusion_or_unit_test`, `test_routes_are_the_routes_the_independent_reading_makes_in_the_file_the_evidence_names`; `test_the_harness_fails_when_the_report_is_wrong` shows the check can fail |
| Shape A traps (a failed read is an incompleteness, never an absence) | `test_spring_traps`: symlinked file, file name that cannot be cited, not UTF-8, cut search, failed search, unreadable configuration, tree that cannot be checked out, base class that does not parse, package file that does not parse, path that cannot be evaluated; `test_spring_routes`: `test_a_controller_that_stops_parsing_is_not_a_removed_route`, `test_a_controller_that_did_not_parse_at_the_base_is_not_an_added_route`, `test_a_search_that_cannot_be_made_is_an_incomplete_table_never_an_empty_one`, `test_an_unreadable_file_is_an_incomplete_table`; `test_spring_reach.Completeness` (three); `test_jvm_adapter`: `test_a_candidate_that_does_not_lex_makes_the_list_incomplete_and_says_so`, `test_an_unreadable_candidate_is_not_an_absence`, `test_a_file_that_does_not_lex_is_one_module_level_change_and_a_parse_problem`, `test_a_file_that_does_not_lex_makes_the_count_a_lower_bound`; scenarios sb11, sb12 end to end |
| generic word is dropped (Shape B) | `test_jvm_closure.test_a_member_name_that_is_too_common_is_followed_only_where_it_is_used_on_its_own`; `test_jvm_adapter`: `test_a_generic_member_name_is_not_linked_by_name_only_by_the_type`, `test_a_function_name_defined_many_times_is_not_followed_through_a_wildcard`; `test_spring_reach.test_a_generic_member_name_is_not_followed_by_name`; `test_scenarios_sb.test_a_generic_name_is_recorded_and_links_nothing_by_name`; scenarios sb02, sb06 |
| comment and string are never a use | `test_jvm_adapter.test_a_comment_or_a_string_that_names_the_class_is_not_an_import_or_a_use`; `test_scenarios_sb.test_the_text_of_a_comment_and_a_string_is_never_a_use`; `test_sb_fixtures.test_the_trap_files_name_the_class_and_are_not_code` |
| every citation resolves at its pinned commit | `test_scenarios_sb.test_every_evidence_in_every_report_resolves_at_its_pinned_sha`; `test_sb_fixtures`: `test_every_locator_names_a_line_of_code_at_the_revision_it_is_read_at`, `test_a_locator_in_a_java_or_kotlin_file_is_in_code_not_in_a_string_or_a_comment` |
| end to end through `reachmap analyse`, explicit types, no skip | `test_scenarios_sb`: `test_every_run_completes_with_exit_0_and_a_report`, `test_no_type_is_skipped_in_any_scenario`, `test_a_removed_or_added_route_is_a_finding_and_the_other_kinds_are_the_pipelines`, `test_one_scenario_runs_as_a_separate_process_and_the_kind_and_the_languages_are_known_to_a_fresh_interpreter` |
| undecidable means affected | `test_spring_reach.NotFollowed`, `test_a_path_that_cannot_be_evaluated_is_a_partial_route_and_a_record_when_a_change_reaches_it`, `test_jvm_adapter.test_reflection_in_the_changed_file_stops_narrowing`, `test_without_a_pre_change_version_nothing_is_narrowed` |
| routes_changed guard from the start | `test_spring_routes.RemovedAndAdded` (the four tests above and `test_without_a_pre_change_version_nothing_is_computed`), `test_spring_traps.test_a_removed_route_is_not_claimed_while_the_table_of_the_head_is_not_whole`, scenario sb11 |
| determinism, wording, no machine details | `test_two_runs_give_the_same_bytes_except_the_run_object`, `test_every_human_readable_string_passes_the_wording_check`, `test_no_report_says_anything_about_the_machine_it_ran_on`, `test_the_scenario_files_hold_no_ascii_arrow_in_prose_and_each_scenario_has_its_own_purpose` |

## Choices

- Reading by tokens, not by regular expressions, in the adapter; regular expressions only in the independent oracle, so the two can disagree.
- Members are `Class.member` symbols; a type header (annotations, supertypes, constructor parameter list) is the class itself. A constructor,
  init, bean or post-construct method, and a static initialiser that calls something reached, reach the whole type. A constructor that only stores
  its parameters does not make the type whole when the type was reached by spelling a name.
- A class name is resolved by the import, then the same package, then wildcard imports, then a qualified name; a name that two files declare
  keeps both candidates and records `type_ambiguous` (non-blocking).
- An unreadable or unlexable file in a searched package is a blocking `parse_degraded` for the whole table (its handlers or its base class could be
  anywhere in it), not a missing route.
- Configuration: a property reaches only the routes whose path used the key, so a properties change is not "everything". Other keys are not
  followed, and say so. Profiles are compared by value (the same value changes nothing; another value makes the prefix unknown).
- The scenario trees cite the top-level declaration start (for example the `@Service` line) for an exclusion's symbol; `Symbol.start` is the
  top-level span.
- The fixture is fast: the 20 scenarios build in about 4 s. A synthetic project of 1361 files (300 services, 60 controllers, 1000 unrelated
  files) analysed and walked in about 6 s on a loaded machine (scratch test, not committed).

## Known limits

- No component scan or unmounted-module model: every controller in the repository's source roots is taken as mounted.
- No request conditions (`params`, `headers`, `consumes`, `produces`): two handlers differing only by those are two routes with the same method
  and path.
- Functional routes (`RouterFunction`) and Jakarta REST (`@Path`) are found and recorded (`route_style_unread`, blocking) but not read.
- Environment variables, `spring.config.import`, profile files beyond the equal-value check and `@ConfigurationProperties` are not followed.
- Bean wiring and dependency injection beyond static imports and interface dispatch are not followed; classes that wire the application are a
  blocking `config_not_followed`. A qualifier or a profile that selects one of several implementations is not understood.
- Reflection in a changed file stops narrowing (the importers are all callers); shadowing of a name by a local or a parameter is not understood
  (a spelled name counts).
- Kotlin: a type is found by the folders of its package and by the `package` line of the files (one search); a file that has no `package`
  line is in the root package only by its folder. A Kotlin extension function is called as a member of its receiver by the importer rule, and
  its receiver type is not checked.
- Unicode escapes outside strings (`\u0041` in an identifier) are not translated by the lexer.
- A member reached through the result of a call, by a file that never imports its type (`order.getCustomer().total()`), is not found: the
  importers of a class are the files that import it or name it, and a type that only a getter returns has no import to follow.
- A meta annotation is read one level (a project annotation that carries a Spring one); one that carries another project annotation is not.
- `backend_calls` from a client list explicit imports only, not same-package use.
- Yaml: flow mappings, literal blocks, anchors and tabs are reported unread (a key under one is hidden and said).
- Annotation processors, Lombok-generated members, `@RequestMapping` on a Kotlin extension, and generated sources are not seen.

## Review fixes (second pass)

An independent review found no blocker and eight answers that were complete and wrong on common layouts. Each has a test in
`tests/test_spring_review.py` (39 tests) that failed before the fix:

1. a placeholder default used when the key is set to something unknown (a profile disagrees, a yaml flow mapping may hold it, it is set only
   under a profile): `<unknown:${key}>`, partial, `config_unread`.
2. a context path taken from a sibling module: the prefix is unknown, `config_unread` says so.
3. a class declared in two modules (or two source roots) resolved to the first file: the controller's own module, then source root; else unknown
   and a blocking `type_ambiguous` (the kind existed for importers, non-blocking; the Spring table records it blocking for the routes it affects).
4. controllers made of project annotations only were never searched: the files that use such an annotation are searched for by its name.
5. a project annotation's own path and `method` were ignored: now used when the usage has none; `@AliasFor` attributes make the route partial.
6. `@ExceptionHandler`, `@ModelAttribute` and `@InitBinder` methods are wide (the whole class), and `@ControllerAdvice` reaches the routes it applies to.
7. Kotlin (and Java) files whose folders omit or misspell the package: found by their `package` line.
8. `#---` document separators in `.properties`: split, with the same profile logic as yaml.

Smaller: the class's `method` is added to a handler's methods like Spring does; the guard is scoped by file (a route moved into a file that does
not parse is still withheld); Jakarta REST is a resource only with a non-client `ws.rs` import; duplicate keys: the last value wins; a route that
cannot be cited makes the table not whole; more wiring names (`@AutoConfiguration`, `@SpringBootConfiguration`, method security, runners,
`ErrorController`) and test folders (`src/integrationTest/`, `src/it/`); dead code removed (`installed`, `stem_path`, `notes`, two duplicated
helpers); the ASCII arrows in comments and docstrings are gone and a test scans both packages for them; tests for unicode escapes and for a Kotlin
template with braces inside (they kill two mutants the review found). `Table.complete` is a property that composes the routes first, because what
composing finds (ambiguity, a package that could not be listed) is part of the answer.

## Not done

- No mutation tool is kept in the repository. Four guards were removed one at a time by a scratch script and the targeted modules were run: the
  routes_changed guard (3 tests fail), the distinctiveness gate (3 fail), member narrowing of importers (2 fail) and the gap check of a searched
  package (1 fails). The other guards were not mutated in the first pass. In the review pass 20 more were made, each failing at least one test of
  `test_spring_review` (one survived at first, the module preference over the source root, and got its own test), and the two lexer mutants the
  review named (unicode escapes, braces inside a template) are killed by the new lexer tests and not by the old ones.
- Nothing was run against a real Spring Boot repository: the only code read is the synthetic fixture and the strings the unit tests build.
- Gradle and Maven files are not read: files are `.java` and `.kt` under any directory, the package comes from the file's own `package` line (or
  from the path, for a file that does not parse), and tests are the paths holding a test marker (`src/test/` by default).

## Tests

First pass. New modules (3.12, parallel runner, per-module time on a loaded machine): `test_jvm_lexer` 44, `test_jvm_syntax` 44, `test_jvm_closure` 14,
`test_jvm_adapter` 48 (19 s), `test_spring_props` 14, `test_spring_routes` 45 (10 s), `test_spring_reach` 29 (17 s), `test_spring_traps` 11, `test_sb_fixtures` 21
(15 s), `test_scenarios_sb` 21 (35 s): 291 tests. The fixture builds in about 4 s.

After the merge of `main` (the Django adapter and the shared guard), last run on the commit that holds these notes' code:

| run | tests | skipped | time | result |
|---|---|---|---|---|
| `python3.12 tests/run_parallel.py` | 4182 | 6 | 327.5 s | OK |
| `python3.11 tests/run_parallel.py` | 4187 | 5 | 305.9 s | OK |
| `python3.13 tests/run_parallel.py` | 4182 | 6 | 323.2 s | OK |
| `python3.12 -m unittest discover -s tests` (serial) | 4182 | 6 | 620.7 s | OK |

Load average at the start of the runs: 3.8, 4.3, 4.8 and 5.1 (the machine is shared; earlier runs of this branch were at 11 to 13 and took 450 s
and 269 s to 387 s for the same suite). The serial run prints 26 `ResourceWarning: unclosed file` lines from `unittest/case.py`; their source was not
traced. A synthetic project of 1361 files (300 services, 60 controllers, 1000 unrelated files) was analysed and walked in about 6 s (a scratch test,
not committed). Two failures found by the first full run were fixed (raw invisible characters in the lexer source, and two shared tests that used `java` as
the language with no adapter); the merge with `main` then needed one scenario change (sb11 gets the guard's `route_change_withheld`).

Review pass (after the merge of `main` with Express and NestJS, 4c9baa0, and the fixes above): `test_spring_review` 40 tests, `test_spring_props` 15,
`test_spring_routes` 45, `test_spring_reach` 29, the rest as before. `python3.12 tests/run_parallel.py`: 4629 tests, 6 skipped, 748.6 s, one failure
(`tests.test_py_modules` `test_a_large_monorepo_builds_in_well_under_a_few_seconds`, 1.8 s against a 1 s limit, in the Python adapter, which this branch does
not touch: the machine load average went from 8 to 29 during the run, and the module passes alone in 0.9 s at a load of 26). Python 3.11 and 3.13 and
the serial discover were not run again in this pass.
