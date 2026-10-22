"""The shell both language adapters share (M5 JavaScript, M6 Python).

What differs between languages is small: how a file is parsed, which files are code, how the module that
a specifier names is found, which candidates may import a file, how a change reaches an importer (narrowing),
which uses of an import are call sites, which statements define a name, and whether a guard can exclude a call.
Those are the hooks a subclass fills in. Everything else lives here once: reading at a pinned commit and caching
what was read, changed symbols from a diff, the importer search (candidates, the cap, unreadable and degraded
files, what the search could not cover), definitions and distinctiveness, callers, unit tests (one hop), the hub
rule, and the record helpers.

The rules are the context rules: every read answers found, absent or unreadable, an unreadable read is an
incompleteness record and never an absence, a list that could not be completed says so (`complete=False`), and
every answer cites Evidence pinned to a commit.
"""

from __future__ import annotations

import contextlib
import posixpath
from dataclasses import dataclass
from typing import Any, Iterable, Iterator, Sequence

from reachmap import walk as _walk
from reachmap.adapters import (
    CallSite, Caller, ChangeAnalysis, ChangedFile, DefinitionCount, Exclusion, FileImpact, GuardVerdict, ImportEdge,
    ImporterResult, MODULE, Reach, Symbol, UnitTest,
)
from reachmap.errors import WordingError
from reachmap.gitlayer import Checkout, Git, ReadFailed
from reachmap.incomplete import record, records_of
from reachmap.model import Area, Evidence, MUST_TEST, Read, ReadResult
from reachmap.testpaths import is_test_path, validate_markers

__all__ = [
    "AdapterBase", "Context", "Narrowing", "NO_GUARD", "Search", "api_area", "citable", "dedupe", "direct_hop", "distinct_chains", "ev_list", "evidence_at", "import_lines", "lines_to_names",
    "module_changes", "not_distinctive_detail", "printable", "record_quoted", "screen_area",
]

NO_GUARD = GuardVerdict("affected", None, ())


def citable(path: str) -> bool:
    """Whether Evidence can name the path (no backslash, valid UTF-8)."""
    if "\\" in path:
        return False
    try:
        path.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def printable(text: str) -> str:
    """Text that can go into a record: a character that is not valid UTF-8 (a lone surrogate from a
    non-UTF-8 name or a string literal) is shown as an escape."""
    return text.encode("utf-8", "backslashreplace").decode("utf-8")


def not_distinctive_detail(found: DefinitionCount, sha: str, limit: int) -> str:
    """The detail of a `not_distinctive` record: the same words wherever the record is made, so equal records are one."""
    return (f"defined {'at least ' if not found.complete else ''}{found.count} times in non-test code at {sha[:12]}; "
            f"the limit is {limit}, so the name is not used to link code")


def record_quoted(target: Any, kind: str, subject: str, fixed: str, quoted: str = "", *, blocks_selection: bool | None = None, once: bool = False) -> Any:
    """`record` with a detail made of our own words (`fixed`) and text quoted from the code (`quoted`: a
    module name, a path, a parser message). The detail is checked for banned wording as written
    (context rule 4.2), and a repository may have a folder or a module that is called `safe`: when the quoted
    text trips the check it is left out of the detail, never allowed to crash the analysis."""
    subject, fixed, quoted = printable(subject), printable(fixed), printable(quoted)
    if once:  # one record for a (kind, subject, blocking): whoever finds the problem second adds nothing
        from reachmap.incomplete import kind_spec

        blocking = kind_spec(kind).blocks_selection if blocks_selection is None else blocks_selection
        for existing in records_of(target):
            if (existing.kind, existing.subject, existing.blocks_selection) == (kind, subject, blocking):
                return existing
    detail = f"{fixed} ({quoted})" if quoted else fixed
    try:
        return record(target, kind, subject, detail=detail, blocks_selection=blocks_selection)
    except WordingError:
        return record(target, kind, subject, detail=fixed, blocks_selection=blocks_selection)


def lines_to_names(items: Iterable[tuple[Iterable[int], Iterable[str]]]) -> dict[int, set[str]]:
    """For each line of an import statement, the local names the statement binds (`items`: the lines of one statement
    and its names), so a changed line can be tied to what it changes."""
    out: dict[int, set[str]] = {}
    for lines, names in items:
        bound = set(names)
        for n in lines:
            out.setdefault(n, set()).update(bound)
    return out


def evidence_at(repo: str, path: str, sha: str, parsed: Any, line: int) -> Evidence | None:
    detail = parsed.line_text(line)
    if not detail.strip():
        return None  # a blank line is no evidence
    return Evidence(repo=repo, path=path, line=line, ref=sha, detail=detail)


def import_claim(target: str, names: Iterable[str] | None) -> str:
    """The claim of an import line (`Evidence.claim`): the file the specifier resolved to and the names the import takes from it (`*` for
    the whole module), not how the specifier is spelled, so an alias and a relative path to the same file say the same thing."""
    return f"import:{target}:{'*' if names is None else ','.join(sorted(names))}"


def ev_list(repo: str, path: str, sha: str, parsed: Any, lines: Sequence[int]) -> list[Evidence]:
    return [e for n in sorted(set(lines)) if (e := evidence_at(repo, path, sha, parsed, n)) is not None]


def distinct_chains(chains: Iterable[Sequence[Evidence]]) -> tuple[tuple[Evidence, ...], ...]:
    """The chains of an area without a repeat, in the order they were found (the same chain twice is listed once)."""
    out: list[tuple[Evidence, ...]] = []
    for chain in chains:
        if tuple(chain) not in out:
            out.append(tuple(chain))
    return tuple(out)


