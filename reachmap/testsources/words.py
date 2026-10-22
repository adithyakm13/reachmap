"""The distinctive words rule that ties a test case to an area (context section 5, shape B: widely shared tokens masquerading as
distinctive).

A case is a suggestion for an area only when it shares at least `MIN_SHARED` (two) distinctive words with it, and at least one of them
comes from the area's own label or route and is not the word that only restates which area it is (the configured prefix or module:
a case that shares `billing`, the module, and a symbol the change happens to touch has shown nothing about this screen). A word is
a lowercased token of `MIN_LENGTH` (four) letters or more, taken from the case title and from the area's label, module, route
segments and changed symbol names; it is dropped when it is on the stop list (generic words that name no feature) and when it is in
more than `DF_SHARE` (10 percent) of the fetched cases: such a word is shared by too many cases to point at one thing. The cap counts
cases (a word is counted once however often a title repeats it).

Choices beyond the brief, all tested:
- camel case, snake case and kebab case are split (`formatAmount` is `format` and `amount`);
- words are compared by their stem (`stem`): a plural, `-ed`, `-ing` and `-ly` are folded (`invoices` and `invoice`, `created` and `create`,
  `correctly` and `correct`), with a short list of words that only look inflected (`series`, `canvas`, `alias`, `family`, `nothing`) left
  alone. A stem is only ever compared, never shown: a matched word is shown as the case wrote it, so a stem that is not a word
  (`creat`) cannot reach a report;
- the cap has a floor, `limit = max(DF_SHARE * n, min(DF_FLOOR, n / 3))`: on a short list ten percent is less than one case, so without a
  floor every word would be "in too many cases" and nothing could ever be suggested. The floor is three cases from 9 cases up and a
  third of the cases below that; from 30 cases up the share is the rule. A corpus under `SMALL_CORPUS` (30) cases is said in a note.
"""

from __future__ import annotations

import re
from typing import Iterable, Mapping, Sequence

__all__ = [
    "DF_FLOOR", "DF_SHARE", "MIN_LENGTH", "MIN_SHARED", "SMALL_CORPUS", "STOP_WORDS", "area_words", "common_words", "keyed", "label_path", "route_words", "shared",
    "small_corpus_note", "stem", "tokens",
]

MIN_LENGTH = 4
MIN_SHARED = 2
DF_SHARE = 0.10
DF_FLOOR = 3  # a word in this many cases or fewer is never too common, on a list long enough for it (see the module docstring)
SMALL_CORPUS = 30  # under this many cases the cap is a weak guard, and a run says so

# Words that are in the titles and routes of every product and name no feature: test-writing words, steps, UI furniture and
# interactions, CRUD and code verbs, place words, function words of four letters or more, the irregular forms a stem cannot fold
# (shown, done, given) and a few adverbs and adjectives of the genre (correctly, successful). Lowercase, four letters or more; an
# inflection of a word on the list (created, displayed, handled) is dropped with it, because words are compared by their stem. A
# domain word (invoice, dashboard, payment, ledger) is never on it: an area called `/dashboard` has to be able to name itself.
STOP_WORDS = frozenset("""
    able about above access across component ensure expected should action active actual added after again alert allow also always another appear applies apply
    banner before behave behaviour being below between both button call cancel card case cell change check checkbox choose clear click
    close collapse column come confirm content control copy correct could create current custom data default delete detail dialog
    disabled display done down drawer dropdown each edit either email empty enabled enter entry error even every exist expand expect
    fail failure feature fetch field file filter find first flow follow footer form format found from given grid handle happy have
    header hidden home icon index info input into item just keep kind know label last later layout left like link list load loading
    login logout main make many menu message modal module more most move much must name navigate need never next none note only open
    option other output over page paginate panel part password path perform place please popup press preview primary proper properly
    real refresh remove render report request reset response result retry return route same save scenario screen scroll search
    section select selected send service setting settings show shown side sidebar sign signin signout sort some state
    step still submit subsequent success successful such suite table take tasks test text than that them then there these they this
    those through time title toggle tooltip type under until update upon used user uses using valid validate
    validation value verify view visible want warning were what when where which while will window with within without wizard work
    would your
""".split())

# Words that end like an inflection and are not one: left as they are by `stem`.
STEM_EXCEPTIONS = frozenset("""
    series species news canvas alias atlas bias status address analysis basis process class access bonus focus chaos lens gas
    family apply supply reply early only assembly daily weekly monthly yearly anomaly italy july rally bully jelly belly fly rely ally
    need speed feed indeed seed deed bulletin string thing nothing anything something everything king ring bring spring sing during
    morning evening ceiling sibling darling bed red shed wed hundred sacred naked wicked kindred
""".split())

