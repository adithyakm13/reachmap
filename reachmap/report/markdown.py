"""The Markdown rendering of a document: GitHub-flavoured, readable as plain text, and inert (context rule 4.8).

Content from the report is never trusted to be text:

- paths, labels, branches, commits and every other short value are inline code, the delimiter one backtick longer than the longest
  run inside (a space pads a value that starts or ends with a backtick); source lines are fenced blocks whose fence is longer than
  any backtick run in them;
- any other content is escaped: `\\`, backtick, `*`, `_`, `[`, `]`, `|` and `~` get a backslash, `<`, `>` and `&` become entities (raw
  HTML is never interpreted, link syntax cannot form), a leading `#`, `>`, `-`, `+`, `=` or `1.` is escaped, a newline becomes a
  space (content cannot start a block), and a text that holds a link or an address (`://`, `www.`, `@`) is put in code instead,
  because a reader would turn it into a link;
- in a table cell a `|` is escaped even inside code; `$` is escaped (math) and a text with an emoji shortcode is put in code; a trailing `#` of a
  heading is escaped.

Nothing here does I/O.
"""

from __future__ import annotations

import re

from reachmap.report.blocks import (
    Block, Code, CodeBlock, Details, Document, Heading, Item, Items, Para, Section, Strong, Table,
)

__all__ = ["render"]

_SPECIAL = re.compile(r"([\\`*_\[\]|~$])")
_ENTITIES = {"&": "&amp;", "<": "&lt;", ">": "&gt;"}
_ENTITY = re.compile(r"[&<>]")
_LEAD = re.compile(r"^[#>+=-]")
_LEAD_NUMBER = re.compile(r"^(\d+)([.)])")
_LINKISH = re.compile(r"://|www\.|@|(?i:mailto:)|(?<![A-Za-z0-9/:]):[A-Za-z0-9_+-]+:(?![A-Za-z0-9/:])")  # a link, an address, or an emoji shortcode (`:smile:`): a reader would turn each into something else
_SPACE = re.compile(r"\s+")


def _squash(text: str) -> str:
    return _SPACE.sub(" ", text)


def _escape(text: str) -> str:
    text = _SPECIAL.sub(r"\\\1", _squash(text))
    return _ENTITY.sub(lambda m: _ENTITIES[m.group(0)], text)


def _code(text: str, table: bool = False) -> str:
    text = _squash(text)
    if table:
        text = text.replace("|", "\\|")
    if text == "":
        text = " "
    longest = max((len(r) for r in re.findall(r"`+", text)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if text.startswith("`") or text.endswith("`") or (text.startswith(" ") and text.endswith(" ") and text.strip()) else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _runs(rs: tuple, table: bool = False) -> str:
    out = []
    for r in rs:
        if isinstance(r, Code):
            out.append(_code(r.value, table))
        elif isinstance(r, Strong):
            out.append("**" + _escape(r.value) + "**")
        elif _LINKISH.search(r):
            out.append(_code(r, table))
        else:
            out.append(_escape(r))
    line = "".join(out).lstrip(" ")
    if _LEAD.match(line):
        return "\\" + line
    return _LEAD_NUMBER.sub(r"\1\\\2", line)  # `1.` starts an ordered list: the delimiter is escaped, not the digit


def _fence(lines: list[str]) -> tuple[str, list[str]]:
    flat: list[str] = []
    for line in lines:
        flat.extend(line.split("\n"))
    longest = max((len(r) for line in flat for r in re.findall(r"`+", line)), default=0)
    return "`" * max(3, longest + 1), flat


def _indent(lines: list[str], prefix: str) -> list[str]:
    return [prefix + ln if ln else ln for ln in lines]


def _block(b: Block, top: bool) -> list[str]:
    if isinstance(b, Heading):
        text = _runs(b.text).rstrip()
        return ["#" * b.level + " " + re.sub(r"#+$", lambda m: "\\#" * len(m.group(0)), text)]  # a trailing # would close the heading
    if isinstance(b, Para):
        return [_runs(b.text)]
    if isinstance(b, CodeBlock):
        fence, flat = _fence(list(b.lines))
        return [fence, *flat, fence]
    if isinstance(b, Items):
        out: list[str] = []
        for i, item in enumerate(b.items, 1):
            out += _item(item, f"{i}. " if b.ordered else "- ")
        return out
    if isinstance(b, Details):
        if top:
            return [("**" + _runs(b.summary) + "**")] + [""] + _blocks(b.children, False, top=True)
        return _item(Item(b.summary, b.children), "- ")
    if isinstance(b, Table):
        rows = [b.headers] + [None] + list(b.rows)  # type: ignore[list-item]
        lines = []
        for row in rows:
            if row is None:
                lines.append("| " + " | ".join("---" for _ in b.headers) + " |")
            elif row is b.headers:
                lines.append("| " + " | ".join(_escape(h) for h in b.headers) + " |")
            else:
                lines.append("| " + " | ".join(_runs(c, table=True) for c in row) + " |")
        return lines
    if isinstance(b, Section):
        head = [] if b.title is None else ["## " + _escape(b.title), ""]
        return head + _blocks(b.children, False, top=True)
    raise TypeError(f"not a block: {b!r}")


def _blocks(blocks: tuple[Block, ...], tight: bool, top: bool = False) -> list[str]:
    out: list[str] = []
    for b in blocks:
        if out and not tight:
            out.append("")
        out += _block(b, top)
    return out


def _item(item: Item, marker: str) -> list[str]:
    head = marker + _runs(item.text)
    out = [head]
    pad = " " * len(marker)
    for child in item.children:
        out += _indent(_block(child, False), pad)
    return out


def render(doc: Document) -> str:
    """The Markdown of a document: one trailing newline, no trailing space on any line the tool wrote."""
    lines = _blocks(doc.children, False, top=True)
    return "\n".join(lines).rstrip("\n") + "\n"
