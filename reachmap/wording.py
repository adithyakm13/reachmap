"""Banned wording (context section 4, rule 2): no output claims safety or coverage.

Matching is case-insensitive on word boundaries, so "safe" matches but "unsafe",
"safety" and "safely" do not. Between the words of a phrase any run of whitespace or
hyphens matches ("fully-covered" is "fully covered").

Gaps are worded "no mapped test", never "not covered".

Applied to our own claims (finding title, workflow and confidence basis, incompleteness
text, analysis notes). Not applied to an area label (a route path) or to source code
quoted in evidence: that is code, not our claim.
"""

import re

from reachmap.errors import WordingError

BANNED: tuple[str, ...] = (
    "covered by",
    "already covers",
    "no need to test",
    "nothing to test",
    "safe",
    # close variants
    "is safe",
    "safe to release",
    "fully covered",
    "no impact",
)


def _pattern(phrase: str) -> re.Pattern[str]:
    words = r"[\s-]+".join(re.escape(word) for word in phrase.split())
    return re.compile(rf"\b{words}\b", re.IGNORECASE)


_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple((p, _pattern(p)) for p in BANNED)


def check(text: str) -> list[str]:
    """Return the banned phrases found in text, in the order of BANNED."""
    return [phrase for phrase, pattern in _PATTERNS if pattern.search(text)]


def require_clean(text: str, field: str) -> None:
    """Raise WordingError naming the field and every banned phrase found in text."""
    found = check(text)
    if found:
        phrases = ", ".join(repr(p) for p in found)
        raise WordingError(f"{field}: banned wording {phrases}")
