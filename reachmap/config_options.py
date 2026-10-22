"""Option validators for the repo sub-tables that the core and the first adapters read: `entry_points` (`react-router`, `fastapi`,
`celery`), `http_client` (`endpoints-file`), `contract` (`pydantic`), `context` (`jira-snapshot`) and the two tables that have no kind,
`tests` and `aliases`.

Each validator is called by `config` as `validator(options, describe)` with the options after `${VAR}` expansion, and returns messages. A
table takes only the keys the adapter reads; any other key is refused with the allowed keys and, when one is close, a did-you-mean
hint (a misspelled `routes_glb` used to be ignored, the default glob was used and the answer was empty). A message names the key and
what was expected; it never quotes a configured value (`describe(value)` gives a type name). A value that is `None` is a `${VAR}` that
was not set: `config` has said so, and the validator leaves it.

The kinds other adapters register (`django`, `nextjs`, `spring`, `express`, `nestjs`, `openapi`) keep their own validators; these are
the ones that had none. The allowed keys are what each adapter reads:

- `entry_points = { kind = "react-router", routes_glob = ... }`: `ReactRouterAdapter.from_config` reads `routes_glob` (a path or glob inside
  the repository, or a list of them).
- `entry_points = { kind = "fastapi" }`, `{ kind = "celery" }`: no option (celery has no adapter yet).
- `http_client = { kind = "endpoints-file", path = ... }`: `EndpointsFileClient.from_config` reads `path`.
- `contract = { kind = "pydantic", root, package, versions, tag_prefix }`: `edges.contract` reads `root` and `package`, `edges.package` reads
  `tag_prefix`; `versions` is the way releases are found (`git-tags`, the only one).
- `tests = { markers, command }`: the language adapters read `markers` (`reachmap.testpaths`), the pipeline reads `command`.
- `aliases = { from }`: `JsAdapter.from_config` reads `from`, the file the import aliases are read from.
- `context = [{ kind = "jira-snapshot", path }]`.
"""

from __future__ import annotations

import difflib
from typing import Any, Callable, Mapping

from reachmap import testpaths

__all__ = ["PLAIN", "VALIDATORS", "inside", "keys"]

Describe = Callable[[Any], str]

ROUTES_KEYS = ("routes_glob",)
ENDPOINTS_KEYS = ("path",)
CONTRACT_KEYS = ("root", "package", "versions", "tag_prefix")
CONTRACT_VERSIONS = ("git-tags",)
TESTS_KEYS = ("markers", "command")
ALIASES_KEYS = ("from",)
JIRA_KEYS = ("path",)


def inside(value: Any) -> bool:
    """A non-empty string that names a path inside the repository: not absolute, no `..`, no backslash, no `~`."""
    if not isinstance(value, str) or not value.strip():
        return False
    return not (value.startswith(("/", "~")) or "\\" in value or any(part == ".." for part in value.split("/")))


def keys(options: Mapping[str, Any], allowed: tuple[str, ...], what: str, *, ignore: tuple[str, ...] = ("kind",)) -> list[str]:
    """One message for each key of `options` that is not in `allowed` (or `ignore`): the allowed keys and the closest one. `what` is
    how the table is called (`a react-router entry_points table`)."""
    out: list[str] = []
    for key in options:
        if key in ignore or key in allowed:
            continue
        hint = difflib.get_close_matches(str(key), allowed, n=1)
        out.append(f"{key}: not a key of {what}; allowed keys: {', '.join(allowed)}" + (f" (did you mean {hint[0]!r}?)" if hint else ""))
    return out


def _no_options(kind: str, section: str) -> Callable[[Mapping[str, Any], Describe], list[str]]:
    def check(options: Mapping[str, Any], describe: Describe) -> list[str]:
        return [f"{key}: not an option of the {kind} {section} (there is none besides kind)" for key in options if key != "kind"]

    return check


def _path(options: Mapping[str, Any], key: str, describe: Describe, out: list[str], what: str = "a path inside the repository") -> None:
    value = options.get(key)
    if value is None:
        return
    if not inside(value):
        out.append(f"{key}: expected {what} (not absolute, no .., no backslash), got {describe(value)}")


