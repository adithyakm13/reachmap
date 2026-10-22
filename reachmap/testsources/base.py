"""What every test source shares: the error a source raises when it cannot answer, the case and the corpus a hosted source
reads, and the one function that turns a test case title into text that is safe to put in a report."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

from reachmap import redact

__all__ = ["Case", "Context", "Corpus", "Found", "SourceError", "StopRun", "clean_title"]


class SourceError(Exception):
    """A configured source did not answer (an authentication failure, a rate limit, a 5xx, a timeout, text that is not JSON, a
    page that failed in the middle of a list, an answer of the wrong shape). The message says why and holds no credential. A source
    that raises it is `source_unavailable`: never "no suggestions"."""


class StopRun(Exception):
    """What a run's `check` raises to stop it (the run hit its time limit). `run_all` lets it through: it is the run ending, not a source
    that failed."""


@dataclass(frozen=True, slots=True)
class Case:
    """A test case as a test-management system lists it. `group` is the suite (Qase) or section (TestRail) it sits in."""

    id: str
    title: str
    group: int | None


@dataclass(frozen=True, slots=True)
class Corpus:
    """Every case a source listed, with the tree of its suites or sections. `complete` is False when a list was cut by the page
    cap: `cut` says which list and how much of it was read (the 500-of-899 trap), and the suggestion list built from it is partial."""

    cases: tuple[Case, ...]
    parents: Mapping[int, int | None]
    cut: str = ""

    @property
    def complete(self) -> bool:
        return not self.cut

    def within(self, roots: Iterable[int]) -> set[int]:
        """The groups below `roots` and the roots themselves (a root the tree does not have contains only itself)."""
        children: dict[int, list[int]] = {}
        for child, parent in self.parents.items():
            if parent is not None:
                children.setdefault(parent, []).append(child)
        out: set[int] = set()
        todo = list(roots)
        while todo:
            node = todo.pop()
            if node in out:
                continue  # a loop in the tree ends here
            out.add(node)
            todo.extend(children.get(node, ()))
        return out


_SPACE = re.compile(r"\s+")
# control characters, the line and paragraph separators and the bidirectional and zero width marks, written as ranges of code points
_STRIP = ((0x00, 0x08), (0x0B, 0x1F), (0x7F, 0x9F), (0x200B, 0x200F), (0x2028, 0x202E), (0x2066, 0x2069), (0xFEFF, 0xFEFF))
_CONTROL = re.compile("[" + "".join(f"{chr(a)}-{chr(b)}" for a, b in _STRIP) + "]")
MAX_TITLE = 300


def clean_title(text: str) -> str:
    """A case title as shown: one line, control and bidirectional characters removed, a credential scrubbed, cut at `MAX_TITLE`.
    The title is the system's own words (content, never ours): nothing else is changed."""
    one = _SPACE.sub(" ", _CONTROL.sub(" ", text or "")).strip()
    one = redact.scrub(one).encode("utf-8", errors="replace").decode("utf-8")
    return one if len(one) <= MAX_TITLE else one[: MAX_TITLE - 3].rstrip() + "..."


@dataclass(frozen=True, slots=True)
class Found:
    """What a source made of a run: the suggestions, the non-blocking records it made on the way (a spec address that is not a
    literal) and `cut`, which says why the list may be partial ("" when the source was read completely)."""

    suggestions: tuple[Any, ...] = ()
    records: tuple[Any, ...] = ()
    cut: str = ""
    notes: tuple[str, ...] = ()  # what a person should know about how the list was made (a short corpus), not a record
    unconfigured: str = ""  # why the source had nothing to look at (a config-shaped problem): it is then not configured, not "none suggested"


@dataclass
class Context:
    """What a source needs from the run besides its own configuration. `git` and `run` are for the sources that read a repository (the
    spec sources): `run` is the walker's `propagate.Run` (it holds the language and entry-point adapter of every repository and pins
    a branch as of `as_of`); with only `git` one is made when first needed. `origin` is `(repository, head sha)` of the change: that
    repository is read at the change's head (the specs go with the change), every other at the head of its primary branch.
    `env`, `transport` and `check` are for the hosted sources: the variables the tokens are read from, an injected transport (a fake
    in tests; the default sends real requests) and the run's deadline."""

    workspace: Any
    git: Any = None
    run: Any = None
    origin: tuple[str, str] | None = None
    as_of: str | None = None
    env: Mapping[str, str] | None = None
    transport: Any = None
    check: Callable[[], None] | None = None

    def get_run(self) -> Any:
        if self.run is None:
            if self.git is None:
                raise SourceError("this run has no repository access, so specs cannot be read")
            from reachmap import propagate

            self.run = propagate.Run(self.workspace, self.git, as_of=self.as_of)
        return self.run
