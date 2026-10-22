"""The core data model (context section 3) with the rules of section 4 built in.

Plain dataclasses, validated in `__post_init__`, with `to_dict` / `from_dict`. Everything
is frozen except Analysis, which a run fills in through validating `add_*` methods (its
collections are read-only tuples) and ReadResult, a small frozen class so that its `text`
can be a guarded property. Sequence fields are stored as tuples so a frozen value really is
fixed. Nothing here does I/O.

Rules a data model can enforce, so no adapter can break them:
- a Finding cannot exist without Evidence (4.1), and a Decision is never Evidence (4.4);
- our own claims never use banned wording (4.2, see reachmap.wording);
- suggestions and mapped tests are different fields, and one function counts gaps (4.3);
- an unreadable read is its own state, never an absence (4.5);
- risk and confidence are separate fields (context section 3);
- a timed out or failed run exposes no finding count (4.9).
"""

import copy
import json
import re
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, ClassVar, Sequence

from reachmap.errors import ModelError, UnreadableError, check_encodable, expect_keys
from reachmap.identity import area_id, canonical_json, finding_id
from reachmap.identity import fingerprint as claim_fingerprint
from reachmap.incomplete import Incompleteness, record
from reachmap.wording import require_clean

__all__ = [
    "Analysis", "Area", "BranchState", "Confidence", "Decision", "Evidence", "Finding",
    "Incompleteness", "MappedTest", "ModelError", "Read", "ReadResult", "Risk", "Suggestion", "UnreadableError",
    "SUGGESTION_LABEL", "MUST_TEST_MAX_SCREENS", "assign_priorities", "coverage_gaps",
    "describe", "dumps", "finding_kinds", "register_finding_kind",
]

# ----------------------------------------------------------------------------- helpers

_SHA = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")  # git sha-1 or sha-256 commit id


def _is_commit(value: object) -> bool:
    """A full commit id that names a commit: 40 or 64 hex, and not the null object id (all zeros), which names none."""
    return isinstance(value, str) and bool(_SHA.match(value)) and set(value) != {"0"}
_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
_TARGET_ID = re.compile(r"^[af]-[0-9a-f]{16}$")
_KIND_NAME = re.compile(r"^[a-z][a-z0-9_-]*$")


