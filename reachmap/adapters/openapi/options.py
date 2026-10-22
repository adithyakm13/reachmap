"""The options of the two OpenAPI kinds, checked when the config loads (`reachmap.config` calls them as
`validator(options, describe)` and never lets a message quote a configured value).

- `http_server = { kind = "openapi", spec = "api/openapi.json", handlers = "app/handlers/*.py" }` on the repository that serves
  the API. `spec` is the committed spec (JSON is read; a `.yaml` or `.yml` name is refused when the config loads, and a spec that
  becomes YAML later is a blocking `spec_unreadable` at run time), `handlers` the files (a path, a glob, or a
  list of them) that hold the functions the operations are served by; without it no code is tied to an operation and a change to
  code reaches no route, which the run says.
- `http_client = { kind = "openapi", spec = "api/openapi.json", client = "src/api/generated" }` on a repository that calls the API
  through a generated client. `spec` is the copy of the spec the client was generated from, `client` the generated files (a file,
  a folder or a glob, or a list): the code that defines the operations is not a call of them.

Paths are inside the repository: not absolute, no `..`, no backslash.
"""

from __future__ import annotations

import difflib
from typing import Any, Callable, Mapping

__all__ = ["CLIENT_KEYS", "SERVER_KEYS", "as_list", "client", "server"]

SERVER_KEYS = ("spec", "handlers")
CLIENT_KEYS = ("spec", "client")
Describe = Callable[[Any], str]


def _inside(value: Any) -> bool:
    """A non-empty string that names a path inside the repository."""
    if not isinstance(value, str) or not value.strip():
        return False
    return not (value.startswith("/") or "\\" in value or value.startswith("~") or any(p == ".." for p in value.split("/")))


def as_list(value: Any) -> list[str]:
    """The strings of an option that is a string or a list of strings (the config freezes a list into a tuple)."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [v for v in value if isinstance(v, str)]
    return []


def _check(options: Mapping[str, Any], describe: Describe, kind_keys: tuple[str, ...], required: tuple[str, ...], lists: tuple[str, ...], what: str) -> list[str]:
    out: list[str] = []
    for key in options:
        if key != "kind" and key not in kind_keys:
            hint = difflib.get_close_matches(key, kind_keys, n=1)
            out.append(f"{key}: not a key of {what}; allowed keys: {', '.join(kind_keys)}" + (f" (did you mean {hint[0]!r}?)" if hint else ""))
    for key in kind_keys:
        if key not in options:
            if key in required:
                out.append(f"{key}: missing; " + ("name the path of the spec file in the repository" if key == "spec" else "name the generated client files (a file, a folder or a glob)"))
            continue
        value = options[key]
        if value is None:  # an unset variable: `config` says so
            continue
        if key in lists:
            items = value if isinstance(value, (list, tuple)) else [value]
            if isinstance(value, (list, tuple)) and not value:
                out.append(f"{key}: expected a path or a list of paths inside the repository, got an empty list")
            elif not all(_inside(v) for v in items):
                out.append(f"{key}: expected a path or a list of paths inside the repository (not absolute, no .., no backslash), got {describe(value)}")
        elif not _inside(value):
            out.append(f"{key}: expected a path inside the repository (not absolute, no .., no backslash), got {describe(value)}")
        elif key == "spec" and value.lower().endswith((".yaml", ".yml")):
            out.append("spec: YAML is not read; commit a JSON copy of the spec and name it here")
    return out


def server(options: Mapping[str, Any], describe: Describe) -> list[str]:
    return _check(options, describe, SERVER_KEYS, ("spec",), ("handlers",), "an openapi http_server")


def client(options: Mapping[str, Any], describe: Describe) -> list[str]:
    return _check(options, describe, CLIENT_KEYS, ("spec", "client"), ("client",), "an openapi http_client")
