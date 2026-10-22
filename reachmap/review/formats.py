"""The shapes the review layer checks in one place: a commit id, the id of an area or a finding, a fingerprint, a time, and the words for a
rule a decision breaks (chosen by the code of the model's error, never by reading its message)."""

from __future__ import annotations

import re
import unicodedata

from reachmap.errors import ModelError

__all__ = ["AUTHOR_WORDS", "REASON_WORDS", "StoreError", "TextRefused", "check_author", "check_reason", "AREA_ID", "FINDING_ID", "FINGERPRINT", "SHA", "TARGET_ID", "TIME", "rule_words"]

class StoreError(ValueError):
    """The store, the revision log or the lock cannot be read or written. The message names the file (never its folder) and says what is wrong."""


SHA = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")
AREA_ID = re.compile(r"^a-[0-9a-f]{16}$")
FINDING_ID = re.compile(r"^f-[0-9a-f]{16}$")
TARGET_ID = re.compile(r"^[af]-[0-9a-f]{16}$")
FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$")

_RULES = {
    "decision_target": "the target is not the id of an area or a finding (a- or f- and 16 hex)",
    "decision_verdict": "the verdict does not fit the target (areas: test, sample, skip; findings: accept, reject)",
    "decision_reason": "a skip or a reject needs a reason",
    "decision_fingerprint": "the fingerprint must be the 64 hex the report exported",
    "decision_author": "the author needs a name",
    "decision_channel": "the channel needs a name",
    "decision_revision": "the revision is a whole number from 1",
}


def rule_words(exc: ModelError) -> str:
    """Which rule a decision broke, in this tool's words: the model's message repeats the value, which is a person's or a file's."""
    return _RULES.get(getattr(exc, "code", None) or "", "the decision is not valid")


AUTHOR_WORDS = "the name cannot hold control, invisible or direction-changing characters"
REASON_WORDS = "the reason cannot hold control characters other than a newline and a tab, or direction-changing characters"
_NEVER_IN_A_NAME = frozenset({"Cc", "Cf", "Zl", "Zp", "Cs"})  # control, format (invisible and direction marks), line and paragraph separators, lone surrogates
_NEVER_IN_A_REASON = frozenset({"Cc", "Zl", "Zp", "Cs"})
_DIRECTION = frozenset("\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")


class TextRefused(ValueError):
    """The text a person typed holds a character that is refused. The message is fixed words: it never repeats the text."""


def check_author(name: str) -> str:
    """An author's name: no control, format (invisible, direction), separator or surrogate character at all. Every channel (the command line,
    the decisions file the page exports, the runner) applies this one rule."""
    if any(unicodedata.category(c) in _NEVER_IN_A_NAME for c in name):
        raise TextRefused(AUTHOR_WORDS)
    return name


def check_reason(text: str) -> str:
    """A reason: a newline and a tab are the only control characters, there is no line or paragraph separator, no lone surrogate and no
    direction-changing character (the invisible joiners of emoji are allowed)."""
    for c in text:
        if c in "\n\t":
            continue
        if unicodedata.category(c) in _NEVER_IN_A_REASON or c in _DIRECTION:
            raise TextRefused(REASON_WORDS)
    return text
