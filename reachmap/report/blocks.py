"""The document a report is laid out as, before it is Markdown or HTML: headings, paragraphs, lists, tables, code blocks and
collapsible details, whose text is a sequence of runs (plain text, inline code, strong text).

Two kinds of text are kept apart by type. `Content` is text copied from the report (a source line, a path, a title, a record's
detail): it is shown whole (cut when very long, odd characters written out) and never checked or altered beyond that. Every other
`str` is something this tool says (a heading, a label, a sentence) and passes the wording check (context rule 4.2) before the
document is handed out: `require_clean`. Never put content into an f-string that becomes a plain `str`; use runs.

Nothing here does I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator, Sequence, Union

from reachmap import wording
from reachmap.report import text

__all__ = [
    "Block", "Code", "CodeBlock", "Content", "Details", "Document", "Heading", "Item", "Items", "Para", "Section", "Strong", "Table",
    "content", "require_clean", "runs", "say", "strings",
]


class Content(str):
    """Text copied from the report. Made by `content()`; a plain `str` is something the tool says."""

    __slots__ = ()


def content(value: Any) -> Content:
    """A piece of the report as it is shown: not a string becomes one, a very long one is cut, odd characters are written out."""
    return Content(text.show(value if isinstance(value, str) else str(value)))


def say(sentence: str) -> str:
    """A sentence this tool says: refused if it holds a banned phrase."""
    wording.require_clean(sentence, "report text")
    return sentence


@dataclass(frozen=True, slots=True)
class Code:
    """Inline code: `value` is content (a path, a label, a sha) or a fixed word."""

    value: str


@dataclass(frozen=True, slots=True)
class Strong:
    value: str


Run = Union[str, Code, Strong]
Runs = tuple[Run, ...]


def runs(*parts: Any) -> Runs:
    """Runs from strings, runs and (nested) lists of runs, in order; None is left out."""
    out: list[Run] = []
    for part in parts:
        if isinstance(part, (str, Code, Strong)):
            out.append(part)
        elif part is not None:
            out.extend(runs(*part))
    return tuple(out)


class Block:
    __slots__ = ()


@dataclass(frozen=True, slots=True)
class Heading(Block):
    level: int
    text: Runs
    id: str = ""


@dataclass(frozen=True, slots=True)
class Para(Block):
    text: Runs


@dataclass(frozen=True, slots=True)
class Item:
    """A list item: its own runs, then blocks nested under it. `data` becomes `data-*` attributes in HTML (numbers only: the
    indices the page's script filters by)."""

    text: Runs
    children: tuple[Block, ...] = ()
    data: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True, slots=True)
class Items(Block):
    items: tuple[Item, ...]
    ordered: bool = False


@dataclass(frozen=True, slots=True)
class Table(Block):
    """`row_headers`: the first cell of every row names the row (a branch, a count), so a page marks it as the header of its row."""

    caption: str
    headers: tuple[str, ...]
    rows: tuple[tuple[Runs, ...], ...]
    row_headers: bool = False


@dataclass(frozen=True, slots=True)
class CodeBlock(Block):
    lines: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Details(Block):
    """Something that can be folded away (a chain, a long list). It starts closed, with or without a script (`kind` is a fixed word
    the page's style and script know it by); printing opens them all."""

    summary: Runs
    children: tuple[Block, ...]
    kind: str = ""


@dataclass(frozen=True, slots=True)
class Section(Block):
    """A part of the report. `id` is one of a fixed set (the page's anchors); `title` is None for the header."""

    id: str
    title: str | None
    children: tuple[Block, ...]


@dataclass(frozen=True, slots=True)
class Document:
    title: Runs
    children: tuple[Block, ...]

    def sections(self) -> tuple[Section, ...]:
        return tuple(b for b in self.children if isinstance(b, Section))


# ------------------------------------------------------------------------------ what the document says


def _run_text(run: Run) -> tuple[str, bool]:
    if isinstance(run, (Code, Strong)):
        return run.value, isinstance(run.value, Content)
    return run, isinstance(run, Content)


def _runs_strings(rs: Sequence[Run]) -> Iterator[tuple[str, bool]]:
    for r in rs:
        yield _run_text(r)


def _walk(node: Any) -> Iterator[tuple[str, bool]]:
    if isinstance(node, Document):
        yield from _runs_strings(node.title)
        for b in node.children:
            yield from _walk(b)
    elif isinstance(node, Heading):
        yield from _runs_strings(node.text)
    elif isinstance(node, Para):
        yield from _runs_strings(node.text)
    elif isinstance(node, Items):
        for item in node.items:
            yield from _walk(item)
    elif isinstance(node, Item):
        yield from _runs_strings(node.text)
        for b in node.children:
            yield from _walk(b)
    elif isinstance(node, Table):
        yield node.caption, False
        for h in node.headers:
            yield h, False
        for row in node.rows:
            for cell in row:
                yield from _runs_strings(cell)
    elif isinstance(node, CodeBlock):
        for line in node.lines:
            yield line, isinstance(line, Content)
    elif isinstance(node, Details):
        yield from _runs_strings(node.summary)
        for b in node.children:
            yield from _walk(b)
    elif isinstance(node, Section):
        if node.title is not None:
            yield node.title, False
        for b in node.children:
            yield from _walk(b)
    else:
        raise TypeError(f"not a block: {node!r}")


def strings(doc: Any) -> Iterator[tuple[str, bool]]:
    """Every piece of text in a document with whether it is content: `(text, is_content)`."""
    return _walk(doc)


def require_clean(doc: Document) -> Document:
    """Refuse a document in which something the tool says holds a banned phrase; content is never checked."""
    for s, is_content in strings(doc):
        if not is_content:
            wording.require_clean(s, "report text")
    return doc
