"""The TypeScript / JavaScript language adapter.

Given a change, it answers which symbols changed (placed by diff line number), who imports the
changed file (by every specifier: all aliases and relative paths, static and dynamic), which
importers named-export narrowing excludes, which call sites a parameter guard excludes, which
paths are tests, and how many times a name is defined. Every answer is Evidence pinned to a
commit, or an incompleteness record; a list that could not be established completely says so
(`complete=False`) and is never presented as the whole list.

No TypeScript compiler, no node: the files are read through the git layer at the pinned commit
and analysed on tokens (`lexer`, `syntax`). The shell it shares with the Python adapter is
`reachmap.adapters.base`. See docs/briefs/M5-js-adapter.md, including the limits of the lexer and
the parser.
"""

from __future__ import annotations

import dataclasses
import posixpath
from typing import Any, Sequence

from reachmap import config
from reachmap.adapters import Binding, CallSite, GuardVerdict, ImportEdge, MODULE, Reach, Symbol
from reachmap.adapters.base import (
    AdapterBase, Context, NO_GUARD, Narrowing, Search, citable as _citable, ev_list as _ev_list, evidence_at as _evidence, import_claim, record_quoted,
)
from reachmap.adapters.js.aliases import AliasConfig, detect_config, load_aliases
from reachmap.adapters.js.lexer import tokenize, unquote
from reachmap.adapters.js.resolve import Resolver, is_js_path
from reachmap.adapters.js.syntax import Declaration, Parsed, parse
from reachmap.gitlayer import Git
from reachmap.incomplete import record
from reachmap.model import Evidence

__all__ = ["DEFAULT_MARKERS", "JsAdapter", "symbols_for_change"]

# Files that JavaScript imports through a loader but that this adapter does not read; a change to one
# says so (documents, images and lock files are not on the list: nothing imports them as code).
NOT_ANALYSED = frozenset({".vue", ".svelte", ".astro", ".mdx", ".css", ".scss", ".sass", ".less", ".styl", ".svg", ".graphql", ".gql", ".html", ".yaml", ".yml", ".wasm"})

DEFAULT_MARKERS = ("__tests__/", "test/", "tests/", ".test.", ".spec.")
LANGUAGES = ("typescript", "javascript")


def _strip_dts(path: str) -> str:
    return path[:-5] + ".ts" if path.endswith(".d.ts") else path


# ------------------------------------------------------------------------------ changed symbols


