"""`reachmap init`: read repositories through the git layer and write a starter `reachmap.toml` with every guess marked.

    from reachmap.init import detect, render
    guesses = [detect(spec, git) for spec in specs]       # RepoGuess each; SpecError for a repository that cannot be read
    text = render(guesses)                                # TOML text that loads with `config.load`

`generate` does both for a list of specs, collecting what cannot be read instead of raising. See `detect.py` (detectors),
`registry.py` (how another adapter registers its own), `edges.py` and `template.py`.
"""

from __future__ import annotations

import os
from typing import Sequence

from reachmap import redact
from reachmap.gitlayer import Git
from reachmap.init import entries, clients, contract  # noqa: F401  (registers the detectors)
from reachmap.init.detect import detect, guess_repo
from reachmap.init.guess import RepoGuess, SpecError
from reachmap.init.source import Spec, name_collisions, parse_spec
from reachmap.init.template import Rendered, build, render

__all__ = ["Rendered", "RepoGuess", "SpecError", "build", "detect", "generate", "guess_repo", "render"]


def generate(specs: Sequence[str | Spec], git: Git, out_dir: str | os.PathLike | None = None) -> Rendered:
    """Detect every repository and build the file. A spec that cannot be read is listed in the output (`Rendered.failures`), never
    skipped silently. Raises `ValueError` for a spec that is no spec, or two specs that get one name (nothing is read then)."""
    parsed = [parse_spec(s) if isinstance(s, str) else s for s in specs]
    clash = name_collisions(parsed)
    if clash:
        raise ValueError("; ".join(clash))
    guesses: list[RepoGuess] = []
    failures: list[tuple[str, str]] = []
    for spec in parsed:
        try:
            guesses.append(detect(spec, git))
        except SpecError as exc:
            failures.append((exc.spec or spec.shown, redact.scrub(exc.reason)))
        except Exception as exc:  # a repository that breaks a detector is listed with why, never a run that writes nothing
            failures.append((spec.shown, "could not be analysed: " + redact.for_record(f"{type(exc).__name__}: {exc}")))
    return build(guesses, failures, out_dir)
