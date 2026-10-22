"""Edges between repositories (context 7.4): the types a hop is made of, the adapter protocol, and the registry.

A change in one repository is followed along the configured edges, hop by hop, into other repositories. Each edge kind
has an adapter (`http`, `package`, `contract`) that answers two narrow questions about a pass in a repository:

- `applies`: is there something in this pass that the edge could carry (a route reached, a changed contract field)?
  Cheap, reads nothing in another repository. The walker uses it to say `hop_cap` only for an edge that had something
  to carry.
- `follow`: read the other repository (pinned) and return the hops: what was found there, with evidence at both ends,
  and the seeds (files and lines) the other repository's own pass starts from. A hop with no seeds is terminal (a
  consumer listed with its pin, an http edge to a server).

The walker (`reachmap.propagate`) owns the rules of section 7.5 of the context: the hop cap, the module cap and hubs
(the in-repo walk), distinctiveness, candidates, pins and failed reads. An adapter records what it could not read
and returns `complete=False` hops; it never turns a failed read into "not adopted", "no caller" or "no consumer".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Protocol

from reachmap.incomplete import kind_spec, record, register_kind
from reachmap.model import Evidence

__all__ = ["EdgeAdapter", "FindingInput", "Hop", "KINDS", "Pin", "Seed", "adapter_for", "edge_incomplete", "edge_subject", "incomplete_hop", "register"]


for _kind, _blocking, _template in (
    ("client_url_partial", False,
     "Which route {subject} calls was not established, because its URL cannot be evaluated statically"),
    ("version_unparsed", False,
     "Whether the consumer at {subject} has the change was not established, because its pinned version cannot be read as a version"),
    ("pin_unresolved", False,
     "Whether the consumer at {subject} has the change was not established, because no tag of the producer names its pinned version"),
    ("client_url_unmatched", False,
     "Which route {subject} calls was not established, because its URL fits no route of the server"),
    ("edge_unfollowed", False,
     "What lies past {subject} was not established, because no adapter follows that kind of edge yet"),
    ("entry_points_unread", False,
     "What {subject} runs was not established, because its entry points are of a kind that no adapter reads yet"),
    ("contract_construction_unseen", False,
     "Where {subject} is built with the new required field was not established, because only direct constructions of the model are seen"),
    ("tag_prefix_unmatched", False,
     "Whether the change to {subject} is released was not established, because its tag prefix matches none of its tags"),
    ("edge_incomplete", True,
     "What {subject} reaches was not established, because the edge could not be read completely"),
    ("contract_change_unscoped", True,
     "Which consumer code is affected by the change to {subject} was not established, because it is not a change to one field of a model"),
):
    try:
        kind_spec(_kind)
    except Exception:
        register_kind(_kind, _template, _blocking)


def subject_of(kind: str, declared_from: str, declared_to: str) -> str:
    return f"edge:{kind}:{declared_from}:{declared_to}"


def edge_subject(edge: Any) -> str:
    """The subject of every record about an edge: `edge:<kind>:<from>:<to>` as the config declares it."""
    return subject_of(edge.kind, edge.from_repo, edge.to_repo)


def edge_incomplete(target: Any, edge: Any, why: str) -> Any:
    """The record that an edge could not be read completely (blocking): what was not established is what lies past it."""
    return record(target, "edge_incomplete", edge_subject(edge), detail=why)


def incomplete_hop(target: Any, edge: Any, repo: str, depth: int, reasons: "list[str] | tuple[str, ...]") -> "Hop":
    """An edge that could not be followed: one blocking `edge_incomplete` per reason and a hop (no seeds) that says it is
    incomplete, so the propagation is too. `repo` is the repository the pass was in; the hop goes to the other end."""
    for why in reasons:
        edge_incomplete(target, edge, why)
    forward = repo == edge.from_repo
    return Hop(edge.kind, repo, edge.to_repo if forward else edge.from_repo, (edge.from_repo, edge.to_repo), "forward" if forward else "reverse",
               depth + 1, complete=False, reasons=tuple(reasons), detail=reasons[0])


@dataclass(frozen=True, slots=True)
class Pin:
    """A revision of a repository that a hop read: `role` says why it is that one (`head` of the change, the `primary`
    branch, a configured `branch` of a consumer, the `pre_change` version, the `base`)."""

    repo: str
    ref: str  # the branch or the label (`head`) that was resolved
    sha: str
    role: str


@dataclass(frozen=True, slots=True)
class Seed:
    """Lines of one file of the target repository at `sha` that are handed to its own pass as if they were the changed
    lines (the consumer line of a contract read, the client line of an http call). `evidence` cites them."""

    repo: str
    path: str
    lines: tuple[int, ...]
    sha: str
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True, slots=True)
class Hop:
    """One step along an edge: from the repository the change was in (`from_repo`) into `to_repo`.

    `declared` is the edge as the config declares it (`from`, `to`), whichever way the change travels: it names the
    edge in a `hop_cap` record. `direction` is `forward` when the change travels the declared way and `reverse`
    when it goes back along it (a server route reached from its clients). `pins` are the revisions read in the
    target repository. `source_evidence` and `target_evidence` are the evidence at the two ends; `chain` is the
    evidence the report shows from the original change to the target end of this hop, in order. `seeds` are what the
    target repository's own pass starts from (none: the hop ends there). `complete` is False when something on the
    edge could not be established (`reasons` says what); the records are made where it happened."""

    kind: str
    from_repo: str
    to_repo: str
    declared: tuple[str, str]
    direction: str
    depth: int
    pins: tuple[Pin, ...] = ()
    source_evidence: tuple[Evidence, ...] = ()
    target_evidence: tuple[Evidence, ...] = ()
    chain: tuple[Evidence, ...] = ()
    seeds: tuple[Seed, ...] = ()
    detail: str = ""
    complete: bool = True
    reasons: tuple[str, ...] = ()
    note: str = ""
    candidate: bool = False  # found by name or by a partial URL: shown apart, never must test

    @property
    def subject(self) -> str:
        """The `hop_cap` subject of the edge: `edge:<kind>:<from>:<to>` in the declared direction."""
        return subject_of(self.kind, self.declared[0], self.declared[1])  # the text `edge_subject` gives for the config edge


@dataclass(frozen=True, slots=True)
class FindingInput:
    """The input of a finding, with the risk and confidence the README gives it, as data for the report (M9 turns it
    into a `Finding`). Risk and confidence are separate and never averaged. `evidence` is never empty: a claim without
    a citation does not exist."""

    kind: str  # removed_route_still_called | added_route_uncalled
    risk: str
    confidence: str
    confidence_basis: str
    evidence: tuple[Evidence, ...]
    identity: tuple[str, ...]
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.evidence:
            raise ValueError("a finding needs evidence: a claim without a citation must not exist")

    def all_evidence(self) -> tuple[Evidence, ...]:
        return self.evidence


class EdgeAdapter(Protocol):
    """An edge kind. `applies(run, pass_, edge)` and `follow(run, pass_, edge)` are described in the module docstring;
    `run` is the walker's context (`reachmap.propagate.Run`), `pass_` the pass in one repository, `edge` the config
    edge."""

    kind: str

    def applies(self, run: Any, pass_: Any, edge: Any) -> bool: ...

    def follow(self, run: Any, pass_: Any, edge: Any) -> Iterable[Hop]: ...


Factory = Callable[[], EdgeAdapter]
_FACTORIES: dict[str, Factory] = {}


def register(kind: str, factory: Factory) -> None:
    """Add an edge kind: `factory()` returns the adapter. Registering a kind again replaces it."""
    _FACTORIES[kind] = factory


def _http() -> EdgeAdapter:
    from reachmap.edges.http import HttpEdgeAdapter

    return HttpEdgeAdapter()


def _package() -> EdgeAdapter:
    from reachmap.edges.package import PackageEdgeAdapter

    return PackageEdgeAdapter()


def _contract() -> EdgeAdapter:
    from reachmap.edges.contract import ContractEdgeAdapter

    return ContractEdgeAdapter()


register("http", _http)
register("package", _package)
register("contract", _contract)
KINDS = tuple(_FACTORIES)


def adapter_for(kind: str) -> EdgeAdapter | None:
    """The adapter of an edge kind; None for a kind with none (`event`, `library`): a declared edge that is not
    followed, which the propagation lists (`Propagation.unfollowed`) and never reads as "nothing flows there"."""
    factory = _FACTORIES.get(kind)
    return factory() if factory is not None else None
