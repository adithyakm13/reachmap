"""Incompleteness: what a run could not establish (context section 3).

Incompleteness is data, not prose. `record()` is the one function that creates the record
and the human note together; the note is derived from the record by `note()` and never
stored, so the two cannot drift.

Each kind is registered with a default `blocks_selection` and a template for the
conclusion the run cannot support; a record's `not_established` is derived from that
template and its subject, never written by hand. A needed input that is missing or unreadable is
blocking (an absence was never established); a stated limit, or an optional source that is
not configured, is not. Kinds that are always blocking cannot be created non-blocking.
"""

import re
from dataclasses import dataclass
from typing import Any

from reachmap.errors import ModelError, check_encodable, expect_keys
from reachmap.wording import require_clean

_KIND_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


SCOPES = ("selection", "suggestions")


@dataclass(frozen=True, slots=True)
class KindSpec:
    """`scope` says what a record of the kind is about: `selection` (which areas and findings the change has: what blocks it is a
    needed input that is missing) or `suggestions` (the test cases suggested for them: a record of a test source). A record blocks
    its own scope and no other: `blocks_selection` is the default for a `selection` kind and `blocks_suggestions` for a `suggestions`
    kind (a source that did not answer says nothing about the areas)."""

    kind: str
    template: str  # the conclusion that cannot be supported; {subject} is filled in
    blocks_selection: bool  # default for records of this kind (always False for a suggestions kind)
    always_blocking: bool  # True: a record of this kind can never be non-blocking (for its scope)
    scope: str = "selection"
    blocks_suggestions: bool = False  # default for records of a suggestions kind


_KINDS: dict[str, KindSpec] = {}


def register_kind(kind: str, template: str, blocks_selection: bool, *, always_blocking: bool = False, scope: str = "selection") -> KindSpec:
    """Add a kind. Rejects a duplicate name, a bad name or template, and a contradiction. `blocks_selection` is whether a record of the
    kind blocks its scope by default: with `scope="suggestions"` it makes the kind block suggestions and never selection."""
    if scope not in SCOPES:
        raise ModelError(f"incompleteness kind {kind!r}: scope must be one of {list(SCOPES)}")
    if not isinstance(kind, str) or not _KIND_NAME.match(kind):
        raise ModelError(f"incompleteness kind {kind!r}: use lowercase letters, digits and underscores")
    if kind in _KINDS:
        raise ModelError(f"incompleteness kind {kind!r} is already registered")
    if not isinstance(blocks_selection, bool) or not isinstance(always_blocking, bool):
        raise ModelError(f"incompleteness kind {kind!r}: blocks_selection and always_blocking must be bools")
    if always_blocking and not blocks_selection:
        raise ModelError(f"incompleteness kind {kind!r}: always_blocking needs blocks_selection=True")
    try:
        sample = template.format(subject="x")
    except (KeyError, IndexError, ValueError) as exc:
        raise ModelError(f"incompleteness kind {kind!r}: template may only use {{subject}} ({exc})") from exc
    if sample == template:
        raise ModelError(f"incompleteness kind {kind!r}: template must contain {{subject}}")
    require_clean(template, f"template of {kind}")
    _KINDS[kind] = KindSpec(kind, template, blocks_selection and scope == "selection", always_blocking, scope, blocks_selection and scope == "suggestions")
    return _KINDS[kind]


def kind_spec(kind: str) -> KindSpec:
    try:
        return _KINDS[kind]
    except (KeyError, TypeError):
        raise ModelError(f"unknown incompleteness kind {kind!r}") from None


def kinds() -> tuple[str, ...]:
    return tuple(_KINDS)