def symbols_for_change(
    repo: str,
    path: str,
    before: str | None,
    after: str | None,
    added: Sequence[int],
    removed: Sequence[int],
    *,
    before_sha: str | None,
    after_sha: str | None,
    status: str = "modified",
    before_path: str | None = None,
    parsed_before: Parsed | None = None,
    parsed_after: Parsed | None = None,
) -> list[Symbol]:
    """The symbols a change touches. Each added line (its number in `after`) is placed in the
    top-level declaration whose span holds it in the after text, each removed line (its number in
    `before`) in the span in the before text; a line is never searched for by its text. A changed
    line outside every declaration gives the module-level symbol `<module>` (a comment or blank
    line outside a declaration changes no code and gives nothing). If either text did not lex cleanly
    (`Parsed.problems`) every changed line is module level: nothing can be trusted to sit in a
    declaration. `added` and `removed` are the line numbers of the diff; removals are only placed
    when `before` is given."""
    pa = parsed_after if parsed_after is not None else (parse(after) if after is not None else None)
    pb = parsed_before if parsed_before is not None else (parse(before) if before is not None else None)
    degraded = bool((pa is not None and pa.problems) or (pb is not None and pb.problems))
    groups: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
    mod = {"after": [], "before": []}

    def place(parsed: Parsed, lines: Sequence[int], side: str) -> None:
        for n in sorted(set(lines)):
            decls = parsed.placed(n)
            if not decls:
                if n in parsed.code_lines:
                    mod[side].append(n)
                continue
            for d in decls:
                g = groups.setdefault((d.name, parsed.nested(d, n)), {"after": [], "before": [], "decl_after": None, "decl_before": None})
                g[side].append(n)
                if g[f"decl_{side}"] is None:
                    g[f"decl_{side}"] = d

    if degraded:  # the lexer lost its way somewhere: no line can be trusted to belong to a declaration
        mod["after"] = sorted(set(added)) if pa is not None else []
        mod["before"] = sorted(set(removed)) if pb is not None else []
    else:
        if pa is not None:
            place(pa, added, "after")
        if pb is not None:
            place(pb, removed, "before")

    def by_name(parsed: Parsed | None, name: str) -> Declaration | None:
        if parsed is None:
            return None
        return next((d for d in parsed.declarations if d.name == name), None)

    def export_names(name: str) -> tuple[str, ...]:
        names: set[str] = set()
        for parsed in (pa, pb):
            if parsed is not None:
                for d in parsed.declarations:
                    if d.name == name:
                        names.update(d.export_names)
        return tuple(sorted(names))

    out: list[Symbol] = []
    for (name, nested), g in groups.items():
        decl_a = g["decl_after"] or by_name(pa, name)
        decl_b = g["decl_before"] or by_name(pb, name)
        exists_after = by_name(pa, name) is not None
        exists_before = by_name(pb, name) is not None
        if status == "added":
            change = "added"
        elif status == "deleted":
            change = "removed"
        elif g["after"] and pb is not None and not exists_before:
            change = "added"
        elif g["before"] and not g["after"] and pa is not None and not exists_after:
            change = "removed"
        else:
            change = "modified"
        side = "after" if decl_a is not None else "before"
        decl = decl_a if side == "after" else decl_b
        assert decl is not None
        evidence: list[Evidence] = []
        if pa is not None and after_sha is not None:
            evidence += _ev_list(repo, path, after_sha, pa, g["after"])
        if pb is not None and before_sha is not None:
            evidence += _ev_list(repo, before_path or path, before_sha, pb, g["before"])
        names = export_names(name)
        out.append(Symbol(
            repo=repo, path=path, name=name, kind=decl.kind, start=decl.start, end=decl.end, exported=bool(names),
            nested=nested, side=side, change=change, export_names=names, evidence=tuple(evidence),
            added_lines=tuple(sorted(set(g["after"]))), removed_lines=tuple(sorted(set(g["before"]))),
        ))
    if mod["after"] or mod["before"]:
        side = "after" if mod["after"] else "before"
        lines = mod[side]
        evidence = []
        if pa is not None and after_sha is not None:
            evidence += _ev_list(repo, path, after_sha, pa, mod["after"])
        if pb is not None and before_sha is not None:
            evidence += _ev_list(repo, before_path or path, before_sha, pb, mod["before"])
        out.append(Symbol(
            repo=repo, path=path, name=MODULE, kind="module", start=min(lines), end=max(lines), exported=False, nested=(),
            side=side, change="modified", evidence=tuple(evidence),
            added_lines=tuple(sorted(set(mod["after"]))), removed_lines=tuple(sorted(set(mod["before"]))),
        ))
    out.sort(key=lambda s: (s.is_module, s.start, s.name, s.nested))
    return out



# ------------------------------------------------------------------------------ the adapter


