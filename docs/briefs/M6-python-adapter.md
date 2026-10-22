# M6 brief: Python language adapter

Goal: the second language adapter, with the same contract as the JS adapter (M5): given
a change in a Python repository, which symbols changed (placed by diff line number, nested
scope kept), who imports the changed module (every import form, confirmed by resolving the
import, never by name alone), which callers survive narrowing, which files are tests, and
how many times a name is defined. Read `docs/CONTEXT.md` sections 3, 4, 5, 6, 7.3, 7.5,
`tests/fixtures/README.md`, and `docs/briefs/M5-js-adapter.md` including its
Implementation notes and review fixes: reuse its shared types (`reachmap/adapters/__init__.py`),
`reachmap/testpaths.py`, the scenario harness (`tests/scenario_support.py`) and its
conventions (FileImpact, ChangeAnalysis, records, completeness flags) rather than inventing
parallel ones. Where M5 learned a lesson (intra-file closure for narrowing, parse problems
make results incomplete, unresolvable imports are recorded, non-code files are not
"complete with no importers"), apply it here from the start.

Python 3.11+, standard library only. Parse with the standard `ast` module (and `tokenize`
for comments) for the version that runs Reachmap; a file that does not parse under that
version (newer syntax, Python 2, syntax errors) is a parse problem: recorded, the change
treated as module-level, the candidate makes importer lists incomplete. Never fall back to
regex placement.

## Files

```text
reachmap/adapters/python/__init__.py   PyAdapter
reachmap/adapters/python/modules.py    module map: path to dotted name and back (roots, packages, namespace packages)
reachmap/adapters/python/syntax.py     ast-based declarations, spans (decorators included), nested scopes, imports, references
tests/test_py_syntax.py, test_py_modules.py, test_py_adapter.py
tests/test_scenarios_py.py             the api, contracts and worker scenarios at the language level
```

## Rules

