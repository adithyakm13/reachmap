"""A strict JSON reader that keeps the line of every key and every value.

An OpenAPI operation is cited at a line of the spec (the line of its method key), so the reader the spec adapter uses has to
know where each key sits; `json.loads` does not say. This is a small recursive-descent reader over a few regular
expressions (the text is scanned once; a line is found by bisecting the offsets of the line breaks), written for the
rules of this tool and not as a general parser:

- RFC 8259 and nothing else: no comments, no trailing comma, no single quotes, no `NaN`, no leading zero.
- A line is counted by `\\n` only, the way git and `str.split("\\n")` count (a lone `\\r`, and U+2028 inside a string, are
  not line breaks), so a line number here is the line of the evidence a report cites.
- A duplicate key in one object is refused: which of the two a reader keeps differs between readers, so the document says
  nothing certain about that member.
- A nesting deeper than `MAX_DEPTH` is refused (a `JsonError`, never a `RecursionError`).
- Every failure is a `JsonError` with the line, so a caller can say the spec could not be read; nothing here returns an
  empty result for text that did not parse.
"""

from __future__ import annotations

import json
import re
from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Any

__all__ = ["JsonError", "MAX_DEPTH", "Member", "Node", "parse"]

MAX_DEPTH = 100

_WS = re.compile(r"[ \t\r\n]*")
_STRING = re.compile(r'"((?:[^"\\\x00-\x1f]|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*)"')
_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
_WORDS = {"true": True, "false": False, "null": None}
_AFTER_NUMBER = re.compile(r"[ \t\r\n,\]}]|$")


class JsonError(ValueError):
    """The text is not JSON this reader accepts. `line` and `column` are 1-based."""

    def __init__(self, message: str, line: int = 1, column: int = 1) -> None:
        super().__init__(f"{message} (line {line}, column {column})")
        self.line = line
        self.column = column
        self.reason = message


@dataclass(slots=True)
class Member:
    """One `"key": value` of an object: the key and the line the key is on."""

    key: str
    key_line: int
    node: "Node"


@dataclass(slots=True)
class Node:
    """A JSON value with the lines it spans. `kind` is object, array, string, number, bool or null. For an object `members` holds
    its members in order, for an array `items` holds its elements, for a scalar `value` holds the Python value."""

    kind: str
    line: int
    end_line: int
    value: Any = None
    members: list[Member] | None = None
    items: list["Node"] | None = None
    index: dict[str, Member] | None = field(default=None, repr=False)  # key to member, for an object

    def get(self, key: str) -> "Node | None":
        """The value of a member of an object, None when this is not an object or has no such member."""
        m = self.member(key)
        return m.node if m is not None else None

    def member(self, key: str) -> Member | None:
        return self.index.get(key) if self.index is not None else None