@dataclass(frozen=True, slots=True)
class Incompleteness:
    kind: str
    subject: str  # what it is about; the format is fixed per kind
    detail: str  # prose, may be empty; checked for banned wording as written
    blocks_selection: bool

    def __post_init__(self) -> None:
        spec = kind_spec(self.kind)
        if not isinstance(self.subject, str) or not self.subject:
            raise ModelError("Incompleteness.subject must be a non-empty string")
        check_encodable("Incompleteness.subject", self.subject)
        if not isinstance(self.detail, str):
            raise ModelError("Incompleteness.detail must be a string")
        check_encodable("Incompleteness.detail", self.detail)
        if not isinstance(self.blocks_selection, bool):
            raise ModelError("Incompleteness.blocks_selection must be a bool")
        if spec.scope == "suggestions" and self.blocks_selection:
            raise ModelError(f"{self.kind} is a record about suggestions: it can block suggestions and never selection")
        if spec.always_blocking and spec.scope == "selection" and not self.blocks_selection:
            raise ModelError(f"{self.kind} always blocks selection: a needed input is missing, so it cannot be non-blocking")
        require_clean(self.detail, "Incompleteness.detail")

    @property
    def scope(self) -> str:
        return kind_spec(self.kind).scope

    @property
    def blocks_suggestions(self) -> bool:
        """Whether the record says the list of suggested cases may be incomplete (a source that did not answer, a list cut short)."""
        return kind_spec(self.kind).blocks_suggestions

    @property
    def not_established(self) -> str:
        """The conclusion the run cannot support: the kind's template filled with the subject.
        (The template is checked for banned wording when the kind is registered; the subject is
        quoted from the code and is not our claim.)"""
        return kind_spec(self.kind).template.format(subject=self.subject)

    def note(self) -> str:
        """The human note, derived from the record."""
        return f"{self.not_established}: {self.detail}" if self.detail else self.not_established

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "subject": self.subject,
            "not_established": self.not_established,
            "detail": self.detail,
            "blocks_selection": self.blocks_selection,
            "scope": self.scope,
            "blocks_suggestions": self.blocks_suggestions,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Incompleteness":
        """A record written before `scope` existed has neither key: a record of a test source then blocked selection in the JSON; it is
        read in its own scope (it blocks suggestions)."""
        base = ("kind", "subject", "not_established", "detail", "blocks_selection")
        newer = isinstance(data, dict) and "scope" in data
        expect_keys("Incompleteness", data, base + (("scope", "blocks_suggestions") if newer else ()))
        blocks = data["blocks_selection"]
        if not newer and isinstance(data["kind"], str) and data["kind"] in _KINDS and _KINDS[data["kind"]].scope == "suggestions":
            blocks = False
        rec = cls(data["kind"], data["subject"], data["detail"], blocks)
        if rec.not_established != data["not_established"]:
            raise ModelError("Incompleteness.not_established does not match the template of its kind")
        if newer and (data["scope"], data["blocks_suggestions"]) != (rec.scope, rec.blocks_suggestions):
            raise ModelError("Incompleteness.scope or blocks_suggestions does not match its kind")
        return rec


def records_of(target: Any) -> tuple[Incompleteness, ...]:
    """The records a target holds: a list, or an object with `incompleteness` (an Analysis)."""
    return tuple(target) if isinstance(target, list) else tuple(getattr(target, "incompleteness", ()))


def record(target: Any, kind: str, subject: str, *, detail: str = "", blocks_selection: bool | None = None) -> Incompleteness:
    """Create the record and add it to target: a list, or an object with `add_incompleteness`
    (an Analysis).

    `blocks_selection` defaults to the registered value; it may be raised to True for a
    kind that is non-blocking by default, never lowered for an always-blocking kind.
    A record equal to one already present is not added twice; the existing one is returned.
    """
    spec = kind_spec(kind)
    rec = Incompleteness(
        kind=kind,
        subject=subject,
        detail=detail,
        blocks_selection=spec.blocks_selection if blocks_selection is None else blocks_selection,
    )
    if isinstance(target, list):
        for existing in target:
            if existing == rec:
                return existing
        target.append(rec)
        return rec
    add = getattr(target, "add_incompleteness", None)
    if add is None:
        raise ModelError("record() needs a list or an object with add_incompleteness")
    return add(rec)


_SUGGESTION_SCOPED = ("source_unconfigured", "source_unavailable", "suggestions_incomplete", "spec_url_partial", "spec_visit_unmatched")  # the records of a test source

for _kind, _blocking, _always, _template in (
    ("hub_cutoff", False, False, "What imports {subject} was not established, because the walk stopped at this hub"),
    ("router_unmounted", False, False, "No route was established for the handlers on {subject}, because nothing mounts that router"),
    ("not_distinctive", False, False, "Links made through the name {subject} were not established, because it is defined too often to point at one thing"),
    ("pre_change_unavailable", True, True, "Removed routes, removed calls and narrowed exports for {subject} were not established, because there is no pre-change version"),
    ("hop_cap", False, False, "What lies past {subject} was not established, because the hop limit was reached"),
    ("module_cap", False, False, "Modules past the cap at {subject} were not established, because the per-hop module limit was reached"),
    ("read_unreadable", True, True, "Whether {subject} exists, and what it contains, was not established, because the read failed"),
    ("search_truncated", True, True, "Whether further matches exist for {subject} was not established, because the search was cut short"),
    ("diff_count_mismatch", True, True, "The full set of changed files for {subject} was not established, because the diff and the host file count disagree"),
    ("timed_out", True, True, "The rest of the analysis of {subject} was not established, because the run hit its time limit"),
    ("source_unconfigured", False, False, "Suggestions from {subject} were not looked up, because the source is not configured"),
    ("source_unavailable", True, True, "Suggestions from {subject} were not looked up, because the source did not answer"),
    ("suggestions_incomplete", True, True, "All suggestions from {subject} were not established, because the list of cases was cut short"),
    ("spec_url_partial", False, False, "Which screen {subject} visits was not established, because its address is not a literal"),
    ("spec_visit_unmatched", False, False, "Which screen the visits of {subject} reach was not established, because their addresses match no screen route"),
    ("run_failed", True, True, "The analysis of {subject} was not completed, because the run failed"),
):
    register_kind(_kind, _template, _blocking, always_blocking=_always, scope="suggestions" if _kind in _SUGGESTION_SCOPED else "selection")
