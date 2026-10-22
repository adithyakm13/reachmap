"""The decisions file: what the HTML report's "Export decisions" button downloads and `--decisions` (or `reachmap decide --import`) reads.

```json
{
  "format": "reachmap-decisions",
  "version": 1,
  "report": {"key": {"workspace": "...", "repo": "...", "change": "..."}, "head_sha": "<commit analysed>", "revision": 2},
  "exported_at": "2026-02-03T04:05:06.789Z",
  "decisions": [
    {"target_id": "a-...", "verdict": "skip", "reason": "...", "author": "...", "fingerprint": "<64 hex>", "created_at": "2026-02-03T04:00:00.000Z"}
  ]
}
```

The fingerprint is the one the page read from the report (the `fingerprint` of the area or finding in the report it was made from); the page never computes one. An import
keeps it, so a decision made on an older revision is judged against what the person saw. The decisions are stamped with the channel
`html-import` (a channel in the file is not a key of the format and is refused) and the revision of the file's `report`.

The reader is strict: exactly these keys, the key of the analysis it is read for (a file for another one is refused with a message), a
target id of an area or a finding with a verdict that fits its kind, a reason for skip and reject, a 64 hex fingerprint, times as the store
writes them, text the store can hold (no lone surrogate). The messages name the decision and the rule; they never repeat the file.
Everything in the file but the strings of a decision is a fixed word or a number. Nothing here does I/O.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Mapping

from reachmap.errors import ModelError
from reachmap.model import Decision
from reachmap.report import text as shown
from reachmap.review import store
from reachmap.review.formats import SHA, TextRefused, check_author, check_reason, rule_words

__all__ = ["CHANNEL", "Entry", "Exchange", "ExchangeError", "FORMAT", "MAX_BYTES", "MAX_CHARS", "MAX_DECISIONS", "VERSION", "parse"]

FORMAT = "reachmap-decisions"
VERSION = 1
CHANNEL = "html-import"
MAX_BYTES = 5_000_000  # the size of a file, checked on its bytes before they are decoded (`cli`)
MAX_CHARS = MAX_BYTES  # and of the text `parse` is given (a caller that was not reading a file)
MAX_DECISIONS = 10_000
_TOP = ("format", "version", "report", "exported_at", "decisions")
_REPORT = ("key", "head_sha", "revision")
_KEY = ("workspace", "repo", "change")
_ENTRY = ("target_id", "verdict", "reason", "author", "fingerprint", "created_at")


class ExchangeError(ValueError):
    """The decisions file cannot be used. The message says which decision and which rule, in this tool's words."""


@dataclass(frozen=True, slots=True)
class Entry:
    target_id: str
    verdict: str
    reason: str
    author: str
    fingerprint: str
    created_at: str


@dataclass(frozen=True, slots=True)
class Exchange:
    key: dict[str, str]
    head_sha: str
    revision: int
    exported_at: str
    decisions: tuple[Entry, ...]

    def require_key(self, key: Mapping[str, str]) -> None:
        """Refuse a file made for another analysis (another workspace, repository or change)."""
        if all(self.key[k] == key[k] for k in _KEY):
            return
        mine = ", ".join(f"{k} {shown.show(key[k], 80)}" for k in _KEY)
        theirs = ", ".join(f"{k} {shown.show(self.key[k], 80)}" for k in _KEY)
        raise ExchangeError(f"this file is for another analysis: it names ({theirs}) and this one is ({mine}); nothing was imported")

    def to_stored(self) -> list[store.Stored]:
        """The decisions as the store keeps them: channel `html-import`, the exported fingerprint, the file's revision, the page's time."""
        return [
            store.Stored(Decision(e.target_id, e.verdict, e.reason, e.author, CHANNEL, e.fingerprint, self.revision), e.created_at)
            for e in self.decisions
        ]


def _need(ok: bool, where: str, rule: str) -> None:
    if not ok:
        raise ExchangeError(f"{where}: {rule}")


