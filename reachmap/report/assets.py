"""The static template of the HTML report: `assets/report.html`, `report.css` and `report.js`, inlined into the page at render time.

They are the only files a renderer reads. Read once per process (`clear_cache` for the tests).
"""

from __future__ import annotations

from importlib import resources

__all__ = ["clear_cache", "read"]

_CACHE: dict[str, str] = {}
NAMES = ("report.html", "report.css", "report.js")


def read(name: str) -> str:
    if name not in NAMES:
        raise ValueError(f"{name} is not an asset of the report")
    if name not in _CACHE:
        _CACHE[name] = resources.files("reachmap.report").joinpath("assets", name).read_text(encoding="utf-8")
    return _CACHE[name]


def clear_cache() -> None:
    _CACHE.clear()
