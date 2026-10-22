"""Stable ids and fingerprints.

Canonical JSON is `json.dumps(obj, sort_keys=True, separators=(",", ":"),
ensure_ascii=False)`. Ids are `f-` or `a-` plus the first 16 hex characters of the sha256
of the canonical JSON.

Ids: a finding id hashes the kind and the identity tuple, never the title (titles carry
counts that move with the code), the risk, the confidence or any evidence ref. An area id
hashes its kind, repo and label.

Fingerprint of a finding or an area (what a decision was made about): the sha256 hex of
the claim type ("finding" or "area"), the kind, the identity, and the sorted list of
evidence triples (repo, path, and the evidence's claim when it has one, else its detail stripped of surrounding whitespace: see
`Evidence.claim`). Duplicates are
kept: two identical call sites are two claims. It excludes Evidence.ref and Evidence.line,
so a branch moving, or a line sliding down because of an unrelated insertion, does not
make decisions stale. It also excludes the title, the risk, the confidence and the area
priority (which depends on how many other screens the change reaches). It changes when the
claim changes: another kind or identity, a different detail line, or evidence added or
removed. The list is sorted, so listing order does not matter.
"""

import hashlib
import json
from typing import Any, ClassVar, Protocol, Sequence

from reachmap.errors import ModelError, check_encodable

FINDING_PREFIX = "f-"
AREA_PREFIX = "a-"
ID_HEX_LENGTH = 16


class _EvidenceLike(Protocol):
    repo: str
    path: str
    detail: str
    claim: str | None


class _Claim(Protocol):
    """What fingerprint() needs: a Finding or an Area."""

    claim_type: ClassVar[str]  # "finding" or "area"
    kind: str

    @property
    def identity(self) -> tuple[str, ...]: ...

    def all_evidence(self) -> Sequence[_EvidenceLike]: ...


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(obj: Any) -> str:
    """Full sha256 hex of the canonical JSON of obj."""
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def _text(name: str, value: str) -> str:
    if not isinstance(value, str):
        raise ModelError(f"{name} must be a string")
    return check_encodable(name, value)


def _strings(name: str, values: Sequence[str]) -> list[str]:
    if isinstance(values, str):
        raise ModelError(f"{name} must be a sequence of strings")
    return [_text(name, v) for v in values]


def finding_id(kind: str, identity: Sequence[str]) -> str:
    return FINDING_PREFIX + digest(["finding", _text("kind", kind), _strings("identity", identity)])[:ID_HEX_LENGTH]


def area_id(kind: str, repo: str, label: str) -> str:
    return AREA_PREFIX + digest(["area", _text("kind", kind), _text("repo", repo), _text("label", label)])[:ID_HEX_LENGTH]


def fingerprint(item: _Claim) -> str:
    """Hash of the claim, not of the commit. See the module docstring for what it excludes."""
    triples = sorted(
        [e.repo, e.path, e.detail.strip()] if e.claim is None else [e.repo, e.path, "claim", e.claim] for e in item.all_evidence())
    return digest([item.claim_type, item.kind, list(item.identity), triples])
