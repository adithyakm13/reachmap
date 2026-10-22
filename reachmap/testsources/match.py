"""Tying a test case to an area (context rule 4.3: a place to look, never coverage).

A case is suggested for an area of the run when three things hold, and only then:

1. the area matches a configured area (`[[test_sources.areas]]`): its route path starts with one of the `route_prefixes` (on a
   segment boundary: `/billing` is `/billing/invoices` and not `/billingx`), or its module is one of the `modules`;
2. the case is in the configured area's `suites`, descendants included (a case with no suite is in scope only when the configured
   area names no suites);
3. the case shares at least two distinctive words with the area (`words`): the words of the area's label, module, route segments
   and the symbols the change touches, which the area's finding names (the definition lines it cites carry `def:<symbol>`).

Only import-backed areas are considered; a candidate found by name is not a place the change was shown to reach. Everything made
here is a `Suggestion`; nothing here creates or imports a `MappedTest`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from reachmap.model import Area, Suggestion
from reachmap.testsources import words
from reachmap.testsources.base import Corpus

__all__ = ["AreaInfo", "area_matches", "infos", "suggest"]


@dataclass(frozen=True, slots=True)
class AreaInfo:
    area: Area
    words: frozenset[str]  # stems: its route, its module and the symbols
    symbols: tuple[str, ...]  # the changed symbols that reach it, as the finding names them
    route: frozenset[str] = frozenset()  # stems of the words of its label and the other paths it is mounted at


def _symbols(area: Area, findings: Iterable[Any]) -> tuple[str, ...]:
    out: dict[str, None] = {}
    for f in findings:
        if f.kind in ("direct", "cross-repo") and tuple(f.identity[1:]) == (area.repo, area.kind, area.label):
            for e in f.evidence:
                if e.claim and e.claim.startswith("def:") and len(e.claim) > 4:
                    out[e.claim[4:]] = None
    return tuple(out)


def infos(areas: Iterable[Area], findings: Iterable[Any] = ()) -> list[AreaInfo]:
    """The import-backed areas of a run with the words that tie cases to them."""
    findings = list(findings)
    out = []
    for a in areas:
        if a.by_name or a.priority == "candidate":
            continue
        symbols = _symbols(a, findings)
        out.append(AreaInfo(a, words.area_words(label=a.label, module=a.module, also_mounted=a.also_mounted, symbols=symbols), symbols,
                            words.route_words(label=a.label, also_mounted=a.also_mounted)))
    return out


def _prefix_covers(prefix: str, path: str) -> bool:
    prefix = prefix.rstrip("/")
    return prefix == "" or path == prefix or path.startswith(prefix + "/")


def area_matches(cfg: Mapping[str, Any], area: Area) -> bool:
    """Whether `area` is one the configured area names: by route prefix (on its own path or any it is also mounted at) or by module."""
    paths = [words.label_path(area.label), *area.also_mounted]
    for prefix in cfg.get("route_prefixes") or ():
        if isinstance(prefix, str) and any(_prefix_covers(prefix, p) for p in paths):
            return True
    module = area.module.lower()
    return bool(module) and any(isinstance(m, str) and m.lower() == module for m in cfg.get("modules") or ())


def _restating(cfg: Mapping[str, Any]) -> frozenset[str]:
    """The stems of the words of a configured area's prefixes and modules: they say which area it is, not what is on its screen."""
    return frozenset(words.stem(w) for text in (*(cfg.get("route_prefixes") or ()), *(cfg.get("modules") or ())) if isinstance(text, str) for w in words.tokens(text))


def suggest(source: str, corpus: Corpus, configured: Sequence[Mapping[str, Any]], areas: Sequence[AreaInfo]) -> list[Suggestion]:
    """The suggestions for the areas, in the order of the areas and then of the cases. A case is one suggestion per area, however
    many configured areas select it. One of its shared words has to come from the area's route and not only restate the configured
    prefix or module (when the route holds nothing else, its words are all there is to go by)."""
    titles = [words.keyed(c.title) for c in corpus.cases]
    common = words.common_words([set(t) for t in titles])
    out: list[Suggestion] = []
    for info in areas:
        chosen: dict[int, tuple[str, ...]] = {}
        for cfg in configured:
            if not area_matches(cfg, info.area):
                continue
            anchor = (info.route - _restating(cfg)) or info.route
            suites = [s for s in cfg.get("suites") or () if isinstance(s, int)]
            scope = corpus.within(suites) if suites else None
            for i, case in enumerate(corpus.cases):
                if i in chosen or (scope is not None and case.group not in scope):
                    continue
                got = words.shared(titles[i], info.words, common, anchor)
                if got:
                    chosen[i] = got
        for i in sorted(chosen):
            case = corpus.cases[i]
            out.append(Suggestion(source, case.id, case.title, chosen[i], info.area.id))
    return out