def _text(options: Mapping[str, Any], key: str, describe: Describe, out: list[str], *, empty: bool = False) -> None:
    value = options.get(key)
    if value is None:
        return
    if not isinstance(value, str) or (not empty and not value.strip()):
        out.append(f"{key}: expected {'a string' if empty else 'a non-empty string'}, got {describe(value)}")


def validate_react_router(options: Mapping[str, Any], describe: Describe) -> list[str]:
    out = keys(options, ROUTES_KEYS, "a react-router entry_points table")
    glob = options.get("routes_glob")
    if glob is not None:
        if isinstance(glob, (list, tuple)):
            if not glob:
                out.append("routes_glob: expected a path or glob inside the repository, or a list of them, got an empty list")
            elif not all(g is None or inside(g) for g in glob):  # None: a variable that was not set, said by the core
                out.append("routes_glob: every entry must be a path or glob inside the repository (a non-empty string, not absolute, no .., no backslash)")
        elif not inside(glob):
            out.append(f"routes_glob: expected a path or glob inside the repository (not absolute, no .., no backslash), or a list of them, got {describe(glob)}")
    return out


def validate_endpoints_file(options: Mapping[str, Any], describe: Describe) -> list[str]:
    out = keys(options, ENDPOINTS_KEYS, "an endpoints-file http_client table")
    _path(options, "path", describe, out, "the path of the endpoints file inside the repository")
    return out


def validate_pydantic(options: Mapping[str, Any], describe: Describe) -> list[str]:
    out = keys(options, CONTRACT_KEYS, "a pydantic contract table")
    _path(options, "root", describe, out, "the folder of the models inside the repository")
    _text(options, "package", describe, out)
    _text(options, "tag_prefix", describe, out, empty=True)
    versions = options.get("versions")
    if versions is not None and versions not in CONTRACT_VERSIONS:
        out.append(f"versions: expected one of {', '.join(CONTRACT_VERSIONS)}, got {describe(versions)}")
    return out


def validate_jira_snapshot(options: Mapping[str, Any], describe: Describe) -> list[str]:
    out = keys(options, JIRA_KEYS, "a jira-snapshot context table")
    _text(options, "path", describe, out)  # a file on this machine: the core resolves it against the config file
    return out


def validate_tests(options: Mapping[str, Any], describe: Describe) -> list[str]:
    out = keys(options, TESTS_KEYS, "a tests table")
    markers = options.get("markers")
    if markers is not None:
        if not isinstance(markers, (list, tuple)):
            out.append(f"markers: expected a list of test markers (strings such as \"tests/\" or \".spec.\"), got {describe(markers)}")
        else:  # an empty list is allowed: no file is a test (leave the key out to use the defaults of the language)
            for i, marker in enumerate(markers):
                if marker is None:
                    continue  # a variable that was not set: the core says so
                if not isinstance(marker, str):
                    out.append(f"markers[{i}]: expected a string, got {describe(marker)}")
                    continue
                try:
                    testpaths.split_marker(marker)
                except ValueError:
                    out.append(f"markers[{i}]: not a test marker: a folder name ending in a slash (tests/), a file name part (.spec.), a prefix (test_) or a suffix (_test)")
    _text(options, "command", describe, out)
    return out


def validate_aliases(options: Mapping[str, Any], describe: Describe) -> list[str]:
    out = keys(options, ALIASES_KEYS, "an aliases table")
    _path(options, "from", describe, out, "the path of the config the import aliases are read from, inside the repository")
    return out


# the validators of the kinds that had none, by (section, kind); the two tables with no kind are `PLAIN`
VALIDATORS: dict[tuple[str, str], Callable[[Mapping[str, Any], Describe], list[str]]] = {
    ("entry_points", "react-router"): validate_react_router,
    ("entry_points", "fastapi"): _no_options("fastapi", "entry points"),
    ("entry_points", "celery"): _no_options("celery", "entry points"),
    ("http_client", "endpoints-file"): validate_endpoints_file,
    ("contract", "pydantic"): validate_pydantic,
    ("context", "jira-snapshot"): validate_jira_snapshot,
}
PLAIN: dict[str, Callable[[Mapping[str, Any], Describe], list[str]]] = {"tests": validate_tests, "aliases": validate_aliases}
