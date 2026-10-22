"""The Express and NestJS entry-point adapters (M16, section 3): the API routes a change reaches in a Node service, with their
full paths, read from the source with the JavaScript adapter's tokens (no Node, no TypeScript compiler).

    entry_points = { kind = "express" }     # app.get(...), router.route(...), app.use(prefix, router) across files
    entry_points = { kind = "nestjs" }      # @Controller + @Get, setGlobalPrefix, @Module({ controllers: [...] })

Neither kind has an option besides `kind`. The framework is told apart by what it imports (`express`, `@nestjs/common`), never by
the name of a variable or a decorator: a `router` that is not an Express router and a `Get` that does not come from
`@nestjs/common` claim nothing. Both register the `routes_unread` incompleteness kind; the others they record are the ones FastAPI
records (`router_unmounted`, `mount_unresolved`, `route_path_partial`) and `parse_degraded`.
"""

from __future__ import annotations

from typing import Any, Mapping

from reachmap import config
from reachmap.adapters.express import common as _common  # noqa: F401  (registers the incompleteness kind `routes_unread`)
from reachmap.adapters.express.express import ExpressAdapter
from reachmap.adapters.express.nest import NestAdapter
from reachmap.adapters.js import JsAdapter

__all__ = ["ExpressAdapter", "NestAdapter", "register", "validate_options"]

KINDS = ("express", "nestjs")


def validate_options(kind: str) -> Any:
    def check(options: Mapping[str, Any], describe: Any) -> list[str]:
        return [f"{key}: not an option of the {kind} entry points (there is none besides kind)" for key in options if key != "kind"]

    return check


def _factory(cls: Any, kind: str) -> Any:
    def make(repo: config.Repo, language: Any, record_to: Any = None) -> Any:
        if not isinstance(language, JsAdapter):
            raise TypeError(f"the {kind} entry points are read with the JavaScript adapter")
        return cls.from_config(repo, language, record_to=record_to)

    return make


def register(register_entry: Any, register_config_kind: Any = None) -> None:
    """Make `express` and `nestjs` known `entry_points` kinds: in the registry of adapters (`register_entry(kind, factory)`) and in the
    config (`register_config_kind(section, kind, validator)`, default `reachmap.config.register_kind`; an already registered kind is left)."""
    add = register_config_kind or config.register_kind
    for kind, cls in (("express", ExpressAdapter), ("nestjs", NestAdapter)):
        register_entry(kind, _factory(cls, kind))
        try:
            add("entry_points", kind, validate_options(kind))
        except ValueError:
            pass  # registered before (a second import, a test): the same kind
