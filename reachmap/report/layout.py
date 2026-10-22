"""The report as a document: the sections of the view model in the order a QA reader needs, with every heading, label and sentence
this tool says (context section 1). All of it passes the wording check before the document is handed out; everything copied
from the report stays `Content`.

Never says a release is safe, that a change is covered or that nothing needs testing; a run that was not whole says so at the
top; a run that did not complete shows its header and its records and nothing else.

Nothing here does I/O.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, Mapping, Sequence

from reachmap.model import verdict_text
from reachmap.report import author as author_model
from reachmap.report import model
from reachmap.report.notewords import KIND_WORDS, NOTES_LABEL, REASON_WORDS, STAMP
from reachmap.report.notewords import STATE_WORDS as NOTE_STATE_WORDS
from reachmap.report.blocks import (
    Block, Code, CodeBlock, Content, Details, Document, Heading, Item, Items, Para, Section, Strong, Table, content, require_clean, runs,
)

__all__ = ["build"]

CANDIDATE = "candidate, matched by name, not import-backed"
CLAIM_NO_AREAS = "No areas reached by this change were found; see the report for what was examined."
STATUS_WORDS = {"completed": "finished", "failed": "failed", "timed_out": "timed out"}  # a whole run never says "complete": that reads as coverage
PRIORITY_WORDS = {"must_test": "must test", "sample": "sample", "candidate": CANDIDATE}
ADOPTION_WORDS = {"adopted": "adopted", "on_adoption": "will be exposed on adoption", "undecided": "undecided"}
BASIS_WORDS = {"typed": "typed read: the receiver of the field is proven", "by_name": "read by name"}
STATE_WORDS = {
    "present": "carries the change", "partial": "has part of the change", "pre_change": "still has the code from before the change",
    "diverged": "differs from both versions", "file_absent": "does not have the file", "unreadable": "could not be read, so nothing is established",
}
KIND_NOTES = {
    "direct": "The change reaches an area in the same repository.",
    "cross-repo": "The change reaches an area in another repository.",
    "contract-tightened": "A field of a shared contract became stricter; the consumers listed read it.",
    "contract-relaxed": "A field of a shared contract became looser; the consumers listed read it.",
    "removed_route_still_called": "A route the change removes is still called by a client.",
    "added_route_uncalled": "A route the change adds has no caller found.",
    "operation-changed": "An operation of a spec changes (parameters, request body, responses or a schema they reach).",
    "release": "Whether each consumer of a changed package has adopted the change, per branch.",
    "branch-missing": "The change is on some branches and missing from others.",
    "duplicate": "A parallel implementation that may need the same change.",
}
COUNT_LABELS = {
    "areas": "Areas to test", "must_test": "Must test", "sample": "Sample", "candidates": "Candidates, matched by name", "findings": "Findings",
    "exclusions": "Not reached (with evidence)", "records": "Records of what was not established", "blocking_records": "Blocking records",
    "blocking_suggestion_records": "Blocking records about suggestions",
}


def _verdict(d: model.DecisionView) -> Any:
    """The words for a verdict when it is one the tool knows (the model's own), else the value as content in code."""
    try:
        return verdict_text(str(d.verdict))
    except KeyError:
        return Code(d.verdict)


def _what_was_decided(d: model.DecisionView) -> tuple[Any, ...]:
    """`risk accepted, not tested (reason from <author>: "<reason>")`: the verdict in the tool's words, the person's own words quoted and
    attributed (a reason is never the tool's claim), JSON-quoted so that a newline or a quote in it cannot pass for the end of it."""
    if str(d.reason).strip():
        return runs(_verdict(d), " (reason from ", d.author, ": ", content(json.dumps(str(d.reason), ensure_ascii=False)), ")")
    return runs(_verdict(d), " (by ", d.author, ")")


def _claim_differs(d: model.DecisionView) -> str:
    """How a stale decision is worded: against the revision this report is, which is not always the latest (an older report is read too)."""
    if d.viewed is not None and d.viewed != d.revision:
        return f"the claim on revision {d.viewed} differs from the claim "
    return "the claim differs from the claim "


def _decision_line(decided: model.Decided | None) -> Item | None:
    """The line of an area or a finding about its decision: what was decided, that the decision needs review (and is not applied), or
    that there is none. Nothing at all in a report that has no decisions."""
    if decided is None:
        return None
    if decided.state == "current" and decided.entry is not None:
        return _sub("Decision:", _what_was_decided(decided.entry))
    if decided.state == "stale" and decided.entry is not None:
        d = decided.entry
        return _sub("Decision:", Strong("decision needs review"), ": ", _claim_differs(d), d.author, f" decided on at revision {d.revision} (", _verdict(d), "). It is not applied.")
    return _sub("Decision:", "No decision recorded.")


def _n(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def _join(parts: Sequence[Any], sep: str = ", ") -> tuple[Any, ...]:
    out: list[Any] = []
    for i, p in enumerate(parts):
        if i:
            out.append(sep)
        out.append(p)
    return tuple(out)


def _codes(values: Sequence[str]) -> tuple[Any, ...]:
    return _join([Code(v) for v in values])


def _sub(label: str, *rest: Any, children: Sequence[Block] = ()) -> Item:
    """A labelled line of a list: `**Label:** rest`."""
    return Item(runs(Strong(label), " " if rest else "", *rest), tuple(children))


# ------------------------------------------------------------------------------ evidence


def _ev_item(e: model.Ev) -> Item:
    return Item(runs(Code(e.repo), " ", Code(e.locator), " at ", Code(e.short)), (CodeBlock((e.detail,)),))


def _ev_items(evs: Sequence[model.Ev], ordered: bool = False) -> Items:
    return Items(tuple(_ev_item(e) for e in evs), ordered)


def _evidence_of(label: str, evs: Sequence[model.Ev]) -> Item:
    return _sub(label, children=(_ev_items(evs),))


# ------------------------------------------------------------------------------ the header


def _header(v: model.View) -> tuple[Section, Any]:
    h = v.header
    title = runs("Reachmap report: ", h.repo, ", ", h.label)
    facts: list[Item] = [_sub("Repository:", Code(h.repo)), _sub("Change:", Code(h.label))]
    if h.pr is not None:
        facts.append(_sub("Pull request:", "#", h.pr.number, " ", h.pr.title, " (", h.pr.state, "), from ", Code(h.pr.head_ref), " into ", Code(h.pr.base_ref)))
    facts.append(_sub("Run status:", STATUS_WORDS.get(h.status, h.status)))
    if h.head_short is not None:
        facts.append(_sub("Head commit:", Code(h.head_short)))
    if h.pre_change_short is not None:
        facts.append(_sub("Commit before the change:", Code(h.pre_change_short)))
    if h.as_of is not None:
        facts.append(_sub("Pinned as of:", Code(h.as_of)))
    facts.append(_sub("Tool:", "reachmap ", Code(h.tool)))
    blocks: list[Block] = [Heading(1, title, "title"), Items(tuple(facts))]
    ids = [(label, sha) for label, sha in (("head       ", h.head), ("before     ", h.pre_change), ("base       ", h.base)) if sha is not None]
    if ids:
        blocks.append(Details(runs("Whole commit ids"), (CodeBlock(tuple(Content(label + sha) for label, sha in ids)),), "ids"))
    if h.status == "completed" and h.complete is False:
        blocks.append(Para(runs(Strong("This run did not establish everything:"), f" {_n(h.reasons, 'reason', 'reasons')} recorded. See What the run could not establish.")))
    if h.counts is not None:
        blocks.append(Table("Counts", ("What", "Number"), tuple((runs(COUNT_LABELS[k]), runs(str(n))) for k, n in h.counts if n or k != "blocking_suggestion_records"), row_headers=True))  # the last row only when a test source made such a record
    if h.how:
        blocks.append(Details(runs("How the change was found"), (Items(tuple(Item(runs(Code(k), ": ", v)) for k, v in h.how)),), "how"))
    if not h.files and h.file_count:
        # a fact about the change (the host's or git's own count), not a count of what the analysis found: a run that failed keeps it
        blocks.append(Items((_sub("Files in the change:", str(h.file_count)),)))
    if h.files:
        items = []
        for f in h.files:
            old = runs(", from ", Code(f.old_path)) if f.old_path is not None else ()
            role = runs(", role ", Code(f.role)) if f.role is not None else ()
            items.append(Item(runs(Code(f.path), " ", Code(f.status), old, role, f", {f.added} added, {f.removed} removed")))
        blocks.append(Details(runs(f"Files in the change ({len(h.files)} of {h.file_count})"), (Items(tuple(items)),), "files"))
    return Section("header", None, tuple(blocks)), title


# ------------------------------------------------------------------------------ what the run could not establish


def _record_item(r: model.Record) -> Item:
    lines = [_sub("Not established:", r.not_established)]
    if str(r.detail):
        lines.append(_sub("Detail:", r.detail))
    return Item(runs(Code(r.kind), " ", Code(r.subject)), (Items(tuple(lines)),))


def _records(v: model.View) -> Section:
    blocks: list[Block] = []
    if not v.completed:
        blocks.append(Para(runs("No findings are shown because the run did not complete.")))
    blocking = [r for r in v.records if r.blocking or r.blocks_suggestions]
    other = [r for r in v.records if not (r.blocking or r.blocks_suggestions)]
    if blocking:
        blocks.append(Heading(3, runs("Blocking records"), "records-blocking"))
        if v.completed:
            for_areas = [r for r in blocking if r.blocking]
            if for_areas:
                blocks.append(Para(runs("A blocking record means the lists in this report may be incomplete.")))
            if len(for_areas) < len(blocking):
                blocks.append(Para(runs("A blocking record of a test source means the list of suggested cases may be incomplete. The areas come from the code and are not affected.")))
        blocks.append(Items(tuple(_record_item(r) for r in blocking)))
    if other:
        blocks.append(Heading(3, runs("Records that do not block selection"), "records-other"))
        blocks.append(Items(tuple(_record_item(r) for r in other)))
    if not v.records:
        blocks.append(Para(runs("No records were made by this run.")))
    if v.notes:
        blocks.append(Details(runs(f"Notes ({len(v.notes)})"), (Items(tuple(Item(runs(n)) for n in v.notes)),), "notes"))
    return Section("records", "What the run could not establish", tuple(blocks))


# ------------------------------------------------------------------------------ areas


def _area_item(a: model.AreaView) -> Item:
    candidate = a.by_name or a.priority == "candidate"
    kids: list[Item] = [
        _sub("Priority:", CANDIDATE if candidate else PRIORITY_WORDS.get(a.priority, a.priority)),
        _sub("Repository:", Code(a.repo)),
        _sub("Kind:", Code(a.kind)),
    ]
    if str(a.module):
        kids.append(_sub("Module:", Code(a.module)))
    if a.also_mounted:
        kids.append(_sub("Also mounted at:", _codes(a.also_mounted)))
    if a.findings:
        kids.append(_sub("Findings:", _join([f"F{n}" for n in a.findings])))
    if a.complete is False:
        why = _join(list(a.reasons), "; ") if a.reasons else ("no reason was given",)
        kids.append(_sub("Chain read in part:", "the chain passes through a read that did not finish: ", why))
    line = _decision_line(a.decision)
    if line is not None:
        kids.append(line)
    r = a.route_line
    kids.append(Item(runs(Strong("Route line:"), " ", Code(r.repo), " ", Code(r.locator), " at ", Code(r.short)), (CodeBlock((r.detail,)),)))
    blocks: list[Block] = [Items(tuple(kids))]
    if a.chains:
        chains = tuple(Item(runs(f"Chain {i} of {len(a.chains)}, {_n(len(c), 'step', 'steps')}"), (_ev_items(c, ordered=True),)) for i, c in enumerate(a.chains, 1))
        blocks.append(Details(runs(f"Reached through {_n(len(a.chains), 'chain', 'chains')}"), (Items(chains),), "chains"))
    return Item(runs(Code(a.label)), tuple(blocks), (("a", a.index),))


def _areas(v: model.View) -> Section:
    blocks: list[Block] = []
    if not (v.must_test or v.sample or v.candidates):
        blocks.append(Para(runs("No areas were established; the blocking records say what could not be read.") if v.records_first else runs(CLAIM_NO_AREAS)))
    for title, group, note in (
        ("Must test", v.must_test, "A change that reaches eight screens or fewer puts each of them here."),
        ("Sample", v.sample, "A change that reaches more than eight screens puts them here: test a sample."),
        ("Candidates", v.candidates, "Candidates are matched by name, not import-backed, and are not counted as areas to test."),
    ):
        if group:
            blocks += [Heading(3, runs(title), "areas-" + title.lower().replace(" ", "-")), Para(runs(note)), Items(tuple(_area_item(a) for a in group))]
    return Section("areas", "Areas to test", tuple(blocks))


# ------------------------------------------------------------------------------ findings


def _finding_item(f: model.FindingView, with_kind: bool = False) -> Item:
    line = _decision_line(f.decision)
    facts = Items((
        _sub("Risk:", f.risk),
        _sub("Confidence:", f.confidence),
        _sub("Confidence basis:", f.basis),
        _evidence_of("Evidence:", f.evidence),
        *((line,) if line is not None else ()),
    ))
    head = runs(Strong(f"F{f.number}"), " ", Code(f.kind), " ", f.title) if with_kind else runs(Strong(f"F{f.number}"), " ", f.title)
    return Item(head, (facts,), (("f", f.index),))


def _findings(v: model.View) -> Section:
    blocks: list[Block] = []
    if not v.groups and not v.exclusions:
        blocks.append(Para(runs("No findings are listed in this report.")))
    for i, g in enumerate(v.groups):
        blocks.append(Heading(3, runs(g.kind), f"findings-{i}"))
        if str(g.kind) in KIND_NOTES:
            blocks.append(Para(runs(KIND_NOTES[str(g.kind)])))
        blocks.append(Items(tuple(_finding_item(f) for f in g.findings)))
    if v.exclusions:
        blocks += [
            Heading(3, runs("Not reached (with evidence)"), "findings-excluded"),
            Para(runs("Each of these was considered and left out; the evidence shows why.")),
            Items(tuple(_finding_item(f, with_kind=True) for f in v.exclusions)),
        ]
    return Section("findings", "Findings", tuple(blocks))


# ------------------------------------------------------------------------------ decisions

SIDE_WORDS = {"removed": "Removed", "added": "Added"}
TARGET_WORDS = {"a-": "area", "f-": "finding"}


def _decision_item(d: model.DecisionView) -> Item:
    kind = TARGET_WORDS.get(str(d.target_id)[:2])
    head = runs(Code(d.label) if d.label is not None else Code(d.target_id))
    lines: list[Item] = [
        _sub("Decision:", _what_was_decided(d)),
        _sub("Made by:", d.author, " through ", Code(d.channel), f" on revision {d.revision}, recorded ", Code(d.created_at)),
        _sub("About:", kind if kind is not None else Code(d.kind), " ", Code(d.target_id)),
    ]
    if d.state == "stale":
        lines.append(_sub("Claim when decided:", Code(d.fingerprint)))
        if d.now_fingerprint is not None:
            lines.append(_sub("Claim now:", Code(d.now_fingerprint)))
        if d.changes_known and d.changes:
            lines.append(_sub("What changed:", children=(Items(tuple(
                Item(runs(SIDE_WORDS.get(str(side), Code(side)), " ", Code(where)), (CodeBlock((detail,)),)) for side, where, detail in d.changes)),)))
        elif d.changes_known:
            lines.append(_sub("What changed:", "no line of the evidence was added or removed; the claim itself differs."))
        else:
            lines.append(_sub("What changed:", "what changed is not known: the report the decision was made on is not at hand."))
    return Item(head, (Items(tuple(lines)),))


def _decisions(v: model.View) -> Section | None:
    d = v.decisions
    if d is None:
        return None
    blocks: list[Block] = [Para(runs("A decision records a person's choice to test, to sample or to accept a risk. It is never evidence: it changes no priority, risk, confidence, count or gap in this report."))]
    if d.records:
        blocks += [
            Heading(3, runs("Decisions that could not be read"), "decisions-unreadable"),
            Para(runs("A blocking record means the decisions recorded for this change are not shown, so no area or finding is said to have none.")),
            Items(tuple(_record_item(r) for r in d.records)),
        ]
    if d.current:
        blocks += [
            Heading(3, runs("Current decisions"), "decisions-current"),
            Para(runs("Each was made about the claim this report makes.")),
            Items(tuple(_decision_item(x) for x in d.current)),
        ]
    if d.stale:
        blocks += [
            Heading(3, runs("Decisions that need review"), "decisions-stale"),
            Para(runs("The claim in this report differs from the one the decision was made on, so the decision is not applied. Review it and decide again.")),
            Items(tuple(_decision_item(x) for x in d.stale)),
        ]
    if d.orphaned:
        blocks += [
            Heading(3, runs("Decisions about something no longer in this report"), "decisions-orphaned"),
            Para(runs("The area or finding these were about is not in this revision. They are listed here and applied to nothing.")),
            Items(tuple(_decision_item(x) for x in d.orphaned)),
        ]
    if d.undecided_areas or d.undecided_findings:
        items = [Item(runs("Area ", Code(label))) for label in d.undecided_areas] + [Item(runs(Strong(f"F{n}"), " ", title)) for n, title in d.undecided_findings]
        blocks += [Heading(3, runs("Not decided"), "decisions-none"), Para(runs("Areas and findings with no decision recorded.")), Items(tuple(items))]
    return Section("decisions", "Decisions", tuple(blocks))


# ------------------------------------------------------------------------------ model notes (M14)

NOTES_INTRO = (
    "A note is what a model client wrote beside this report. It is not a finding, not evidence and not coverage, and it changes no area, "
    "priority, risk, confidence, count or gap. A verified proposed file is still a note: the check says the file exists at a revision the report "
    "read and names the symbol in code, not that the change reaches it."
)
NOTE_GROUPS = (
    ("current", "Current notes", "Made on the latest revision, about the claim this report makes.", "notes-current"),
    ("stale", "Notes on an earlier revision or an older claim", "Each shows the revision it was made on. A rerun makes a note stale; it is kept, not applied.", "notes-stale"),
    ("orphaned", "Notes about something no longer in the latest report", "The area or finding these were about is gone. They are listed here and attached to nothing.", "notes-orphaned"),
)
OTHER_NOTES = ("Notes with a state this version does not know", "The state is shown as it was recorded.", "notes-other")


def _verification_items(v: model.VerificationView) -> list[Item]:
    status = {"verified": "verified", "not_verified": "not verified"}.get(str(v.status))
    reason = REASON_WORDS.get(str(v.reason))
    out = [_sub("Verification:", status if status is not None else Code(v.status), ": ", reason if reason is not None else Code(v.reason))]
    if v.sha is not None:
        out.append(_sub("Read at:", Code(v.sha)))
    if v.lines:
        out.append(_sub("Where:", "the symbol is in the code of the file at lines " + ", ".join(str(n) for n in v.lines)))
    if v.detail is not None:
        out.append(_sub("Detail:", v.detail))
    return out


def _client_version(n: model.NoteView) -> tuple[Any, ...]:
    """The rest of the stamp line: the name is the client's own claim, and its version (or that it gave none)."""
    if str(n.client_version).strip():
        return (" (the name is the client's own claim; its version is ", Code(n.client_version), ")")
    return (" (the name is the client's own claim; it gave no version)",)


def _note_item(n: model.NoteView) -> Item:
    kind = KIND_WORDS.get(str(n.kind))
    state = NOTE_STATE_WORDS.get(str(n.state))
    about = runs(Code(n.target_label if n.target_label is not None else n.target_id)) if n.target_id is not None else runs("the report as a whole")
    facts: list[Item] = [
        _sub("Text:", children=(CodeBlock((n.text,)),)),
        _sub("About:", about),
        _sub("State:", state if state is not None else Code(n.state)),
        _sub("Made on:", f"revision {n.revision}", *((" at commit ", Code(n.head)) if n.head is not None else ())),
        _sub("Stamp:", STAMP, " ", Code(n.client_name), *_client_version(n)),
        _sub("Recorded:", Code(n.created_at)),
    ]
    if n.proposal is not None:
        repo, path, symbol = n.proposal
        facts.append(_sub("Proposed file:", Code(repo), " ", Code(path), ", symbol ", Code(symbol)))
    if n.verification is not None:
        facts += _verification_items(n.verification)
    return Item(runs(kind if kind is not None else Code(n.kind), " ", Code(n.id)), (Items(tuple(facts)),))


def _model_notes(v: model.View) -> Section | None:
    m = v.model_notes
    if m is None:
        return None
    blocks: list[Block] = [Para(runs(Strong(NOTES_LABEL), " ", NOTES_INTRO))]
    if m.records:
        blocks += [
            Heading(3, runs("Notes that could not be read"), "notes-unreadable"),
            Para(runs("A blocking record means the notes recorded for this change are not shown. It blocks this section only.")),
            Items(tuple(_record_item(r) for r in m.records)),
        ]
    known = {state for state, *_ in NOTE_GROUPS}
    for state, title, note, ident in NOTE_GROUPS:
        mine = [n for n in m.notes if str(n.state) == state]
        if mine:
            blocks += [Heading(3, runs(title), ident), Para(runs(note)), Items(tuple(_note_item(n) for n in mine))]
    other = [n for n in m.notes if str(n.state) not in known]
    if other:
        title, note, ident = OTHER_NOTES
        blocks += [Heading(3, runs(title), ident), Para(runs(note)), Items(tuple(_note_item(n) for n in other))]
    return Section("model-notes", "Model notes", tuple(blocks))


# ------------------------------------------------------------------------------ branch matrix


def _matrix(v: model.View) -> Section:
    if not v.matrix:
        return Section("matrix", "Branch matrix", (Para(runs("No branch of the repository was compared.")),))
    rows = tuple(
        (runs(Code(b.branch)), runs(Code(b.short)), runs(Code(b.state)), runs(str(b.found)), runs(_join([f"F{n}" for n in b.findings])))
        for b in v.matrix)
    table = Table("Branch matrix", ("Branch", "Commit", "State", "Added lines found", "Findings"), rows, row_headers=True)
    items = tuple(
        Item(runs(Code(b.branch), ": ", b.detail), (Items((_evidence_of("Evidence:", b.evidence),)),) if b.evidence else ()) for b in v.matrix)
    return Section("matrix", "Branch matrix", (table, Heading(3, runs("Details per branch"), "matrix-details"), Items(items)))


# ------------------------------------------------------------------------------ cross-repository reach


def _hop_item(h: model.HopView) -> Item:
    kids: list[Item] = []
    if str(h.detail):
        kids.append(_sub("What was looked at:", h.detail))
    if h.pins:
        kids.append(_sub("Pins read:", _join([runs(Code(b), " (", r, ") at ", Code(s)) for b, r, s in h.pins])))
    if h.complete is False or h.reasons:
        kids.append(_sub("Read in part:", *(_join(list(h.reasons), "; ") if h.reasons else ("no reason was given",))))
    flag = runs(" (", CANDIDATE, ")") if h.candidate else ()
    return Item(runs(Code(h.from_repo), " to ", Code(h.to_repo), ": ", Code(h.kind), f" edge, depth {h.depth}", flag), (Items(tuple(kids)),) if kids else ())


def _consumer_groups(rows: Sequence[model.ConsumerRow]) -> list[Item]:
    keys: list[tuple[str, str]] = []
    for r in rows:
        k = (str(r.producer), str(r.repo))
        if k not in keys:
            keys.append(k)
    out = []
    for producer, repo in keys:
        mine = [r for r in rows if (str(r.producer), str(r.repo)) == (producer, repo)]
        branch_items = []
        for r in mine:
            pin = runs("pin ", Code(r.pin)) if r.pin is not None else runs("no pin read")
            tag = runs(", tag ", Code(r.tag)) if r.tag is not None else ()
            first = runs(", first released in ", Code(r.first_release)) if r.first_release is not None else ()
            word = ADOPTION_WORDS.get(str(r.adoption))
            detail = runs(": ", r.detail) if str(r.detail) else ()
            kids = (Items((_evidence_of("Evidence:", r.evidence),)),) if r.evidence else ()
            branch_items.append(Item(runs(Code(r.branch), " ", pin, " (", Code(r.state), ")", tag, first, ", ", word if word is not None else Code(r.adoption), detail), kids))
        out.append(Item(runs(Code(mine[0].producer), " consumed by ", Code(mine[0].repo)), (Items(tuple(branch_items)),)))
    return out


def _http_item(e: model.HttpView) -> Item:
    flags = ([CANDIDATE] if e.candidate else []) + (["route removed by the change"] if e.removed else [])
    kids: list[Item] = []
    if e.method is not None:
        kids.append(_sub("Method:", Code(e.method)))
    kids += [_sub("URL:", Code(e.url)), _sub("Match:", Code(e.kind))]
    if e.rematched is not None:
        kids.append(_sub("Answered after the change by:", Code(e.rematched)))
    if str(e.note):
        kids.append(_sub("Note:", e.note))
    kids.append(_evidence_of("Client line:", (e.client,)))
    if e.via:
        kids.append(_evidence_of("Lines that define the URL:", e.via))
    if e.route_evidence:
        kids.append(_evidence_of("Route and mounts:", e.route_evidence))
    tail = runs(" (", _join(flags, "; "), ")") if flags else ()
    return Item(runs(Code(e.client_repo), " calls ", Code(e.route), " in ", Code(e.server_repo), tail), (Items(tuple(kids)),))


def _read_item(r: model.ContractReadView) -> Item:
    basis = BASIS_WORDS.get(str(r.basis))
    pin = runs(", pin ", Code(r.pin)) if r.pin is not None else ()
    word = ADOPTION_WORDS.get(str(r.adoption))
    kids = (Items((_evidence_of("Evidence:", r.evidence),)),) if r.evidence else ()
    head = runs(
        Code(Content(f"{r.owner}.{r.field}")), " read in ", Code(r.symbol), " of ", Code(r.repo), " on ", Code(r.branch), pin, ", ",
        word if word is not None else Code(r.adoption), ": ", basis if basis else Code(r.basis), " (", Code(r.form), ")")
    return Item(head, kids)


def _cross(v: model.View) -> Section:
    c = v.cross
    if c.empty and not (c.unfollowed or c.unread):
        return Section("cross", "Cross-repository reach", (Para(runs("No other repository was reached by this change.")),))
    blocks: list[Block] = []
    if c.empty:
        blocks.append(Para(runs("No other repository was reached by this change.")))
    if c.hops:
        blocks += [Heading(3, runs("Edges followed"), "cross-edges"), Items(tuple(_hop_item(h) for h in c.hops))]
    if c.consumers:
        blocks += [Heading(3, runs("Package edges"), "cross-package"), Items(tuple(_consumer_groups(c.consumers)))]
    if c.http:
        blocks += [Heading(3, runs("HTTP edges"), "cross-http"), Items(tuple(_http_item(e) for e in c.http))]
    if c.changes or c.reads or c.candidates:
        blocks.append(Heading(3, runs("Contract edges"), "cross-contract"))
        if c.changes:
            items = []
            for ch in c.changes:
                kids = [_sub("Before:", Code(ch.before)), _sub("After:", Code(ch.after))]
                if ch.kinds:
                    kids.append(_sub("Kinds:", _codes(ch.kinds)))
                kids.append(_evidence_of("Evidence:", ch.evidence))
                items.append(Item(runs(Code(ch.symbol), " ", Code(ch.change), " in ", Code(ch.repo), " ", Code(ch.path)), (Items(tuple(kids)),)))
            blocks += [Para(runs("Changed members of the contract:")), Items(tuple(items))]
        if c.reads:
            blocks += [Para(runs("Reads of a changed member in the consumers:")), Items(tuple(_read_item(r) for r in c.reads))]
        if c.candidates:
            blocks += [Heading(4, runs("Candidates"), "cross-candidates"), Para(runs(f"Reads that are {CANDIDATE}.")), Items(tuple(_read_item(r) for r in c.candidates))]
    if c.unfollowed or c.unread:
        items = [Item(runs("Edge not followed: ", Code(x))) for x in c.unfollowed] + [Item(runs("Entry points not read in ", Code(x))) for x in c.unread]
        blocks += [Heading(3, runs("Not followed"), "cross-not-followed"), Items(tuple(items))]
    return Section("cross", "Cross-repository reach", tuple(blocks))


# ------------------------------------------------------------------------------ backend calls, suggestions, mapped tests


def _backend(v: model.View) -> Section:
    if not v.backend:
        return Section("backend", "Backend calls", (Para(runs("No backend call was reached by this change.")),))
    items = []
    for b in v.backend:
        kids: list[Item] = [_evidence_of("Client line:", (b.client,)), _sub("Handler:", Code(b.handler))]
        if not b.complete or b.reasons:
            kids.append(_sub("Modules not listed whole:", *(_join(list(b.reasons), "; ") if b.reasons else ("no reason was given",))))
        if b.modules:
            mods = tuple(Item(runs(f"Depth {d}: ", Code(p), " imported by ", Code(by)), (CodeBlock((e.detail,)),)) for d, p, by, e in b.modules)
            kids.append(_sub("Modules the handler runs:", children=(Items(mods),)))
        items.append(Item(runs(Code(b.client.locator), " calls ", Code(Content(f"{b.method} {b.path}")), " in ", Code(b.repo)), (Items(tuple(kids)),)))
    return Section("backend", "Backend calls", (Items(tuple(items)),))


def _suggestion_item(x: model.SuggestionView) -> Item:
    kids: list[Item] = []
    if x.words:
        kids.append(_sub("Matched words:", _codes(x.words)))
    if x.evidence:
        kids.append(_evidence_of("Spec line:", x.evidence))
    return Item(runs(Code(x.source), " case ", Code(x.case_id), ": ", x.title), (Items(tuple(kids)),) if kids else ())


def _source_records(v: model.Suggested) -> list[Block]:
    """What each test source that was not read completely says: a record per source, under a sentence that says what it means for the list."""
    intro = {
        "source_unavailable": "A test source did not answer, so its suggestions are missing:",
        "suggestions_incomplete": "A test source was read in part, so this list is incomplete:",
        "source_unconfigured": "A test source is not configured, so its suggestions were not looked up:",
    }
    out: list[Block] = []
    for kind, sentence in intro.items():
        rs = [r for r in v.records if str(r.kind) == kind]
        if rs:
            out += [Para(runs(sentence)), Items(tuple(_record_item(r) for r in rs))]
    return out


def _suggested(v: model.View) -> Section:
    s = v.suggested
    blocks: list[Block] = [Para(runs("Suggested cases are places to look. They are not coverage, and they are never counted as tests mapped to an area."))]
    if s.state == "listed":
        areas = (*v.must_test, *v.sample, *v.candidates)
        known = {a.index for a in areas}
        for a in areas:
            mine = [x for x in s.items if x.area == a.index]
            if mine:
                blocks += [Heading(3, runs(Code(a.label), " in ", Code(a.repo)), f"suggested-{a.index}"), Items(tuple(_suggestion_item(x) for x in mine))]
        rest = [x for x in s.items if x.area not in known]
        if rest:
            blocks += [Heading(3, runs("Not tied to an area of this report"), "suggested-other"), Items(tuple(_suggestion_item(x) for x in rest))]
        blocks += _source_records(s) + _partial(s)
    elif s.state == "unavailable":
        blocks += [Para(runs("A test source did not answer, so no suggestions are listed.")), Items(tuple(Item(runs(r.not_established)) for r in s.records if str(r.kind) == "source_unavailable"))]
        blocks += _other_records(s, skip="source_unavailable")
    elif s.state == "incomplete":
        blocks += [Para(runs("A test source was read in part, and nothing it listed matched an area, so the list is incomplete and no suggestions are listed.")),
                   Items(tuple(Item(runs(r.not_established)) for r in s.records if str(r.kind) == "suggestions_incomplete"))]
        blocks += _other_records(s, skip="suggestions_incomplete")
    elif s.state == "unconfigured":
        read = [name for name, state in s.sources if state != "unconfigured"]
        sentence = "Some test sources are not configured, and the others were read and suggested nothing." if read else "No test source is configured."
        blocks += [Para(runs(sentence)), Items(tuple(Item(runs(r.not_established)) for r in s.records))]
    elif s.state == "read_none":
        blocks += [Para(runs("None suggested from what could be read; " + _partial_count(s) + ":")) if s.partial else Para(runs("None suggested."))]
        blocks += _partial_items(s)
    else:
        blocks.append(Para(runs("No test source was read by this run.")))
    return Section("suggested", "Suggested, not verified coverage", tuple(blocks))


def _partial_count(s: model.Suggested) -> str:
    urls = sum(1 for r in s.partial if str(r.kind) == "spec_url_partial")
    unmatched = [r for r in s.partial if str(r.kind) == "spec_visit_unmatched"]
    parts = []
    if urls:
        parts.append(f"{urls} {'visit has' if urls == 1 else 'visits have'} an address that is not a literal")
    if unmatched:
        parts.append("some visits match no screen route")
    return " and ".join(parts)


def _partial_items(s: model.Suggested) -> list[Block]:
    return [Items(tuple(Item(runs(Code(r.kind), " ", Code(r.subject)), (Items((_sub("Detail:", r.detail),)),) if str(r.detail) else ()) for r in s.partial))] if s.partial else []


def _partial(s: model.Suggested) -> list[Block]:
    """Under a list of suggestions: what the spec sources read and could not use, so that the list is not read as all they hold."""
    if not s.partial:
        return []
    return [Para(runs("The spec sources could not use everything they read: " + _partial_count(s) + ":")), *_partial_items(s)]


def _other_records(s: model.Suggested, skip: str) -> list[Block]:
    """The records of the other kinds, for a state that already said its own."""
    return _source_records(replace(s, records=tuple(r for r in s.records if str(r.kind) != skip)))


def _mapped(v: model.View) -> Section:
    items = tuple(Item(runs(Code(m.source), " case ", Code(m.case_id), ": ", m.title, " (area ", Code(m.area_id), ", mapping ", Code(m.mapping), ")")) for m in v.mapped)
    return Section("mapped", "Mapped tests", (Para(runs("Tests the configuration maps to an area.")), Items(items)))


# ------------------------------------------------------------------------------ for the author


def _author(a: author_model.AuthorView) -> Section:
    blocks: list[Block] = [Heading(3, runs("Other repositories reached"), "author-repos")]
    if a.repos:
        blocks.append(Items(tuple(Item(runs(Code(r.repo), " (", _join([Code(e) if isinstance(e, Content) else e for e in r.edges]), ")")) for r in a.repos)))
    else:
        blocks.append(Para(runs("No other repository was reached by this change.")))
    blocks.append(Heading(3, runs("Files beyond the diff"), "author-files"))
    if a.files:
        blocks += [Para(runs("Files the report cites that the change did not touch: the places the change reaches.")),
                   Items(tuple(Item(runs(Code(f.repo), " ", Code(f.path))) for f in a.files))]
    else:
        blocks.append(Para(runs("No file beyond the diff is cited by this report.")))
    blocks.append(Heading(3, runs("Other branches"), "author-branches"))
    if a.branches:
        blocks.append(Items(tuple(Item(runs(Code(b.branch), " ", Code(b.state), ": ", STATE_WORDS.get(str(b.state), "see the branch matrix"))) for b in a.branches)))
    else:
        blocks.append(Para(runs("No other branch was compared.")))
    blocks.append(Heading(3, runs("Unit tests to run"), "author-tests"))
    if a.tests:
        blocks.append(Para(runs("Unit tests the change reaches, with the command of each repository's configuration.")))
        for t in a.tests:
            blocks.append(Para(runs(Code(t.repo), f": {_n(len(t.quoted), 'test file', 'test files')}")))
            if t.command is None:
                blocks.append(Para(runs("No test command is configured for this repository. The test files, quoted for a shell:")))
            blocks.append(CodeBlock((t.text,)))
    else:
        blocks.append(Para(runs("No unit test was found among the files this change reaches.")))
    return Section("author", "For the author", tuple(blocks))


# ------------------------------------------------------------------------------ the document


def build(report: Mapping[str, Any], view: str = "full") -> Document:
    """The document of a checked report. `view` is `full` or `author` (the header and the author section)."""
    v = model.build(report, view)
    header, title = _header(v)
    sections: list[Section] = [header]
    if not v.completed:
        sections.append(_records(v))
    elif view == "author":
        sections.append(_author(v.author))
    else:
        records = _records(v)
        if v.records_first:
            sections.append(records)
        sections += [_areas(v), _findings(v)]
        decisions = _decisions(v)
        if decisions is not None:
            sections.append(decisions)
        model_notes = _model_notes(v)
        if model_notes is not None:
            sections.append(model_notes)
        if not v.records_first:
            sections.append(records)
        sections += [_matrix(v), _cross(v), _backend(v)]
        if v.mapped:
            sections.append(_mapped(v))
        sections += [_suggested(v), _author(v.author)]
    return require_clean(Document(title, tuple(sections)))
