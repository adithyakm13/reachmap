"""Showing text that came from somewhere else (a source line, a path, a record's detail) without sending it: control, bidi
and zero-width characters, line separators and lone surrogates are written out as `\\xNN` or `\\uNNNN` so that they can be read and
cannot act (an escape sequence in a terminal, a right-to-left override that reorders a path, a surrogate no file can hold), and a
very long line is cut and says so. The full text stays in the report JSON.

Nothing here does I/O.
"""

from __future__ import annotations

import unicodedata

__all__ = ["CONTENT_LIMIT", "clip", "one_line", "show", "visible"]

CONTENT_LIMIT = 2000  # characters of one piece of content shown in a rendering; the JSON keeps all of it
_KEEP = frozenset("\t\n")
_ODD = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})


def _escape(ch: str) -> str:
    n = ord(ch)
    if n < 0x100:
        return f"\\x{n:02x}"
    if n < 0x10000:
        return f"\\u{n:04x}"
    return f"\\U{n:08x}"


def visible(text: str) -> str:
    """`text` with every character that could act without being seen written out (tab and newline stay)."""
    if text.isascii() and text.isprintable():
        return text
    return "".join(_escape(ch) if ch not in _KEEP and unicodedata.category(ch) in _ODD else ch for ch in text)


def clip(text: str, limit: int = CONTENT_LIMIT) -> str:
    """The first `limit` characters and, when there were more, a marker that says how many were left out."""
    if len(text) <= limit:
        return text
    more = len(text) - limit
    return f"{text[:limit]} [cut: {more} more character{'s' if more != 1 else ''} in the report JSON]"


def show(text: str, limit: int = CONTENT_LIMIT) -> str:
    """What a rendering shows of a piece of content: cut first (a 2 MB line is never scanned character by character), then made visible."""
    if len(text) > limit:
        more = len(text) - limit
        return visible(text[:limit]) + f" [cut: {more} more character{'s' if more != 1 else ''} in the report JSON]"
    return visible(text)


def one_line(text: str) -> str:
    """Whitespace of every kind (a newline included) as one space, so that content cannot start a line of its own."""
    return " ".join(text.split())