1. Changed symbols: map each added line (new numbering) and removed line (old numbering)
   to the innermost enclosing definition in the after/before `ast`. A definition's span
   starts at its first decorator line (decorators at column 0 belong to the function
   below, s09) and ends at `end_lineno`. Report the outermost top-level definition as
   `name`, the nesting path in `nested` (s08: nested `_load` inside `get_settlement` is
   `get_settlement` with nested `_load`; a sibling handler's own `_load` is unrelated).
   Class methods: `name` is the class, `nested` the method (or report `Class.method`;
   choose one consistent with the fixtures' expected `symbol` values and document it).
   Lines outside any definition: `<module>`. Placement by line number only (s22: identical
   `request: Request,` lines in several handlers).
2. Module map: a module's dotted name comes from configured source roots: the repo root,
   each service marker directory (`service_marker`, e.g. `pyproject.toml` directories in
   the api monorepo), `library_roots` from config, and `src/` layouts; packages with
   `__init__.py` and namespace packages. The contracts package maps `contracts/` to
   `acme_contracts` through its `pyproject.toml` `package-dir` (read `[tool.setuptools]
   package-dir` and `packages`; anything not understood is recorded, not guessed).
   Ambiguity (two files map to the same dotted name) is recorded and both are kept.
3. Imports: `import a.b`, `import a.b as x`, `from a.b import c`, `from a.b import (\n c,\n
   d,\n)` (multi-line), `from a.b import c as d`, `from . import x`, `from ..pkg import y`
   (relative, resolved against the importer's package), `from a import *`, imports inside
   functions and `if TYPE_CHECKING:` blocks (still importers; type-only marked),
   `importlib.import_module("literal")` (resolved), non-literal `import_module(expr)` and
   `__import__` recorded like the JS dynamic case. `from pkg import name` where `name` is
   a submodule imports that submodule. Evidence is the import statement's line that names
   the imported module or name (for multi-line imports, the line holding the name).
4. Importer search: `git grep` (fixed strings) for the module's last segment as a
   candidate filter (and the package name for `__init__.py`), then parse every candidate
   with `ast` and resolve each import to a module path; count only real resolutions. A
   candidate that fails to parse, an unreadable candidate, a grep cut, or an import that
   ends in the target's name but resolves nowhere makes the list incomplete with a
   record, exactly as in M5.
5. Narrowing (same as M5 after review): an importer that imports only names outside the
   changed-name closure (top-level definitions referencing a changed one, to a fixed
   point; class bodies included) is excluded with its import line and the changed span
   as evidence. `import module` / `import module as m` (attribute access) narrows by
   `m.<name>` attribute references in the importer: an importer using only `m.other` is
   excluded; any non-attribute use of `m` (passed around, `getattr`) is not narrowed.
   Star imports and module-level changes are not narrowed. No pre-change version: no
   narrowing.
6. Callers: the first hop outside the changed module: importers that survive narrowing,
   with the call sites of changed names (`name(`, `alias(`, `m.name(`) placed by `ast`
   positions; aliased names followed (s15: `from x import y as z`, `import a.b as
   settlement_svc`, multi-line import). Test files are never callers; they are unit tests
   (one hop: tests importing a changed module or a caller), each with import evidence.
7. Test paths via `reachmap/testpaths.py` with the repo's markers (s23:
   `services/billing/app/backtests/replay.py` and `latest_rates.py` are not tests;
   `services/billing/tests/test_money.py` is). Files under a test directory that are
   helpers (`conftest.py`) are tests too.
8. Definitions: `definitions(name, sha)` counts `def`, `async def`, `class`, module-level
   and class-level assignments and annotated assignments (`interval: int`), across non-test
   code; references, imports and attribute reads do not count. Grep candidate filter plus
   `ast` confirmation. A grep cut or unreadable file: a partial count above the limit is
   not distinctive, otherwise the name is unreadable, never distinctive. This is what the
   contract edge (M8) uses for field names (s11 `settlement_reference` at most 2; s12
   `interval` 5).
9. Guards are out of scope for Python in M6 (no Python scenario needs them): Python
   call sites are never excluded by a guard; say so in the notes.

## Scenario harness

Extend `tests/scenario_support.py` to run the Python adapter for scenarios in `acme/api`,
`acme/contracts`, `acme/worker`, checking `changed_symbol`, `changed_file`, `caller`,
`unit_test`, and the language-level `not_established` entries, with an explicit skip table
for route, screen, http_edge, consumer, contract_read, finding, branch_state (later
milestones). Scenarios that must pass at this level: s06, s07, s08, s09, s10 (changed
symbols, removal), s13 (changed symbols), s15, s22, s23, and the changed-symbol parts of
s11, s12, s17, s18 (contract models: `symbol` is dotted for a model field, e.g.
`Invoice.settlement_reference`; place class-body field lines as the class with the field
in `nested`, or as the dotted name: follow the fixtures). Cross-check importer sets with
`tests/fixtures/claims.py`'s Python import oracle.

## Tests beyond the scenarios

- Decorator lines, multi-line decorators, nested functions with the same name in sibling
  functions, methods, async defs, lambdas assigned at module level, class-body fields.
- Every import form in rule 3 with resolution; relative imports beyond the top level
  (recorded, not crash).
- Parse problem in the changed file and in a candidate (Python 2 `print "x"`): recorded,
  module-level, list incomplete.
- Every Evidence resolves at its pinned sha (reuse the M5 helper).

## Implementation notes

Where the code differs from, fills in, or goes beyond the text above. Each point is covered by a test
unless it says otherwise.

Files added or changed beyond the list: `tests/support_universe.py` (the M5 "one repository, one case per
test" builder, shared by the Python adapter tests), `tests/scenario_support.py` (see "The harness"),
`reachmap/adapters/base.py` (the shell the two adapters share, see "The shared shell"),
`reachmap/adapters/__init__.py` (the three record kinds both adapters make, `parse_degraded`,
`specifier_unresolved` and `file_not_analysed`, are registered there with wording that names no language;
`Binding` gains `line`, the line of a name in a multi-line import, 0 when it is the statement's line). The
adapter has the same public entries as `JsAdapter`: `analyze(change_set)` returns a `ChangeAnalysis` with one
`FileImpact` per changed file, and `changed_symbols`, `importers`, `callers`, `guards`, `definitions` and
`is_test_path` are the pieces; one more, `names_for(path, sha)`, lists the dotted names a file has.
`PyAdapter.from_config(repo, git, limits=...)` takes `service_marker`, `library_roots`, `tests.markers`,
`hub_importers` and `distinct_max`. One adapter per run, as in M5 (answers are kept for its life).

### Rules, as built

1. Symbols. `name` is the top-level declaration, or `Class.member` for a line inside a class body
   (`Invoice.settlement_reference`, `Invoice.total`, `Outer.Inner.x`); the fixtures dot fields, so methods are
   dotted the same way. `nested` lists the named functions and classes inside the member that hold the line,
   outermost first (s08), and is empty for a field. `start` and `end` are the span of the top-level declaration
   (decorators included), `kind` is the kind of the member (`function`, `class`, `variable`). `change` is
   judged on the top-level declaration: a new field or method in an existing class is `modified` (s17
   expects `Invoice.due_date` as modified), a declaration that is new or gone is one `added` or `removed`
   symbol for the whole declaration, not one per line. `export_names` are all the names the top-level
   declaration binds (`a, b = 1, 2` has both), from the before and the after text. Module-level assignments
   to plain names (annotated, augmented, tuple targets, `lambda` values) are declarations; so are defs,
   classes and assignments inside module-level `if`, `try`, `except*`, `with`, `for`, `while` and `match`
   blocks. Every other module-level statement, and the header lines of those blocks, is module code (`<module>`).
   A module docstring, a comment and a blank line outside a declaration change nothing; inside a declaration
   they belong to it. A file that does not parse under the running Python (Python 2, newer syntax, a syntax
   error, a null byte) or holds a carriage return that is not part of CRLF (git and `ast` would count
   lines differently) is a parse problem: every changed line is module level and a non-blocking
   `parse_degraded` is recorded (a candidate importer file with one makes the list incomplete with a blocking
   record). A text with a UTF-8 BOM is parsed without it (and cited as git has it). Two declarations on one line (`x = 1; y = 2`)
   are both placed. A symbol whose only changed lines are blank cites the first line of its declaration, so it
   always has evidence. Without a pre-change version only added lines are
   placed. A pure rename is a module-level `removed` symbol for the old path; a deleted file reports its
   declarations as removed and is searched at the pre-change commit.
2. Module map (`modules.py`). A file has a dotted name under every mount that holds it: the repository root,
   each folder with the service marker, each `library_roots` folder, `src/` under those (unless it is a
   package), and what a `pyproject.toml` there declares (setuptools `package-dir` and `packages.find.where`,
   poetry `packages` `from`, hatch wheel `packages`, pdm `package-dir`). `names_of` lists them from the most
   specific mount; a file under a mapped folder has the mapped name and the plain one
   (`acme_contracts.models.invoice` and `contracts.models.invoice`), so nothing is missed. An importer sees
   the mounts of its nearest service (or library) folder, the library roots and the repository root; a file
   under no service sees every mount, and then two services' `app.main` is ambiguity: both files are kept as
   importers and a non-blocking `module_ambiguous` is recorded when the target is one of them. Folders without
   `__init__.py` are namespace packages; names whose parts are not identifiers, and files under `.venv`,
   `site-packages`, `node_modules`, `__pycache__` and similar, have no dotted name. Not understood is recorded
   (non-blocking `module_map_gap`): a malformed or out-of-repository `package-dir`, a folder with no files,
   hatch `sources`, `setup.py` or `setup.cfg` that mention `package_dir`, a library root that is not a folder.
   A `pyproject.toml` that does not parse is a blocking `read_unreadable` and the map is incomplete, so every
   importer list is too.
3. Imports. One `ImportEdge` per statement per file it reaches. `names` is the tuple of names of a
   `from` import and None for a whole module (`import a.b`, `import a.b as x`, a submodule, star, dynamic).
   `from pkg import name` reaches `pkg/__init__.py` with `names=("name",)` and, when `name` is a submodule,
   that submodule as a whole-module edge bound to `name`. Binding conventions: `from m import n as l` is
   `Binding("n", "l")`; `import a.b as x` and a submodule are `Binding("*", local)`; `import a.b.c` without an
   alias is `Binding("a.b.c", "a")` (used through `a.b.c.name`). Kinds: `import`, `export_star`, `dynamic`;
   `type_only` for `if TYPE_CHECKING:` and `typing.TYPE_CHECKING` bodies (an `else` of a `not TYPE_CHECKING` too).
   The Evidence line is the line of the statement that names the module; the line of each name of a
   multi-line import is `Binding.line` and is cited too (s15 cites `from acme_common.money import (` and
   `    format_cents,`). `importlib.import_module("x")`, an alias of it and `__import__("x")` with a literal are
   dynamic edges (a relative literal needs a literal `package`); with an expression they make the list
   incomplete (blocking `specifier_unresolved`). Importing a submodule also runs its parent packages, which
   this adapter does not list as importers of those `__init__.py` files (an import names the module, not its
   parents); a changed `__init__.py` therefore records a non-blocking `package_init_implicit`.
4. Importer search. Candidates: `git grep` (fixed string) for the last segment of every dotted name of the
   file (the package name for an `__init__.py`), plus every Python file under the folder of an `__init__.py`
   (`from . import x` names no package); a file with no name to search for makes every Python file a
   candidate. The list is also incomplete, with a record, for a symlinked Python file git grep did not search
   (blocking `read_unreadable`), a file name that cannot be cited, and an import that resolves to nothing but
   spells the module's name (the last segment, any segment for a package, or a name imported from it, which
   may be the submodule): blocking `specifier_unresolved`, one per prefix (`vendor.pkg` of
   `vendor.pkg.totals`). Standard library modules (`sys.stdlib_module_names`) are never such a miss unless
   something in the repository could shadow them: a module or package of that name next to the importer (a
   script run from its folder imports the sibling first; `tools/logging.py` and `tools/run.py` with `import
   logging`), which resolves or is recorded; third party packages are, only when they spell the name. A relative import
   that goes above the repository root is recorded once per importer file as a non-blocking
   `specifier_unresolved` (Python refuses it, it reaches nothing); relative imports are resolved by path and
   are generous: `..` from `pkg/mod.py` reaches the repository root (Python refuses it, the top level package
   is `pkg`), and `...` is above the repository (nothing, recorded as said).
5. Narrowing (`PyAdapter._narrowing`, one implementation for `analyze` and for `callers(symbol, sha,
   change=...)`). It needs a pre-change version, a file that is neither deleted nor renamed, no module-level
   symbol, both texts parsing, and no `eval`, `exec`, `globals()`, `locals()`, `vars()`, `sys.modules[...]`
   (also `from sys import modules`) or module `__getattr__`. The names an importer is compared with are: the names of the changed declarations; the
   names in the decorators of a changed or reached declaration that are top-level declarations of the file
   (`@router.get("/x")` registers the handler on `router`, so whoever imports `router`, usually the module that
   mounts it, is affected; this is a change from the JS rule, needed because Python registers by decorator);
   the module names a changed or reached declaration writes (a `global` statement); and every top-level
   declaration that spells one of them (class bodies, defaults, annotations, decorators included, and strings
   that read as a name or a type such as `"Customer"`, `"list[Customer]"`, ORM relationship targets and `__all__`
   entries), repeated to a fixed point. A changed class member (`Invoice.total`, a field, a method, a nested
   class member) is also reached by name from any object: every top-level declaration that spells `.total` (an
   attribute, a keyword argument `Invoice(total=1)`, a string `getattr(doc, "total")`) joins the set, and
   module-level code that does stops narrowing (p9 of the review: a helper `summarize(doc)` calling
   `doc.total()` made its importers affected). Module-level code (outside every declaration and import) that
   spells a reached name stops narrowing; a module-level assignment such as `REGISTRY = [a]` is a declaration and
   brings in `REGISTRY` instead. The Exclusion cites every import line of the importer, the start line of each
   changed declaration and the start line of each other declaration in the closure. Module imports narrow by
   attribute: `m.other` only is excluded, any non-attribute use of `m` (passed on, `getattr`, `m.__dict__`) keeps the importer,
   `import a.b.c` is narrowed by the chain `a.b.c.name` and a use of `a` that goes elsewhere is not a use of it,
   and a module import with no use at all is kept (importing runs the module). A quoted annotation that reads
   as a dotted name (`x: "m.Invoice"`) is a use of `m` like the unquoted one. Star and dynamic imports are not
   narrowed. Tests are not narrowed. Import time execution: a declaration that runs when the module is imported
   (an assignment value, a decorator applied with or without parentheses, a default value, a base or keyword of a
   class, the statements of a class body, not the body of a function or a lambda) and calls a name whose behaviour
   changed (the changed names and the declarations that reach them, not the receivers a handler is registered
   on) runs for every importer: narrowing stops (tests: `CONFIG = load()`, a decorator, a default value, a class
   body, a chain through a helper). A name that is only held (`REGISTRY = [a]`) is not a call. Shadowing is not understood (a local with an imported name is a use).
6. Callers. A call site is a use of an imported binding: `name(...)`, `alias(...)`, `m.name(...)`, a
   decorator application, or a reference passed on (`run(f)`, `[f]`, a type position), with `form` `call`,
   `reference` or `import` (an importer with no use is a caller at its import line). `CallSite.symbol` is the
   changed name as imported (`format_cents` for `fmt_cents(...)`), `*` for the module itself passed on;
   `caller_symbol` is the top-level declaration the use sits in (the class for a method, `<module>` at top
   level); `args` is the source text of each argument, including `*rest`, `key=value` and `**opts`.
   `Caller.reason` is always None. A star importer's sites are the uses of the names the change reaches (or,
   without narrowing, of every name the target declares). Unit tests are the test files that import the changed
   file, and the test files that import a surviving caller (one hop); each cites its import lines.
7. Test paths: `reachmap/testpaths.py`. With no `tests.markers` the adapter uses `tests/`, `test/`,
   `test_`, `_test` and `conftest.py`.
8. Definitions: grep proposes non-test `.py` files, `ast` confirms: def, async def and class at any depth,
   assignments and annotated assignments at module or class level (also inside `if`, `try`, `with`, `for`,
   `while` and `match` blocks there; a local variable of a function does not count). `FileImpact` checks the
   leaf of each changed name (`settlement_reference` of `Invoice.settlement_reference`, not the dotted name)
   and records `not_distinctive` as M5 does; dunder names are skipped (they are never a name to link code by,
   and `__init__` would search every class of the repository). A file that does not parse, cannot be read or
   cannot be cited makes the count incomplete (never distinctive), and so does a code file git grep did not
   search (a symlinked `.py`: a blocking `read_unreadable` is recorded, the count is a lower bound).
9. Guards: out of scope for Python. `guards()` always answers `affected`, `Symbol.guards` is empty and
   `Caller.reason` is None, so no Python call site is ever excluded by a guard (test: the guarded function
   of the M5 shape keeps its call).
10. Records made here: `module_map_gap`, `module_ambiguous` and `package_init_implicit` (new kinds, all
    non-blocking) beside the kinds the git layer and M5 use. A record's `detail` is checked for banned wording as
    written, and a repository can have a folder or module called `safe`: `record_quoted` leaves the quoted
    code out of the detail when it trips the check, instead of raising (tests: a module and a package-dir folder
    called `safe`). `JsAdapter` quotes specifiers in its details without that protection (not changed here).
    A `.py` file that git calls binary (a null byte) is one module-level change: nothing can be placed, its
    importers are searched (they need no text of it) and a non-blocking `file_not_analysed` says it was not
    read. The non-Python files that other machinery loads (`.pyi`,
    `.pyx`, `.pxd`, `.pyw`, `.so`, `.pyd`) make
    `importers_complete` False with a non-blocking `file_not_analysed`; documents, data, `pyproject.toml`,
    `requirements.txt` and `setup.cfg` are not flagged (a changed `pyproject.toml` can change the module map,
    which is not followed: a stated limit below). A changed code file whose name is not UTF-8 or holds a
    backslash cannot be cited: `importers_complete` is False with a blocking `read_unreadable`, not a quiet
    "nothing changed" (this applies to both adapters). A deleted or renamed file with no pre-change version has
    no importers to find: its `importers_complete` is False with the reason "there is no pre-change version to
    find the importers of the old path in" (both adapters; before this the early return for "no symbols" hid it).
11. Barrels. A package `__init__.py` that re-exports (`from .money import format_cents`) is a caller of the
    module (form `import`, or the use of the name inside it); who imports the package is the walk's job (M7 and
    later), as in M5: no hop past the first is taken here, and no `__all__` is read.

### The shared shell

`reachmap/adapters/base.py` (`AdapterBase`) holds what the two adapters had as copies, about 300 lines each:
reading at a pinned commit and caching it, the checkout context, changed symbols from a diff (placing is the
language's, reading and the module-level fallbacks are shared), the importer search loop (candidates through
`_grep_for` and `_pool`, symlinks and uncitable names, parse problems, expressions in dynamic imports, the cap),
definitions and distinctiveness, callers, unit tests one hop out, the hub rule, `analyze`, and the record helpers
`record_quoted`, `printable`, `evidence_at`. A language fills in hooks: `_parse_text`, `_is_code_path`,
`_prepare_context`, `_gather`, `_scan_candidate`, `_definition_lines`, `_narrowing`, `_survives`, `_call_sites`
and optionally `_special_symbols` (JS `.json`), `_enrich_symbols` (JS guards, the Python `__init__` record),
`_verdicts` (JS guards), `_edge_lines`, `_context_gaps`, `_distinct_name`. `record_quoted` is used by both
adapters: a module, folder or specifier called `safe` no longer crashes either. Fixes made once for both:
the deleted or renamed file with no pre-change version, a non-UTF-8 file name, a code file git calls binary,
definitions that git grep could not search (a symlink), definitions in a file that does not parse. The shell
is covered by the two suites; JS behaves as before except for those points (M5 notes carry the same list).

### The harness

- `ss.check(run, not_established=kinds)` compares the adapter's records, as a complete list, with the
  `expected.json` entries of the implemented kinds (`PY_NOT_ESTABLISHED`: `hub_cutoff`, `not_distinctive`,
  `pre_change_unavailable`); without the argument it compares every entry, as M5 does. An entry that names
  another repository than the scenario's is the answer of that repository's adapter and is not checked here
  (the unit test of the api in the contracts scenario s11, which is `complete_for` `unit_test`: the contracts
  adapter must report none). A `must_exclude` caller that is a test, with the import line as evidence, is
  checked against the unit tests of the change: a test is never a caller but is stated with that evidence, and the
  unit test must be of the file its import line resolves to (`claims.import_target`), not of any changed file.
- `ss.skipped_types(expected, implemented, kinds, repo=...)` lists what is not checked as `section:type`, and
  `section:type@repo` for an entry of another repository. `tests/test_scenarios_py.py` pins the list per
  scenario and checks that implemented and skipped types are every type in the scenarios.
- The scenarios checked: s06, s07, s08, s09, s10, s13, s15, s22, s23 (api) and s11, s12, s17, s18 (contracts), and
  also s21 (contracts, the s11 change with `limits.hops = 1`) for its changed symbol, which the brief does not
  list. No scenario has `acme/worker` as its repository. Skipped, per scenario: routes, http edges and screens
  (s06, s07, s08, s10, s11, s21, s22), findings (s10, s22), branch states (s13), contract reads and consumers
  (s11, s12, s17, s18, s21), the `router_unmounted` record (s07) and `hop_cap` (s21).
- The importer set the adapter finds for each changed source file is compared with the independent parser
  of `tests/fixtures/claims.py` (`direct_importers`); they agree for every file checked.

### Known limits

- Resolution knows source roots from the service marker, library roots, `src/`, `package-dir`,
  `packages.find`, poetry, hatch and pdm. It knows nothing of `sys.path` edits, `.pth` files, editable installs
  of packages outside the repository, a `conftest.py` rootdir, `setup.py` layouts, namespace packages that span
  roots, `__all__`, `importlib.util.spec_from_file_location`, `runpy`, `pkgutil`, entry points, plugin
  registries by string (Django `INSTALLED_APPS`, Celery `autodiscover_tasks(["worker.tasks"])`) or
  `sys.modules` tricks; a file reached only that way has fewer importers than it has. Imports of packages
  that live in another repository (`acme_contracts` in the api repository) resolve to nothing there and are
  only recorded when they spell the name of the file under analysis.
- Narrowing and import time execution: a reached name used where a declaration runs at import stops narrowing
  only when it is called (see rule 5); a name that is only read there (`x = helper` is not a call, a class
  that inherits from a reached class or a metaclass that is reached, a descriptor, `__init_subclass__`) is
  treated as reaching that declaration alone. JS stops narrowing for a constant whose initializer calls a
  reached name, not for a class body, a static block or a decorator.
- Strings that read as a name are followed inside a declaration (see rule 5), but a name built at run time
  (`getattr(m, "na" + "me")`) is not, and a string that is not name shaped (`"a.b c"`) is not. Registration is
  followed by decorators and `global` writes only; state that one function mutates (`REGISTRY["k"] = f` inside
  a function, an attribute set on a shared object) and another reads is not followed, as in M5; `REGISTRY["k"] = f`
  at module level is module code, which stops narrowing when it spells a reached name.
- Shadowing is not understood: a parameter or local with an imported name counts as a use of the import
  (more call sites, never fewer). Type positions count as uses. A quoted bare name (`x: "Invoice"`) is a
  reference for narrowing but not a listed call site.
- A relative import above the top level package is read by path (up to the repository root); Python would
  refuse it, so such an import can over-report an importer.
- Under Python 3.11 the positions of names inside a multi-line f-string can be off by lines (a `CallSite` line
  then cites a nearby line of the same statement); 3.12 and later place them exactly.
- Importing a submodule runs its parent packages' `__init__.py`; those are not importers by an edge (a
  `package_init_implicit` record says so when an `__init__.py` changes). Importing `a.b.c` does not make
  `a/__init__.py` an importer either.
