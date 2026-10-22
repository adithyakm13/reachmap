"""Reading a report that has been checked but whose deep payload is not: a part that is missing or of another type is shown as absent
and never guessed. Shared by the view model and the author view. Nothing here does I/O."""

from __future__ import annotations

from typing import Any, Mapping

__all__ = ["blocks", "dct", "integer", "lst", "scope_of", "text"]


def dct(v: Any) -> Mapping[str, Any]:
    return v if isinstance(v, dict) else {}


def lst(v: Any) -> list[Any]:
    return v if isinstance(v, list) else []


def text(v: Any) -> str:
    return v if isinstance(v, str) else ("" if v is None else str(v))


def integer(v: Any) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


def scope_of(record: Mapping[str, Any]) -> str:
    """`selection` or `suggestions`: what a record is about. A record written before `scope` existed is told from its kind."""
    given = record.get("scope")
    if given in ("selection", "suggestions"):
        return given
    from reachmap.errors import ModelError
    from reachmap.incomplete import kind_spec

    try:
        return kind_spec(text(record.get("kind"))).scope
    except ModelError:
        return "selection"


def blocks(record: Mapping[str, Any]) -> tuple[bool, bool]:
    """(blocks selection, blocks suggestions) of a record of a report. A record written before `scope` existed has neither key: a record
    of a test source then said it blocked selection, and it is read as blocking suggestions, which is what it always meant."""
    selection = record.get("blocks_selection") is True
    if "scope" in record:
        return selection, record.get("blocks_suggestions") is True
    return (False, selection) if scope_of(record) == "suggestions" else (selection, False)