def screen_area(repo: str, routes: Sequence[Any], chains: Iterable[Sequence[Evidence]]) -> Area:
    """The `Area` of a screen made of `routes` (the primary route first: its path is the label and its line the route line; the paths of
    the others are `also_mounted`, sorted), reached through `chains`. The priority is a placeholder: the model assigns it over all areas."""
    primary = routes[0]
    return Area(
        label=primary.path, kind="screen", repo=repo, module=primary.module, route_line=primary.line, chains=distinct_chains(chains),
        also_mounted=tuple(sorted({r.path for r in routes} - {primary.path})), priority=MUST_TEST, by_name=False,
    )


def api_area(repo: str, route: Any, chains: Iterable[Sequence[Evidence]]) -> Area:
    """The `Area` of an API route (label `METHOD path`, the route's first evidence line its route line) reached through `chains`."""
    return Area(
        label=route.label, kind="api_route", repo=repo, module=route.module, route_line=route.evidence[0], chains=distinct_chains(chains),
        also_mounted=(), priority=MUST_TEST, by_name=False,
    )


def module_changes(changed: Iterable[Any], after: Any, before: Any, import_spans: Any) -> tuple[set[str], bool]:
    """What the changed lines outside every declaration of a file touch: (the names the changed import statements bind, whether some
    changed line is not an import statement, which could change anything the file does when it loads). `changed` are the changed
    symbols, `after` and `before` the parsed file at the head and at the pre-change commit (None when it could not be read: then
    every changed line counts as the second kind), `import_spans(parsed)` maps each line of an import statement to the names it binds."""
    imported: set[str] = set()
    other = False
    for s in changed:
        if not s.is_module:
            continue
        for parsed, lines in ((after, s.added_lines), (before, s.removed_lines)):
            if not lines:
                continue
            if parsed is None:
                other = True
                continue
            spans = import_spans(parsed)
            for n in lines:
                if n in spans:
                    imported |= spans[n]
                elif n in parsed.code_lines:
                    other = True
    return imported, other


def dedupe(items: Sequence[Evidence]) -> list[Evidence]:
    out: list[Evidence] = []
    for e in items:
        if e not in out:
            out.append(e)
    return out


def import_lines(parsed: Any) -> dict[int, set[str]]:
    """For each line of a Python import statement (the statement's line and the line of each name): the local names that statement binds.
    A star import binds names that cannot be listed, so it is left out (its line is module code)."""
    return lines_to_names(
        ([rec.line] + [x.line for x in rec.names], {(n.asname or n.name.split(".")[0]) for n in rec.names})
        for rec in getattr(parsed, "imports", ()) if rec.kind in ("import", "from") and rec.names
    )


def direct_hop(chain: Any, inside: tuple[int, int] | None, route_lines: Iterable[tuple[str, int]] = ()) -> tuple[Evidence, ...]:
    """For a change inside the entry file itself: the changed line within the handler's lines (`inside`, first and last line, in the entry
    file) when there is one, else the first changed line that belongs to the route (`route_lines`: (path, line) pairs), else the first
    changed line."""
    lines = [e for s in chain.changed for e in s.evidence if e.ref == chain.sha and e.path == chain.entry]
    within = [e for e in lines if inside is not None and inside[0] <= e.line <= inside[1]]
    on_route = [e for e in lines if (e.path, e.line) in set(route_lines)]
    return tuple((within or on_route or lines or list(chain.hops))[:1])


@dataclass(frozen=True, slots=True)
class _Outcome:
    """What one fixed-string search found, as the importer search needs it: the files that match, and how far the
    search got (a failed or cut search is not an empty one). One combined `git grep -e a -e b` is split into one
    outcome per pattern; the cut or failure of the single search belongs to each of them."""

    state: str
    detail: str | None
    truncated_by: str | None
    examined: int | None
    total: int
    not_searched: tuple[tuple[str, str], ...]
    paths: frozenset[str]


def _outcome_of(res: Any, pattern: str | None = None) -> _Outcome:
    paths: frozenset[str] = frozenset()
    if res.state != Read.UNREADABLE:
        paths = frozenset(m.path for m in res.partial_matches if pattern is None or pattern in m.text)
    return _Outcome(res.state, res.detail, res.truncated_by, res.examined, res.total, res.not_searched, paths)


class Context:
    """The tree at one commit: its files, and whatever the language adds (`_prepare_context`)."""

    def __init__(self, checkout: Checkout, files: frozenset[str]) -> None:
        self.checkout = checkout
        self.files = files


class Search:
    """The state of one importer search: what was found and what could not be covered."""

    def __init__(self, path: str, sha: str, ctx: Context) -> None:
        self.path = path
        self.sha = sha
        self.ctx = ctx
        self.complete = True
        self.reasons: list[str] = []
        self.candidates: set[str] = set()
        self.skipped: list[tuple[str, str]] = []
        self.edges: list[ImportEdge] = []

    def incomplete(self, reason: str) -> None:
        self.complete = False
        self.reasons.append(reason)


class Narrowing:
    """What narrowing compares imported names against: the names of every declaration the change reaches
    inside its file, and the start lines of the declarations it reached beyond the changed ones (cited with
    an exclusion)."""

    def __init__(self, names: set[str], reach: list[Evidence]) -> None:
        self.names = names
        self.reach = reach


