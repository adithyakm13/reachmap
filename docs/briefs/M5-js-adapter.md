# M5 brief: TypeScript / JavaScript language adapter

Goal: the first language adapter. Given a change in a TS/JS repository, it answers which
symbols changed (placed by diff line number), who imports the changed file (by every
specifier: all aliases and relative paths, static and dynamic), which importers are
excluded by named-export narrowing, which call sites a single-parameter guard excludes,
which paths are tests, and how many times a name is defined. Every answer is Evidence
pinned to a commit, or an incompleteness record. Read `docs/CONTEXT.md` sections 3, 4,
5 (Shape A and Shape B), 6, 7.3 and 7.5 first; then `tests/fixtures/README.md` (schema,
"What the tests recompute"), and the M2 to M4 code and briefs' implementation notes
(`reachmap/model.py`, `incomplete.py`, `config.py`, `gitlayer.py`, `diffparse.py`,
`changeset.py`).

Python 3.11+, standard library only. No TypeScript compiler, no node.

## Files

```text
reachmap/adapters/__init__.py      Protocols (context 7.3) and shared types
reachmap/testpaths.py              test path markers by segment (shared with Python in M6)
reachmap/adapters/js/__init__.py   JsAdapter (LanguageAdapter for typescript/javascript)
reachmap/adapters/js/lexer.py      tokenizer: comments, strings, template literals, regex
reachmap/adapters/js/syntax.py     imports, exports, top-level declarations and spans, calls, guards
reachmap/adapters/js/aliases.py    alias config (tsconfig/jsconfig paths, baseUrl, extends)
reachmap/adapters/js/resolve.py    specifier to file, and file to every specifier
tests/test_js_lexer.py, test_js_syntax.py, test_js_aliases.py, test_js_adapter.py
tests/test_testpaths.py
tests/scenario_support.py          runs adapters on M1 scenarios and checks expected.json
tests/test_scenarios_js.py         the web scenarios at the language-adapter level
```

## Shared types (`reachmap/adapters/__init__.py`)

- `Symbol(repo, path, name, kind, start, end, exported, nested: tuple[str, ...], side)`:
  `side` is `before` or `after`; `start`/`end` are 1-based inclusive line numbers.
- `CallSite(repo, path, line, symbol, evidence: Evidence, import_evidence: Evidence |
  None, args: tuple[str, ...])`.
- `ImportEdge(importer_path, specifier, names: tuple[str, ...] | None (None = whole
  module: namespace, default+side effects, dynamic, require), evidence: Evidence)`.
- `Exclusion(subject_path, reason, evidence: tuple[Evidence, ...])`: reasons
  `named_export_narrowing`, `guard`.
- Protocol `LanguageAdapter` with the context 7.3 methods; every method that reads code
  takes the Git layer, the pinned sha and a record target (list or Analysis), and returns
  data or records why it could not. Never None for a failure.

## Rules

1. Changed symbols: from the `ChangeSet` hunks, map each added line (new line number) to
   the innermost top-level declaration span in the after text, each removed line (old line
   number) to the span in the before text. Never search for the line's text. Report the
   outermost top-level declaration as `name` and nested functions in `nested`. A changed
   line outside any declaration (top-level statement, import line) gives a module-level
   change (`name` = `<module>`), which affects every importer. Removed declarations and
   added declarations are reported with `change` removed/added. If `before` is None
   (pre-change unavailable), removals are not computed and the adapter does not record a
   second `pre_change_unavailable` (the change set already did).
2. Lexer: correct for line comments, block comments, single and double quoted strings,
   template literals with nested `${ ... }` (including braces and strings inside), regex
   literals (heuristic by previous token), JSX text is treated as code but braces balance.
   Every later step works on tokens, so text in comments and strings never counts as code
   (Shape B: match code, not comments).
3. Declarations and spans: `function f`, `export function f`, `export default function`,
   `const f = (...) => {...}`, `const f = function`, `class C`, `export const X = ...`
   (a value constant spanning lines, including a two-line string constant), object and
   array literals; `export { a, b as c }`, `export * from`, `export { x } from`
   (re-exports count as importers of the source file and re-export its names).
