"""The entry-point adapters by kind, and the whole in-repo step: change analysis to areas.

`entry_adapter(repo_config, language)` is the registry: the config's `entry_points.kind` picks the adapter
(`react-router`, `fastapi`, `django`); a kind with no adapter yet (`celery`) or a repository with no `entry_points` has none
(None), which is a configuration answer, not a failure. `reach` runs the walk (`reachmap.walk`) from a language
adapter's `ChangeAnalysis` to the entry-point files, turns the chains into areas, and sets their priorities with the
model's `assign_priorities`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable

from reachmap import config
from reachmap.adapters import Reached
from reachmap.adapters.base import record_quoted
from reachmap.adapters.django import options as django_options
from reachmap.model import Area, assign_priorities
from reachmap.walk import WalkResult, walk

__all__ = ["EntryResult", "KINDS", "check_unmatched", "entry_adapter", "reach", "register"]

Factory = Callable[..., Any]
_FACTORIES: dict[str, Factory] = {}


def register(kind: str, factory: Factory) -> None:
    """Add an entry-point kind: `factory(repo_config, language_adapter, record_to=None)` returns the adapter."""
    _FACTORIES[kind] = factory


def _react_router(repo: config.Repo, language: Any, record_to: Any = None) -> Any:
    from reachmap.adapters.js import JsAdapter
    from reachmap.adapters.react_router import ReactRouterAdapter

    if not isinstance(language, JsAdapter):
        raise TypeError("the react-router entry points are read with the JavaScript adapter")
    return ReactRouterAdapter.from_config(repo, language, record_to=record_to)


def _fastapi(repo: config.Repo, language: Any, record_to: Any = None) -> Any:
    from reachmap.adapters.fastapi import FastApiAdapter
    from reachmap.adapters.python import PyAdapter

    if not isinstance(language, PyAdapter):
        raise TypeError("the fastapi entry points are read with the Python adapter")
    return FastApiAdapter.from_config(repo, language, record_to=record_to)


def _django(repo: config.Repo, language: Any, record_to: Any = None) -> Any:
    from reachmap.adapters.django import DjangoAdapter
    from reachmap.adapters.python import PyAdapter

    if not isinstance(language, PyAdapter):
        raise TypeError("the django entry points are read with the Python adapter")
    return DjangoAdapter.from_config(repo, language, record_to=record_to)


def _openapi(repo: config.Repo, language: Any, record_to: Any = None) -> Any:
    from reachmap.adapters.openapi.server import OpenApiServer

    return OpenApiServer.from_config(repo, language, record_to=record_to)


register("react-router", _react_router)
register("fastapi", _fastapi)
register("django", _django)
config.register_kind("entry_points", "django", django_options.validate)
register("openapi", _openapi)
from reachmap.adapters.nextjs import register as _register_nextjs  # noqa: E402  (M16: Next.js routes, a screen and API route kind)
_register_nextjs(register)
from reachmap.adapters.spring import register as _register_spring  # noqa: E402  (M16: Spring Boot, an API route kind read with the Java and Kotlin adapter)
_register_spring(register)
from reachmap.adapters.express import register as _register_express  # noqa: E402  (M16: Express and NestJS, both API route kinds)
_register_express(register)
KINDS = tuple(_FACTORIES)


def entry_adapter(repo: config.Repo, language: Any, *, record_to: Any = None) -> Any | None:
    """The entry-point adapter a repository's config selects, built on its language adapter; None when the
    repository declares no `entry_points` or its kind has no adapter."""
    for section in (repo.entry_points, getattr(repo, "http_server", None)):  # `entry_points` first; a committed spec (`http_server`) when it has no adapter
        kind = (section or {}).get("kind")
        factory = _FACTORIES.get(kind) if isinstance(kind, str) else None
        if factory is not None:
            return factory(repo, language, record_to=record_to)
    return None


@dataclass(frozen=True, slots=True)
class EntryResult:
    """What the in-repo step found: the walk (chains, and whether it is complete), the areas reached, and whether the
    whole answer is complete: False when the walk was cut (a cap, a hub, an importer list that is not whole), when an
    entry-point adapter could not read or parse what it needed (`problems`), or when an area's own chains were not
    established completely. `reasons` says why; a short list of areas is never "the" list when this is False."""

    walk: WalkResult
    reached: tuple[Reached, ...]
    complete: bool = True  # the walk, the route tables and every area were established completely
    reasons: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()  # what the entry-point adapter says about how it reached the areas (a layout that wraps many pages); the run's notes
    findings: tuple[Any, ...] = ()  # finding inputs the entry-point adapter made of the change itself (a spec diff); the walker adds them to the run's

    @property
    def areas(self) -> tuple[Area, ...]:
        return tuple(r.area for r in self.reached)


def _code_changed(analysis: Any) -> bool:
    """Whether the change has code the walk looks at (a changed source file with symbols): only then does an entry-point adapter's empty answer
    mean something. A change with no code reaches no one, whatever the routing files are."""
    return any(i.role != "test" and i.symbols for i in analysis.impacts)


def _moved(change: Any, before: list[str]) -> tuple[str, str]:
    """What the change did to the routing files that matched before it: the wording and the quoted names (a rename names its target)."""
    renames = {fc.old_path: fc.path for fc in change.files if getattr(fc, "old_path", None) and fc.status in ("renamed", "copied")}
    gone = {fc.path for fc in change.files if fc.status == "deleted"}
    parts = []
    for path in before[:3]:
        parts.append(f"{path} renamed to {renames[path]}" if path in renames else f"{path} deleted" if path in gone else f"{path} not at the head")
    more = f" (and {len(before) - 3} more)" if len(before) > 3 else ""
    n = len(before)
    return (f"it matched {'1 file' if n == 1 else f'{n} files'} before this change and the change deleted or moved {'it' if n == 1 else 'them'}, so no screen or route was read",
            "; ".join(parts) + more)


def check_unmatched(analysis: Any, entry: Any) -> None:
    """An adapter that looked for its routing files (a glob, a folder, a search) and found none says so: a blocking `routes_unmatched` record
    (subject `<repo>:<what was looked for>`) and a problem, so the result is incomplete. The adapter's `unmatched(sha)` lists (what, detail) for
    what matches nothing in the tree at `sha`, and nothing when the tree or a search could not be read (that is the incompleteness of the read,
    never an absence). Said only for a change with code. When it matched files before the change (`routing_files(sha, what)`) the record says the
    change deleted or moved them, naming a rename target: routes moved out of a glob are not an empty, complete answer."""
    check = getattr(entry, "unmatched", None)
    if check is None or not _code_changed(analysis):
        return
    change = analysis.change
    head, pre = change.head_sha, getattr(change, "pre_change_sha", None)
    for what, detail in list(check(head)):
        quoted = ""
        lister = getattr(entry, "routing_files", None)
        before = sorted(lister(pre, what)) if pre is not None and pre != head and lister is not None else []
        if before:
            detail, quoted = _moved(change, before)
        record_quoted(entry.target, "routes_unmatched", f"{entry.repo}:{what}", detail, quoted, once=True)
        why = f"{what} matches nothing in {entry.repo} at {head[:12]}: which routes it serves was not established" + (f" ({detail.split(',')[0]})" if before else "")
        if why not in entry.problems:
            entry.problems.append(why)


def reach(analysis: Any, language: Any, entry: Any, *, limits: config.Limits | None = None, record_to: Any = None, **caps: Any) -> EntryResult:
    """Walk from the changed files of `analysis` to the entry points of `entry` and build the areas, with the
    priorities the model assigns (all must-test up to `MUST_TEST_MAX_SCREENS` import-backed screens, else sample).
    `caps` may override `hub_importers` and `module_cap`."""
    result = walk(analysis, language, entry, limits=limits, record_to=record_to, **caps)
    if getattr(entry, "begin_change", None) is not None:  # an adapter may need the change itself: a deleted layout is no chain of the walk
        entry.begin_change(analysis)
    check_unmatched(analysis, entry)
    reached = entry.entry_points_reached(result.chains)
    by_files = getattr(entry, "reached_by_files", None)  # an adapter whose routes live in a file no language adapter reads (a spec) adds what a change to it reaches
    found: tuple[Any, ...] = ()  # and the findings it makes of that change
    if by_files is not None:
        reached, made = by_files(analysis.change, reached)
        found = tuple(made)
    areas = assign_priorities([r.area for r in reached])
    reasons = list(result.reasons) + [why for why in getattr(entry, "problems", ()) if why not in result.reasons]
    reasons += [f"{r.area.label}: {why}" for r in reached if not r.complete for why in (r.reasons or ("not established completely",))]
    complete = result.complete and not getattr(entry, "problems", ()) and all(r.complete for r in reached)
    return EntryResult(result, tuple(replace(r, area=a) for r, a in zip(reached, areas)), complete, tuple(dict.fromkeys(reasons)),
                       tuple(dict.fromkeys(getattr(entry, "notes", ()))), found)
