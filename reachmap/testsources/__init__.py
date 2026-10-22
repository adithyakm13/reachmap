"""Suggested test cases from test-management systems and from local end-to-end specs (M15).

Everything here yields `Suggestion` objects and records, never a mapped test: a suggestion is a place to look, not coverage
(context rule 4.3), and nothing in this package imports `MappedTest`. Every request a source makes is a GET (rule 4.10).

`run_all(workspace, analysis)` reads every configured source and returns the suggestions and the records. A source answers one of
four ways, and the records say which:

- not configured (`configured` is False, an unset token variable, a missing key): `source_unconfigured`, non-blocking;
- configured and read completely: its suggestions, and nothing else (so "none suggested" is a real answer);
- read, but the list is partial (a page cap, a route table that is not whole): its suggestions and a blocking
  `suggestions_incomplete`;
- configured and failing to answer (authentication, a rate limit, a 5xx, a timeout, text that is not JSON, a page that fails
  halfway, a repository that cannot be read): `source_unavailable`, blocking. Never "no suggestions".

The blocking kinds block the claim about suggestions only (a record of the scope `suggestions`, `blocks_suggestions`): the areas come from the code and
a test source cannot make them incomplete.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterator, Mapping, Sequence

from reachmap import config, redact
from reachmap.hosts import DefaultTransportForbidden
from reachmap.incomplete import Incompleteness, record, records_of
from reachmap.model import Suggestion
from reachmap.testsources import match, words  # noqa: F401
from reachmap.testsources.base import Case, Context, Corpus, Found, SourceError, StopRun  # noqa: F401

__all__ = ["Case", "Context", "Corpus", "Found", "Outcome", "SourceError", "StopRun", "SourceRead", "all_read", "drafts", "label_of", "read_states", "register", "run_all"]


@dataclass(frozen=True, slots=True)
class SourceRead:
    """What became of one configured source: `read` (completely), `incomplete`, `unconfigured` or `unavailable`."""

    source: str
    kind: str
    state: str

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "kind": self.kind, "state": self.state, "read": self.state == "read"}


@dataclass(frozen=True, slots=True)
class Outcome:
    """`suggestions` and `records` are what `run_all` makes (unpack it as `suggestions, records = run_all(...)`); `reads` says what
    became of each configured source and `notes` is what a person should know that is not a record."""

    suggestions: tuple[Suggestion, ...]
    records: tuple[Incompleteness, ...]
    reads: tuple[SourceRead, ...] = ()
    notes: tuple[str, ...] = ()

    def __iter__(self) -> Iterator[Any]:
        yield self.suggestions
        yield self.records


# ------------------------------------------------------------------------------ the registry

Factory = Callable[[config.TestSource, Context], Any]
_FACTORIES: dict[str, Factory] = {}


def register(kind: str, factory: Factory) -> None:
    """Make a configured test source kind readable: `factory(source, ctx)` returns an object with `label`, `unconfigured()` and
    `suggest(infos, ctx)`. (The config registry, `config.register_kind`, is what accepts the kind in a file.)"""
    _FACTORIES[kind] = factory


def _qase(source: config.TestSource, ctx: Context) -> Any:
    from reachmap.testsources import qase

    return qase.Qase(source, env=ctx.env, transport=ctx.transport, check=ctx.check)


def _testrail(source: config.TestSource, ctx: Context) -> Any:
    from reachmap.testsources import testrail

    return testrail.TestRail(source, env=ctx.env, transport=ctx.transport, check=ctx.check)


def _specs(source: config.TestSource, ctx: Context) -> Any:
    from reachmap.testsources import specs

    return specs.Specs(source, kind=source.kind)


register("qase", _qase)
register("testrail", _testrail)
register("playwright", _specs)
register("cypress", _specs)


def drafts(analysis: Any) -> tuple[str, ...]:
    """Draft test cases for a person to add to the test-management system (rule 4.10: the analyser never writes to it). Out of scope
    for this version: a stub that renders nothing, so a caller can already ask and a later version can fill it in."""
    return ()


def all_read(reads: Sequence[SourceRead]) -> bool:
    """Every configured source was read completely, and there is at least one: "none suggested" may be said."""
    return bool(reads) and all(r.state == "read" for r in reads)


def label_of(source: config.TestSource) -> str:
    """The name a source goes by in its records and its suggestions (`qase:ACME`, `testrail:3`, `playwright:acme/e2e`)."""
    return _FACTORIES[source.kind](source, Context(None)).label


# ------------------------------------------------------------------------------ running them


def run_all(workspace: config.Workspace, analysis: Any, *, env: Mapping[str, str] | None = None, transport: Any = None, git: Any = None,
            run: Any = None, origin: tuple[str, str] | None = None, as_of: str | None = None,
            check: Callable[[], None] | None = None) -> Outcome:
    """Read every configured test source and suggest cases for the areas of `analysis` (anything with `areas` and `findings`: an
    `Analysis`). Reads nothing for a workspace with no test source. See the module docstring for the four states."""
    ctx = Context(workspace, git=git, run=run, origin=origin, as_of=as_of, env=env, transport=transport, check=check)
    infos = match.infos(analysis.areas, analysis.findings)
    suggestions: list[Suggestion] = []
    records: list[Incompleteness] = []
    reads: list[SourceRead] = []
    notes: list[str] = []
    for ts in workspace.test_sources:
        factory = _FACTORIES.get(ts.kind)
        if factory is None:
            record(records, "source_unconfigured", ts.kind, detail=f"nothing reads a test source of kind {ts.kind}")
            reads.append(SourceRead(ts.kind, ts.kind, "unconfigured"))
            continue
        label = ts.kind
        try:
            src = factory(ts, ctx)
            label = src.label
            why = src.unconfigured()
            if why:
                record(records, "source_unconfigured", label, detail=redact.for_record(why))
                reads.append(SourceRead(label, ts.kind, "unconfigured"))
                continue
            for a in ts.areas:  # an area that names neither a prefix nor a module can match no area of any run: said, never silent
                if isinstance(a, Mapping) and not a.get("route_prefixes") and not a.get("modules"):
                    notes.append(f"the area {a.get('name')!r} of {label} names no route_prefixes and no modules, so it matches no area of a run")
            found = src.suggest(infos, ctx)
        except SourceError as exc:
            record(records, "source_unavailable", label, detail=redact.for_record(str(exc)))
            reads.append(SourceRead(label, ts.kind, "unavailable"))
            continue
        except (StopRun, DefaultTransportForbidden):  # the run ending, and the test suite's guard against a real request: neither is a source failing
            raise
        except Exception as exc:  # a failure nobody listed (a bug, JSON nested too deep): the source did not answer, and the run goes on
            record(records, "source_unavailable", label, detail=f"the reader failed with {type(exc).__name__}; the message is not recorded")
            reads.append(SourceRead(label, ts.kind, "unavailable"))
            continue
        if found.unconfigured:
            record(records, "source_unconfigured", label, detail=redact.for_record(found.unconfigured))
            reads.append(SourceRead(label, ts.kind, "unconfigured"))
            continue
        suggestions.extend(found.suggestions)
        records.extend(found.records)
        notes.extend(found.notes)
        if found.cut:
            record(records, "suggestions_incomplete", label, detail=redact.for_record(found.cut))
            reads.append(SourceRead(label, ts.kind, "incomplete"))
        else:
            reads.append(SourceRead(label, ts.kind, "read"))
    return Outcome(tuple(suggestions), tuple(records), tuple(reads), tuple(notes))


def read_states(workspace: config.Workspace, analysis: Any) -> list[SourceRead]:
    """What became of each configured source, told from the records of a finished analysis (a source with no record was read
    completely): what the report keeps as `inputs.test_sources`."""
    held = records_of(analysis)
    out = []
    for ts in workspace.test_sources:
        label = label_of(ts) if ts.kind in _FACTORIES else ts.kind
        kinds = {r.kind for r in held if r.subject == label}
        state = ("unavailable" if "source_unavailable" in kinds else "unconfigured" if "source_unconfigured" in kinds
                 else "incomplete" if "suggestions_incomplete" in kinds else "read")
        out.append(SourceRead(label, ts.kind, state))
    return out