4. Aliases: read from the config's `aliases.from` file (`tsconfig.json` in the fixture;
   also `jsconfig.json`), following `extends`, `baseUrl`, `paths` with `*` wildcards and
   multiple targets. JSON with comments and trailing commas (tsconfig allows both) is
   parsed by stripping them with the lexer, not a regex. A paths target directory that
   does not exist is recorded (non-blocking) and ignored. The stale editor config in the
   fixture must not be used when the config names tsconfig.
5. Resolution, specifier to file ("which file does this specifier mean"): relative,
   alias (longest matching pattern wins), extensions `.ts .tsx .js .jsx .mjs .cjs .d.ts`
   and `/index.*`, exact file existence checked in the tree at the pinned sha.
6. Reverse lookup, file to specifiers ("which specifiers mean this file"): ALL of them,
   every alias pattern that maps to it (not only the longest), with and without extension
   and `/index`, plus relative specifiers computed per importer. Importer search: `git
   grep` (M4 grep, fixed strings) for the file stem (or the directory name for an index
   file) as a candidate filter, then parse every candidate and resolve each of its import
   specifiers with rule 5; a candidate counts only when a specifier resolves to the
   target. Candidate filtering must not depend on the alias spelling. An examine cap or
   unreadable grep is recorded by M4; the adapter passes the result through and never
   presents a truncated list as complete.
7. Import forms: `import x from`, `import {a, b as c} from`, `import * as ns from`,
   `import "side-effect"`, `import type` (still an importer, but type-only: names marked),
   `export ... from`, `import("...")` dynamic (including inside `React.lazy`), `require()`.
8. Named-export narrowing against the pre-change version: an importer whose named imports
   include none of the names the change reaches is excluded with evidence: its import line
   and the changed symbol's span start line (and the start line of each declaration the
   change reaches beyond the changed ones). The names the change reaches are the export names
   (looked up in the before AND after export lists, so a removed or renamed export still
   counts) of the changed declarations and of every declaration of the same file that uses
   them by identifier, repeated to a fixed point (an exported function that calls a changed
   one, a constant that holds it, a class method that calls it, an unexported helper's
   exported users). Namespace, default, side effect, dynamic, require and `export *` imports
   are not narrowed (whole module). A module-level change is not narrowed, nor is a change
   in a file whose references cannot be determined (it does not lex cleanly, or uses `eval`,
   `new Function`, `with`, `this[...]` or `window[...]`) or whose module-level code uses a
   reached name. Without a pre-change version, narrowing is not computed: every importer is
   affected.
9. Callers: the first hop outside the changed file: direct importers that survive
   narrowing, and for each the call sites of the changed symbol in that file
   (`symbol(` tokens after the import binding, alias-renamed bindings followed, `ns.symbol(`
   for namespace imports). Test files are never callers: they are reported separately as
   unit tests (rule 11).
10. Guards (rule 4.6, undecidable means affected): a call site is excluded only when the
    changed lines all lie inside an `if (<param> === "<lit>")` / `!==` / `==` / switch
    `case "<lit>":` block of the changed function, where `<param>` is a parameter of
    that function, and the call passes a string literal at that parameter's position that
    provably cannot enter the block. Any other argument form (variable, expression,
    spread, missing, default parameter) is undecidable, so affected, with reason
    `undecidable`. The exclusion cites the guard line and the call line.
11. Test paths (`reachmap/testpaths.py`): markers match by path segment, never by
    substring. `foo/` matches a directory segment equal to `foo`; `.test.` and `.spec.`
    match a file-name infix; `test_` matches a file-name prefix; `_test` a suffix before
    the extension. `src/contest/engine.ts` is not a test for marker `test/`; `latest_rates.py`
    is not a test for marker `test_`. Unit tests of the change are the test files that
    import a changed file or a caller (one hop), each with its import evidence.
12. Definitions (distinctiveness cap): `definitions(name, sha)` counts declarations of
    `name` (function, class, const/let/var binding, method, exported name, object
    property assignment `name:` excluded) in non-test code across the repo, using grep as
    the candidate filter and the parser to confirm; references and imports do not count.
    A grep failure is unreadable and the caller must treat the name as not established,
    never as distinctive.

