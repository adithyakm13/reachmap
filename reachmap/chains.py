"""Chains across repositories: the evidence that leads from the original change to the end of one repository's part.

`crossing_prefix` is what a hop out of an area shows of the way inside the repository (see its docstring); `python_segment`
and the in-file steps are what a Python repository adds to the imports of the walk. Used by the walker and by the edge
adapters (kept apart from both so neither imports the other).
"""

from __future__ import annotations

from typing import Any, Sequence

from reachmap.adapters.base import dedupe, evidence_at
from reachmap.model import Evidence

__all__ = ["crossing_prefix", "python_segment"]


def crossing_prefix(run: Any, p: Any, reached: Any, index: int = 0) -> tuple[Evidence, ...]:
    """The evidence that leads from the original change to the end of this pass's part of the chain, before an
    edge is taken out of the area `reached`: the pass's own prefix, then the way inside the repository from the seed
    to the entry point (without the route's own evidence, which belongs to the route). For a change inside the entry
    file itself there is no way to show: the chain continues from the edge. A Python repository's way names the line
    where each file uses what it imports and the lines where one declaration uses another inside a file (what the
    import alone does not say); another language shows its imports."""
    sources = [c for c in getattr(reached, "sources", ()) if c is not None]
    if not sources:
        return p.prefix
    chain = sources[min(index, len(sources) - 1)]
    if chain.direct:
        return p.prefix
    from reachmap.adapters.python import PyAdapter

    lang = run.language(p.repo)
    segment: Sequence[Evidence] = chain.hops
    if isinstance(lang, PyAdapter):
        segment = python_segment(lang, p, chain) or chain.hops
    return tuple(dedupe([*p.prefix, *segment]))


def python_segment(lang: Any, p: Any, chain: Any) -> list[Evidence]:
    """The evidence of a walk chain through Python files, hop by hop: inside the first file, the lines where one
    declaration uses the next on the way from the seed to the name the importer takes; then for each import the
    import line and the first line where the importer uses what arrives; inside the files after the first, the same
    for the way from that use to the name the next importer takes."""
    files, sha = chain.files, chain.sha
    out: list[Evidence] = []
    try:
        first = p.analysis.impact_for(files[0])
    except KeyError:
        return []
    names: frozenset[str] | None = frozenset(first.reached_names) if first.reached_names is not None else None
    entering: set[str] = {n for s in first.symbols if not s.is_module for n in (s.export_names or (s.name.split(".")[0],))}
    for i in range(len(files) - 1):
        src, dst = files[i], files[i + 1]
        hop_ev = chain.hops[i]
        edges = [e for e in lang.importers(src, sha).edges if e.importer_path == dst and e.evidence == hop_ev]
        if not edges:
            return []
        sites = lang.sites_for(dst, edges, sha, names)
        used = [c for c in sites if c.form != "import"]
        targets = {c.symbol for c in used if c.symbol not in ("*", "<module>")}
        out += _in_file_steps(lang, src, sha, entering, targets or None)
        out.append(hop_ev)
        if used:
            out.append(used[0].evidence)
        reach = lang.reach(dst, edges, sha, names, sites)
        names = reach.names
        entering = {c.caller_symbol for c in used if c.caller_symbol != "<module>"}
    return out


def _in_file_steps(lang: Any, path: str, sha: str, start: set[str], goals: set[str] | None) -> list[Evidence]:
    """The lines inside one file where a declaration uses the one before it, from a declaration in `start` to the
    nearest one named in `goals` (None: no way is shown). Empty when the start already is a goal or no way is found."""
    if not goals or not start or start & goals:
        return []
    parsed = lang.parsed(sha, path)
    if parsed is None:
        return []
    decls = {n: d for d in parsed.declarations for n in d.names}
    uses_of: dict[str, list[tuple[str, int]]] = {}
    for user in parsed.declarations:
        refs = parsed.decl_references(user)
        for name in refs & set(decls):
            if name in user.names:
                continue
            lines = [u.line for u in parsed.uses(name) if user.start <= u.line <= user.end]
            if lines:
                uses_of.setdefault(name, []).append((user.name, min(lines)))
    paths: dict[str, list[int]] = {n: [] for n in start if n in decls}
    frontier = sorted(paths)
    while frontier:
        nxt: list[str] = []
        for name in frontier:
            for user, line in sorted(uses_of.get(name, ())):
                decl = decls[user]
                for alias in decl.names:
                    if alias not in paths:
                        paths[alias] = paths[name] + [line]
                        nxt.append(alias)
        hit = [g for g in sorted(goals) if g in paths]
        if hit:
            lines = paths[hit[0]]
            return [e for n in lines if (e := _evidence(lang, path, sha, parsed, n)) is not None]
        frontier = sorted(set(nxt))
    return []


def _evidence(lang: Any, path: str, sha: str, parsed: Any, line: int) -> Evidence | None:
    return evidence_at(lang.repo, path, sha, parsed, line)
