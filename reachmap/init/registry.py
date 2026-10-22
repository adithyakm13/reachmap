"""The detector registry: one function per kind, registered next to the adapter or in `reachmap/init/entries.py`.

A detector takes a `Ctx` (the tree of one repository, its language, and what earlier steps learned) and returns `Found` values
(candidates for one config table). Adding the detector of another kind (Express, NestJS, Spring) is one `register` call:

    from reachmap.init import registry

    def detect_express(ctx):
        ...
        return [registry.Found("express", {}, ("backend",), (Note("...", ("src/app.ts:3",), "high"),), score=n)]

    registry.register("entry_points", "express", detect_express, languages=("typescript", "javascript"))

`kind` must already be accepted by the config (`config.register_kind`, done by the adapter), or `register` refuses it. `section` is the config table (`entry_points`, `http_server`, `http_client`, `contract`); a kind is only offered for the languages
it lists. The orchestrator ranks the candidates of a section by `score` and keeps one (a repository has one `entry_points`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from reachmap import config, entrypoints  # noqa: F401  (entrypoints registers the kinds the config accepts: django, nextjs, express, nestjs, spring)
from reachmap.init.guess import Facts, Note
from reachmap.init.tree import Tree

__all__ = ["Ctx", "Found", "detectors", "register", "SECTIONS"]

SECTIONS = ("entry_points", "http_server", "http_client", "contract")


@dataclass(slots=True)
class Ctx:
    """What a detector sees. `facts` is where it leaves what the edge guesses need (route hints, client paths, topics)."""

    tree: Tree
    language: str
    facts: Facts
    tags: tuple[str, ...] = ()  # tag names of the repository (read when a detector asks: `tags_loader`)
    tags_loader: Callable[[], tuple[str, ...]] | None = None
    cache: dict[str, Any] = field(default_factory=dict)  # detectors may share parsed files here (package.json, pyproject.toml)
    notes: list[str] = field(default_factory=list)  # what a detector saw and did not use (written as comments of the repository)

    def tag_names(self) -> tuple[str, ...]:
        if self.tags_loader is not None and not self.tags:
            self.tags = self.tags_loader()
        return self.tags


@dataclass(frozen=True, slots=True)
class Found:
    kind: str
    options: Mapping[str, Any]  # the table's keys after `kind`, in the order they are written
    roles: tuple[str, ...]
    notes: tuple[Note, ...]
    score: int = 1  # how much evidence: the candidate with the highest score of a section wins
    extra_notes: tuple[str, ...] = ()  # comment lines about what was found and not used


Detector = Callable[[Ctx], list[Found]]
_REGISTRY: dict[str, dict[str, tuple[Detector, tuple[str, ...]]]] = {s: {} for s in SECTIONS}


def register(section: str, kind: str, fn: Detector, *, languages: tuple[str, ...] = ("typescript", "javascript", "python")) -> None:
    if section not in _REGISTRY:
        raise ValueError(f"unknown section {section!r}; known: {', '.join(SECTIONS)}")
    if kind not in config.kinds(section):  # a detector for a kind the loader refuses could only write a file that does not load
        raise ValueError(f"the config does not accept kind {kind!r} in {section}; known: {', '.join(config.kinds(section))} (register it with config.register_kind first)")
    _REGISTRY[section][kind] = (fn, tuple(languages))


def detectors(section: str, language: str) -> list[tuple[str, Detector]]:
    """The detectors of a section that apply to a language, in registration order."""
    return [(kind, fn) for kind, (fn, langs) in _REGISTRY[section].items() if language in langs]