def _exact(obj: Any, keys: tuple[str, ...], where: str) -> Mapping[str, Any]:
    _need(isinstance(obj, dict), where, "must be an object")
    _need(tuple(sorted(obj)) == tuple(sorted(keys)), where, f"must have exactly the keys {', '.join(keys)}")
    return obj


def _time(value: Any, where: str) -> str:
    try:
        return store.validate_time(value)
    except store.StoreError:
        raise ExchangeError(f"{where}: must be a time like 2026-02-03T04:05:06Z (UTC)") from None


def _text(value: Any, where: str, limit: int) -> str:
    _need(isinstance(value, str), where, "must be text")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ExchangeError(f"{where}: must be text without a lone surrogate") from None
    _need(len(value) <= limit, where, f"is at most {limit} characters")
    return value


def parse(raw: str) -> Exchange:
    """A decisions file, checked. `ExchangeError` for anything the rules above do not allow."""
    _need(isinstance(raw, str), "the file", "must be text")
    _need(len(raw) <= MAX_CHARS, "the file", f"is too big (at most {MAX_CHARS} characters)")

    def refuse(_name: str) -> Any:
        raise ExchangeError("the file: a number that JSON does not have (NaN or Infinity) is not allowed")

    def number(literal: str) -> float:
        value = float(literal)
        if not math.isfinite(value):
            raise ExchangeError("the file: a number is too large")
        return value

    try:
        data = json.loads(raw, parse_constant=refuse, parse_float=number)
    except RecursionError:
        raise ExchangeError("the file: JSON nested too deeply") from None
    except ExchangeError:
        raise
    except ValueError:
        raise ExchangeError("the file: not JSON (the Export decisions button of the HTML report writes the right file)") from None
    top = _exact(data, _TOP, "the file")
    _need(top["format"] == FORMAT, "format", f"must be {FORMAT}")
    _need(top["version"] == VERSION and not isinstance(top["version"], bool), "version", f"must be {VERSION}")
    report = _exact(top["report"], _REPORT, "report")
    key = _exact(report["key"], _KEY, "report.key")
    for name in _KEY:
        _need(isinstance(key[name], str) and key[name] != "", f"report.key.{name}", "must be text that is not empty")
        _text(key[name], f"report.key.{name}", 1000)
    _need(isinstance(report["head_sha"], str) and bool(SHA.match(report["head_sha"])), "report.head_sha", "must be a full commit id")
    rev = report["revision"]
    _need(isinstance(rev, int) and not isinstance(rev, bool) and rev >= 1, "report.revision", "must be a whole number from 1")
    exported = _time(top["exported_at"], "exported_at")
    items = top["decisions"]
    _need(isinstance(items, list), "decisions", "must be a list")
    _need(len(items) <= MAX_DECISIONS, "decisions", f"at most {MAX_DECISIONS} at a time")
    entries: list[Entry] = []
    for i, item in enumerate(items, 1):
        where = f"decision {i}"
        d = _exact(item, _ENTRY, where)
        entry = Entry(
            _text(d["target_id"], f"{where}.target_id", 40), _text(d["verdict"], f"{where}.verdict", 40), _text(d["reason"], f"{where}.reason", store.MAX_REASON),
            _text(d["author"], f"{where}.author", store.MAX_AUTHOR), _text(d["fingerprint"], f"{where}.fingerprint", 64), _time(d["created_at"], f"{where}.created_at"),
        )
        for field, check, value in (("reason", check_reason, entry.reason), ("author", check_author, entry.author)):
            try:  # the one rule for the text of a decision, in every channel
                check(value)
            except TextRefused as exc:
                raise ExchangeError(f"{where}.{field}: {exc}") from None
        try:  # the model's own rules: the id of an area or finding, a verdict that fits it, a reason where one is needed, a 64 hex fingerprint
            Decision(entry.target_id, entry.verdict, entry.reason, entry.author, CHANNEL, entry.fingerprint, rev)
        except ModelError as exc:
            raise ExchangeError(f"{where}: {rule_words(exc)}") from None
        entries.append(entry)
    return Exchange({k: key[k] for k in _KEY}, report["head_sha"], rev, exported, tuple(entries))