class JsAdapter(AdapterBase):
    """TypeScript / JavaScript adapter for one repository (see `AdapterBase`)."""

    languages = LANGUAGES
    DEFAULT_MARKERS = DEFAULT_MARKERS
    NOT_ANALYSED = NOT_ANALYSED
    DYNAMIC_CALLS = "import() or require()"
    LANGUAGE = "JS adapter"

    def __init__(
        self,
        repo: str,
        remote: str,
        git: Git,
        *,
        aliases_from: str | None = None,
        test_markers: Sequence[str] | None = None,
        record_to: Any = None,
        examine_cap: int | None = None,
        hub_importers: int | None = None,
        distinct_max: int | None = None,
    ) -> None:
        super().__init__(repo, remote, git, test_markers=test_markers, record_to=record_to, examine_cap=examine_cap,
                         hub_importers=hub_importers, distinct_max=distinct_max)
        self.aliases_from = aliases_from
        self._unresolved_prefixes: set[str] = set()

    @classmethod
    def from_config(
        cls,
        repo: config.Repo,
        git: Git,
        *,
        limits: config.Limits | None = None,
        remote: str | None = None,
        record_to: Any = None,
        examine_cap: int | None = None,
    ) -> "JsAdapter":
        """The adapter a repository's config selects: `aliases.from`, `tests.markers`, and the
        hub and distinctiveness limits of the workspace."""
        aliases = repo.aliases or {}
        tests = repo.tests or {}
        return cls(
            repo.name,
            remote or repo.remote or "",
            git,
            aliases_from=aliases.get("from"),
            test_markers=tests.get("markers"),
            record_to=record_to,
            examine_cap=examine_cap,
            hub_importers=limits.hub_importers if limits else None,
            distinct_max=limits.distinct_max if limits else None,
        )

    # ---- hooks of the shell

    def _parse_text(self, text: str) -> Parsed:
        return parse(text)

    def _is_code_path(self, path: str) -> bool:
        return is_js_path(path)

    def _is_relevant_path(self, path: str) -> bool:
        return is_js_path(path) or path.lower().endswith(".json")

    def _prepare_context(self, ctx: Context, sha: str) -> None:
        files = set(ctx.files)
        dirs = {""} | {"/".join(f.split("/")[:i]) for f in files for i in range(1, len(f.split("/")))}
        config_path = self.aliases_from or detect_config(lambda p: p in files)
        if config_path is None:
            alias_cfg = AliasConfig.empty()
        else:
            alias_cfg = load_aliases(lambda p: self._read(sha, p), config_path, lambda d: d in dirs, self.target)
        ctx.aliases = alias_cfg  # type: ignore[attr-defined]
        ctx.resolver = Resolver(files, alias_cfg)  # type: ignore[attr-defined]

    def _context_gaps(self, ctx: Context) -> list[str]:
        if ctx.aliases.complete:  # type: ignore[attr-defined]
            return []
        return ["the alias configuration could not be read completely (a package it extends is not in the repository, or a config could not be read)"]

    def _special_symbols(self, change: Any, fc: Any) -> list[Symbol] | None:
        if fc.path.endswith(".json") and not fc.binary and fc.path_is_text and _citable(fc.path):
            # data, not code: the whole file is one module-level change, whatever lines changed
            removed = fc.status in ("deleted", "renamed") and not (fc.status == "renamed" and fc.hunks)
            if removed and change.pre_change_sha is None:
                return []  # removals are not computed without a pre-change version
            return [Symbol(repo=self.repo, path=fc.path, name=MODULE, kind="module", start=1, end=1, exported=False, nested=(),
                           side="before" if removed else "after", change="removed" if removed else "added" if fc.status == "added" else "modified")]
        return None

    def _build_symbols(self, fc, before, after, added, removed, pre, head, before_path, pb, pa) -> list[Symbol]:
        return symbols_for_change(
            self.repo, fc.path, before, after, added, removed, before_sha=pre, after_sha=head,
            status=fc.status, before_path=before_path, parsed_before=pb, parsed_after=pa,
        )

    def _enrich_symbols(self, fc, symbols, pre, head):
        return tuple(dataclasses.replace(s, guards=g) if (g := self.symbol_guards(s, pre, head)) else s for s in symbols)

    def _definition_lines(self, parsed: Parsed, name: str) -> list[int]:
        return list(parsed.definition_lines(name))

    # ---- importers

    def _stem(self, path: str) -> str | None:
        base = posixpath.basename(path)
        name = base[:-5] if base.endswith(".d.ts") else base.rsplit(".", 1)[0] if "." in base else base
        if name == "index":
            folder = posixpath.dirname(path)
            return posixpath.basename(folder) if folder else None
        return name

    def _patterns(self, ctx: Context, path: str) -> list[str]:
        """The file stem (the folder name for an index file) plus the spelling of any exact alias that does not hold the
        stem; none for a root index file."""
        stem = self._stem(path)
        if stem is None:
            return []
        patterns = [stem]
        for spec in ctx.resolver.specifiers_for(path):  # type: ignore[attr-defined]
            if stem not in spec and spec not in patterns:
                patterns.append(spec)  # an exact alias whose spelling does not hold the stem
        return patterns

    def _gather(self, ctx: Context, s: Search) -> None:
        """Candidates: `git grep` for the file stem (the folder name for an index file) plus the spelling of any
        exact alias that does not hold the stem; for an index file also every JS file under its folder (`from "."`
        and `from ".."` name no folder); for a root index file every JS file."""
        path = s.path
        patterns = self._patterns(ctx, path)
        if not patterns:  # a root index file has no name to search for: every JS file is a candidate
            self._pool(s, [p for p in ctx.checkout.files if is_js_path(p) and p != path])
            return
        if posixpath.splitext(posixpath.basename(_strip_dts(path)))[0] == "index":
            folder = posixpath.dirname(path)
            s.candidates.update(p for p in ctx.checkout.files if is_js_path(p) and p != path and p.startswith(folder + "/"))
        for pattern in patterns:
            self._grep_for(s, pattern, is_js_path)

    def _scan_candidate(self, ctx: Context, s: Search, cand: str, parsed: Parsed) -> None:
        stem = self._stem(s.path)
        for rec in parsed.imports:
            resolved = ctx.resolver.resolve(cand, rec.specifier)  # type: ignore[attr-defined]
            if resolved is None and self._names_file(rec.specifier, s.path, stem):
                s.incomplete(f"{cand} imports {rec.specifier!r}, which resolves to nothing at this commit but names the file")
                self._record_unresolved(ctx, rec.specifier, cand, s.path, s.sha)
            if resolved != s.path:
                continue
            ev = _evidence(self.repo, cand, s.sha, parsed, rec.line)
            if ev is None:
                continue
            ev = dataclasses.replace(ev, claim=import_claim(s.path, rec.names))
            s.edges.append(ImportEdge(
                importer_path=cand, specifier=rec.specifier, names=rec.names, evidence=ev, kind=rec.kind,
                type_only=rec.type_only, bindings=tuple(Binding(b.imported, b.local, b.type_only) for b in rec.bindings), target=s.path,
            ))


    @staticmethod
    def _names_file(spec: str, path: str, stem: str | None) -> bool:
        """Whether a non-relative specifier's last segment is the file's stem (the folder name of an index
        file, with or without `/index`)."""
        if stem is None or spec.startswith((".", "/")):
            return False
        for mark in ("?", "#"):
            if mark in spec[1:]:
                spec = spec[:1] + spec[1:].split(mark)[0]
        last = spec.rstrip("/").rsplit("/", 1)[-1]
        for ext in (".d.ts", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".json"):
            if last.endswith(ext):
                last = last[: -len(ext)]
                break
        if last == "index" and posixpath.basename(_strip_dts(path)).startswith("index."):
            return True
        return last == stem

    def _record_unresolved(self, ctx: Context, spec: str, importer: str, path: str, sha: str) -> None:
        """One record per prefix of an unresolved specifier (`~/lib` of `~/lib/format`)."""
        prefix = spec.rstrip("/").rsplit("/", 1)[0] if "/" in spec.rstrip("/") else spec
        if prefix in self._unresolved_prefixes:
            return
        self._unresolved_prefixes.add(prefix)
        record_quoted(self.target, "specifier_unresolved", prefix,
                      f"an import resolves to nothing at {sha[:12]} and its last segment names the file: an importer may be missed "
                      "(an alias, a workspace package or a build-tool path that this adapter does not read?)", f"{spec!r} in {importer}, file {path}")
        if ctx.aliases.missing and ctx.aliases.source:  # type: ignore[attr-defined]
            record(self.target, "alias_config_gap", ctx.aliases.source,  # type: ignore[attr-defined]
                   detail="named by aliases.from but absent at this commit, so no alias was read")

    def specifiers_for(self, path: str, sha: str, importer: str | None = None) -> list[str]:
        """Every specifier that means `path` at `sha` (all alias patterns, with and without
        extension and `/index`, relative spellings for `importer`)."""
        ctx = self._context(sha)
        return [] if ctx is None else ctx.resolver.specifiers_for(path, importer)  # type: ignore[attr-defined]

    def _survives(self, importer: str, edges: Sequence[ImportEdge], sha: str, names: set[str]) -> bool:
        """Whether any import of the importer takes a name of the narrowing set; namespace, default, side
        effect, dynamic, require and `export *` imports take the whole module."""
        return any(e.names is None or bool(set(e.names) & names) for e in edges)

    # ---- guards

    @staticmethod
    def _literal_of(text: str) -> str | None:
        toks = tokenize(text).tokens
        if len(toks) == 1 and toks[0].kind in ("string", "template"):
            raw = toks[0].value
            if toks[0].kind == "template" and not (raw.startswith("`") and raw.endswith("`") and len(raw) >= 2):
                return None
            return unquote(raw)
        return None

    def _literal_for(self, call: CallSite, param: Any, info: Any) -> str | None:
        """The string literal a call passes for `param`, or None when it passes anything else or the
        parameter cannot be reasoned about (a default value, a rest parameter, rebound in the
        function, a spread before it, a missing argument, a reference rather than a call)."""
        if param.has_default or param.rest or param.name in info.rebound or call.form != "call":
            return None
        if param.index >= len(call.args) or any(a.startswith("...") for a in call.args[: param.index + 1]):
            return None
        return self._literal_of(call.args[param.index])

    def _guard_cover(self, symbol: Symbol, before_sha: str | None, after_sha: str | None) -> list[Any] | None:
        """For a modified function: per side of the change (after, then before), the parsed text,
        the function's parameters and, for each changed line, the guard blocks on a parameter that
        enclose it. None when some changed line is enclosed by none (or the symbol is not a
        function, or a text cannot be read): no guard covers the change."""
        if symbol.is_module or symbol.kind != "function" or symbol.change != "modified" or before_sha is None or after_sha is None:
            return None
        sides: list[tuple[str, str, Sequence[int]]] = []
        if symbol.added_lines:
            sides.append((after_sha, symbol.path, symbol.added_lines))
        if symbol.removed_lines:
            old_path = next((e.path for e in symbol.evidence if e.ref == before_sha), symbol.path)
            sides.append((before_sha, old_path, symbol.removed_lines))
        if not sides:
            return None
        out: list[Any] = []
        for sha, path, lines in sides:
            parsed = self._parse(sha, path)
            if parsed is None or parsed.problems:
                return None
            decl = next((d for d in parsed.declarations if d.name == symbol.name and d.start <= min(lines) and max(lines) <= d.end), None)
            info = parsed.function_info(decl) if decl is not None else None
            if decl is None or info is None:
                return None
            params = {p.name: p for p in info.params if p.name}
            blocks = [b for b in parsed.guard_blocks(decl) if b.param in params]
            per_line = []
            for line in lines:
                covering = [b for b in blocks if b.first_line <= line <= b.last_line]
                if not covering:
                    return None
                per_line.append((line, covering))
            out.append((sha, path, parsed, info, params, per_line))
        return out

    def symbol_guards(self, symbol: Symbol, before_sha: str | None, after_sha: str | None) -> tuple[Evidence, ...]:
        """The guard lines that enclose every changed line of a function (see `Symbol.guards`)."""
        cover = self._guard_cover(symbol, before_sha, after_sha)
        if cover is None:
            return ()
        out: list[Evidence] = []
        for sha, path, parsed, _info, _params, per_line in cover:
            for _line, covering in per_line:
                ev = _evidence(self.repo, path, sha, parsed, covering[0].guard_line)
                if ev is not None and not any((e.path, e.line, e.detail) == (ev.path, ev.line, ev.detail) for e in out):
                    out.append(ev)
        return tuple(out)

    def guard_verdict(self, symbol: Symbol, call: CallSite, *, before_sha: str | None, after_sha: str | None) -> GuardVerdict:
        """Whether the changed lines of `symbol` all lie inside `if (<param> === "<lit>")` (or `!==`,
        `==`, `!=`, an `else`, a `switch` `case "<lit>":`) blocks of the changed function, and what
        that says about `call`. The call is excluded only when it passes a string literal at the
        parameter's position that provably cannot enter a block that holds each changed line; any
        other argument form (variable, expression, spread, missing, default parameter, reassigned
        parameter) is undecidable, so affected. Without a guard covering every changed line the
        call is simply affected."""
        if symbol.is_module or symbol.kind != "function" or symbol.change != "modified" or before_sha is None or after_sha is None:
            return NO_GUARD
        cover = self._guard_cover(symbol, before_sha, after_sha)
        if cover is None:
            return NO_GUARD
        excluded_all = True
        undecidable = False
        evidence: list[Evidence] = []
        for sha, path, parsed, info, params, per_line in cover:
            for line, covering in per_line:
                literals = [(b, self._literal_for(call, params[b.param], info)) for b in covering]
                hit = next((b for b, lit in literals if lit is not None and not b.permits(lit)), None)
                if hit is not None:
                    ev = _evidence(self.repo, path, sha, parsed, hit.guard_line)
                    if ev is not None and not any((e.path, e.line, e.detail) == (ev.path, ev.line, ev.detail) for e in evidence):
                        evidence.append(ev)
                    continue
                excluded_all = False
                if any(lit is None for _b, lit in literals):
                    undecidable = True
        if excluded_all:
            return GuardVerdict("excluded", None, tuple(evidence))
        if undecidable:
            return GuardVerdict("undecidable", "undecidable", ())
        return GuardVerdict("affected", "literal_enters_guard", ())

    def guards(self, symbol: Symbol, call: CallSite, *, before_sha: str | None, after_sha: str | None) -> str:
        """`excluded`, `affected` or `undecidable` (see `guard_verdict`)."""
        return self.guard_verdict(symbol, call, before_sha=before_sha, after_sha=after_sha).state

    def _narrowing(self, fc: Any, symbols: Sequence[Symbol], head: str, pre: str | None) -> Narrowing | None:
        """Named-export narrowing, or None when it does not apply (everything is then affected).

        It applies only with a pre-change version, for a file that is neither deleted nor renamed,
        when no change is module level, and when both versions parse reliably and use no dynamic
        access (`eval`, `this[...]`). The names compared with an importer's are the export names of
        the changed declarations and of every declaration that reaches them, by identifier, inside
        the file, repeated to a fixed point, in the before and the after text; module-level code that
        uses a name in that set stops narrowing, and so does a set with no export at all."""
        if pre is None or fc.status in ("deleted", "renamed") or not symbols or any(s.is_module for s in symbols):
            return None
        pa = self._parse(head, fc.path)
        pb = self._parse(pre, fc.path) if fc.status != "added" else None
        if pa is None or (fc.status != "added" and pb is None):
            return None
        parses = [p for p in (pa, pb) if p is not None]
        if any(p.problems or p.uses_dynamic_access() for p in parses):
            return None
        closure = {s.name for s in symbols}
        # members of a class that changed: another declaration of the file reaches one by `.member`, whatever the object
        members: set[str] = set()
        for s in symbols:
            if s.is_module:
                continue
            for p, lines in ((pa, s.added_lines), (pb, s.removed_lines)):
                if p is not None and any(d.name == s.name and d.kind == "class" for d in p.declarations):
                    members |= set(s.nested) | set(p.line_identifiers(lines))
        grown = True
        while grown:
            grown = False
            for p in parses:
                for d in p.declarations:
                    if d.name not in closure and (p.decl_references(d) & closure or members and p.decl_properties(d) & members):
                        closure.add(d.name)
                        grown = True
        if any(closure & p.module_references() or members & p.module_properties() for p in parses):
            return None
        # a constant whose initializer calls a name whose behaviour changed runs for every importer when the module loads
        if any(closure & p.initializer_calls(d) for p in parses for d in p.declarations):
            return None
        names = {n for p in parses for d in p.declarations if d.name in closure for n in d.export_names}
        if not names:
            return None
        changed = {s.name for s in symbols}
        reach: list[Evidence] = []
        for sha, p in ((head, pa), (pre, pb)):
            if p is None:
                continue
            for d in p.declarations:
                if d.name in closure and d.name not in changed:
                    ev = _evidence(self.repo, fc.path, sha, p, d.start)
                    if ev is not None and not any((e.path, e.line, e.detail) == (ev.path, ev.line, ev.detail) for e in reach):
                        reach.append(ev)
        return Narrowing(names, reach)

    def _reach(self, importer: str, edges: Sequence[ImportEdge], sha: str, names: frozenset[str] | None, sites: Sequence[CallSite]) -> Reach:
        """What the use of `names` touches inside `importer` (the walk's hop). The declarations that use a reached
        import binding are reached, and so is every declaration that uses a reached one, to a fixed point; the names
        the module exports them under are what the next hop is narrowed with. The whole module is reached (None)
        when a use sits at module level, an import is never used, a dynamic import or `require` sits at module
        level, `export *` re-exports without a known set of names, the file does not lex cleanly or reaches names
        dynamically, a reached name is used by module-level code or called where a constant is initialised, or
        nothing reached is exported (undecidable means affected, as in the narrowing of the first hop)."""
        parsed = self._parse(sha, importer)
        if parsed is None:
            return Reach(frozenset(), None, (), False, ("the file could not be read",), True)
        complete, reasons = True, []
        if parsed.problems:
            complete = False
            reasons.append(f"the file did not lex cleanly ({parsed.problems[0]})")
        whole = not complete or parsed.uses_dynamic_access()
        locals_: set[str] = set()
        reached: set[str] = set()
        out: set[str] = set()
        for e in edges:
            if e.kind == "reexport":  # `export { a as b } from "x"`: the module exports what it takes, under the new name
                for ent in parsed.exports.values():
                    if ent.source == e.specifier and ent.local is not None and (names is None or ent.local in names or ent.local == "*"):
                        out.add(ent.name)
            elif e.kind == "export_star":
                if names is None:
                    whole = True
                else:
                    out |= names
            elif not e.bindings:  # a side effect import, `import()` or `require()` with no binding: the declaration it sits in
                placed = parsed.placed(e.evidence.line)
                if placed:
                    reached.update(d.name for d in placed)
                else:
                    whole = True
            else:
                locals_.update(b.local for b in e.bindings if names is None or b.imported in names or b.imported in ("default", "*"))
        has_uses = any(e.kind not in ("reexport", "export_star") for e in edges)
        lines: list[int] = []
        for cs in sites:
            if not has_uses:
                break
            if cs.form == "import":
                whole = True  # nothing ties the import to a declaration (importing the module runs it)
                continue
            lines.append(cs.line)
            if cs.caller_symbol == MODULE:
                whole = True
            else:
                reached.add(cs.caller_symbol)
        return self._close(parsed, reached, locals_, out, whole, lines, complete, reasons)

    def _close(self, parsed: Parsed, reached: set[str], locals_: set[str], out: set[str], whole: bool, lines: list[int],
               complete: bool, reasons: list[str]) -> Reach:
        """The closure of the reached declarations inside the file (every declaration that uses a reached one, to a
        fixed point) and the names the module exports it under; the whole module when module-level code uses a reached
        name or a constant is initialised by a call of one."""
        closure = set(reached)
        grown = True
        while grown:
            grown = False
            for d in parsed.declarations:
                if d.name not in closure and parsed.decl_references(d) & closure:
                    closure.add(d.name)
                    grown = True
        used = closure | locals_
        if parsed.module_references() & used or any(used & parsed.initializer_calls(d) for d in parsed.declarations):
            whole = True
        for d in parsed.declarations:
            if d.name in closure:
                out.update(d.export_names)
        seen = tuple(sorted(set(lines)))
        names = None if whole or not out else frozenset(out)
        return Reach(frozenset(locals_ | closure), names, seen, complete, tuple(reasons), whole)

    def _own_reach(self, path: str, sha: str, seeds: set[str]) -> Reach:
        parsed = self._parse(sha, path)
        if parsed is None:
            return Reach(frozenset(seeds), None, (), False, ("the file could not be read",), True)
        complete, reasons = not parsed.problems, [f"the file did not lex cleanly ({parsed.problems[0]})"] if parsed.problems else []
        return self._close(parsed, set(seeds), set(), set(), not complete or parsed.uses_dynamic_access(), [], complete, reasons)

    def _verdicts(self, symbols: Sequence[Symbol], sites: Sequence[CallSite], pre: str | None, head: str) -> list[GuardVerdict]:
        return [self._verdict(symbols, cs, pre, head) for cs in sites]

    def _verdict(self, symbols: Sequence[Symbol], cs: CallSite, pre: str | None, head: str) -> GuardVerdict:
        sym = self._merged(symbols, cs.symbol)
        if sym is None:
            return NO_GUARD
        return self.guard_verdict(sym, cs, before_sha=pre, after_sha=head)

    @staticmethod
    def _merged(symbols: Sequence[Symbol], export_name: str) -> Symbol | None:
        """The changed symbol exported as `export_name`, with the changed lines of every
        (declaration, nested function) pair of it: a guard must hold all of them."""
        same = [s for s in symbols if export_name in s.export_names]
        if not same:
            return None
        first = same[0]
        if len(same) == 1:
            return first
        return dataclasses.replace(
            first,
            added_lines=tuple(sorted({n for s in same for n in s.added_lines})),
            removed_lines=tuple(sorted({n for s in same for n in s.removed_lines})),
            evidence=tuple(dict.fromkeys(e for s in same for e in s.evidence)),
            nested=(),
        )

    def _call_sites(self, importer: str, edges: list[ImportEdge], sha: str, changed_names: set[str] | None) -> list[CallSite]:
        """The uses in `importer` of what it imports from the changed file: of the changed names
        only when narrowing applies, else of everything it imports. An importer with no use gets
        one site at its import line."""
        parsed = self._parse(sha, importer)
        sites: list[CallSite] = []
        for edge in edges:
            if parsed is None:
                continue
            for b in edge.bindings:
                uses: list[tuple[str, Any]] = []
                if changed_names is None:
                    uses = [(b.imported, u) for u in parsed.find_uses(b.local)]
                elif b.imported == "*":
                    for name in sorted(changed_names):
                        uses += [(name, u) for u in parsed.find_uses(b.local, member=name, whole_references=True)]
                elif b.imported in changed_names:
                    uses = [(b.imported, u) for u in parsed.find_uses(b.local)]
                for callee, u in uses:
                    ev = _evidence(self.repo, importer, sha, parsed, u.line)
                    if ev is None:
                        continue
                    sites.append(CallSite(
                        repo=self.repo, path=importer, line=u.line, symbol=callee, evidence=ev, import_evidence=edge.evidence,
                        args=u.args, caller_symbol=u.enclosing, form=u.form,
                    ))
        if not sites:
            edge = edges[0]
            sites.append(CallSite(
                repo=self.repo, path=importer, line=edge.evidence.line, symbol=edge.bindings[0].imported if edge.bindings else MODULE,
                evidence=edge.evidence, import_evidence=edge.evidence, args=(), caller_symbol=MODULE, form="import",
            ))
        sites.sort(key=lambda c: (c.line, c.symbol))
        return sites

    def resolve_specifier(self, importer: str, spec: str, sha: str) -> str | None:
        """The file a specifier means for `importer` at `sha` (aliases, relative paths, extensions, `/index`), or
        None when it resolves to nothing in the tree (a package, an alias this adapter does not read)."""
        ctx = self._context(sha)
        return None if ctx is None else ctx.resolver.resolve(importer, spec)  # type: ignore[attr-defined]
