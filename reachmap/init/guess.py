"""What `reachmap init` knows about a repository: guesses with the reason, the evidence and a confidence word.

A `Note` is one reason for a value ("typescript from 47 files"), with `path:line` evidence and a confidence word (`high`, `medium`
or `low`). A `Guess` is a value for one config key with the notes behind it; the template writes one `# guess:` comment per note
above the key. Values that are read directly (a remote the user typed) are not guesses and carry no comment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["CONFIDENCE", "Dep", "EdgeGuess", "Facts", "Guess", "Note", "RepoGuess", "SpecError"]

CONFIDENCE = ("high", "medium", "low")


class SpecError(Exception):
    """A repository spec that could not be read. `reason` is scrubbed text that never holds a credential."""

    def __init__(self, spec: str, reason: str) -> None:
        super().__init__(f"{spec}: {reason}")
        self.spec = spec
        self.reason = reason


@dataclass(frozen=True, slots=True)
class Note:
    why: str
    evidence: tuple[str, ...] = ()  # path:line, relative to the repository
    confidence: str = "medium"

    def __post_init__(self) -> None:
        if self.confidence not in CONFIDENCE:
            raise ValueError(f"confidence is one of {CONFIDENCE}, not {self.confidence!r}")


@dataclass(frozen=True, slots=True)
class Guess:
    """A value for a config key (a string, a list of strings or a table as a dict, written in key order) and why."""

    value: Any
    notes: tuple[Note, ...]


@dataclass(frozen=True, slots=True)
class Dep:
    """One dependency a manifest names: `name` is normalised (lower case, runs of `-_.` as `-` for pip; as written for npm)."""

    ecosystem: str  # pip | npm
    name: str
    manifest: str
    line: int
    text: str  # the manifest line as written
    pinned: bool  # the line names a version (an exact `==` pin, a number for npm, a poetry version)
    spelling: str = ""  # the name as the line writes it (a single-line array can hold several dependencies)


@dataclass(slots=True)
class Facts:
    """What the edge guesses need to know about a repository beyond its config tables."""

    provides: dict[str, tuple[str, str, str]] = field(default_factory=dict)  # `pip:` or `npm:` and the normalised package name to (name code imports it by, `path:line`, why)
    deps: list[Dep] = field(default_factory=list)
    imports: dict[str, str] = field(default_factory=dict)  # top level module or package name code imports to the `path:line` of the first import
    client_paths: list[str] = field(default_factory=list)  # URL paths a client names (endpoints constants, spec paths)
    route_hints: list[str] = field(default_factory=list)  # URL segments a server declares (prefixes, decorators, spec paths)
    route_evidence: str | None = None  # where the server routes were found
    client_evidence: str | None = None
    topics: str | None = None  # a module of topic names (`path:line`), for an event edge
    has_pydantic_models: bool = False
    e2e: tuple[str, str] | None = None  # (framework, `path:line`) of Playwright or Cypress specs that visit URLs
    spec_text: dict[str, str] = field(default_factory=dict)  # spec file path to the text read (for matching a client's copy to a server)


@dataclass(slots=True)
class RepoGuess:
    spec: str
    name: str
    host: str
    remote: str | None  # as the config writes it: a path (made relative to the file at render time), a URL, or None for a host
    remote_local: bool  # the remote is a path on this machine
    default_branch: str
    sha: str
    branches: Guess
    language: Guess
    roles: Guess
    sections: dict[str, Guess] = field(default_factory=dict)  # aliases, entry_points, http_server, http_client, tests, service_marker, library_roots, contract
    notes: list[str] = field(default_factory=list)  # comment lines about this repository that belong to no key
    unread: list[tuple[str, str]] = field(default_factory=list)  # (path, why) of reads that failed or were skipped
    facts: Facts = field(default_factory=Facts)
    remote_notes: list[str] = field(default_factory=list)  # what was done to the remote (credentials removed)

    def kind_of(self, section: str) -> str | None:
        guess = self.sections.get(section)
        return guess.value.get("kind") if guess is not None and isinstance(guess.value, dict) else None

    @property
    def role_set(self) -> tuple[str, ...]:
        return tuple(self.roles.value)


@dataclass(slots=True)
class EdgeGuess:
    kind: str  # http | package | contract | event
    producer: str  # from for http and event
    consumer: str  # to for http and event
    options: dict[str, str]  # manifest, pin_pattern, import_marker, topics_glob
    notes: tuple[Note, ...]
    spelling: str = "from_to"  # from_to | producer_consumer