- A changed `pyproject.toml` or `setup.cfg` is not analysed: it may change the module map, and the importers of
  everything under it, which is not followed.
- A source that is not UTF-8 (a latin-1 coding cookie with non-ASCII bytes) is unreadable and blocks, as in M5;
  a file name with a backslash cannot be cited and is recorded.
- Parsed files are kept for the life of the adapter, including every candidate (an AST of each grep hit); a
  very large repository with a very common name (`main`, `utils`) holds many.
- Speed: every file of the Python 3.12 standard library (686 files, 11 MB) parses, places lines, indexes imports
  and uses and counts definitions in under 3 seconds on this machine. Tests cut a file at every third offset and
  parse random token soup without a crash or a hang.

### Tests, speed

The symbol placement tests call `symbols_for_change` directly with the diff line numbers (`PlacementOfChanges`,
no repository); the same rules through git, with records and evidence checked against the commits, stay in
`ChangedSymbols`. A timing test builds the module map of 300 services with 100 modules each (30,000 files) in
about 0.15 s (the map was built by testing every file against every mount, 1.5 s, and several seconds at 52,000
files and 200 services).

`tests/test_py_adapter.py` builds every case as commits on top of one empty root in a single repository, as
the M5 tests do (`tests/support_universe.py`); the scenario module shares the fixture repositories built once
per process. The syntax and module tests build no repository at all.

Measured on this machine with the full suite (1,242 tests; 985 before M6, so 257 are new: syntax 60, modules 42,
adapter 138, scenarios 17): Python 3.11 148.7 s, 3.12 146.4 s, 3.13 147.7 s, all passing. Alone, on 3.12: the
adapter module 31.9 s, the scenario module 7.6 s, the syntax and module tests about 0.01 s each. The suite took
about 109 s before M6.
