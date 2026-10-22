"""Option validators for the test source kinds (M15): `qase`, `testrail`, `playwright`, `cypress`.

Each validator is called by `config` as `validator(options, describe)` and returns messages, never putting a configured value in
one. A kind's keys are checked for their type and their range, not for being present: a qase table with no `project`, or no
`token_env`, is a source that is not configured (the run records `source_unconfigured` and says which key is missing), the
same as a source whose variable is unset. The two things a file can get plainly wrong are a spec source with no repository and a
repository that is not configured; the first is checked here, the second by `config` (it knows the repositories).

Every key is checked, and a key the kind does not know is a problem that names the near miss. A spec source also needs a repository
whose `entry_points` make screens (`config` checks it: it knows the repositories).

Keys
- all hosted sources: `token_env` (the NAME of the variable that holds the credential), `base_url`, `page_size`, `max_pages`,
  `areas`.
- `qase`: `project` (the project code).
- `testrail`: `project_id`, `suite_id` (a project in multi-suite mode), `base_url` (the instance, required to run).
- `playwright`, `cypress`: `repo` (where the specs are), `screens_repo` (the repository whose route table the visited paths are
  matched against, `repo` by default), `globs` (which files are specs; a default per framework).
- an area (`[[test_sources.areas]]`; one with neither `route_prefixes` nor `modules` matches no area of a run, which a run notes): `name`, `route_prefixes`, `modules`, `suites` (Qase suite ids, TestRail section ids,
  descendants included).
"""

from __future__ import annotations

import difflib
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

__all__ = ["AREA_KEYS", "SCREEN_ENTRY_KINDS", "SPEC_KINDS", "validators"]

SPEC_KINDS = ("playwright", "cypress")
SCREEN_ENTRY_KINDS = ("react-router", "nextjs")  # the entry_points kinds whose route table has screens (the others have API routes, tasks and jobs)
AREA_KEYS = ("name", "route_prefixes", "modules", "suites")

Describe = Callable[[Any], str]


def _int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _texts(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and all(v is None or _text(v) for v in value)


def _ints(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and all(v is None or _int(v) for v in value)


def _url(value: Any) -> str | None:
    """None when `value` is an http(s) address with no user, password, query or fragment; else why not (without the value)."""
    if not isinstance(value, str):
        return "expected a string"
    try:
        parts = urlsplit(value)
        user, password = parts.username, parts.password
    except ValueError:
        return "is not a valid address"
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc.rpartition("@")[2]:
        return "must be an http or https address"
    if user is not None or password is not None or "@" in parts.netloc:
        return "must not hold a user or a password: name the credential's variable in token_env"
    if parts.query or parts.fragment:
        return "must not hold a query or a fragment"
    return None


_EXPECT = {_int: "a whole number of 1 or more", _text: "a non-empty string", _texts: "a list of non-empty strings", _ints: "a list of whole numbers of 1 or more"}


def _check(options: Mapping[str, Any], describe: Describe, kind: str, keys: Mapping[str, Callable[[Any], bool]], limits: Mapping[str, int],
           areas: bool = True) -> list[str]:
    """A key the kind does not know is a problem (a mistyped `max_page` or `tokn_env` would otherwise be a source quietly read with its
    defaults, or not configured for a reason nobody can see), with the near miss named."""
    out: list[str] = []
    allowed = tuple(k for k in ("areas", *keys) if areas or k != "areas")
    for key in options:
        if key != "kind" and key not in allowed:
            hint = difflib.get_close_matches(key, allowed, n=1)
            out.append(f"{key}: not a key of a {kind} test source; allowed keys: {', '.join(allowed)}" + (f" (did you mean {hint[0]!r}?)" if hint else ""))
    for key, ok in keys.items():
        if key not in options or options[key] is None:  # absent is a source that is not configured; None is an unset variable
            continue
        value = options[key]
        if key == "base_url":
            why = _url(value)
            if why:
                out.append(f"base_url: {why}")
        elif not ok(value):
            out.append(f"{key}: expected {_EXPECT[ok]}, got {describe(value)}")
        elif key in limits and isinstance(value, int) and value > limits[key]:
            out.append(f"{key}: must be at most {limits[key]}")
    return out


def _areas(options: Mapping[str, Any], describe: Describe) -> list[str]:
    areas = options.get("areas")
    out: list[str] = []
    if areas is None:
        return out
    for i, area in enumerate(areas):
        if not isinstance(area, Mapping):
            continue  # `config` already said it is not a table
        for key in area:
            if key not in AREA_KEYS:
                out.append(f"areas[{i}].{key}: not a key of a test source area; allowed keys: {', '.join(AREA_KEYS)}")
        for key, ok in (("route_prefixes", _texts), ("modules", _texts), ("suites", _ints)):
            if key in area and area[key] is not None and not ok(area[key]):
                out.append(f"areas[{i}].{key}: expected {_EXPECT[ok]}, got {describe(area[key])}")
        prefixes = area.get("route_prefixes") or ()
        if _texts(prefixes) and any(isinstance(p, str) and not p.startswith("/") for p in prefixes):
            out.append(f"areas[{i}].route_prefixes: every prefix starts with a slash")
    return out


def _hosted(kind: str, extra: Mapping[str, Callable[[Any], bool]], max_page: int) -> Callable[[Mapping[str, Any], Describe], list[str]]:
    keys = {"token_env": _text, "base_url": _text, "page_size": _int, "max_pages": _int, **extra}

    def validate(options: Mapping[str, Any], describe: Describe) -> list[str]:
        return _check(options, describe, kind, keys, {"page_size": max_page}) + _areas(options, describe)

    return validate


def _spec(kind: str) -> Callable[[Mapping[str, Any], Describe], list[str]]:
    keys = {"repo": _text, "screens_repo": _text, "globs": _texts}

    def validate(options: Mapping[str, Any], describe: Describe) -> list[str]:
        out = _check(options, describe, kind, keys, {}, areas=False)
        if "repo" not in options:
            out.append("repo: missing; name the repository the specs are in")
        if "areas" in options:
            out.append("areas: a spec source maps specs to screens by the route table, so it takes no areas")
        return out

    return validate


def validators() -> dict[str, Callable[[Mapping[str, Any], Describe], list[str]]]:
    return {
        "qase": _hosted("qase", {"project": _text}, 100),
        "testrail": _hosted("testrail", {"project_id": _int, "suite_id": _int}, 250),
        "playwright": _spec("playwright"),
        "cypress": _spec("cypress"),
    }
