"""Names: how an operationId is written as a function name, and which names are too generic to link code by.

An operationId such as `getInvoice` becomes `get_invoice` in a Python handler and stays `getInvoice` in a generated
TypeScript client. A name is a link between two pieces of code only when it is distinctive: `get`, `list` or `health`
are defined and called everywhere, so a match on one of them says nothing about the spec (context rule: every name
match has a distinctiveness cap, and the cap counts definitions, not references). `is_generic` is the cheap half of that
cap, decided from the words of the name alone; the other half is the definition count of the language adapter.
"""

from __future__ import annotations

import re

__all__ = ["GENERIC_WORDS", "is_generic", "snake", "words"]

_SPLIT = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|[^A-Za-z0-9]+")
_SEPARATORS = re.compile(r"[^A-Za-z0-9]+")

# Words that name an action, a plain noun of any API or a part of speech. A name made only of these says nothing about
# one operation of one spec.
GENERIC_WORDS = frozenset("""
get list find fetch load read write save create update delete remove add set put post patch send search query run do call
handle process check test show view open close start stop init new make build all one many any each by for with of the to
from in on at id ids data info item items status health healthz ping version index root count total value values name names
result results response responses request requests resource resources page pages entity entities record records object
objects detail details summary default main home me self current entry entries element elements thing things
""".split())


def words(name: str) -> list[str]:
    """The lowercase words of a name written in camelCase, PascalCase, snake_case, kebab-case or with dots."""
    return [w.lower() for w in _SPLIT.split(name) if w]


def snake(name: str) -> str:
    """The name in snake_case: `getInvoiceByID` is `get_invoice_by_id`, `HTMLParser` is `html_parser`."""
    return "_".join(words(name))


def is_generic(name: str) -> bool:
    """Whether the name is made only of generic words (`getAll`, `list`, `health`), or is too short to point at anything."""
    parts = words(name)
    return not parts or len(_SEPARATORS.sub("", name)) < 3 or all(w in GENERIC_WORDS for w in parts)