## Scenario harness (`tests/scenario_support.py`)

- Builds fixtures once per process (cache by temp dir), loads scenarios with
  `tests.fixtures.build.scenarios`, resolves each change with M4's `changeset.resolve`
  using the plain host and `file://` remotes, runs the adapter, and compares the adapter's
  answers to `expected.json` for the types this milestone implements: `changed_symbol`,
  `changed_file` (role), `caller` (must_find, and must_exclude entries with evidence as
  exclusions with that evidence), `unit_test`, and `not_established` kinds the adapter
  owns (`not_distinctive` via definitions for s19). Types it does not implement are
  skipped explicitly (a list per milestone), never silently.
- `complete_for` is enforced for the implemented types.
- Matching: repo, path, symbol exact; evidence by `{path, contains}` at the right
  revision (reuse `tests/fixtures/claims.py` helpers).
- Web scenarios that must pass at this level: s01, s03, s04, s14, s16, s19, s20 (and
  the language-level parts of s02 and s05: changed symbols and callers).

## Tests (beyond the scenarios)

- Lexer: comment-like text in strings, strings in comments, nested template literals,
  regex containing `//` and quotes.
- Shape B: a removed import line's module path is not a fingerprint (s20); `mapStateToProps`
  defined in 4 files is not distinctive (s19); text placement ambiguity (two functions with
  an identical line, change one, only that one is reported).
- Shape A: importer search sees alias, second alias and relative importers (s01); a grep
  examine cap passes through as `search_truncated` and the importer list is marked
  incomplete; an unreadable candidate file makes the importer list incomplete (recorded),
  not shorter.
- Aliases: `extends` chain, multiple targets, wildcard, missing target directory.
- Every Evidence produced resolves: `git show <ref>:<path>` line `<line>` equals
  `detail` (a helper asserting this for every evidence the scenario run produced).

## Implementation notes

Where the code differs from, fills in, or goes beyond the text above. Each point is covered by
a test unless it says otherwise.

Files added beyond the list: `tests/test_js_resolve.py` (resolution and reverse lookup). The
adapter has one more public entry than the brief names: `JsAdapter.analyze(change_set)` runs
every rule over a resolved change and returns a `ChangeAnalysis` with one `FileImpact` per
changed file (symbols, importers, callers, exclusions, unit tests, hub, narrowing); the
protocol methods (`changed_symbols`, `importers`, `callers`, `guards`, `definitions`,
`is_test_path`) are the pieces it is made of.

### Shared types and the adapter object