class AdapterBase:
    """A language adapter for one repository. It is bound to the git layer and to the target the records it
    makes go to (a list or an Analysis; by default the git layer's); every method that reads takes the pinned
    sha. A method's `record_to` overrides the target for that call, for the git layer's records too. A
    commit never changes, so file reads, parsed files, the tree, importer answers and definition counts are
    kept for the life of the adapter: use one adapter per run."""

    languages: tuple[str, ...] = ()
    DEFAULT_MARKERS: tuple[str, ...] = ()
    NOT_ANALYSED: frozenset[str] = frozenset()  # extensions that are loaded by other means and not read
    PROBLEMS_STOP_READING = False  # a candidate that does not parse is not read for imports at all
    DYNAMIC_CALLS = "a dynamic import"  # how the language spells an import with an expression
    LANGUAGE = "the adapter"
    subject_prefix = ""  # `<repo>:` for a repository that is not the changed one: the subjects of its hub and module cap records

    def __init__(
        self,
        repo: str,
        remote: str,
        git: Git,
        *,
        test_markers: Sequence[str] | None = None,
        record_to: Any = None,
        examine_cap: int | None = None,
        hub_importers: int | None = None,
        distinct_max: int | None = None,
    ) -> None:
        self.repo = repo
        self.remote = remote
        self.git = git
        self.markers = validate_markers(test_markers if test_markers is not None else self.DEFAULT_MARKERS)
        self._target = record_to
        self.examine_cap = examine_cap
        self.hub_importers = hub_importers
        self.distinct_max = distinct_max
        self._contexts: dict[str, Context | None] = {}
        self._reads: dict[tuple[str, str], ReadResult] = {}
        self._parsed: dict[tuple[str, str], Any] = {}
        self._importer_cache: dict[tuple[str, str], ImporterResult] = {}
        self._definition_cache: dict[tuple[str, str], DefinitionCount] = {}
        self._reach_cache: dict[tuple[Any, ...], Reach] = {}
        self._grep_memo: dict[tuple[str, str], _Outcome] = {}

    # ---- hooks

    def _parse_text(self, text: str) -> Any:
        raise NotImplementedError

    def _is_code_path(self, path: str) -> bool:
        """Whether the path is a file of this language that other files import."""
        raise NotImplementedError

    def _is_relevant_path(self, path: str) -> bool:
        """Whether a change to the path is the adapter's business (code, and what it also analyses)."""
        return self._is_code_path(path)

    def _prepare_context(self, ctx: Context, sha: str) -> None:
        """Add what the language needs of the tree at `sha` (aliases, the module map) to `ctx`."""

    def _context_gaps(self, ctx: Context) -> list[str]:
        """Reasons the language's view of the tree is incomplete (every importer list is then)."""
        return []

    def _special_symbols(self, change: Any, fc: Any) -> list[Symbol] | None:
        """A language specific whole-file answer (JS: a changed `.json` file); None when not applicable."""
        return None

    def _build_symbols(self, fc: Any, before: str | None, after: str | None, added: Sequence[int], removed: Sequence[int],
                       pre: str | None, head: str, before_path: str, pb: Any, pa: Any) -> list[Symbol]:
        raise NotImplementedError

    def _enrich_symbols(self, fc: Any, symbols: tuple[Symbol, ...], pre: str | None, head: str) -> tuple[Symbol, ...]:
        """Per-language additions to the changed symbols (JS: the guard lines; Python: a stated limit)."""
        return symbols

    def _convention(self, leaf: str) -> bool:
        """Whether a name is one the framework has every module of a kind define (never counted for distinctiveness, never a link)."""
        return False

    def _distinct_name(self, name: str) -> str | None:
        """The name to count definitions of for a changed symbol's name; None to skip it."""
        return name

    def _gather(self, ctx: Context, s: Search) -> None:
        """Fill `s.candidates` (files that may import `s.path`) with `_grep_for` and `_pool`."""
        raise NotImplementedError

    def _patterns(self, ctx: Context, path: str) -> list[str]:
        """The fixed strings `_gather` searches for to find the importers of `path` (empty: it has none to search for)."""
        return []

    def _scan_candidate(self, ctx: Context, s: Search, cand: str, parsed: Any) -> None:
        """Resolve each import of the candidate and add an edge to `s.edges` for those that reach `s.path`;
        mark the search incomplete for what could not be resolved."""
        raise NotImplementedError

    def _definition_lines(self, parsed: Any, name: str) -> list[int]:
        raise NotImplementedError

    def _narrowing(self, fc: Any, symbols: Sequence[Symbol], head: str, pre: str | None) -> Narrowing | None:
        raise NotImplementedError

    def _survives(self, importer: str, edges: Sequence[ImportEdge], sha: str, names: set[str]) -> bool:
        """Whether any import of the importer can reach a name of the narrowing set."""
        raise NotImplementedError

    def _call_sites(self, importer: str, edges: Sequence[ImportEdge], sha: str, changed_names: set[str] | None) -> list[CallSite]:
        raise NotImplementedError

    def _edge_lines(self, importer: str, edges: Sequence[ImportEdge], sha: str, names: set[str] | None) -> list[Evidence]:
        """The import lines to cite for an importer (default: the line of each import)."""
        return dedupe([e.evidence for e in edges])

    def _verdicts(self, symbols: Sequence[Symbol], sites: Sequence[CallSite], pre: str | None, head: str) -> list[GuardVerdict]:
        """What a guard says about each call site; the default is that nothing is guarded."""
        return [NO_GUARD for _ in sites]

    def _reach(self, importer: str, edges: Sequence[ImportEdge], sha: str, names: frozenset[str] | None, sites: Sequence[CallSite]) -> Reach:
        """What the use of `names` (None: everything the importer takes) touches inside `importer`; see `Reach`."""
        raise NotImplementedError

    # ---- recording

    @property
    def target(self) -> Any:
        return self._target if self._target is not None else self.git.target

    @contextlib.contextmanager
    def _recording(self, record_to: Any) -> Iterator[None]:
        if record_to is None:
            yield
            return
        saved_target, saved_git = self._target, self.git.target
        self._target, self.git.target = record_to, record_to
        try:
            yield
        finally:
            self._target, self.git.target = saved_target, saved_git

    # ---- paths

    def is_test_path(self, path: str) -> bool:
        return is_test_path(path, self.markers)

    # ---- reading

    def _read(self, sha: str, path: str) -> ReadResult:
        key = (sha, path)
        if key not in self._reads:
            res = self._read_checkout(sha, path)
            self._reads[key] = res if res is not None else self.git.file_at(self.remote, path, sha)
        return self._reads[key]

    def _read_checkout(self, sha: str, path: str) -> ReadResult | None:
        """A bulk read from the commit's checkout, when there is one and it holds the file plainly (the
        blobs are written exactly as git stores them). Anything unusual (a size over the limit, text that
        is not UTF-8, a file that is not there) returns None and the Git layer reads the file, with its own
        found, absent, unreadable answer and its records."""
        ctx = self._contexts.get(sha)
        if ctx is None or path not in ctx.files:
            return None
        target = ctx.checkout.path / path
        try:
            if target.stat().st_size > self.git.max_bytes:
                return None
            return ReadResult.found(target.read_bytes().decode("utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            return None

    def _parse(self, sha: str, path: str) -> Any:
        """The parsed file at the commit, or None when it could not be read (an unreadable read was
        recorded by the git layer) or is absent."""
        key = (sha, path)
        if key not in self._parsed:
            res = self._read(sha, path)
            self._parsed[key] = self._parse_text(res.text) if res.is_found else None
        return self._parsed[key]

    def _context(self, sha: str) -> Context | None:
        if sha not in self._contexts:
            try:
                checkout = self.git.checkout(self.remote, sha)
            except ReadFailed:
                self._contexts[sha] = None  # the git layer recorded it
                return None
            ctx = Context(checkout, frozenset(checkout.files))
            self._contexts[sha] = ctx  # reads go through the checkout while the language prepares its view
            self._prepare_context(ctx, sha)
        return self._contexts[sha]

    # ---- changed symbols

    def changed_symbols(self, change: Any, file: Any, record_to: Any = None) -> list[Symbol]:
        """The symbols of one changed file that the change touches. A file that cannot be read in full is
        a module-level change, never silently nothing."""
        with self._recording(record_to):
            return self._changed_symbols(change, file)

    def _module_symbol(self, fc: Any, side: str, change: str) -> Symbol:
        return Symbol(repo=self.repo, path=fc.path, name=MODULE, kind="module", start=1, end=1, exported=False, nested=(), side=side, change=change)

    def _changed_symbols(self, change: Any, fc: Any) -> list[Symbol]:
        special = self._special_symbols(change, fc)
        if special is not None:
            return special
        if not self._is_code_path(fc.path) or not fc.path_is_text or not citable(fc.path):
            return []
        if fc.binary:  # git calls it binary (a null byte): nothing can be placed, so it is one module-level change
            if fc.status in ("deleted", "renamed") and change.pre_change_sha is None:
                return []
            gone = fc.status == "deleted"
            return [self._module_symbol(fc, "before" if gone else "after", "removed" if gone else "added" if fc.status == "added" else "modified")]
        head, pre = change.head_sha, change.pre_change_sha
        if fc.status != "deleted":
            self._context(head)  # the checkout the importer search needs anyway: the changed files are then read from disk
        after_text: str | None = None
        before_text: str | None = None
        before_path = fc.old_path if fc.status in ("renamed", "copied") and fc.old_path else fc.path
        unreadable = False
        if fc.status != "deleted":
            res = self._read(head, fc.path)
            if res.is_found:
                after_text = res.text
            else:
                unreadable = True
                if res.is_absent:
                    record(self.target, "read_unreadable", f"{fc.path} at {head[:12]}", detail="the diff names the file but it is not in the tree at that commit")
        if pre is not None and fc.status != "added" and not unreadable:
            res = self._read(pre, before_path)
            if res.is_found:
                before_text = res.text
            elif res.is_unreadable:
                unreadable = True
            elif fc.status == "deleted" or fc.status == "modified":
                unreadable = True
                record(self.target, "read_unreadable", f"{before_path} at {pre[:12]}", detail="the diff names the file but it is not in the tree at that commit")
        if unreadable or not fc.content_available:
            return [self._module_symbol(fc, "after", "modified")]
        if fc.status == "renamed" and not fc.hunks and before_text is not None:
            return [self._module_symbol(fc, "before", "removed")]
        added = [dl.number for dl in fc.added_lines] if after_text is not None else []
        removed = [dl.number for dl in fc.removed_lines] if before_text is not None else []
        pa = self._parse(head, fc.path) if after_text is not None else None
        pb = self._parse(pre, before_path) if before_text is not None and pre is not None else None
        bad = [p.problems[0] for p in (pa, pb) if p is not None and p.problems]
        if bad:
            record_quoted(self.target, "parse_degraded", fc.path,
                          "every changed line is treated as module level, so the change reaches every importer", bad[0])
        return self._build_symbols(fc, before_text, after_text, added, removed, pre, head, before_path, pb, pa)

    # ---- importers

    def importers(self, path: str, sha: str, record_to: Any = None) -> ImporterResult:
        """Every import of the file `path` at `sha`, in every form. Candidates come from `git grep` for the
        name the file is imported by; each candidate is parsed and each of its imports resolved, and a
        candidate counts only when one resolves to `path`. The result is marked incomplete when a search was cut
        or failed, a candidate could not be read or did not parse, an import has an expression, an import
        names the file but resolves nowhere, or the language's view of the tree could not be read completely."""
        with self._recording(record_to):
            return self._importers(path, sha)

    def _importers(self, path: str, sha: str) -> ImporterResult:
        """`importers` without the record override. A commit never changes, so an answer is kept for
        the life of the adapter (its records were made when it was first computed)."""
        key = (path, sha)
        if key not in self._importer_cache:
            self._importer_cache[key] = self._search_importers(path, sha)
        return self._importer_cache[key]

    def _outcome(self, ctx: Context, sha: str, pattern: str) -> _Outcome:
        key = (sha, pattern)
        if key not in self._grep_memo:  # a commit never changes: the same search is made once
            self._grep_memo[key] = _outcome_of(self.git.grep(ctx.checkout, pattern, fixed=True, examine_cap=self.examine_cap))
        return self._grep_memo[key]

    def prefetch_importers(self, paths: Sequence[str], sha: str) -> None:
        """Make the one `git grep` that finds the candidates of many files at once (`-e` for each name searched for)
        instead of one search per file. The result is split per pattern, so each file's importer search reads exactly what
        its own search would have read, including a search that was cut or failed (which is still recorded, once, by the
        Git layer, and marks each of the searches incomplete)."""
        ctx = self._context(sha)
        if ctx is None:
            return
        wanted: list[str] = []
        for path in paths:
            if (path, sha) in self._importer_cache:
                continue
            for pattern in self._patterns(ctx, path):
                if (sha, pattern) not in self._grep_memo and pattern not in wanted:
                    wanted.append(pattern)
        if len(wanted) < 2:
            return
        res = self.git.grep(ctx.checkout, wanted, fixed=True, examine_cap=self.examine_cap)
        for pattern in wanted:
            self._grep_memo[(sha, pattern)] = _outcome_of(res, pattern)

    def _grep_for(self, s: Search, pattern: str, accept: Any) -> None:
        """Add the files that match the fixed string to the candidates; a cut or failed search is noted."""
        res = self._outcome(s.ctx, s.sha, pattern)
        if res.state == Read.UNREADABLE:
            s.incomplete(f"the search for {pattern!r} was unreadable: {res.detail}")
            return
        if res.truncated_by == "examine_cap":
            s.incomplete(f"search_truncated: the search for {pattern!r} examined {res.examined} of {res.total} files")
        s.candidates.update(p for p in res.paths if accept(p) and p != s.path)
        for link, why in res.not_searched:
            if accept(link) and (link, why) not in s.skipped:
                s.skipped.append((link, why))

    def _pool(self, s: Search, pool: list[str]) -> None:
        """Every file of the pool is a candidate (nothing to search for); the examine cap applies."""
        if self.examine_cap is not None and len(pool) > self.examine_cap:
            record(self.target, "search_truncated", f"importers of {s.path} at {s.sha[:12]}",
                   detail=f"examined {self.examine_cap} of {len(pool)} files (examine_cap {self.examine_cap})")
            s.incomplete(f"search_truncated: examined {self.examine_cap} of {len(pool)} files")
            pool = pool[: self.examine_cap]
        s.candidates.update(pool)

    def _search_importers(self, path: str, sha: str) -> ImporterResult:
        ctx = self._context(sha)
        if ctx is None:
            return ImporterResult(path, sha, (), False, ("the tree could not be read at this commit",))
        s = Search(path, sha, ctx)
        for why in self._context_gaps(ctx):
            s.incomplete(why)
        self._gather(ctx, s)
        for link, why in s.skipped:
            s.incomplete(f"{link} is a {why}: git grep does not search it, so its imports were not read")
            record_quoted(self.target, "read_unreadable", f"{printable(link)} at {sha[:12]}",
                          f"a {why}: git grep does not search it, so an import in it may be missed", path)
        for cand in sorted(s.candidates):
            if not citable(cand):
                s.incomplete(f"{cand!r} cannot be cited as evidence (its name holds a backslash or bytes that are not UTF-8)")
                record(self.target, "read_unreadable", f"{printable(cand)} at {sha[:12]}",
                       detail="the file name cannot be cited as evidence, so its imports were not read")
                continue
            parsed = self._parse(sha, cand)
            if parsed is None:
                s.incomplete(f"{cand} could not be read")
                continue
            if parsed.problems:
                s.incomplete(f"{cand} did not parse cleanly ({parsed.problems[0]}): imports in it may be missed")
                record_quoted(self.target, "parse_degraded", cand, f"an import of the file may be hidden in it; {parsed.problems[0]}", path, blocks_selection=True, once=True)
                if self.PROBLEMS_STOP_READING:
                    continue
            if parsed.unresolved_dynamic:
                lines = ", ".join(str(n) for n in parsed.unresolved_dynamic[:5])
                s.incomplete(f"{cand} imports with an expression at line {lines}: where it points was not resolved")
                record_quoted(self.target, "specifier_unresolved", cand,
                              f"{self.DYNAMIC_CALLS} with an expression at line {lines}: it may reach the file", path)
            self._scan_candidate(ctx, s, cand, parsed)
        s.edges.sort(key=lambda e: (e.importer_path, e.evidence.line, e.specifier, e.names or ()))
        return ImporterResult(path, sha, tuple(s.edges), s.complete, tuple(s.reasons), len(s.candidates))

    # ---- definitions

    def definitions(self, name: str, sha: str, record_to: Any = None) -> DefinitionCount:
        """How many times `name` is defined in non-test code at `sha`: `git grep` for the name proposes
        files, the parser confirms. References, imports and attribute reads do not count. A failed search is
        state `unreadable` with no count: the name is not established, and is never distinctive; a cut search,
        a file that cannot be read, cannot be cited or does not parse, and a file git grep does not search (a
        symlink) give a count marked incomplete (a lower bound), which is not distinctive either."""
        with self._recording(record_to):
            key = (name, sha)
            if key not in self._definition_cache:
                self._definition_cache[key] = self._count_definitions(name, sha)
            return self._definition_cache[key]

    def _count_definitions(self, name: str, sha: str) -> DefinitionCount:
        ctx = self._context(sha)
        if ctx is None:
            return DefinitionCount(name, sha, "unreadable", None, (), False)
        res = self.git.grep(ctx.checkout, name, fixed=True, examine_cap=self.examine_cap)
        if res.state == Read.UNREADABLE:
            return DefinitionCount(name, sha, "unreadable", None, (), False)
        complete = res.truncated_by != "examine_cap"
        for link, why in res.not_searched:
            if self._is_code_path(link):
                complete = False  # a definition in a file git grep did not search would be missed
                record_quoted(self.target, "read_unreadable", f"{printable(link)} at {sha[:12]}",
                              f"a {why}: git grep does not search it, so a definition of {name} in it may be missed", link)
        count = 0
        sites: list[Evidence] = []
        for path in sorted({m.path for m in res.partial_matches if self._is_code_path(m.path) and not self.is_test_path(m.path)}):
            if not citable(path):
                complete = False  # a definition there could not be cited
                continue
            parsed = self._parse(sha, path)
            if parsed is None or parsed.problems:
                complete = False
                continue
            for line in self._definition_lines(parsed, name):
                count += 1
                ev = evidence_at(self.repo, path, sha, parsed, line)
                if ev is not None:
                    sites.append(ev)
        return DefinitionCount(name, sha, "found", count, tuple(sites), complete)

    # ---- callers

    def callers(self, symbol: Symbol, sha: str, record_to: Any = None, *, change: Any = None) -> list[CallSite]:
        """The first hop: the uses of `symbol` in the files that import its file at `sha` (non-test files
        only; tests are unit tests, never callers). Given the `change` (a resolved change set) the importers
        and uses are narrowed exactly as `analyze` does; without it nothing is narrowed (no pre-change
        version is known), and every use of what an importer takes from the file counts. An importer with
        no use gets one site at its import line (`form` is `import`)."""
        with self._recording(record_to):
            names: set[str] | None = None
            if change is not None:
                fc = next((x for x in change.files if x.path == symbol.path), None)
                if fc is not None:
                    nar = self._narrowing(fc, self._changed_symbols(change, fc), change.head_sha, change.pre_change_sha)
                    names = nar.names if nar is not None else None
            res = self._importers(symbol.path, sha)
            by_importer: dict[str, list[ImportEdge]] = {}
            for e in res.edges:
                if not self.is_test_path(e.importer_path):
                    by_importer.setdefault(e.importer_path, []).append(e)
            sites: list[CallSite] = []
            for importer, edges in sorted(by_importer.items()):
                if names is not None and not self._survives(importer, edges, sha, names):
                    continue
                sites += self._call_sites(importer, edges, sha, names)
            return sites

    def guards(self, symbol: Symbol, call: CallSite, *, before_sha: str | None, after_sha: str | None) -> str:
        """`excluded`, `affected` or `undecidable`; the default is that no call is excluded by a guard."""
        return NO_GUARD.state

    # ---- what the in-repo walk (reachmap.walk) and the entry-point adapters ask

    def tree(self, sha: str) -> frozenset[str]:
        """The files of the tree at `sha` (empty when it could not be read: the git layer recorded it)."""
        ctx = self._context(sha)
        return ctx.files if ctx is not None else frozenset()

    def checkout(self, sha: str) -> Checkout | None:
        """The checkout of the commit (None when it could not be made: the git layer recorded it)."""
        ctx = self._context(sha)
        return ctx.checkout if ctx is not None else None

    def search(self, sha: str, pattern: "str | Sequence[str]") -> Any:
        """`git grep` (fixed string) over the tree at `sha`, with this adapter's examine cap; None when there is no
        checkout. The result says found, truncated or unreadable: the caller turns a cut or failed search into an
        incomplete answer, never into an absence."""
        co = self.checkout(sha)
        return self.git.grep(co, pattern, fixed=True, examine_cap=self.examine_cap) if co is not None else None

    def file_text(self, sha: str, path: str) -> ReadResult:
        """Found, absent or unreadable (recorded by the git layer), kept for the life of the adapter."""
        return self._read(sha, path)

    def parsed(self, sha: str, path: str) -> Any:
        """The file parsed by the language (None when it could not be read or is absent)."""
        return self._parse(sha, path)

    def survivors(self, importer: str, edges: Sequence[ImportEdge], sha: str, names: frozenset[str] | None) -> list[ImportEdge]:
        """The imports of `importer` that can reach one of `names` (all of them when `names` is None), by line."""
        ordered = sorted(edges, key=lambda e: (e.evidence.line, e.specifier))
        if names is None:
            return ordered
        return [e for e in ordered if self._survives(importer, [e], sha, set(names))]

    def sites_for(self, importer: str, edges: Sequence[ImportEdge], sha: str, names: frozenset[str] | None) -> list[CallSite]:
        """The uses in `importer` of the names its imports take from the reached file (no guards: those belong to
        the changed function of the first hop)."""
        return self._call_sites(importer, list(edges), sha, set(names) if names is not None else None)

    def _own_reach(self, path: str, sha: str, seeds: set[str]) -> Reach:
        """What the change of the declarations `seeds` reaches inside their own file; see `Reach`."""
        raise NotImplementedError

    def own_reach(self, path: str, sha: str, seeds: set[str], record_to: Any = None) -> Reach:
        """What a change to the top-level declarations `seeds` of `path` reaches inside the file itself (the
        declarations that use them, to a fixed point, and the names the module exports those under): the in-file
        closure the walk needs for a change inside an entry-point file."""
        with self._recording(record_to):
            return self._own_reach(path, sha, seeds)

    def reach(self, importer: str, edges: Sequence[ImportEdge], sha: str, names: frozenset[str] | None,
              sites: Sequence[CallSite] | None = None, record_to: Any = None) -> Reach:
        """What the reached `names` touch inside `importer` (see `Reach`). `sites` are the uses to start from (the
        first hop passes the call sites that survived guards); by default every use of what the importer takes."""
        with self._recording(record_to):
            use = list(sites) if sites is not None else self.sites_for(importer, edges, sha, names)
            key = (importer, sha, names, tuple((e.evidence.line, e.specifier, e.kind, e.names) for e in edges),
                   tuple((c.line, c.symbol, c.form, c.caller_symbol) for c in use))
            if key not in self._reach_cache:  # a commit never changes: the same uses of the same names reach the same things
                self._reach_cache[key] = self._reach(importer, list(edges), sha, names, use)
            return self._reach_cache[key]

    # ---- a change

    def analyze(self, change: Any, record_to: Any = None, *, distinct: bool = True) -> ChangeAnalysis:
        """Analyse every changed file of a change set. With `distinct` False the changed names are not counted for
        distinctiveness (a synthetic change made of seed lines names code to follow, not names to link code by)."""
        with self._recording(record_to):
            saved = self.distinct_max
            if not distinct:
                self.distinct_max = None
            try:
                files: list[ChangedFile] = []
                impacts: list[FileImpact] = []
                for fc in change.files:
                    role = "test" if self.is_test_path(fc.path) else "source"
                    files.append(ChangedFile(self.repo, fc.path, role, fc.status))
                    impacts.append(self._analyze_file(change, fc))
                return ChangeAnalysis(self.repo, change, tuple(files), tuple(impacts))
            finally:
                self.distinct_max = saved

    def analyze_file(self, change: Any, file: Any, record_to: Any = None) -> FileImpact:
        with self._recording(record_to):
            return self._analyze_file(change, file)

    def _analyze_file(self, change: Any, fc: Any) -> FileImpact:
        role = "test" if self.is_test_path(fc.path) else "source"
        common = {"repo": self.repo, "path": fc.path, "role": role, "status": fc.status}
        ext = posixpath.splitext(fc.path)[1].lower()
        relevant = self._is_relevant_path(fc.path) or ext in self.NOT_ANALYSED
        if relevant and not citable(fc.path):  # includes a name that is not UTF-8
            record(self.target, "read_unreadable", f"{printable(fc.path)} at {change.head_sha[:12]}",
                   detail="the file name cannot be cited as evidence, so the change to it was not analysed")
            return FileImpact(**common, importers_complete=False, importer_reasons=("the file name cannot be cited as evidence",))
        if ext in self.NOT_ANALYSED:
            record(self.target, "file_not_analysed", fc.path, detail=f"a {ext} file is loaded by other means than an import this adapter reads, so its importers were not searched")
            return FileImpact(**common, importers_complete=False, importer_reasons=(f"{ext} files are not analysed by the {self.LANGUAGE}",))
        if not relevant or not fc.path_is_text:
            return FileImpact(**common)
        if fc.binary and self._is_code_path(fc.path):
            record(self.target, "file_not_analysed", fc.path, detail="git treats the file as binary, so what it declares was not read; it is one module-level change")
        elif fc.binary:
            return FileImpact(**common)
        head, pre = change.head_sha, change.pre_change_sha
        symbols = tuple(self._changed_symbols(change, fc))
        if role != "test" and fc.status in ("deleted", "renamed") and pre is None:
            return FileImpact(**common, symbols=symbols, importers_complete=False,
                              importer_reasons=("there is no pre-change version to find the importers of the old path in",))
        if role == "test" or not symbols:
            return FileImpact(**common, symbols=symbols)  # a test is not walked; no changed code reaches no one
        symbols = self._enrich_symbols(fc, symbols, pre, head)

        not_distinct: list[str] = []
        unreadable_names: list[str] = []
        if self.distinct_max is not None:
            leaves: dict[str, str | None] = {}
            for s in symbols:
                if s.is_module:
                    continue
                leaf = self._distinct_name(s.name)
                if leaf is None:
                    continue
                if self._convention(leaf):  # a name the framework has every module define: not counted, not recorded, and one the change cannot be linked by
                    if leaf not in not_distinct:
                        not_distinct.append(leaf)
                    continue
                sha = head if s.side == "after" else pre
                if leaf not in leaves or sha == head:
                    leaves[leaf] = sha
            for leaf in sorted(leaves):
                sha = leaves[leaf]
                if sha is None:
                    continue
                found = self.definitions(leaf, sha)
                if found.state != "found" or found.count is None:
                    unreadable_names.append(leaf)
                elif found.count > self.distinct_max:  # a partial count is a lower bound, and already over the limit
                    not_distinct.append(leaf)
                    record(self.target, "not_distinctive", leaf, detail=not_distinctive_detail(found, sha, self.distinct_max))
                elif not found.complete:
                    unreadable_names.append(leaf)  # the count is not established: never distinctive
        names = {"not_distinctive": tuple(not_distinct), "names_unreadable": tuple(unreadable_names)}

        search_path, search_sha = fc.path, head
        if fc.status in ("deleted", "renamed"):
            search_path = fc.old_path if fc.status == "renamed" and fc.old_path else fc.path
            search_sha = pre  # type: ignore[assignment]  (checked above)
        res = self._importers(search_path, search_sha)
        non_test = sorted({e.importer_path for e in res.edges if not self.is_test_path(e.importer_path)})
        base = {"importers": res.edges, "importer_count": len(non_test), "importers_complete": res.complete, "importer_reasons": res.reasons}
        by_importer: dict[str, list[ImportEdge]] = {}
        for e in res.edges:
            by_importer.setdefault(e.importer_path, []).append(e)
        if _walk.exceeds_hub(len(non_test), self.hub_importers):  # the one hub rule (reachmap.walk) for every hop
            _walk.record_hub_cutoff(self.target, self.subject_prefix + fc.path, len(non_test), self.hub_importers)
            hub_tests = [UnitTest(self.repo, p, fc.path, tuple(self._edge_lines(p, by_importer[p], search_sha, None)))
                         for p in sorted(by_importer) if self.is_test_path(p)]
            return FileImpact(**common, symbols=symbols, hub=True, unit_tests=tuple(hub_tests), **base, **names)
        nar = self._narrowing(fc, symbols, head, pre)
        narrow = nar is not None
        changed_names = nar.names if nar is not None else None

        exclusions: list[Exclusion] = []
        callers: list[Caller] = []
        unit_tests: list[UnitTest] = []
        complete = res.complete
        reasons = list(res.reasons)
        symbol_lines = self._symbol_evidence(symbols, head, pre) + (nar.reach if nar is not None else [])
        for importer, edges in sorted(by_importer.items()):
            if self.is_test_path(importer):
                unit_tests.append(UnitTest(self.repo, importer, fc.path, tuple(self._edge_lines(importer, edges, search_sha, None))))
                continue
            if narrow and not self._survives(importer, edges, search_sha, changed_names or set()):
                exclusions.append(Exclusion(importer, "named_export_narrowing", tuple(dedupe(self._edge_lines(importer, edges, search_sha, None) + symbol_lines))))
                continue
            sites = self._call_sites(importer, edges, search_sha, changed_names if narrow else None)
            verdicts = self._verdicts(symbols, sites, pre, head)
            remaining = [(cs, v) for cs, v in zip(sites, verdicts) if v.state != "excluded"]
            if sites and not remaining:
                guard_ev: list[Evidence] = []
                for v in verdicts:
                    guard_ev += [e for e in v.evidence if e not in guard_ev]
                exclusions.append(Exclusion(importer, "guard", tuple(guard_ev + [cs.evidence for cs in sites])))
                continue
            reason = None
            if any(v.reason == "undecidable" for _cs, v in remaining):
                reason = "undecidable"
            elif any(v.reason == "literal_enters_guard" for _cs, v in remaining):
                reason = "literal_enters_guard"
            first = min((cs for cs, _v in remaining), key=lambda c: c.line)
            evidence = self._edge_lines(importer, edges, search_sha, changed_names if narrow else None) + [cs.evidence for cs, _v in remaining if cs.form != "import"]
            callers.append(Caller(self.repo, importer, first.caller_symbol, reason, tuple(cs for cs, _v in remaining), tuple(dedupe(evidence))))
        self.prefetch_importers([c.path for c in callers], search_sha)  # one search for the names of all of them
        for c in callers:  # tests that import a caller: one hop further out
            sub = self._importers(c.path, search_sha)
            if not sub.complete:
                complete = False
                reasons.append(f"tests of {c.path}: " + "; ".join(sub.reasons))
            for p in sub.paths:
                if self.is_test_path(p):
                    unit_tests.append(UnitTest(self.repo, p, c.path, tuple(self._edge_lines(p, [e for e in sub.edges if e.importer_path == p], search_sha, None))))
        base["importers_complete"], base["importer_reasons"] = complete, tuple(reasons)
        return FileImpact(
            **common, symbols=symbols, callers=tuple(callers), exclusions=tuple(exclusions),
            unit_tests=tuple(unit_tests), narrowed=narrow, reached_names=tuple(sorted(changed_names)) if narrow and changed_names else None,
            **base, **names,
        )

    def _symbol_evidence(self, symbols: Sequence[Symbol], head: str, pre: str | None) -> list[Evidence]:
        """The start line of each changed symbol's declaration (in the text its span refers to)."""
        out: list[Evidence] = []
        for s in symbols:
            sha = head if s.side == "after" else pre
            if sha is None:
                continue
            path = s.path
            if s.side == "before":
                path = next((e.path for e in s.evidence if e.ref == pre), s.path)
            parsed = self._parse(sha, path)
            if parsed is None:
                continue
            ev = evidence_at(self.repo, path, sha, parsed, s.start)
            if ev is not None and ev not in out:
                out.append(ev)
        return out
