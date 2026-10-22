"""The Spring Boot entry-point adapter (M16, section 5 of the brief): the API routes of a Spring application, read from the source with the
JVM adapter's reader (no JVM, no build tool, no Spring).

    entry_points = { kind = "spring" }                              # language = "java" or "kotlin"
    entry_points = { kind = "spring", context_path = "/api" }       # the context path, when the configuration does not say it

The only option is `context_path`, a plain path that replaces what `server.servlet.context-path` says. See `adapter.py` and `table.py` for
the rules and docs/briefs/M16-spring.md for the limits.
"""

from __future__ import annotations

from typing import Any, Mapping

from reachmap import config
from reachmap.adapters import third_party
from reachmap.adapters.spring import records  # noqa: F401  (registers the incompleteness kinds: a reader of a report needs them without the adapter)

__all__ = ["KINDS", "SpringAdapter", "register", "validate_options"]

KINDS = ("spring",)
third_party.register("spring", installed=("org.springframework",))  # a Spring import is the installed framework, not a file of the repository
_OPTIONS = ("kind", "context_path")


def validate_options(options: Mapping[str, Any], describe: Any) -> list[str]:
    problems = [f"{key}: not an option of the spring entry points (known: context_path)" for key in options if key not in _OPTIONS]
    if "context_path" in options:
        value = options["context_path"]
        if not isinstance(value, str):
            problems.append(f"context_path: expected a string, found {describe(value)}")
        elif not value.startswith("/") or "${" in value or ".." in value or "\\" in value:
            problems.append("context_path: expected a plain path that starts with `/`")
    return problems


def __getattr__(name: str) -> Any:  # the adapter is imported when it is used: the config only needs the kind and its options
    if name == "SpringAdapter":
        from reachmap.adapters.spring.adapter import SpringAdapter

        return SpringAdapter
    raise AttributeError(name)


def _factory(repo: config.Repo, language: Any, record_to: Any = None) -> Any:
    from reachmap.adapters.jvm import JvmAdapter
    from reachmap.adapters.spring.adapter import SpringAdapter

    if not isinstance(language, JvmAdapter):
        raise TypeError("the spring entry points are read with the Java and Kotlin adapter")
    return SpringAdapter.from_config(repo, language, record_to=record_to)


def register(register_entry: Any, register_config_kind: Any = None) -> None:
    """Make `spring` a known `entry_points` kind: in the registry of adapters (`register_entry(kind, factory)`) and in the config
    (`register_config_kind(section, kind, validator)`, default `reachmap.config.register_kind`; an already registered kind is left)."""
    import reachmap.adapters.jvm  # noqa: F401  (registers the languages `java` and `kotlin`)

    add = register_config_kind or config.register_kind
    register_entry("spring", _factory)
    try:
        add("entry_points", "spring", validate_options)
    except ValueError:
        pass  # registered before (a second import, a test): the same kind