_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
_CAMEL = re.compile(r"(?<=[a-z0-9à-ɏ])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _undouble(w: str) -> str:
    return w[:-1] if len(w) > 3 and w[-1] == w[-2] and w[-1] not in "lsz" else w


def stem(word: str) -> str:
    """The form a word is compared by: a plural, `-ed`, `-ing` and `-ly` folded and a final `e` dropped (`invoices` and `invoice` are
    `invoic`, `created` and `create` are `creat`). Only ever compared, never shown. Words that only look inflected (`series`, `canvas`,
    `status`, `nothing`) are returned as they are, and no suffix is cut when what is left would be shorter than four letters."""
    w = word
    if w in STEM_EXCEPTIONS or len(w) <= 3:
        return w
    if w.endswith("ies") and len(w) > 4:
        w = w[:-3] + "y"
    elif w.endswith("sses"):
        w = w[:-2]
    elif w.endswith("s") and not w.endswith(("ss", "us", "is", "as")):
        w = w[:-1]
    if w in STEM_EXCEPTIONS:
        return w
    if w.endswith("ly") and len(w) > 5:
        w = w[:-2]
    elif w.endswith("ied") and len(w) > 4:
        w = w[:-3] + "y"
    elif w.endswith("ing") and len(w) > 5 and len(w) - 3 >= 4:
        w = _undouble(w[:-3])
    elif w.endswith("ed") and not w.endswith("eed") and len(w) > 4 and len(w) - 2 >= 4:
        w = _undouble(w[:-2])
    if w.endswith("e") and len(w) > 4:
        w = w[:-1]
    return w


_STOP_STEMS = frozenset(stem(w) for w in STOP_WORDS)


def _generic(word: str) -> bool:
    return word in STOP_WORDS or stem(word) in _STOP_STEMS


def tokens(text: str) -> list[str]:
    """The distinctive-word candidates of a text as it wrote them, in order of appearance without repeats: lowercased, camel/snake/kebab
    split, `MIN_LENGTH` letters or more, not on the stop list (nor an inflection of a word on it)."""
    out: dict[str, None] = {}
    for chunk in _WORD.findall(text or ""):
        for part in _CAMEL.sub(" ", chunk).split():
            word = part.lower()
            if len(word) >= MIN_LENGTH and not _generic(word):
                out[word] = None
    return list(out)


def keyed(text: str) -> dict[str, str]:
    """The words of a text by stem, each with the form that was written first: what a case is compared by and what it is shown as."""
    out: dict[str, str] = {}
    for word in tokens(text):
        out.setdefault(stem(word), word)
    return out


def common_words(cases: Iterable[Iterable[str]]) -> set[str]:
    """The stems that are in too many of the fetched cases to be distinctive: in more than `DF_SHARE` of them, and in more than the
    floor (`min(DF_FLOOR, n / 3)` cases). `cases` are the stem sets of the titles."""
    sets = [set(c) for c in cases]
    if not sets:
        return set()
    count: dict[str, int] = {}
    for words in sets:
        for w in words:
            count[w] = count.get(w, 0) + 1
    limit = max(DF_SHARE * len(sets), min(DF_FLOOR, len(sets) / 3))
    return {w for w, n in count.items() if n > limit}


def small_corpus_note(source: str, cases: int) -> str | None:
    """A sentence for a corpus too short for the document frequency cap to be a good guard against common words, else None."""
    if cases >= SMALL_CORPUS:
        return None
    if cases == 0:
        return f"{source} listed no case, so the document frequency cap had nothing to count"
    return (f"{source} listed only {cases} cases (under {SMALL_CORPUS}), so the document frequency cap, which drops a word in more than "
            "10 percent of them, is a weak guard against common words")


def shared(case: Mapping[str, str], area: Iterable[str], common: Iterable[str] = (), anchor: Iterable[str] | None = None) -> tuple[str, ...]:
    """The distinctive words a case (`keyed` text) and an area (stems) share, as the case wrote them, in the order of their stems; empty
    unless there are at least `MIN_SHARED` of them and, when `anchor` is given, at least one is a stem of the anchor (the words of the
    area's label or route that do not only restate which area it is)."""
    skip = set(common)
    both = sorted(k for k in set(case) & set(area) if k not in skip and k not in _STOP_STEMS and case[k] not in STOP_WORDS)
    if len(both) < MIN_SHARED:
        return ()
    if anchor is not None and not (set(both) & set(anchor)):
        return ()
    return tuple(case[k] for k in both)


def label_path(label: str) -> str:
    """An API area's label is `METHOD /path`; a screen's is the path."""
    head, _, rest = label.partition(" ")
    return rest if rest.startswith("/") and head.isupper() else label


def route_words(*, label: str, also_mounted: Sequence[str]) -> frozenset[str]:
    """The stems of the words of an area's route: its label (the route path, without an HTTP method) and the other paths it is mounted at."""
    return frozenset(stem(w) for text in (label_path(label), *also_mounted) for w in tokens(text))


def area_words(*, label: str, module: str, also_mounted: Sequence[str], symbols: Sequence[str]) -> frozenset[str]:
    """The stems of the words of an area: its route, its module and the names of the symbols the change touches that reach it."""
    return route_words(label=label, also_mounted=also_mounted) | frozenset(stem(w) for text in (module, *symbols) for w in tokens(text))