def _str(owner: str, name: str, value: object, *, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise ModelError(f"{owner}.{name} must be {'a string' if empty else 'a non-empty string'}")
    return check_encodable(f"{owner}.{name}", value)


def _int(owner: str, name: str, value: object, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ModelError(f"{owner}.{name} must be an integer >= {minimum}, got {value!r}")
    return value


def _bool(owner: str, name: str, value: object) -> bool:
    if not isinstance(value, bool):
        raise ModelError(f"{owner}.{name} must be a bool")
    return value


def _choice(owner: str, name: str, value: object, choices: Sequence[str]) -> str:
    if value not in choices:
        raise ModelError(f"{owner}.{name} must be one of {list(choices)}, got {value!r}")
    return value  # type: ignore[return-value]


def _strings(owner: str, name: str, values: object, *, nonempty: bool = False) -> tuple[str, ...]:
    if isinstance(values, str) or not isinstance(values, (list, tuple)):
        raise ModelError(f"{owner}.{name} must be a list of strings")
    for value in values:
        _str(owner, name, value)
    if nonempty and not values:
        raise ModelError(f"{owner}.{name} must not be empty")
    return tuple(values)


def _coded(code: str, check: Any, *args: Any, **kwargs: Any) -> Any:
    """`check(*args)` with the model error it raises given a stable `code`."""
    try:
        return check(*args, **kwargs)
    except ModelError as exc:
        raise ModelError(str(exc), code) from None


def _set(obj: object, name: str, value: object) -> None:
    object.__setattr__(obj, name, value)


def dumps(obj: Any) -> str:
    """Canonical JSON (sorted keys, compact) of any model value."""
    return canonical_json(obj.to_dict())


# ----------------------------------------------------------------------------- reads


class Read:
    """Every read answers one of three ways (context 7.3)."""

    FOUND, ABSENT, UNREADABLE = "found", "absent", "unreadable"
    ALL = (FOUND, ABSENT, UNREADABLE)


@dataclass(frozen=True, slots=True, init=False, match_args=False)
class ReadResult:
    """The answer of one read. Test `.state` (or `is_found`, `is_absent`, `is_unreadable`):
    the object itself is neither true nor false, and `.text` only exists when the read found
    something, so an unreadable read can never be mistaken for an absence (rule 4.5)."""

    __match_args__ = ("state",)
    state: str
    _text: str | None
    detail: str | None

    def __init__(self, state: str, text: str | None = None, detail: str | None = None) -> None:
        _choice("ReadResult", "state", state, Read.ALL)
        if state == Read.FOUND:
            if not isinstance(text, str):
                raise ModelError("a found read needs its text")
            check_encodable("ReadResult.text", text)
            if detail is not None:
                raise ModelError("a found read has no detail")
        else:
            if text is not None:
                raise ModelError(f"a read that is {state} has no text")
            if state == Read.UNREADABLE:
                _str("ReadResult", "detail", detail)
            elif detail is not None:
                raise ModelError("an absent read has no detail")
        _set(self, "state", state)
        _set(self, "_text", text)
        _set(self, "detail", detail)

    @classmethod
    def found(cls, text: str) -> "ReadResult":
        return cls(Read.FOUND, text=text)

    @classmethod
    def absent(cls) -> "ReadResult":
        return cls(Read.ABSENT)

    @classmethod
    def unreadable(cls, detail: str) -> "ReadResult":
        return cls(Read.UNREADABLE, detail=detail)

    def __bool__(self) -> bool:
        raise TypeError("a ReadResult has no truth value: test .state (is_found, is_absent, is_unreadable)")

    @property
    def is_found(self) -> bool:
        return self.state == Read.FOUND

    @property
    def is_absent(self) -> bool:
        return self.state == Read.ABSENT

    @property
    def is_unreadable(self) -> bool:
        return self.state == Read.UNREADABLE

    @property
    def text(self) -> str:
        """The text of a found read. Raises ModelError for an absent read and UnreadableError
        for an unreadable one."""
        if self.state == Read.FOUND:
            assert self._text is not None
            return self._text
        if self.state == Read.UNREADABLE:
            raise UnreadableError(f"the read was unreadable: {self.detail}", self.detail)
        raise ModelError("the read found nothing: it is absent, there is no text")

    def text_or_none(self) -> str | None:
        """The text when found, None only when the path is absent; raises UnreadableError
        when the read failed (an unreadable read is never an absence)."""
        if self.state == Read.UNREADABLE:
            raise UnreadableError(f"the read was unreadable: {self.detail}", self.detail)
        return self._text

    def record_if_unreadable(self, target: Any, subject: str) -> bool:
        """If the read was unreadable, add a blocking read_unreadable record to target and
        return True; otherwise return False."""
        if self.state != Read.UNREADABLE:
            return False
        record(target, "read_unreadable", subject, detail=self.detail or "")
        return True

    def to_dict(self) -> dict[str, Any]:
        return {"state": self.state, "text": self._text, "detail": self.detail}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReadResult":
        expect_keys("ReadResult", data, ("state", "text", "detail"))
        return cls(data["state"], data["text"], data["detail"])


# ----------------------------------------------------------------------------- evidence


@dataclass(frozen=True, slots=True)
class Evidence:
    """One pinned citation: the verbatim line `detail` at `path:line` of `repo` at commit `ref`.

    `ref` is a full commit id (40 hex, or 64 for sha-256 repositories), never a branch name.
    `detail` is one line: a newline is refused. Adapters strip the trailing carriage return of
    a CRLF line before building Evidence (a carriage return elsewhere in the line is kept).
    Text must be valid unicode: decode source with errors="replace", never surrogateescape.

    `claim` is optional, set by the adapters where a line says more than its text: an import hop (`import:<resolved target
    path>:<sorted names the import takes, or *>`), a route line (`route:<route path>:<component file>`), the definition line of a
    changed symbol (`def:<symbol>`) and the line of a wrapper that says it wraps the routes below it (`wraps:<wrapper path>`: the Next.js
    adapter's layout, `_app` or middleware line that renders its children, which no file otherwise states). The fingerprint of a finding or an area hashes the claim when there is one and the stripped
    detail otherwise, so a decision does not go stale when an import is respelled (an alias for a relative path) or a line is
    reformatted. `detail` stays what is shown and what "what changed" is told from. Two pieces of evidence for the same place are
    equal whatever their claims (a chain does not list a line twice); the key is in the JSON only when it is set."""

    repo: str
    path: str  # repo-relative POSIX
    line: int  # 1-based
    ref: str  # full commit sha, never a branch name
    detail: str  # the verbatim line, no newline
    claim: str | None = field(default=None, compare=False)  # what the line says, apart from how it is spelled (see below)

    def __post_init__(self) -> None:
        _str("Evidence", "repo", self.repo)
        _int("Evidence", "line", self.line, 1)
        if not _is_commit(self.ref):
            raise ModelError(f"Evidence.ref must be a full commit sha (40 or 64 hex, never all zeros), got {self.ref!r}")
        path = _str("Evidence", "path", self.path)
        if path.startswith("/") or "\\" in path or any(p in ("", ".", "..") for p in path.split("/")):
            raise ModelError(f"Evidence.path must be a repo-relative POSIX path, got {path!r}")
        detail = _str("Evidence", "detail", self.detail)
        if "\n" in detail:
            raise ModelError("Evidence.detail must be one line with no newline")
        if self.claim is not None:
            if "\n" in _str("Evidence", "claim", self.claim):
                raise ModelError("Evidence.claim must be one line with no newline")

    @property
    def locator(self) -> str:
        return f"{self.ref}:{self.path}:{self.line}"

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"repo": self.repo, "path": self.path, "line": self.line, "ref": self.ref, "detail": self.detail}
        if self.claim is not None:
            out["claim"] = self.claim
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Evidence":
        base = ("repo", "path", "line", "ref", "detail")
        expect_keys("Evidence", data, (*base, "claim") if isinstance(data, dict) and "claim" in data else base)
        return cls(**data)


def _evidence_item(owner: str, value: object) -> Evidence:
    if isinstance(value, Decision):
        raise ModelError(f"{owner}: a Decision is never evidence (a human decision accepts risk, it proves nothing)")
    if not isinstance(value, Evidence):
        raise ModelError(f"{owner}: evidence must be Evidence, got {type(value).__name__}")
    return value


def _evidence_tuple(owner: str, values: object) -> tuple[Evidence, ...]:
    if not isinstance(values, (list, tuple)):
        raise ModelError(f"{owner}: evidence must be a list of Evidence")
    return tuple(_evidence_item(owner, v) for v in values)


# ----------------------------------------------------------------------------- findings


_RANKS = {"low": 1, "medium": 2, "high": 3}


class Risk(Enum):
    """How bad it is if the claim is true. A plain Enum: it never equals or orders against
    Confidence, so the two cannot be mixed by accident. Order one scale with `.rank`."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @property
    def rank(self) -> int:
        return _RANKS[self.value]


class Confidence(Enum):
    """How well the evidence supports the claim. See Risk."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @property
    def rank(self) -> int:
        return _RANKS[self.value]


# kind -> whether a finding of that kind is an exclusion ("this is not reached")
_FINDING_KINDS: dict[str, bool] = {
    "direct": False, "cross-repo": False, "duplicate": False, "exclusion": True, "release": False,
    "contract-relaxed": False, "contract-tightened": False, "removed_route_still_called": False,
    "added_route_uncalled": False, "guard-excluded": True, "branch-missing": False, "operation-changed": False,
}


def register_finding_kind(kind: str, *, is_exclusion: bool = False) -> None:
    """Let an adapter add a finding kind. Rejects a duplicate or an odd name. Whether a finding
    is an exclusion belongs to its kind, so an affected claim and an excluded claim about the
    same place never share an id or a fingerprint."""
    if not isinstance(kind, str) or not _KIND_NAME.match(kind):
        raise ModelError(f"finding kind {kind!r}: use lowercase letters, digits, underscores and hyphens")
    if kind in _FINDING_KINDS:
        raise ModelError(f"finding kind {kind!r} is already registered")
    if not isinstance(is_exclusion, bool):
        raise ModelError("is_exclusion must be a bool")
    _FINDING_KINDS[kind] = is_exclusion


def finding_kinds() -> tuple[str, ...]:
    return tuple(_FINDING_KINDS)


@dataclass(frozen=True, slots=True)
class Finding:
    """One evidence-backed claim. `identity` names the claim (kind, repo, path, symbol,
    route ...), not the title, which carries counts that move with the code."""

    kind: str
    title: str
    workflow: str
    risk: Risk
    confidence: Confidence
    confidence_basis: str
    evidence: tuple[Evidence, ...]
    identity: tuple[str, ...]
    claim_type: ClassVar[str] = "finding"

    def __post_init__(self) -> None:
        if self.kind not in _FINDING_KINDS:
            raise ModelError(f"unknown finding kind {self.kind!r}")
        for name in ("title", "confidence_basis"):
            require_clean(_str("Finding", name, getattr(self, name)), f"Finding.{name}")
        require_clean(_str("Finding", "workflow", self.workflow, empty=True), "Finding.workflow")
        for name, enum in (("risk", Risk), ("confidence", Confidence)):
            try:
                _set(self, name, enum(getattr(self, name)))
            except ValueError:
                raise ModelError(f"Finding.{name} must be one of {[m.value for m in enum]}") from None
        evidence = _evidence_tuple("Finding", self.evidence)
        if not evidence:
            kind = "exclusion" if self.is_exclusion else "finding"
            raise ModelError(f"a {kind} needs evidence: a claim without a citation must not exist (rule 4.1)")
        _set(self, "evidence", evidence)
        _set(self, "identity", _strings("Finding", "identity", self.identity, nonempty=True))

    @property
    def is_exclusion(self) -> bool:
        """Derived from the kind (see register_finding_kind)."""
        return _FINDING_KINDS[self.kind]

    @property
    def id(self) -> str:
        return finding_id(self.kind, self.identity)

    def all_evidence(self) -> tuple[Evidence, ...]:
        return self.evidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "title": self.title,
            "workflow": self.workflow,
            "risk": self.risk.value,
            "confidence": self.confidence.value,
            "confidence_basis": self.confidence_basis,
            "evidence": [e.to_dict() for e in self.evidence],
            "identity": list(self.identity),
            "is_exclusion": self.is_exclusion,
            "id": self.id,
            "fingerprint": claim_fingerprint(self),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Finding":
        keys = ("kind", "title", "workflow", "risk", "confidence", "confidence_basis", "evidence", "identity", "is_exclusion", "id", "fingerprint")
        expect_keys("Finding", data, keys)
        found = cls(**{
            **{k: v for k, v in data.items() if k not in ("is_exclusion", "id", "fingerprint")},
            "evidence": [Evidence.from_dict(e) for e in data["evidence"]],
        })
        if found.is_exclusion != data["is_exclusion"]:
            raise ModelError(f"Finding.is_exclusion does not match its kind {found.kind!r}")
        _verify_claim("Finding", found, data)
        return found


def _verify_claim(owner: str, item: Any, data: dict[str, Any]) -> None:
    """The `id` and `fingerprint` in the JSON of a finding or an area are what a decision is made about; they are checked against the
    values computed from the object read, like `is_exclusion` is checked against the kind."""
    if data["id"] != item.id:
        raise ModelError(f"{owner}.id does not match what the {owner.lower()} hashes to")
    if data["fingerprint"] != claim_fingerprint(item):
        raise ModelError(f"{owner}.fingerprint does not match the claim and its evidence")


# ----------------------------------------------------------------------------- areas

AREA_KINDS = ("screen", "api_route", "task", "job", "topic")
MUST_TEST, SAMPLE, CANDIDATE = "must_test", "sample", "candidate"
PRIORITIES = (MUST_TEST, SAMPLE, CANDIDATE)
MUST_TEST_MAX_SCREENS = 8  # at most this many import-backed screens: all must_test, else sample


@dataclass(frozen=True, slots=True)
class Area:
    """A screen or entry point a person can test, with the chains of evidence that reach it."""

    label: str  # a route path or name; exempt from the wording check
    kind: str
    repo: str
    module: str
    route_line: Evidence
    chains: tuple[tuple[Evidence, ...], ...]
    also_mounted: tuple[str, ...]
    priority: str
    by_name: bool  # a candidate found by name, not import-backed
    claim_type: ClassVar[str] = "area"

    def __post_init__(self) -> None:
        _str("Area", "label", self.label)
        _choice("Area", "kind", self.kind, AREA_KINDS)
        _str("Area", "repo", self.repo)
        _str("Area", "module", self.module, empty=True)
        _set(self, "route_line", _evidence_item("Area.route_line", self.route_line))
        if not isinstance(self.chains, (list, tuple)):
            raise ModelError("Area.chains must be a list of chains")
        chains = tuple(_evidence_tuple("Area.chains", c) for c in self.chains)
        if any(not chain for chain in chains):
            raise ModelError("Area.chains: a chain with no evidence is not a chain")
        _set(self, "chains", chains)
        _set(self, "also_mounted", _strings("Area", "also_mounted", self.also_mounted))
        _choice("Area", "priority", self.priority, PRIORITIES)
        _bool("Area", "by_name", self.by_name)
        if self.by_name and self.priority != CANDIDATE:
            raise ModelError("a by-name area is a candidate, never must_test or sample")
        if not self.by_name and not chains:
            raise ModelError("an import-backed area needs at least one chain with evidence")

    @property
    def id(self) -> str:
        return area_id(self.kind, self.repo, self.label)

    @property
    def identity(self) -> tuple[str, ...]:
        return (self.repo, self.label)

    def all_evidence(self) -> tuple[Evidence, ...]:
        return (self.route_line, *(e for chain in self.chains for e in chain))

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "kind": self.kind,
            "repo": self.repo,
            "module": self.module,
            "route_line": self.route_line.to_dict(),
            "chains": [[e.to_dict() for e in chain] for chain in self.chains],
            "also_mounted": list(self.also_mounted),
            "priority": self.priority,
            "by_name": self.by_name,
            "id": self.id,
            "fingerprint": claim_fingerprint(self),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Area":
        keys = ("label", "kind", "repo", "module", "route_line", "chains", "also_mounted", "priority", "by_name", "id", "fingerprint")
        expect_keys("Area", data, keys)
        found = cls(**{
            **{k: v for k, v in data.items() if k not in ("id", "fingerprint")},
            "route_line": Evidence.from_dict(data["route_line"]),
            "chains": [[Evidence.from_dict(e) for e in chain] for chain in data["chains"]],
        })
        _verify_claim("Area", found, data)
        return found


def assign_priorities(areas: Sequence[Area]) -> list[Area]:
    """Return the areas with priorities set. The count that decides is the number of
    import-backed screens; the resulting priority applies to every import-backed area (routes,
    tasks, jobs and topics too): all must_test when the change reaches MUST_TEST_MAX_SCREENS or
    fewer import-backed screens, else all sample. By-name areas stay candidates. The input is
    not changed."""
    screens = sum(1 for a in areas if a.kind == "screen" and not a.by_name)
    backed = MUST_TEST if screens <= MUST_TEST_MAX_SCREENS else SAMPLE
    return [a if a.by_name else replace(a, priority=backed) for a in areas]


# ----------------------------------------------------------------------------- branches

BRANCH_STATES = ("present", "partial", "pre_change", "diverged", "file_absent", "unreadable")


@dataclass(frozen=True, slots=True)
class BranchState:
    """Whether a release branch carries the change. `unreadable` is not `file_absent`."""

    branch: str
    commit: str  # the branch tip read (full sha); empty only when unreadable and no tip could be pinned
    state: str
    added_lines_found: int
    evidence: tuple[Evidence, ...]
    detail: str = ""  # why, required when unreadable

    def __post_init__(self) -> None:
        _str("BranchState", "branch", self.branch)
        _choice("BranchState", "state", self.state, BRANCH_STATES)
        _int("BranchState", "added_lines_found", self.added_lines_found, 0)
        require_clean(_str("BranchState", "detail", self.detail, empty=True), "BranchState.detail")
        evidence = _evidence_tuple("BranchState", self.evidence)
        _set(self, "evidence", evidence)
        if self.state == "unreadable":
            _str("BranchState", "detail", self.detail)
        if not (self.state == "unreadable" and self.commit == "") and not _is_commit(self.commit):
            raise ModelError(f"BranchState.commit must be a full commit sha (40 or 64 hex), got {self.commit!r}")
        if self.state in ("present", "partial", "pre_change", "diverged") and not evidence:
            raise ModelError(f"a branch that is {self.state} needs evidence for the claim")
        if self.state == "present" and self.added_lines_found == 0:
            raise ModelError("a branch that is present has found added lines (added_lines_found > 0)")
        if self.state in ("file_absent", "pre_change") and self.added_lines_found != 0:
            raise ModelError(f"a branch that is {self.state} has found no added lines")

    def to_dict(self) -> dict[str, Any]:
        return {
            "branch": self.branch,
            "commit": self.commit,
            "state": self.state,
            "added_lines_found": self.added_lines_found,
            "evidence": [e.to_dict() for e in self.evidence],
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BranchState":
        keys = ("branch", "commit", "state", "added_lines_found", "evidence", "detail")
        expect_keys("BranchState", data, keys)
        return cls(**{**data, "evidence": [Evidence.from_dict(e) for e in data["evidence"]]})


# ----------------------------------------------------------------------------- decisions

AREA_VERDICTS = ("test", "sample", "skip")
FINDING_VERDICTS = ("accept", "reject")
NEEDS_REASON = ("skip", "reject")
_DESCRIPTIONS = {
    "test": "marked to test",
    "sample": "marked to sample",
    "skip": "risk accepted, not tested",
    "accept": "finding accepted as stated",
    "reject": "finding rejected by the reviewer",
}


@dataclass(frozen=True, slots=True)
class Decision:
    """A human decision about a finding (`f-...`) or an area (`a-...`). It accepts risk; it is
    never evidence and clears no gap."""

    target_id: str
    verdict: str
    reason: str
    author: str
    channel: str
    fingerprint: str  # identity.fingerprint of the target when the decision was made
    revision: int  # report revision when the decision was made

    def __post_init__(self) -> None:
        target = self.target_id
        if not isinstance(target, str) or not _TARGET_ID.match(target):
            raise ModelError(f"Decision.target_id must be an area id or a finding id (a- or f- and 16 hex), got {target!r}", "decision_target")
        _coded("decision_verdict", _choice, "Decision", "verdict", self.verdict, AREA_VERDICTS if target.startswith("a-") else FINDING_VERDICTS)
        _coded("decision_reason", _str, "Decision", "reason", self.reason, empty=True)
        if self.verdict in NEEDS_REASON and not self.reason.strip():
            raise ModelError(f"a {self.verdict} decision needs a reason", "decision_reason")
        _coded("decision_author", _str, "Decision", "author", self.author)
        _coded("decision_channel", _str, "Decision", "channel", self.channel)
        if not isinstance(self.fingerprint, str) or not _FINGERPRINT.match(self.fingerprint):
            raise ModelError("Decision.fingerprint must be a 64-hex fingerprint", "decision_fingerprint")
        _coded("decision_revision", _int, "Decision", "revision", self.revision, 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "verdict": self.verdict,
            "reason": self.reason,
            "author": self.author,
            "channel": self.channel,
            "fingerprint": self.fingerprint,
            "revision": self.revision,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Decision":
        expect_keys("Decision", data, ("target_id", "verdict", "reason", "author", "channel", "fingerprint", "revision"))
        return cls(**data)


def verdict_text(verdict: str) -> str:
    """The words for a verdict, the tool's own (a skip is "risk accepted, not tested": rule 4.4). `describe` adds the reviewer's reason."""
    return _DESCRIPTIONS[verdict]


def describe(decision: Decision) -> str:
    """Plain wording for a decision. A skip is risk accepted, not tested (rule 4.4). The
    reviewer's reason is quoted and attributed, so "it is safe" never reads as the report's
    own claim."""
    text = verdict_text(decision.verdict)
    if not decision.reason.strip():
        return text
    return f"{text} (reason from {decision.author}: {json.dumps(decision.reason, ensure_ascii=False)})"


# ----------------------------------------------------------------------------- suggestions

SUGGESTION_LABEL = "suggested, not verified coverage"


@dataclass(frozen=True, slots=True)
class Suggestion:
    """A test case from a test source: a place to look, never coverage.

    `area_id` is the area (an id of the run's `areas`) the case was suggested for; a case suggested for two areas is two
    suggestions. `evidence` is for a source that reads code (a Playwright or Cypress spec): the spec line that visits the
    screen, pinned like every citation. Neither makes a suggestion more than it is: `coverage_gaps` never reads them."""

    source: str
    case_id: str
    title: str  # the case's own title, quoted; exempt from the wording check
    matched_words: tuple[str, ...]
    area_id: str | None = None
    evidence: tuple[Evidence, ...] = ()

    def __post_init__(self) -> None:
        _str("Suggestion", "source", self.source)
        _str("Suggestion", "case_id", self.case_id)
        _str("Suggestion", "title", self.title)
        _set(self, "matched_words", _strings("Suggestion", "matched_words", self.matched_words))
        if self.area_id is not None and (not isinstance(self.area_id, str) or not re.match(r"^a-[0-9a-f]{16}$", self.area_id)):
            raise ModelError(f"Suggestion.area_id must be an area id (a- and 16 hex) or None, got {self.area_id!r}")
        _set(self, "evidence", _evidence_tuple("Suggestion", self.evidence))

    @property
    def label(self) -> str:
        return SUGGESTION_LABEL

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "case_id": self.case_id,
            "title": self.title,
            "matched_words": list(self.matched_words),
            "area_id": self.area_id,
            "evidence": [e.to_dict() for e in self.evidence],
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Suggestion":
        base = ("source", "case_id", "title", "matched_words", "label")
        optional = tuple(k for k in ("area_id", "evidence") if isinstance(data, dict) and k in data)  # a suggestion written before they existed has neither
        expect_keys("Suggestion", data, base + optional)
        if data["label"] != SUGGESTION_LABEL:
            raise ModelError(f"Suggestion.label must be {SUGGESTION_LABEL!r}")
        return cls(data["source"], data["case_id"], data["title"], data["matched_words"], data.get("area_id"),
                   tuple(Evidence.from_dict(e) for e in data.get("evidence", ())))


@dataclass(frozen=True, slots=True)
class MappedTest:
    """A test case that the workspace configuration maps to an area. Kept apart from
    suggestions: only a mapped test can close a gap in `coverage_gaps`."""

    area_id: str
    case_id: str
    title: str
    source: str
    mapping: str  # the configuration entry that maps this case to the area

    def __post_init__(self) -> None:
        if not isinstance(self.area_id, str) or not re.match(r"^a-[0-9a-f]{16}$", self.area_id):
            raise ModelError(f"MappedTest.area_id must be an area id (a- and 16 hex), got {self.area_id!r}")
        for name in ("case_id", "title", "source", "mapping"):
            _str("MappedTest", name, getattr(self, name))

    def to_dict(self) -> dict[str, Any]:
        return {"area_id": self.area_id, "case_id": self.case_id, "title": self.title,
                "source": self.source, "mapping": self.mapping}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MappedTest":
        expect_keys("MappedTest", data, ("area_id", "case_id", "title", "source", "mapping"))
        return cls(**data)


# ----------------------------------------------------------------------------- analysis

COMPLETED, TIMED_OUT, FAILED = "completed", "timed_out", "failed"
STATUSES = (COMPLETED, TIMED_OUT, FAILED)
RESULT_KEYS = ("findings", "areas", "branches", "propagations", "backend_calls", "suggestions", "mapped_tests")  # null in a run that did not complete
_FAILURE_RECORD = {TIMED_OUT: "timed_out", FAILED: "run_failed"}  # status -> required incompleteness kind


def _json_object(owner: str, name: str, value: object) -> dict[str, Any]:
    """A deep copy of a JSON object, refusing anything that is not plain JSON."""
    if not isinstance(value, dict):
        raise ModelError(f"{owner}.{name} must be a JSON object")
    try:
        text = canonical_json(value)
        text.encode("utf-8")
        return json.loads(text)
    except (TypeError, ValueError) as exc:  # includes UnicodeEncodeError (a lone surrogate)
        raise ModelError(f"{owner}.{name} must be plain JSON: {exc}") from None


class Analysis:
    """One run's result. A run fills it in through the `add_*` methods and ends it with
    `finish`; the collections are read-only tuples and the status can only be changed by
    `finish`, so nothing is checked once and then mutated past its checks. `validate()` (called
    by `to_dict`) re-runs every check.

    `change`, `propagations` and `backend_calls` are JSON objects whose shape later milestones
    define; they are deep-copied in and out. A run that timed out or failed has no partial
    results: findings and areas are dropped, `to_dict` writes them as null, and the analysis
    carries the blocking incompleteness record of the failure.
    """

    __slots__ = (
        "_change", "_ref", "_branches", "_findings", "_areas", "_propagations", "_backend_calls",
        "_suggestions", "_mapped_tests", "_incompleteness", "_notes", "_revision", "_status",
    )

    def __init__(
        self,
        change: dict[str, Any],
        ref: str | None,
        branches: Sequence[BranchState] = (),
        findings: Sequence[Finding] = (),
        areas: Sequence[Area] = (),
        propagations: Sequence[dict[str, Any]] = (),
        backend_calls: Sequence[dict[str, Any]] = (),
        suggestions: Sequence[Suggestion] = (),
        mapped_tests: Sequence[MappedTest] = (),
        incompleteness: Sequence[Incompleteness] = (),
        notes: Sequence[str] = (),
        revision: int = 1,
        status: str = COMPLETED,
    ) -> None:
        self._change = _json_object("Analysis", "change", change)
        self._ref = ref
        self._revision = revision
        self._status = COMPLETED  # set for real below, once the items are in
        self._branches: list[BranchState] = []
        self._findings: list[Finding] = []
        self._areas: list[Area] = []
        self._propagations: list[dict[str, Any]] = []
        self._backend_calls: list[dict[str, Any]] = []
        self._suggestions: list[Suggestion] = []
        self._mapped_tests: list[MappedTest] = []
        self._incompleteness: list[Incompleteness] = []
        self._notes: list[str] = []
        for add, items in (
            (self.add_branch, branches), (self.add_finding, findings), (self.add_area, areas),
            (self.add_propagation, propagations), (self.add_backend_call, backend_calls),
            (self.add_suggestion, suggestions), (self.add_mapped_test, mapped_tests),
            (self.add_incompleteness, incompleteness), (self.add_note, notes),
        ):
            if isinstance(items, (str, dict)) or not hasattr(items, "__iter__"):
                raise ModelError("Analysis collections must be lists")
            for item in items:
                add(item)
        self._status = status
        self.validate()

    # ---- read-only views

    @property
    def change(self) -> dict[str, Any]:
        return copy.deepcopy(self._change)

    @property
    def ref(self) -> str | None:
        """The commit analysed; None only for a run that did not complete and never resolved its change."""
        return self._ref

    @property
    def revision(self) -> int:
        return self._revision

    @property
    def status(self) -> str:
        return self._status

    @property
    def branches(self) -> tuple[BranchState, ...]:
        return tuple(self._branches)

    @property
    def findings(self) -> tuple[Finding, ...]:
        return tuple(self._findings)

    @property
    def areas(self) -> tuple[Area, ...]:
        return tuple(self._areas)

    @property
    def propagations(self) -> tuple[dict[str, Any], ...]:
        return tuple(copy.deepcopy(self._propagations))

    @property
    def backend_calls(self) -> tuple[dict[str, Any], ...]:
        return tuple(copy.deepcopy(self._backend_calls))

    @property
    def suggestions(self) -> tuple[Suggestion, ...]:
        return tuple(self._suggestions)

    @property
    def mapped_tests(self) -> tuple[MappedTest, ...]:
        return tuple(self._mapped_tests)

    @property
    def incompleteness(self) -> tuple[Incompleteness, ...]:
        return tuple(self._incompleteness)

    @property
    def notes(self) -> tuple[str, ...]:
        return tuple(self._notes)

    # ---- adding

    def _add(self, store: list[Any], name: str, item: object, cls: type) -> Any:
        if not isinstance(item, cls):
            raise ModelError(f"Analysis.{name} takes {cls.__name__}, got {type(item).__name__}")
        store.append(item)
        return item

    def _add_result(self, store: list[Any], name: str, item: Any, cls: type) -> Any:
        if self._status != COMPLETED:
            raise ModelError(f"an analysis that {self._status} keeps no partial {name}")
        if not isinstance(item, cls):
            raise ModelError(f"Analysis.{name} takes {cls.__name__}, got {type(item).__name__}")
        if any(existing.id == item.id for existing in store):
            raise ModelError(f"Analysis already has {name[:-1]} {item.id}")
        store.append(item)
        return item

    def add_finding(self, finding: Finding) -> Finding:
        return self._add_result(self._findings, "findings", finding, Finding)

    def add_area(self, area: Area) -> Area:
        return self._add_result(self._areas, "areas", area, Area)

    def _completed_only(self, name: str) -> None:
        if self._status != COMPLETED:
            raise ModelError(f"an analysis that {self._status} keeps no partial {name}")

    def add_branch(self, branch: BranchState) -> BranchState:
        self._completed_only("branches")
        return self._add(self._branches, "branches", branch, BranchState)

    def add_suggestion(self, suggestion: Suggestion) -> Suggestion:
        self._completed_only("suggestions")
        return self._add(self._suggestions, "suggestions", suggestion, Suggestion)

    def add_mapped_test(self, mapped_test: MappedTest) -> MappedTest:
        self._completed_only("mapped_tests")
        return self._add(self._mapped_tests, "mapped_tests", mapped_test, MappedTest)

    def add_propagation(self, propagation: dict[str, Any]) -> dict[str, Any]:
        self._completed_only("propagations")
        self._propagations.append(_json_object("Analysis", "propagations", propagation))
        return propagation

    def add_backend_call(self, call: dict[str, Any]) -> dict[str, Any]:
        self._completed_only("backend_calls")
        self._backend_calls.append(_json_object("Analysis", "backend_calls", call))
        return call

    def add_note(self, note: str) -> str:
        require_clean(_str("Analysis", "notes", note), "Analysis.notes")
        self._notes.append(note)
        return note

    def add_incompleteness(self, rec: Incompleteness) -> Incompleteness:
        """Add a record; an equal record already present is returned instead of added twice."""
        if not isinstance(rec, Incompleteness):
            raise ModelError(f"Analysis.incompleteness takes Incompleteness, got {type(rec).__name__}")
        for existing in self._incompleteness:
            if existing == rec:
                return existing
        self._incompleteness.append(rec)
        return rec

    def finish(self, status: str, detail: str = "", subject: str | None = None) -> None:
        """End the run. `completed` changes nothing. `timed_out` and `failed` are final: every partial result (findings, areas,
        branches, propagations, backend calls, suggestions, mapped tests) is dropped and the blocking record of the failure is
        added. Its subject is the analysed ref; a run that never resolved its change has no ref and names `subject` (what was
        asked for, such as `compare main...topic`). A failed run never becomes completed, or another failure."""
        _choice("Analysis", "status", status, STATUSES)
        if self._status != COMPLETED and status != self._status:
            raise ModelError(f"an analysis that {self._status} cannot become {status}")
        if status == COMPLETED:
            return
        named = self._ref if self._ref is not None else subject
        if not named:
            raise ModelError("a run with no ref needs the subject of its failure record (what was asked for)")
        record(self, _FAILURE_RECORD[status], named, detail=detail)  # may raise: change nothing before it
        for store in (self._findings, self._areas, self._branches, self._propagations, self._backend_calls, self._suggestions, self._mapped_tests):
            store.clear()
        self._status = status

    # ---- checks

    def validate(self) -> None:
        """Re-run every check on the stored state. Raises ModelError (or WordingError)."""
        _int("Analysis", "revision", self._revision, 1)
        _choice("Analysis", "status", self._status, STATUSES)
        if self._ref is not None and not _is_commit(self._ref):
            raise ModelError(f"Analysis.ref must be a full commit sha (40 or 64 hex, never all zeros), got {self._ref!r}")
        _json_object("Analysis", "change", self._change)
        for name, store, cls in (
            ("branches", self._branches, BranchState), ("findings", self._findings, Finding),
            ("areas", self._areas, Area), ("suggestions", self._suggestions, Suggestion),
            ("mapped_tests", self._mapped_tests, MappedTest), ("incompleteness", self._incompleteness, Incompleteness),
        ):
            for item in store:
                if not isinstance(item, cls):
                    raise ModelError(f"Analysis.{name} holds {type(item).__name__}, expected {cls.__name__}")
        for name, store in (("findings", self._findings), ("areas", self._areas)):
            ids = [item.id for item in store]
            if len(ids) != len(set(ids)):
                raise ModelError(f"Analysis.{name} has a duplicate id")
        for name, store in (("propagations", self._propagations), ("backend_calls", self._backend_calls)):
            for item in store:
                _json_object("Analysis", name, item)
        for note in self._notes:
            require_clean(_str("Analysis", "notes", note), "Analysis.notes")
        if self._status != COMPLETED:
            if any((self._findings, self._areas, self._branches, self._propagations, self._backend_calls, self._suggestions, self._mapped_tests)):
                raise ModelError(f"an analysis that {self._status} keeps no partial results")
            needed = _FAILURE_RECORD[self._status]
            if not any(rec.kind == needed for rec in self._incompleteness):
                raise ModelError(f"an analysis that {self._status} must carry a {needed} incompleteness record")

    # ---- results

    @property
    def finding_count(self) -> int:
        """Number of findings (exclusions included). Raises for a run that did not complete:
        a timeout is not a result, and a partial run must never read as zero."""
        _require_completed(self, "a finding count")
        return len(self._findings)

    def blocking(self) -> list[Incompleteness]:
        """The records that block selection (which areas and findings the change has)."""
        return [rec for rec in self._incompleteness if rec.blocks_selection]

    def blocking_suggestions(self) -> list[Incompleteness]:
        """The records that say the suggested cases may be incomplete (a test source that did not answer, a list cut short)."""
        return [rec for rec in self._incompleteness if rec.blocks_suggestions]

    def _content(self) -> dict[str, Any]:
        return {
            "change": copy.deepcopy(self._change),
            "ref": self._ref,
            "branches": [b.to_dict() for b in self._branches],
            "findings": [f.to_dict() for f in self._findings],
            "areas": [a.to_dict() for a in self._areas],
            "propagations": copy.deepcopy(self._propagations),
            "backend_calls": copy.deepcopy(self._backend_calls),
            "suggestions": [s.to_dict() for s in self._suggestions],
            "mapped_tests": [m.to_dict() for m in self._mapped_tests],
            "incompleteness": [i.to_dict() for i in self._incompleteness],
            "notes": list(self._notes),
            "revision": self._revision,
            "status": self._status,
        }

    def to_dict(self) -> dict[str, Any]:
        """Validates first. For a run that did not complete every result list (findings, areas, branches, propagations,
        backend calls, suggestions, mapped tests) is null: a partial list must never be countable, and an empty one would
        read as "none found"."""
        self.validate()
        _require_ref(self)
        data = self._content()
        if self._status != COMPLETED:
            for key in RESULT_KEYS:
                data[key] = None
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Analysis":
        keys = ("change", "ref", "branches", "findings", "areas", "propagations", "backend_calls",
                "suggestions", "mapped_tests", "incompleteness", "notes", "revision", "status")
        expect_keys("Analysis", data, keys)
        for name in RESULT_KEYS:
            if data[name] is None and data["status"] == COMPLETED:
                raise ModelError(f"a completed analysis cannot have null {name}")
        if data["ref"] is None and data["status"] == COMPLETED:
            raise ModelError("a completed analysis has the ref it analysed")
        return cls(
            change=data["change"],
            ref=data["ref"],
            branches=[BranchState.from_dict(b) for b in data["branches"] or ()],
            findings=[Finding.from_dict(f) for f in data["findings"] or ()],
            areas=[Area.from_dict(a) for a in data["areas"] or ()],
            propagations=data["propagations"] or (),
            backend_calls=data["backend_calls"] or (),
            suggestions=[Suggestion.from_dict(s) for s in data["suggestions"] or ()],
            mapped_tests=[MappedTest.from_dict(m) for m in data["mapped_tests"] or ()],
            incompleteness=[Incompleteness.from_dict(i) for i in data["incompleteness"]],
            notes=data["notes"],
            revision=data["revision"],
            status=data["status"],
        )

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Analysis) and self._content() == other._content()

    __hash__ = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        return f"Analysis(ref={self._ref!r}, revision={self._revision}, status={self._status!r})"


def _require_completed(analysis: Analysis, what: str) -> None:
    if analysis.status != COMPLETED:
        raise ModelError(f"an analysis that {analysis.status} has no result to give {what}")
    _require_ref(analysis)


def _require_ref(analysis: Analysis) -> None:
    """A run in progress may have no ref yet (the change is not resolved); a completed result always names its commit."""
    if analysis.status == COMPLETED and analysis.ref is None:
        raise ModelError("a completed analysis has the ref it analysed: only a run that did not complete may have none")


def coverage_gaps(analysis: Analysis) -> list[Area]:
    """The one function that counts coverage gaps (rule 4.3): the import-backed areas to test
    (must_test or sample) that have no mapped test. Suggestions and decisions never close a
    gap, and by-name candidates are not counted. Raises for a run that did not complete."""
    _require_completed(analysis, "coverage gaps")
    mapped = {m.area_id for m in analysis.mapped_tests}
    return [a for a in analysis.areas if a.priority in (MUST_TEST, SAMPLE) and a.id not in mapped]
