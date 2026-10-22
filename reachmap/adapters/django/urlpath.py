"""Django route strings and regular expressions as the paths the rest of Reachmap matches: `{param}` for a parameter, `{rest:path}` for a
parameter that takes the rest of the URL, `<unknown:TEXT>` for a segment that cannot be evaluated statically.

`convert_route` reads the syntax of `path()` (`<int:pk>`, `<slug:s>`, `<name>`, `<path:rest>`). `convert_regex` reads what `re_path()` (and the
old `url()`) can be reduced to: literal segments, a group that makes a whole segment (named: a parameter; unnamed: unknown) and anything else
(an alternation, a quantifier, a character class outside a group, a literal mixed with a group) as an unknown segment. A regex is never
claimed to be exact: its routes are partial (the caller marks them).
"""

from __future__ import annotations

import re

__all__ = ["UNKNOWN", "convert_regex", "convert_route", "has_marker", "marker", "substitute"]

UNKNOWN = "<unknown:"
_CONVERTER = re.compile(r"<(?:(?P<converter>[^>:]+):)?(?P<parameter>[^>]+)>")
_LITERAL = re.compile(r"^[A-Za-z0-9_~%.,;@!&=+*'()-]*$")
_SENTINEL = re.compile("\x00(\\d+)\x00")


def marker(index: int) -> str:
    """The stand-in for the `index`th unknown part of an evaluated string while it is converted (it holds no `<`, `/` or regex syntax)."""
    return f"\x00{index}\x00"


def has_marker(text: str) -> bool:
    return _SENTINEL.search(text) is not None


def substitute(text: str, unknown: tuple[str, ...]) -> str:
    """Put `<unknown:EXPR>` where the stand-ins are."""
    return _SENTINEL.sub(lambda m: f"{UNKNOWN}{unknown[int(m.group(1))]}>" if int(m.group(1)) < len(unknown) else m.group(0), text)


def _clean(text: str) -> str:
    """Text for inside `<unknown:...>`: no slash (it would split the segment), no newline."""
    return " ".join(text.split()).replace("/", "%2F")


def convert_route(route: str) -> str:
    """The `path()` syntax as a path: `items/<int:pk>/` is `items/{pk}/`; a `path` converter takes the rest."""

    def sub(m: re.Match[str]) -> str:
        name = m.group("parameter")
        return f"{{{name}:path}}" if m.group("converter") == "path" else f"{{{name}}}"

    return _CONVERTER.sub(sub, route)


def _split(regex: str) -> list[str]:
    """The regex cut at the slashes that are outside every group and every character class."""
    out: list[str] = []
    cur: list[str] = []
    depth, in_class, i = 0, False, 0
    while i < len(regex):
        ch = regex[i]
        if ch == "\\" and i + 1 < len(regex):
            cur.append(regex[i:i + 2])
            i += 2
            continue
        if in_class:
            in_class = ch != "]"
        elif ch == "[":
            in_class = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch == "/" and depth == 0:
            out.append("".join(cur))
            cur = []
            i += 1
            continue
        cur.append(ch)
        i += 1
    out.append("".join(cur))
    return out


def _group(segment: str) -> tuple[str | None, str] | None:
    """(name, body) when the segment is exactly one group (`name` None for an unnamed one), else None."""
    if not segment.startswith("(") or not segment.endswith(")"):
        return None
    depth, in_class, i = 0, False, 0
    while i < len(segment):
        ch = segment[i]
        if ch == "\\":
            i += 2
            continue
        if in_class:
            in_class = ch != "]"
        elif ch == "[":
            in_class = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0 and i != len(segment) - 1:
                return None  # the first group closes before the end: a literal or another group follows it
        i += 1
    inner = segment[1:-1]
    named = re.match(r"\?P<([A-Za-z_][A-Za-z0-9_]*)>(.*)$", inner, re.S)
    if named:
        return named.group(1), named.group(2)
    return None, inner


def _unescape(segment: str) -> str | None:
    """The literal a segment spells (`\\.` is a dot), or None when it holds regex syntax."""
    out: list[str] = []
    i = 0
    while i < len(segment):
        ch = segment[i]
        if ch == "\\":
            if i + 1 >= len(segment) or segment[i + 1].isalnum():
                return None  # a class such as \d or \w
            out.append(segment[i + 1])
            i += 2
            continue
        if ch in "()[]{}|?*+.^$":
            return None
        out.append(ch)
        i += 1
    text = "".join(out)
    return text if _LITERAL.match(text) else None


def _takes_the_rest(body: str) -> bool:
    """Whether a named group can match a slash: it holds one outside a character class, or `.` repeated."""
    bare = re.sub(r"\[[^\]]*\]", "", body)
    return "/" in bare or re.search(r"(?<!\\)\.[*+]", bare) is not None


def convert_regex(regex: str) -> str:
    """A `re_path()` regex as a path; see the module docstring. A leading `^`, a trailing `$` and an optional final slash are dropped."""
    text = regex[1:] if regex.startswith("^") else regex
    if text.endswith("$"):
        text = text[:-1]
    if text.endswith("/?"):
        text = text[:-2]
    parts: list[str] = []
    for segment in _split(text):
        if segment == "":
            parts.append("")
            continue
        group = _group(segment)
        if group is not None:
            name, body = group
            if name is None:
                parts.append(f"{UNKNOWN}{_clean(segment)}>")
            else:
                parts.append(f"{{{name}:path}}" if _takes_the_rest(body) else f"{{{name}}}")
            continue
        literal = _unescape(segment)
        parts.append(literal if literal is not None else f"{UNKNOWN}{_clean(segment)}>")
    return "/".join(parts)
