"""The HTML rendering of a document: one self-contained page, no network, and report content that cannot become markup (rule 4.8).

The page is a static template (`assets/report.html`, `report.css`, `report.js`, inlined) around two things made here:

- the body: the document serialised with `html.escape(quote=True)` for every piece of text, elements and attribute names only
  from this module, attribute values only fixed words and numbers. It is the whole report without a script;
- the report JSON, embedded in `<script type="application/json" id="report-data">` with `<`, `>`, `&` and every non-ASCII character
  (U+2028 and U+2029 among them) written as `\\u` escapes, so that nothing in it can close the script or open a comment. The
  inline script parses it with `JSON.parse` and only adds interaction (collapsed chains, filters, a theme toggle, printing);
  it builds elements with `createElement` and `textContent`, never from markup.

The Content Security Policy in a `<meta>` names the sha256 of the inline script and has no `connect-src`, so the page cannot
call out even if content tried: `default-src 'none'; style-src 'unsafe-inline'; script-src 'sha256-...'; img-src data:; base-uri 'none';
form-action 'none'`.

Fonts are the system stacks (`system-ui` and `ui-monospace` families): they need no files and make no request. This replaces the
self-hosted fonts of the context notes, which need hosting; the posture is the same (no network call).

Nothing here does I/O but reading its assets.
"""

from __future__ import annotations

import base64
import hashlib
import html as _html
import json
import re
from typing import Any, Mapping

from reachmap.report import assets
from reachmap.report.blocks import (
    Block, Code, CodeBlock, Details, Document, Heading, Item, Items, Para, Section, Strong, Table,
)

__all__ = ["embed", "policy", "render"]

_TOKEN = re.compile(r"\{\{(CSP|TITLE|CSS|BODY|DATA|SCRIPT)\}\}")
_SAFE = re.compile(r"^[a-z0-9_-]+$")


def _t(text: str) -> str:
    return _html.escape(text, quote=True)


def _runs(rs: tuple) -> str:
    out = []
    for r in rs:
        if isinstance(r, Code):
            out.append(f"<code>{_t(r.value)}</code>")
        elif isinstance(r, Strong):
            out.append(f"<strong>{_t(r.value)}</strong>")
        else:
            out.append(_t(r))
    return "".join(out)


def _word(value: str) -> str:
    """A fixed word the layout chose (an id, a class, a data name): never content, and checked to be plain."""
    if not _SAFE.match(value):
        raise ValueError(f"not a plain word: {value!r}")
    return value


def _item(item: Item) -> str:
    data = "".join(f' data-{_word(k)}="{int(v)}"' for k, v in item.data)
    return f"<li{data}>{_runs(item.text)}{''.join(_block(c) for c in item.children)}</li>"


def _block(b: Block) -> str:
    if isinstance(b, Heading):
        ident = f' id="{_word(b.id)}"' if b.id else ""
        return f"<h{b.level}{ident}>{_runs(b.text)}</h{b.level}>"
    if isinstance(b, Para):
        return f"<p>{_runs(b.text)}</p>"
    if isinstance(b, CodeBlock):
        return "<pre><code>" + _t("\n".join(b.lines)) + "</code></pre>"
    if isinstance(b, Items):
        tag = "ol" if b.ordered else "ul"
        return f"<{tag}>{''.join(_item(i) for i in b.items)}</{tag}>"
    if isinstance(b, Details):
        kind = f" {_word(b.kind)}" if b.kind else ""
        return f'<details class="fold{kind}"><summary>{_runs(b.summary)}</summary>{"".join(_block(c) for c in b.children)}</details>'
    if isinstance(b, Table):
        head = "".join(f'<th scope="col">{_t(h)}</th>' for h in b.headers)
        def cells(row: tuple) -> str:
            first = f'<th scope="row">{_runs(row[0])}</th>' if b.row_headers and row else ""
            return first + "".join(f"<td>{_runs(c)}</td>" for c in (row[1:] if first else row))

        rows = "".join(f"<tr>{cells(row)}</tr>" for row in b.rows)
        return f"<table><caption>{_t(b.caption)}</caption><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>"
    if isinstance(b, Section):
        tag = "header" if b.title is None else "section"
        title = "" if b.title is None else f"<h2>{_t(b.title)}</h2>"
        return f'<{tag} id="sec-{_word(b.id)}" class="part">{title}{"".join(_block(c) for c in b.children)}</{tag}>'
    raise TypeError(f"not a block: {b!r}")


def body(doc: Document) -> str:
    return "".join(_block(b) for b in doc.children)


def embed(report: Mapping[str, Any]) -> str:
    """The report as JSON text that is safe inside a script element: ASCII only, and no `<`, `>` or `&`."""
    text = json.dumps(report, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def policy(script: str) -> str:
    digest = base64.b64encode(hashlib.sha256(script.encode("utf-8")).digest()).decode("ascii")
    return f"default-src 'none'; style-src 'unsafe-inline'; script-src 'sha256-{digest}'; img-src data:; base-uri 'none'; form-action 'none'"


def render(doc: Document, report: Mapping[str, Any]) -> str:
    script = assets.read("report.js")
    values = {
        "CSP": policy(script), "TITLE": _t("".join(r if isinstance(r, str) else r.value for r in doc.title)),
        "CSS": assets.read("report.css"), "BODY": body(doc), "DATA": embed(report), "SCRIPT": script,
    }
    # one pass over the template: what is put in is never scanned for a token
    return _TOKEN.sub(lambda m: values[m.group(1)], assets.read("report.html"))
