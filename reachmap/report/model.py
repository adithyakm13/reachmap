"""The view model: a report (the JSON of `reachmap analyse`) reordered into the sections a QA reader needs, in one place.

It takes the report JSON and nothing else: no repository, no configuration, no environment, so a report renders the same
anywhere. Everything copied out of the report is `Content` (shown whole, only made visible and cut when very long); what the
tool says about it is added by `layout`. Reading is strict about what the layout relies on (`report.check` has refused anything
else) and lenient about the deep payload of the propagation (a missing or odd part is shown as absent, never guessed).

Sections, in the order of the page: the header, what the run could not establish (before the areas when a record blocks
selection, after the findings otherwise), areas to test, findings (grouped by kind, exclusions apart), the branch matrix,
cross-repository reach, backend calls, suggested test cases (never merged with mapped tests) and, from `author`, the view for
the author of the change.

Nothing here does I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from reachmap.report import author
from reachmap.report.blocks import Content, content
from reachmap.report.read import blocks, scope_of
from reachmap.report.read import dct as _d
from reachmap.report.read import integer as _i
from reachmap.report.read import lst as _l
from reachmap.report.read import text as _s

__all__ = [
    "Decided", "DecisionView", "DecisionsView", "ModelNotesView", "NoteView", "VerificationView", "AreaView", "BackendView", "BranchRow", "ConsumerRow", "ContractChangeView", "ContractReadView", "Cross", "Ev", "FileChange", "FindingGroup",
    "FindingView", "Header", "HopView", "HttpView", "KIND_ORDER", "MappedView", "PullRequest", "Record", "Suggested", "SuggestionView", "View", "build",
]

# The kinds of finding `reachmap.findings` builds, in the order a reader meets them: what the change reaches, then what a contract or a
# release does, then the branches. A kind this list does not know is kept and follows, by name. (A test holds this list to `findings.TABLE_KINDS`.)
KIND_ORDER = (
    "direct", "cross-repo", "contract-tightened", "contract-relaxed", "removed_route_still_called", "added_route_uncalled", "operation-changed", "release",
    "branch-missing", "duplicate",
)
EXCLUSION_KINDS = ("exclusion", "guard-excluded")


def _c(v: Any) -> Content:
    return content(_s(v))


def _opt(v: Any) -> Content | None:
    return None if v is None else _c(v)


@dataclass(frozen=True, slots=True)
class Ev:
    """One pinned line: where it is, the line itself, and the commit it was read at (short)."""

    repo: Content
    path: Content
    line: int
    short: Content
    detail: Content

    @property
    def locator(self) -> Content:
        return Content(f"{self.path}:{self.line}")


def _ev(d: Any) -> Ev:
    d = _d(d)
    return Ev(_c(d.get("repo")), _c(d.get("path")), _i(d.get("line")), _c(_s(d.get("ref"))[:12]), _c(d.get("detail")))


def _evs(v: Any) -> tuple[Ev, ...]:
    return tuple(_ev(x) for x in _l(v))


@dataclass(frozen=True, slots=True)
class Record:
    kind: Content
    subject: Content
    not_established: Content
    detail: Content
    blocking: bool  # blocks selection: which areas and findings the change has
    blocks_suggestions: bool = False  # says the list of suggested cases may be incomplete
    scope: str = "selection"  # what the record is about: selection (areas and findings) or suggestions


@dataclass(frozen=True, slots=True)
class PullRequest:
    number: Content
    title: Content
    state: Content
    base_ref: Content
    head_ref: Content


@dataclass(frozen=True, slots=True)
class FileChange:
    path: Content
    old_path: Content | None
    status: Content
    role: Content | None
    added: int
    removed: int


@dataclass(frozen=True, slots=True)
class Header:
    repo: Content
    label: Content
    status: str
    tool: Content
    head: Content | None
    head_short: Content | None
    pre_change: Content | None
    pre_change_short: Content | None
    base: Content | None
    how: tuple[tuple[str, Content], ...]
    pr: PullRequest | None
    as_of: Content | None
    complete: bool | None
    reasons: int
    counts: tuple[tuple[str, int], ...] | None
    files: tuple[FileChange, ...]
    file_count: int


@dataclass(frozen=True, slots=True)
class DecisionView:
    """A recorded decision as the report carries it (`decisions.current`, `.stale`, `.orphaned`): every text is content, since the person
    typed it, and the words for a verdict are chosen by the layout from fixed ones."""

    state: str  # "current", "stale" or "orphaned": which list it was in
    target_id: Content
    kind: Content
    label: Content | None
    verdict: Content
    reason: Content
    author: Content
    channel: Content
    revision: int
    created_at: Content
    fingerprint: Content  # the claim the decision was made on, short
    now_fingerprint: Content | None  # stale: the claim now
    changes: tuple[tuple[Content, Content, Content], ...] | None  # stale, when known: (side, where, the line) of what was added and removed
    changes_known: bool
    viewed: int | None  # the revision of the report this is shown in (a stale decision is worded against it)


@dataclass(frozen=True, slots=True)
class Decided:
    """What an area or a finding shows of its decision: `current` (the decision), `stale` (it needs review and is not applied) or `none`
    (shown only when the report has decisions at all, so that a report nobody decided on stays as it was)."""

    state: str
    entry: DecisionView | None


@dataclass(frozen=True, slots=True)
class DecisionsView:
    current: tuple[DecisionView, ...]
    stale: tuple[DecisionView, ...]
    orphaned: tuple[DecisionView, ...]
    undecided_areas: tuple[Content, ...]
    undecided_findings: tuple[tuple[int, Content], ...]  # (number, title)
    records: tuple[Record, ...]


@dataclass(frozen=True, slots=True)
class VerificationView:
    """What the check of a proposed file found (M14): the codes are the store's, shown in the tool's words when known, else as content."""

    status: Content
    reason: Content
    read: Content
    sha: Content | None
    symbol: Content | None
    lines: tuple[int, ...]
    detail: Content | None


@dataclass(frozen=True, slots=True)
class NoteView:
    """A model note as the report carries it (`model_notes.notes`): every text is content, since a model wrote it (the client name too: it is the
    client's own claim); the words for a kind, a state and a reason are chosen by the layout from fixed ones."""

    id: Content
    kind: Content
    state: Content
    text: Content
    revision: int
    head: Content | None
    target_id: Content | None
    target_label: Content | None
    client_name: Content
    client_version: Content
    created_at: Content
    proposal: tuple[Content, Content, Content] | None  # repository, path, symbol
    verification: VerificationView | None


@dataclass(frozen=True, slots=True)
class ModelNotesView:
    notes: tuple[NoteView, ...]
    records: tuple[Record, ...]


@dataclass(frozen=True, slots=True)
class AreaView:
    index: int
    label: Content
    kind: Content
    repo: Content
    module: Content
    priority: str
    by_name: bool
    route_line: Ev
    chains: tuple[tuple[Ev, ...], ...]
    also_mounted: tuple[Content, ...]
    complete: bool | None
    reasons: tuple[Content, ...]
    findings: tuple[int, ...]
    decision: Decided | None = None


@dataclass(frozen=True, slots=True)
class FindingView:
    number: int
    index: int
    kind: Content
    title: Content
    risk: str
    confidence: str
    basis: Content
    evidence: tuple[Ev, ...]
    is_exclusion: bool
    decision: Decided | None = None


@dataclass(frozen=True, slots=True)
class FindingGroup:
    kind: Content
    findings: tuple[FindingView, ...]


@dataclass(frozen=True, slots=True)
class BranchRow:
    branch: Content
    short: Content
    state: Content
    found: int
    detail: Content
    evidence: tuple[Ev, ...]
    findings: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class HopView:
    kind: Content
    from_repo: Content
    to_repo: Content
    depth: int
    detail: Content
    candidate: bool
    complete: bool | None
    reasons: tuple[Content, ...]
    pins: tuple[tuple[Content, Content, Content], ...]  # branch, role, short sha


@dataclass(frozen=True, slots=True)
class ConsumerRow:
    repo: Content
    producer: Content
    branch: Content
    pin: Content | None
    state: Content
    adoption: Content
    tag: Content | None
    first_release: Content | None
    detail: Content
    evidence: tuple[Ev, ...]


@dataclass(frozen=True, slots=True)
class HttpView:
    client_repo: Content
    server_repo: Content
    method: Content | None
    url: Content
    route: Content
    kind: Content
    candidate: bool
    removed: bool
    rematched: Content | None
    note: Content
    client: Ev
    via: tuple[Ev, ...]
    route_evidence: tuple[Ev, ...]


@dataclass(frozen=True, slots=True)
class ContractChangeView:
    repo: Content
    symbol: Content
    path: Content
    change: Content
    kinds: tuple[Content, ...]
    before: Content
    after: Content
    evidence: tuple[Ev, ...]


@dataclass(frozen=True, slots=True)
class ContractReadView:
    repo: Content
    branch: Content
    owner: Content
    field: Content
    basis: Content
    form: Content
    pin: Content | None
    adoption: Content
    symbol: Content
    evidence: tuple[Ev, ...]


@dataclass(frozen=True, slots=True)
class Cross:
    hops: tuple[HopView, ...]
    consumers: tuple[ConsumerRow, ...]
    http: tuple[HttpView, ...]
    changes: tuple[ContractChangeView, ...]
    reads: tuple[ContractReadView, ...]
    candidates: tuple[ContractReadView, ...]
    unfollowed: tuple[Content, ...]
    unread: tuple[Content, ...]

    @property
    def empty(self) -> bool:
        return not (self.hops or self.consumers or self.http or self.changes or self.reads or self.candidates)


@dataclass(frozen=True, slots=True)
class BackendView:
    client: Ev
    method: Content
    path: Content
    repo: Content
    handler: Content
    modules: tuple[tuple[int, Content, Content, Ev], ...]  # depth, path, imported by, the import line
    complete: bool
    reasons: tuple[Content, ...]


@dataclass(frozen=True, slots=True)
class SuggestionView:
    """`area` is the index of the report's `areas` the case was suggested for (None: tied to no area of this report); `evidence` is
    the spec line of a suggestion that reads code."""

    source: Content
    case_id: Content
    title: Content
    words: tuple[Content, ...]
    area: int | None = None
    evidence: tuple[Ev, ...] = ()


@dataclass(frozen=True, slots=True)
class MappedView:
    area_id: Content
    case_id: Content
    title: Content
    source: Content
    mapping: Content


@dataclass(frozen=True, slots=True)
class Suggested:
    """`state`: `listed` (suggestions), `unavailable` or `incomplete` or `unconfigured` (the records say), `read_none` (every source
    was read completely and suggested nothing) or `not_read` (this run read no test source: nothing is claimed either way). `records`
    are the source records of every state, blocking ones first: a list of suggestions may still be partial."""

    state: str
    items: tuple[SuggestionView, ...]
    records: tuple[Record, ...]
    partial: tuple[Record, ...] = ()  # what a spec source read but could not use: addresses that are not literals, visits that match no screen
    sources: tuple[tuple[Content, str], ...] = ()  # each configured source and what became of it, when the report says (`inputs.test_sources`)


@dataclass(frozen=True, slots=True)
class View:
    header: Header
    status: str
    records: tuple[Record, ...]
    notes: tuple[Content, ...]
    records_first: bool
    must_test: tuple[AreaView, ...]
    sample: tuple[AreaView, ...]
    candidates: tuple[AreaView, ...]
    groups: tuple[FindingGroup, ...]
    exclusions: tuple[FindingView, ...]
    matrix: tuple[BranchRow, ...]
    cross: Cross
    backend: tuple[BackendView, ...]
    suggested: Suggested
    mapped: tuple[MappedView, ...]
    author: Any  # author.AuthorView, None for a run that did not complete
    decisions: DecisionsView | None = None  # None when the report has no decision, none to show and no unreadable store
    model_notes: ModelNotesView | None = None  # None when the report has no model note and no unreadable note store

    @property
    def completed(self) -> bool:
        return self.status == "completed"


# ------------------------------------------------------------------------------ reading


def _record(r: Mapping[str, Any]) -> Record:
    selection, suggestions = blocks(r)
    return Record(_c(r.get("kind")), _c(r.get("subject")), _c(r.get("not_established")), _c(r.get("detail")), selection, suggestions, scope_of(r))


def _records(report: Mapping[str, Any]) -> tuple[Record, ...]:
    out = [_record(r) for r in _l(report.get("incompleteness")) if isinstance(r, dict)]
    return tuple(sorted(out, key=lambda r: not (r.blocking or r.blocks_suggestions)))  # blocking first, the report's order otherwise (sorted is stable)


def _label(change: Mapping[str, Any]) -> Content:
    spec = _d(change.get("spec"))
    for key in ("pr", "compare", "range"):
        if key in spec:
            return content(f"{key} {_s(spec[key])}")
    return _c(change.get("kind"))


def _propagation(report: Mapping[str, Any]) -> Mapping[str, Any]:
    props = _l(report.get("propagations"))
    return _d(props[0]) if props else {}


COUNT_KEYS = ("areas", "must_test", "sample", "candidates", "findings", "exclusions", "records", "blocking_records")
COUNT_KEYS_ALL = (*COUNT_KEYS, "blocking_suggestion_records")  # the last one is not in a report written before test sources were read


def _counts(report: Mapping[str, Any]) -> tuple[tuple[str, int], ...] | None:
    if report["status"] != "completed":
        return None  # a run that did not complete has no count: a partial list must never be countable
    given = report.get("counts")
    records = [r for r in _l(report.get("incompleteness")) if isinstance(r, dict)]
    flags = [blocks(r) for r in records]
    if isinstance(given, dict) and all(isinstance(given.get(k), int) and not isinstance(given.get(k), bool) for k in COUNT_KEYS):
        last = given.get("blocking_suggestion_records")
        return (*((k, given[k]) for k in COUNT_KEYS), ("blocking_suggestion_records", last if isinstance(last, int) and not isinstance(last, bool) else sum(s for _, s in flags)))
    areas, findings = _l(report.get("areas")), _l(report.get("findings"))
    return (
        ("areas", sum(not a["by_name"] for a in areas)), ("must_test", sum(a["priority"] == "must_test" for a in areas)),
        ("sample", sum(a["priority"] == "sample" for a in areas)), ("candidates", sum(bool(a["by_name"]) for a in areas)), ("findings", len(findings)),
        ("exclusions", sum(bool(f["is_exclusion"]) for f in findings)), ("records", len(records)), ("blocking_records", sum(b for b, _ in flags)),
        ("blocking_suggestion_records", sum(s for _, s in flags)),
    )


def _header(report: Mapping[str, Any]) -> Header:
    change = _d(report.get("change"))
    prop = _propagation(report)
    complete = prop.get("complete") if isinstance(prop.get("complete"), bool) else None
    head, pre, base = change.get("head_sha"), change.get("pre_change_sha"), change.get("base_sha")
    cr = change.get("change_request")
    pr = None
    if isinstance(cr, dict):
        pr = PullRequest(_c(cr.get("number")), _c(cr.get("title")), _c(cr.get("state")), _c(cr.get("base_ref")), _c(cr.get("head_ref")))
    files = tuple(
        FileChange(_c(f.get("path")), _opt(f.get("old_path")), _c(f.get("status")), _opt(f.get("role")), _i(f.get("added")), _i(f.get("removed")))
        for f in _l(change.get("files")) if isinstance(f, dict))
    how = tuple((k, _c(v)) for k, v in _d(change.get("how")).items())
    tool = _d(report.get("tool"))
    return Header(
        repo=_c(change.get("repo") or _d(report.get("inputs")).get("repo")), label=_label(change), status=report["status"], tool=_c(tool.get("version")),
        head=_c(head) if isinstance(head, str) and head else None, head_short=_c(head[:12]) if isinstance(head, str) and head else None,
        pre_change=_c(pre) if isinstance(pre, str) and pre else None, pre_change_short=_c(pre[:12]) if isinstance(pre, str) and pre else None,
        base=_c(base) if isinstance(base, str) and base else None, how=how, pr=pr, as_of=_opt(change.get("as_of")), complete=complete,
        reasons=len(_l(prop.get("reasons"))), counts=_counts(report), files=files, file_count=_i(change.get("file_count")) or len(files),
    )


def _decision(state: str, d: Any, viewed: int | None) -> DecisionView | None:
    if not isinstance(d, dict):
        return None
    ch = d.get("changes")
    known = isinstance(ch, dict)
    lines: list[tuple[Content, Content, Content]] = []
    if known:
        for side in ("removed", "added"):
            for x in _l(ch.get(side)):
                x = _d(x)
                lines.append((content(side), content(f"{_s(x.get('repo'))} {_s(x.get('path'))}"), _c(x.get("detail"))))
    now = d.get("now_fingerprint")
    return DecisionView(
        state=state, target_id=_c(d.get("target_id")), kind=_c(d.get("kind")), label=_opt(d.get("label")), verdict=_c(d.get("verdict")),
        reason=_c(d.get("reason")), author=_c(d.get("author")), channel=_c(d.get("channel")), revision=_i(d.get("revision")),
        created_at=_c(d.get("created_at")), fingerprint=_c(_s(d.get("fingerprint"))[:12]),
        now_fingerprint=_c(_s(now)[:12]) if isinstance(now, str) else None,
        changes=tuple(lines) if known else None, changes_known=known, viewed=viewed,
    )


def _ids(items: list[Any]) -> list[str | None]:
    """The `id` of each area or finding, in the order of the list (None for one that has none: a report from before decisions)."""
    return [x["id"] if isinstance(x, dict) and isinstance(x.get("id"), str) else None for x in items]


def _review(report: Mapping[str, Any]) -> Mapping[str, Any]:
    return _d(report.get("review"))


def _viewed_revision(report: Mapping[str, Any]) -> int | None:
    n = _review(report).get("revision")
    return n if isinstance(n, int) and not isinstance(n, bool) else None


def _records_of(given: Mapping[str, Any]) -> tuple[Record, ...]:
    return tuple(_record(r) for r in _l(given.get("records")) if isinstance(r, dict))


def _decisions(report: Mapping[str, Any], findings: list[tuple[int, Mapping[str, Any]]]) -> tuple[DecisionsView | None, dict[str, Decided], list[str | None], list[str | None]]:
    """The decisions of a report: the section, what each target shows (by id) and the ids of the areas and findings in report order. A report
    whose `review.decisions` is missing or empty of decisions has no section and no per-target line (as before M11); with any decision, an
    area or finding without one says so; with an unreadable store (a record), none says anything about itself."""
    areas = _l(report.get("areas"))
    area_ids, finding_ids = _ids(areas), _ids([f for _, f in findings])
    given = _review(report).get("decisions")
    if not isinstance(given, dict):
        return None, {}, area_ids, finding_ids
    viewed = _viewed_revision(report)
    lists = {state: [v for v in (_decision(state, x, viewed) for x in _l(given.get(state))) if v is not None] for state in ("current", "stale", "orphaned")}
    records = _records_of(given)
    if records:  # the store could not be read: nothing is known about any target, so no target says it has no decision
        return DecisionsView((), (), (), (), (), records), {}, area_ids, finding_ids
    if not any(lists.values()):
        return None, {}, area_ids, finding_ids
    by_id = {str(v.target_id): Decided(state, v) for state in ("current", "stale") for v in lists[state]}
    undecided_areas = tuple(_c(_d(a).get("label")) for a, ident in zip(areas, area_ids) if ident is not None and ident not in by_id)
    undecided_findings = tuple((n, _c(_d(f).get("title"))) for (n, f), ident in zip(findings, finding_ids) if ident is not None and ident not in by_id)
    view = DecisionsView(tuple(lists["current"]), tuple(lists["stale"]), tuple(lists["orphaned"]), undecided_areas, undecided_findings, records)
    return view, by_id, area_ids, finding_ids


def _verification(v: Any) -> VerificationView | None:
    if not isinstance(v, dict):
        return None
    sha, symbol, detail = v.get("sha"), v.get("symbol"), v.get("detail")
    return VerificationView(
        _c(v.get("status")), _c(v.get("reason")), _c(v.get("read")), _c(_s(sha)[:12]) if isinstance(sha, str) and sha else None,
        _c(symbol) if isinstance(symbol, str) and symbol else None, tuple(x for x in _l(v.get("lines")) if isinstance(x, int) and not isinstance(x, bool)),
        _c(detail) if isinstance(detail, str) and detail else None)


def _note(n: Mapping[str, Any]) -> NoteView:
    proposal = n.get("proposal")
    p = _d(proposal)
    head, tid, label = n.get("head_sha"), n.get("target_id"), n.get("target_label")
    client = _d(n.get("client"))
    return NoteView(
        _c(n.get("id")), _c(n.get("kind")), _c(n.get("state")), _c(n.get("text")), _i(n.get("revision")), _c(_s(head)[:12]) if isinstance(head, str) and head else None,
        _c(tid) if isinstance(tid, str) and tid else None, _c(label) if isinstance(label, str) and label else None, _c(client.get("name")), _c(client.get("version")),
        _c(n.get("created_at")), (_c(p.get("repo")), _c(p.get("path")), _c(p.get("symbol"))) if isinstance(proposal, dict) else None, _verification(n.get("verification")))


def _model_notes(report: Mapping[str, Any]) -> ModelNotesView | None:
    """The model notes of a report (M14), or None when it has none and no record. Kept apart from findings, areas and decisions: nothing here
    is counted or matched to an area."""
    given = report.get("model_notes")
    if not isinstance(given, dict):
        return None
    notes = tuple(_note(n) for n in _l(given.get("notes")) if isinstance(n, dict))
    records = _records_of(given)
    if not notes and not records:
        return None
    return ModelNotesView(notes, records)


def _shown(by_id: Mapping[str, Decided], ident: str | None, active: bool) -> Decided | None:
    if not active:
        return None
    return by_id.get(ident) if ident is not None and ident in by_id else Decided("none", None)


def _areas(report: Mapping[str, Any], findings: list[tuple[int, Mapping[str, Any]]], decided: Callable[[int], Decided | None]) -> list[AreaView]:
    reached = {}
    for r in _l(_propagation(report).get("reached")):
        area = _d(_d(r).get("area"))
        key = (_s(area.get("repo")), _s(area.get("kind")), _s(area.get("label")))
        old = reached.get(key)
        if old is None or (_d(r).get("complete") is False and old.get("complete") is not False):
            reached[key] = _d(r)
    out = []
    for i, a in enumerate(_l(report.get("areas"))):
        key = (a["repo"], a["kind"], a["label"])
        got = reached.get(key)
        mine = tuple(n for n, f in findings if f["kind"] in ("direct", "cross-repo") and [_s(x) for x in f["identity"][1:4]] == list(key))
        out.append(AreaView(
            index=i, label=_c(a["label"]), kind=_c(a["kind"]), repo=_c(a["repo"]), module=_c(a["module"]), priority=a["priority"], by_name=a["by_name"],
            route_line=_ev(a["route_line"]), chains=tuple(_evs(c) for c in a["chains"]), also_mounted=tuple(_c(x) for x in a["also_mounted"]),
            complete=got.get("complete") if got is not None and isinstance(got.get("complete"), bool) else None,
            reasons=tuple(_c(x) for x in _l(got.get("reasons"))) if got is not None else (), findings=mine, decision=decided(i),
        ))
    return out


def _sort_areas(areas: list[AreaView]) -> tuple[AreaView, ...]:
    return tuple(sorted(areas, key=lambda x: (_raw(x.repo), _raw(x.kind), _raw(x.label), x.index)))


def _raw(c: Content) -> str:
    return str(c)


def _findings(report: Mapping[str, Any], decided: Callable[[int], Decided | None]) -> tuple[tuple[FindingGroup, ...], tuple[FindingView, ...], list[tuple[int, Mapping[str, Any]]]]:
    raw = [(i + 1, f) for i, f in enumerate(_l(report.get("findings")))]
    views = [FindingView(
        number=n, index=n - 1, kind=_c(f["kind"]), title=_c(f["title"]), risk=f["risk"], confidence=f["confidence"], basis=_c(f["confidence_basis"]),
        evidence=_evs(f["evidence"]), is_exclusion=bool(f["is_exclusion"]), decision=decided(n - 1)) for n, f in raw]
    plain = [v for v in views if not v.is_exclusion]
    kinds = sorted({str(v.kind) for v in plain}, key=lambda k: (KIND_ORDER.index(k) if k in KIND_ORDER else len(KIND_ORDER), k))
    groups = tuple(FindingGroup(_c(k), tuple(v for v in plain if str(v.kind) == k)) for k in kinds)
    exclusions = tuple(sorted((v for v in views if v.is_exclusion), key=lambda v: (str(v.kind), v.number)))
    return groups, exclusions, raw


def _matrix(report: Mapping[str, Any], raw: list[tuple[int, Mapping[str, Any]]]) -> tuple[BranchRow, ...]:
    rows = []
    for b in _l(report.get("branches")):
        name = _s(b["branch"])
        linked = tuple(n for n, f in raw if f["kind"] == "branch-missing" and len(f["identity"]) > 2 and _s(f["identity"][2]) == name)
        rows.append(BranchRow(_c(name), _c(_s(b["commit"])[:12]), _c(b["state"]), b["added_lines_found"], _c(b["detail"]), _evs(b["evidence"]), linked))
    return tuple(rows)


def _cross(report: Mapping[str, Any]) -> Cross:
    p = _propagation(report)
    hops = tuple(HopView(
        _c(h.get("kind")), _c(h.get("from_repo")), _c(h.get("to_repo")), _i(h.get("depth")), _c(h.get("detail")), bool(h.get("candidate")),
        h["complete"] if isinstance(h.get("complete"), bool) else None, tuple(_c(r) for r in _l(h.get("reasons"))),
        tuple((_c(x.get("ref")), _c(x.get("role")), _c(_s(x.get("sha"))[:12])) for x in _l(h.get("pins")) if isinstance(x, dict)))
        for h in _l(p.get("hops")) if isinstance(h, dict))
    consumers = tuple(ConsumerRow(
        _c(c.get("repo")), _c(c.get("producer")), _c(c.get("branch")), _opt(c.get("pin")), _c(c.get("state")), _c(c.get("adoption")), _opt(c.get("tag")),
        _opt(c.get("first_release")), _c(c.get("detail")), _evs(c.get("evidence"))) for c in _l(p.get("consumers")) if isinstance(c, dict))
    http = []
    for e in _l(p.get("http_edges")):
        e = _d(e)
        route = _d(e.get("route"))
        rematched = e.get("rematched")
        http.append(HttpView(
            _c(e.get("client_repo")), _c(e.get("server_repo")), _opt(e.get("method")), _c(e.get("url")),
            content(f"{_s(route.get('method'))} {_s(route.get('path'))}".strip()), _c(e.get("kind")), bool(e.get("candidate")), bool(e.get("removed")),
            content(f"{_s(_d(rematched).get('method'))} {_s(_d(rematched).get('path'))}".strip()) if isinstance(rematched, dict) else None,
            _c(e.get("note")), _ev(e.get("client")), _evs(e.get("via")), _evs(route.get("evidence"))))
    changes = []
    for c in _l(p.get("contract_changes")):
        c = _d(c)
        changes.append(ContractChangeView(
            _c(c.get("repo")), _c(c.get("symbol")), _c(c.get("path")), _c(c.get("change")), tuple(_c(k) for k in _l(c.get("kinds"))),
            _member(c.get("before")), _member(c.get("after")), _evs(c.get("evidence"))))

    def reads(v: Any) -> tuple[ContractReadView, ...]:
        return tuple(ContractReadView(
            _c(r.get("repo")), _c(r.get("branch")), _c(r.get("owner")), _c(r.get("field")), _c(r.get("basis")), _c(r.get("form")), _opt(r.get("pin")),
            _c(r.get("adoption")), _c(r.get("symbol")), _evs(r.get("evidence"))) for r in _l(v) if isinstance(r, dict))

    return Cross(hops, consumers, tuple(http), tuple(changes), reads(p.get("contract_reads")), reads(p.get("contract_candidates")),
                 tuple(_c(x) for x in _l(p.get("unfollowed"))), tuple(_c(x) for x in _l(p.get("unread_entry_points"))))


def _member(m: Any) -> Content:
    m = _d(m)
    if not m:
        return content("")
    text = _s(m.get("annotation")) or _s(m.get("kind"))
    if m.get("has_default"):
        text += f" = {_s(m.get('default'))}"
    return content(text)


def _backend(report: Mapping[str, Any]) -> tuple[BackendView, ...]:
    out = []
    for b in _l(report.get("backend_calls")):
        b = _d(b)
        route = _d(b.get("route"))
        mods = tuple((_i(m.get("depth")), _c(m.get("path")), _c(m.get("imported_by")), _ev(m.get("evidence"))) for m in _l(b.get("modules")) if isinstance(m, dict))
        out.append(BackendView(_ev(b.get("client")), _c(route.get("method")), _c(route.get("path")), _c(route.get("repo")), _c(b.get("handler")), mods,
                               b.get("complete") is not False, tuple(_c(r) for r in _l(b.get("reasons")))))
    return tuple(out)


SOURCE_KINDS = ("source_unavailable", "suggestions_incomplete", "source_unconfigured")
PARTIAL_KINDS = ("spec_url_partial", "spec_visit_unmatched")


def _suggested(report: Mapping[str, Any], records: tuple[Record, ...]) -> Suggested:
    index = {a.get("id"): i for i, a in enumerate(_l(report.get("areas"))) if isinstance(a, dict) and isinstance(a.get("id"), str)}
    items = tuple(SuggestionView(_c(s.get("source")), _c(s.get("case_id")), _c(s.get("title")), tuple(_c(w) for w in _l(s.get("matched_words"))),
                                 index.get(s.get("area_id")), _evs(s.get("evidence")))
                  for s in _l(report.get("suggestions")) if isinstance(s, dict))
    mine = tuple(r for r in records if str(r.kind) in SOURCE_KINDS)
    partial = tuple(r for r in records if str(r.kind) in PARTIAL_KINDS)
    sources = tuple((_c(x.get("source")), _s(x.get("state"))) for x in _l(_d(report.get("inputs")).get("test_sources")) if isinstance(x, dict))
    kinds = {str(r.kind) for r in mine}
    if items:
        return Suggested("listed", items, mine, partial, sources)
    if "source_unavailable" in kinds:
        return Suggested("unavailable", (), mine, partial, sources)
    if "suggestions_incomplete" in kinds:
        return Suggested("incomplete", (), mine, partial, sources)
    if "source_unconfigured" in kinds:
        return Suggested("unconfigured", (), mine, partial, sources)
    if _d(report.get("inputs")).get("test_source_read") is True:
        return Suggested("read_none", (), (), partial, sources)
    return Suggested("not_read", (), (), (), sources)


def build(report: Mapping[str, Any], view: str = "full") -> View:
    """The view of a checked report. `view` is `full` or `author` (the header and the author section)."""
    if view not in ("full", "author"):
        raise ValueError(f"view: {view!r} is not full or author")
    status = report["status"]
    records = _records(report)
    notes = tuple(_c(n) for n in _l(report.get("notes")))  # kept for a run that did not complete too: they say what was done before it stopped
    header = _header(report)
    empty = Cross((), (), (), (), (), (), (), ())
    if status != "completed":
        return View(header, status, records, notes, True, (), (), (), (), (), (), empty, (), Suggested("not_read", (), ()), (), None)
    dview, by_id, area_ids, finding_ids = _decisions(report, [(i + 1, f) for i, f in enumerate(_l(report.get("findings")))])
    active = dview is not None and not dview.records

    def shown_for(ids: list[str | None]) -> Callable[[int], Decided | None]:
        """What the item at an index of the areas or the findings shows of its decision."""
        return lambda i: _shown(by_id, ids[i] if i < len(ids) else None, active)

    groups, exclusions, raw = _findings(report, shown_for(finding_ids))
    areas = _areas(report, raw, shown_for(area_ids))
    mapped = tuple(MappedView(_c(m.get("area_id")), _c(m.get("case_id")), _c(m.get("title")), _c(m.get("source")), _c(m.get("mapping")))
                   for m in _l(report.get("mapped_tests")) if isinstance(m, dict))
    return View(
        header=header, status=status, records=records, notes=notes, records_first=any(r.blocking for r in records),
        must_test=_sort_areas([a for a in areas if a.priority == "must_test" and not a.by_name]),
        sample=_sort_areas([a for a in areas if a.priority == "sample" and not a.by_name]),
        candidates=_sort_areas([a for a in areas if a.priority == "candidate" or a.by_name]), groups=groups, exclusions=exclusions,
        matrix=_matrix(report, raw), cross=_cross(report), backend=_backend(report), suggested=_suggested(report, records), mapped=mapped,
        author=author.build(report), decisions=dview, model_notes=_model_notes(report),
    )