- The adapter is an object bound to the repository name, its remote, the Git layer and a
  record target (a list or an Analysis; by default the Git layer's own). Every method that
  reads takes the pinned sha; a method's `record_to` replaces the target for that call, for
  the Git layer's records too. `JsAdapter.from_config(repo, git, limits=...)` takes
  `aliases.from`, `tests.markers`, `hub_importers` and `distinct_max` from the workspace.
  `examine_cap` is a constructor argument only (no config key names it). A commit never
  changes, so file reads, parsed files, the tree, importer answers and definition counts are
  kept for the life of the adapter; a record is made when an answer is first computed, so
  use one adapter per run (and one `record_to` per run).
- `Symbol` has the brief's nine fields plus `change` (`modified`, `added`, `removed`),
  `export_names` (the names the module exports it under, from the before and the after
  export lists), `evidence` (the changed lines: added lines at the head commit, removed lines
  at the pre-change commit), `added_lines` and `removed_lines`. `CallSite` adds
  `caller_symbol` (the top-level declaration the use sits in, `<module>` at the top level;
  `expected.json` calls it the caller's `symbol`, while `CallSite.symbol` is the changed name
  that is used) and `form` (`call`, `reference` for JSX and
  values passed on, `import` for an importer with no use). `ImportEdge` adds `kind`
  (`import`, `reexport`, `export_star`, `dynamic`, `require`), `type_only`, `bindings` and
  `target`. New types: `Caller`, `UnitTest`, `ChangedFile`, `FileImpact`, `ChangeAnalysis`,
  `ImporterResult`, `DefinitionCount`, `GuardVerdict`.
- `callers(symbol, sha)` returns call sites (the first hop, tests excluded); the brief's
  caller-level answer (survives narrowing, guard verdicts, reason) is `Caller` in `FileImpact`.
- `ImporterResult.complete` is False when a search was cut (`search_truncated`, recorded by
  the Git layer) or unreadable, a candidate file could not be read or cited, or the alias
  configuration could not be read completely; `reasons` says why. The edges are then the ones
  found, never the whole list. `DefinitionCount.count` is None for an unreadable search and
  `distinctive(max)` is False for anything not complete.

### Rules, as built

1. Placement is by line number only. A changed line outside every declaration that holds no
   code (a comment or a blank line between declarations) changes nothing and gives no symbol;
   inside a declaration it belongs to the declaration. A pure rename without hunks is a
   module-level `removed` symbol for the old path. A file that cannot be read in full (the
   Git layer recorded it), or whose hunks are not available, is one module-level symbol, never
   silently nothing. With no pre-change version only added lines are placed and nothing is
   recorded by the adapter. When either text did not lex cleanly (`Parsed.problems`: an
   unterminated string, comment or template, an unmatched or mismatched bracket) no line can
   be trusted to sit in a declaration, so every changed line is module level (no narrowing, no
   guard exclusion) and a non-blocking `parse_degraded` record names the file. A changed
   `.json` file is one module-level change (its importers are searched, a deleted or renamed
   one at the pre-change commit); `.vue`, `.svelte`, `.astro`, `.mdx`, stylesheets, `.svg`,
   `.graphql`, `.html`, `.yaml` and `.wasm` files make `importers_complete` False and record
   a non-blocking `file_not_analysed` (documents, images and lock files are not flagged:
   nothing imports them as code).
2. `Symbol.nested` lists the named functions around the changed lines, outermost first:
   function declarations, constants holding a function, class methods, function-valued class
   fields and object properties. One `Symbol` per (declaration, nested chain) pair.
3. Aliases: when `aliases.from` is not set the adapter reads `tsconfig.json`, then
   `jsconfig.json`, at the repository root. The named config being absent at the pinned
   commit is an answer (that commit defines no aliases): no record. An `extends` that is a
   missing file records a blocking `alias_config_gap`; one that is a package name, a cycle, a
   chain over 10 files (this one also marks the importer lists incomplete), an odd value,
   and a `paths` target whose folder does not exist record non-blocking `alias_config_gap`
   (a new kind, registered by `aliases.py`). A config
   that does not parse is a blocking `read_unreadable` (so `aliases.from` naming a
   `babel.config.js` or a webpack config, which are code, is reported, not guessed). Only the
   one config (and what it extends) is read: no per-package configs, `references` or `rootDirs`.
   An `extends` of a package that sets no `paths` or `baseUrl` of its own anywhere in what was
   read makes the alias configuration (`AliasConfig.complete`) incomplete, and so every
   importer list. A named `aliases.from` that is absent sets `AliasConfig.missing`; it is
   recorded (non-blocking `alias_config_gap`) when an unresolved specifier shows it mattered,
   not on every commit that predates the file (s16 expects no record).
4. Resolution tries the matching alias patterns longest prefix first and goes on to shorter
   ones when a pattern's targets hold no such file (TypeScript stops at the longest); then
   `baseUrl`. `x.js` means `x.ts` or `x.tsx` first. Reverse lookup returns only spellings
   that resolve back to the file (checked), so it agrees with resolution.
5. Importer search: `git grep` (fixed string) for the file stem (the folder name for an
   index file) plus the spelling of any exact alias that does not hold the stem; for an index
   file also every JS file under its folder (`from "."` and `from ".."` name no folder); for a
   root index file every JS file. A name that cannot be cited as Evidence (a backslash, bytes
   that are not UTF-8) is recorded as `read_unreadable` and makes the list incomplete.
   Importers of a deleted or renamed file are searched at the pre-change commit, for the old
   path. The Evidence line of an import is the line of the specifier (the `} from "x"` line of
   a multi-line import). The list is also incomplete, with a reason and a record, when a
   candidate file does not lex cleanly (blocking `parse_degraded`: an import may be hidden in
   it), when a candidate has `import()` or `require()` with an expression (blocking
   `specifier_unresolved`), when `git grep` did not search a symlinked JS file
   (`SearchResult.not_searched`, blocking `read_unreadable`), and when a non-relative
   specifier whose last segment is the file's stem (the folder name for an index file)
   resolves to nothing: an alias, workspace package or build-tool path this adapter does not
   read. That last one is recorded once per prefix (`~/lib` of `~/lib/format`) as a blocking
   `specifier_unresolved`; external packages that do not name the file are not.
6. Default imports have `names = None` like namespace, side effect, dynamic, require and
   `export *`: never narrowed. A named re-export (`export { a } from`) is narrowed like a
   named import.
7. Narrowing (`JsAdapter._narrowing`, one implementation for `analyze` and for `callers(symbol,
   sha, change=...)`) is only computed with a pre-change version, for a file that is neither
   deleted nor renamed, when no changed symbol is module level, when both texts lex cleanly
   and use no dynamic access, when no module-level code (code outside every declaration, import
   and export list) uses a reached name, and when the reached declarations have at least one
   export name (a change that nothing exported reaches is affected, not excluded). It compares
   an importer's names with the closure of rule 8. The identifiers a declaration spells are
   its references (shadowing is not understood, so the closure is generous). The Exclusion
   cites every import line of the importer, the start line of each changed symbol's declaration
   and the start line of each other declaration in the closure. `callers(symbol, sha)` without a
   `change` narrows nothing. Test files are not narrowed: any test that imports the changed
   file is a unit test. State shared between functions that is only mutated (a function
   that reads a variable another function writes) is not followed.
8. Callers: the uses of the changed export names in each surviving importer (alias-renamed
   bindings followed, `ns.name(` for namespace imports; JSX and values passed on are
   `reference` sites). When narrowing does not apply, every use of what the importer takes
   from the file counts. An importer is excluded by a guard only when every one of its call
   sites is excluded.
9. Guards (`JsAdapter.guard_verdict`): conditions of the form `param op "literal"` (or the
   literal first) with `===`, `==`, `!==`, `!=`, in `if`, `else if`, `else` (the complement of
   the conditions before it) and `switch` `case "lit":` (stacked cases and fall through
   without `break`, `return`, `throw` or `continue` are followed; `default` and non-literal
   cases are not guards). A block holds the lines strictly inside its braces (a body on the
   line of its condition holds none, so a change there is not excluded). Every changed line
   of the function, on both sides of the diff and in nested functions too, must lie in a
   block on a parameter; a call is excluded when for each line some block cannot be entered
   by its literal. Template literals without substitutions and single quotes count as
   string literals. Undecidable (a variable, member, call result, expression, spread, missing
   argument, a reference or a bare import, a parameter with a default value or one that is
   assigned, redeclared or shadowed anywhere in the function: `x = ...`, `x++`, destructuring
   assignment, `var`/`let`/`const`/`function` names and patterns, arrow, function and method
   parameters, `catch (x)`, `for (x of ...)`, any use of `arguments`) gives reason
   `undecidable`; a literal the guard lets in gives reason
   `literal_enters_guard` (the fixtures call it `literal_legacy`; the harness maps the name).
   Guards need the pre-change version: without one nothing is excluded by a guard, and a file
   that does not lex cleanly has none. `Symbol.guards` cites the guard lines that enclose every
   changed line of a function. A comparison with `null` or `undefined` is no guard (only
   string literals are); `"01" == "1"` is false in JavaScript, so a string literal argument is
   compared as a string.
10. Test paths: `reachmap/testpaths.py` classifies a marker by its shape (`dir/`, `.infix.`,
    `prefix_`, `_suffix`) and validates it; a multi-segment directory marker (`a/b/`) must
    match consecutive folders. When the repository config has no `tests.markers`, the
    adapter uses `__tests__/`, `test/`, `tests/`, `.test.` and `.spec.`.
11. Definitions: grep proposes files (non-test, JS-like), the parser confirms: function,
    class, interface, enum, namespace and type declarations, `const`/`let`/`var` bindings
    (destructuring included, at any depth), methods of classes and object literals, and
    `export { x as name }`. Imports, re-exports, references, `name:` properties and class
    fields do not count. The adapter records `not_distinctive` (non-blocking) for every
    changed name defined more often than `distinct_max`; a count that is only a lower bound
    (the search was cut or a file could not be read) but already over the limit is recorded too
    ("at least N"); a count that is incomplete and not over the limit, or could not be made,
    puts the name in `FileImpact.names_unreadable` (the Git layer's record blocks): it is never
    treated as distinctive.
12. Hub: the adapter applies `hub_importers` itself (context 7.5 says the core enforces it
    for every edge; the core will use `FileImpact.importer_count` and `hub`). Above the limit
    (distinct non-test importer files) it records `hub_cutoff` with the changed file as
    subject, returns the importer edges and count, and walks nothing: no callers or exclusions.
    Tests that import the hub directly are still listed as unit tests (s02's `complete_for`
    does not include `unit_test`). This is what makes s02 pass at the language level.

### The harness

- `tests/scenario_support.py` is generic over adapters: `check(run)` compares the output with
  `expected.json` for the types a milestone implements. The fixtures are built once per
  process by `tests.support_git.fixture_repos()` (shared with the M4 tests) and served as
  `file://` remotes; changes are resolved by `changeset.resolve` with the plain host.
- `not_established` is compared as the complete list of the run's records (kind, subject,
  blocking), the change set's included; the `importers` count of a `hub_cutoff` and the
  `definitions` count of a `not_distinctive` are checked against the adapter's own counts.
- A changed symbol's `evidence` is looked up among the symbol's own changed-line evidence and
  its `guards` (s03 cites the guard line); `removed_line` (s20) among those and the `<module>`
  evidence of the same file. s20 puts the removed import line under `InvoiceListPage`; rule 1
  makes it a module-level change, so the file has a `<module>` symbol as well, and for
  `complete_for` the harness compares only named symbols (`expected.json` lists functions;
  `tests/fixtures/README.md` now says import lines are module-level changes).
- The importer set the adapter finds for each changed source file is compared with the one the
  independent parser of `tests/fixtures/claims.py` (`direct_importers`) finds.
- `tests/test_scenarios_js.py` pins the skipped types per scenario (`SKIPPED`) and checks
  that the implemented and skipped types together are every type in the web scenarios.

### Known limits of the lexer and the parser

- Lexer. JSX text is lexed as code. A quote glued to the end of a word (`Don't`) is taken as
  text, not a string; a quote after `}` or a space in JSX text starts a string that ends at the
  line end (recorded in `LexResult.problems`); `//` in JSX text starts a comment, except a `//`
  glued to a `:` and to what follows (`http://x`); a `/*` or a backtick in JSX text, or a regex
  after `)`, usually leaves an unterminated token or an unbalanced bracket, which makes the file
  "not lexed cleanly" (changed file: module-level; candidate: incomplete importer list). A `//`
  in JSX text after a space still swallows the line without a problem being detectable; `/` after
  `)`, `]`, `}` is always a division (`if (x) /re/.test(y)` is lexed wrongly), and `/`
  directly after `<` is never a regex (closing tags). `>>` and `>>>` are two and three `>`.
  Escapes in strings are decoded only for the specifiers and literals the adapter reads.
- Statements are split at `;` and at line breaks where the next token cannot continue the
  expression (a line starting with `(`, `[`, a template, `+`, `-` or an operator continues
  the statement, as in JavaScript). Only top-level statements become declarations; the
  contents of `namespace`, `declare module` and `declare global` are one declaration. A
  `{` after a token that expects a type (`:`, `|`, `=`, `<`, `extends` ...) is a type
  literal, not a body; a function whose return type is an unusual construct can be misread.
- `nested` names only named functions; anonymous callbacks are not named. Methods and
  properties are recognised inside a `{` after a separator or modifier, so a method written
  after an unusual token is missed (it then belongs to the enclosing declaration, which is
  the safe direction).
- Shadowing is not understood: a parameter or local with the same name as an import counts
  as a use of the import (more call sites, never fewer). Type positions count as uses.
- `import(x)` and `require(x)` with a non-literal argument are not resolved (`Parsed
  .unresolved_dynamic` lists their lines); a candidate file that has one makes the importer
  list incomplete and is recorded. `require.context`, `import.meta.glob` and `jest.mock("./x")`
  are not seen at all, and a file reached only that way (in a file that is not a candidate) has
  fewer importers than it has.
- Resolution knows no `node_modules`, workspace package names, `package.json` `main`,
  `exports` or `imports` (`#/` paths), `moduleSuffixes`, `rootDirs` or `typeRoots`; an import
  that goes through such a mapping and names the file is reported (unresolved specifier), not
  found. Files other than `.ts .tsx .js .jsx .mjs .cjs .mts .cts` (`.vue`, `.svelte`, `.mdx`)
  are not parsed as importers: a file that imports through them is missed, and a changed one
  is flagged `file_not_analysed`. A changed `.json` file is analysed as a whole.
- Guards: only comparisons of one parameter with a string literal. `a || b`, `&&`,
  `includes`, `typeof`, `opts.mode`, numeric or boolean literals and early returns
  (`if (mode !== "x") return;`) are not recognised, so the call is affected, never excluded.
- Named-export narrowing never narrows default imports, and a module-level change reaches
  every importer. It follows identifiers, not behaviour: state that one function writes and
  another reads is not followed.
- Speed on this machine: about 600 KB of TypeScript parses in 0.3 s; a 1,500 file sample of
  bundled JavaScript (30 MB) parsed without an error. Tests cut a file at every third
  offset and parse random token soup without a crash or a hang.

### Tests, speed

`tests/test_js_adapter.py` builds every case as commits on top of one empty root in a single
repository, on no branch (the remote advertises one ref, so a fetch does not negotiate over
every case), and the adapter reads files from a commit's checkout when it has one. A cache
shared by all tests was slower (it accumulates refs) and is not used. Measured on this machine,
Python 3.12: the adapter and scenario modules took 65.1 s before and 54.6 s after (adapter 57.5
s to about 47 s, scenarios 7.6 s); the 919 test suite took about 82 s before the review fixes.

### Not done

- No barrel walking: a re-exporting file is a caller (first hop); who imports the barrel is
  the walk's job (M7 and later). No hop, module cap or per-hop limit is applied here except
  the hub rule above.
- Screens, routes and http edges (the `screen` and `http_edge` types of s01 to s05, s20) are
  not computed: they belong to the entry-point and edge adapters.
- No config key for `examine_cap`; no integration with `cli.py`, `Analysis` or findings (the
  adapter returns data and writes records; turning them into findings is the core's job).

### Changes made with the Python adapter (M6)

The shell the two adapters shared as copies is now `reachmap/adapters/base.py` (see the M6 notes, "The shared
shell"); `JsAdapter` keeps parsing, candidates, alias resolution, narrowing, call sites and guards. The M5 suite
passes unchanged. Behaviour that changed, each with a test in `tests/test_js_adapter.py`:

- Narrowing reaches a changed class member by property access: when a class changes, every declaration of the file
  that reads one of its members (`doc.total`, `doc?.total`, `doc["total"]`; the names of the changed lines and the
  methods in `nested`) joins the set, and module-level code that does stops narrowing. A constant whose initializer
  calls a name in the set (`export const CONFIG = load()`) runs for every importer, so narrowing stops; class
  bodies, static blocks and decorators are not checked for this.
- A deleted or renamed file with no pre-change version has `importers_complete` False with the existing reason
  (it used to read as "no importers": no symbols, an early return).
- A changed code file whose name is not UTF-8 (or holds a backslash) is incomplete with a blocking `read_unreadable`.
- A code file that git calls binary is one module-level change with its importers searched and a non-blocking
  `file_not_analysed`.
- `definitions` is a lower bound (never distinctive, with a blocking `read_unreadable`) when git grep did not
  search a code file (a symlink) or a candidate does not lex cleanly.
- A specifier or path called `safe` in a record detail no longer raises (the quoted text is left out of the detail).

