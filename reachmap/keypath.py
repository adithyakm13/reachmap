"""Dotted key paths for messages: repos."acme/web".roles[1]."""

import re

_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def join(base: str, key: str | int) -> str:
    """Add a key (or a list index) to a path. Keys that are not bare words are quoted."""
    if isinstance(key, int):
        return f"{base}[{key}]"
    part = key if _BARE_KEY.match(key) else '"' + key.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return f"{base}.{part}" if base else part