class _Reader:
    def __init__(self, text: str) -> None:
        self.text = text
        self.breaks = [m.start() for m in re.finditer("\n", text)]
        self.pos = 0

    def line(self, offset: int) -> int:
        return bisect_right(self.breaks, offset - 1) + 1

    def column(self, offset: int) -> int:
        i = bisect_right(self.breaks, offset - 1) - 1
        return offset - (self.breaks[i] + 1 if i >= 0 else 0) + 1

    def fail(self, message: str, offset: int | None = None) -> JsonError:
        at = self.pos if offset is None else offset
        at = min(at, len(self.text))
        return JsonError(message, self.line(at), self.column(at))

    def skip(self) -> None:
        self.pos = _WS.match(self.text, self.pos).end()  # type: ignore[union-attr]

    def value(self, depth: int) -> Node:
        self.skip()
        text, start = self.text, self.pos
        if start >= len(text):
            raise self.fail("expected a value, found the end of the text")
        ch = text[start]
        if ch == "{":
            return self.object(depth)
        if ch == "[":
            return self.array(depth)
        if ch == '"':
            raw, end = self.string()
            return Node("string", self.line(start), self.line(end - 1), raw)
        m = _NUMBER.match(text, start)
        if m is not None and _AFTER_NUMBER.match(text, m.end()):
            literal = m.group()
            number: Any = float(literal) if any(c in literal for c in ".eE") else int(literal)
            self.pos = m.end()
            return Node("number", self.line(start), self.line(m.end() - 1), number)
        for word, py in _WORDS.items():
            if text.startswith(word, start) and _AFTER_NUMBER.match(text, start + len(word)):
                self.pos = start + len(word)
                return Node("null" if py is None else "bool", self.line(start), self.line(self.pos - 1), py)
        raise self.fail("expected a value", start)

    def string(self) -> tuple[str, int]:
        m = _STRING.match(self.text, self.pos)
        if m is None:
            # say what is wrong with it: unterminated, a control character, or a bad escape
            end = self.pos + 1
            while end < len(self.text) and self.text[end] != '"':
                if self.text[end] == "\\":
                    end += 1
                end += 1
            if end >= len(self.text):
                raise self.fail("unterminated string", self.pos)
            raise self.fail("bad string: a control character or an escape JSON does not have", self.pos)
        self.pos = m.end()
        raw = m.group(1)
        return (json.loads(m.group()) if "\\" in raw else raw), m.end()

    def object(self, depth: int) -> Node:
        if depth >= MAX_DEPTH:
            raise self.fail(f"nested deeper than {MAX_DEPTH} levels")
        text, start = self.text, self.pos
        self.pos += 1
        members: list[Member] = []
        seen: set[str] = set()
        self.skip()
        if self.pos < len(text) and text[self.pos] == "}":
            self.pos += 1
            return Node("object", self.line(start), self.line(self.pos - 1), None, members, None, {})
        while True:
            self.skip()
            if self.pos >= len(text):
                raise self.fail("expected a string key, found the end of the text")
            if text[self.pos] != '"':
                raise self.fail("expected a string key")
            key_at = self.pos
            key, _end = self.string()
            if key in seen:
                raise self.fail(f"duplicate key {key!r}: which value counts is not defined", key_at)
            seen.add(key)
            self.skip()
            if self.pos >= len(text) or text[self.pos] != ":":
                raise self.fail("expected : after a key" if self.pos < len(text) else "expected :, found the end of the text")
            self.pos += 1
            node = self.value(depth + 1)
            members.append(Member(key, self.line(key_at), node))
            self.skip()
            if self.pos >= len(text):
                raise self.fail("expected , or }, found the end of the text")
            if text[self.pos] == ",":
                self.pos += 1
                continue
            if text[self.pos] == "}":
                self.pos += 1
                return Node("object", self.line(start), self.line(self.pos - 1), None, members, None, {m.key: m for m in members})
            raise self.fail("expected , or }")

    def array(self, depth: int) -> Node:
        if depth >= MAX_DEPTH:
            raise self.fail(f"nested deeper than {MAX_DEPTH} levels")
        text, start = self.text, self.pos
        self.pos += 1
        items: list[Node] = []
        self.skip()
        if self.pos < len(text) and text[self.pos] == "]":
            self.pos += 1
            return Node("array", self.line(start), self.line(self.pos - 1), None, None, items)
        while True:
            items.append(self.value(depth + 1))
            self.skip()
            if self.pos >= len(text):
                raise self.fail("expected , or ], found the end of the text")
            if text[self.pos] == ",":
                self.pos += 1
                continue
            if text[self.pos] == "]":
                self.pos += 1
                return Node("array", self.line(start), self.line(self.pos - 1), None, None, items)
            raise self.fail("expected , or ]")


def parse(text: str) -> Node:
    """The document as a tree of `Node`s with lines. Raises `JsonError`."""
    if not isinstance(text, str):
        raise JsonError("the input is not text")
    if text.startswith("\ufeff"):
        text = text[1:]  # a byte order mark is not part of the document and not a column of line 1
    reader = _Reader(text)
    reader.skip()
    if reader.pos >= len(text):
        raise reader.fail("the document is empty")
    node = reader.value(0)
    reader.skip()
    if reader.pos < len(text):
        raise reader.fail("unexpected text after the value")
    return node
