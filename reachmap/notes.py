"""Model notes (M14, context rule 4.7): what a model client records beside a report over MCP.

A note is kept in `<reports dir>/.reachmap/notes/<review key slug>.jsonl`, one ASCII JSON line each, append only, written under the lock of
the review layer (`review.lock`). It holds the report name, the review key, the report's revision and head commit, the target (an area or
a finding id, or none for the report as a whole) with the target's fingerprint when it was written, the kind, the text (capped), the time and
the stamp `recorded via MCP, client reported as <name>`. The stamp is built by `stamp()` from the client name the server recorded at
`initialize`; no function here takes a stamp, and a stored line whose stamp is not the one built from its own client is not read. A text that
says "recorded via MCP" is text: the stamp is a field of its own that renderers show outside the text, as the client's own claim.

A note is never a finding, evidence or coverage (rule 4.4 and 4.7): it changes no area, no priority, no risk, no count and no gap. It is
`current` while its report revision is the latest for the key and the fingerprint of its target is the one it was written against; a rerun
makes it `stale` (it keeps the revision it was made on); a target that is gone from the latest report makes it `orphaned`.

The one model claim the framework checks is a proposed file (`verify_file`): it must exist at a revision the report read, read through the git
layer, and name the change by a distinctive name that appears in code, not in a comment or a string. Either way it stays a note.

Nothing here reads a file but the reports directory's own review files and, through the reader it is given, the git layer.
"""

from __future__ import annotations

import ast
import json
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from reachmap import wording
from reachmap.adapters.js.lexer import tokenize as js_tokens
from reachmap.adapters.js.resolve import is_js_path
from reachmap.adapters.python import syntax as py_syntax
from reachmap.model import Read, ReadResult
from reachmap.report import text as shown
from reachmap.report.notewords import KIND_WORDS, NOTES_LABEL, REASON_WORDS, STAMP, STATE_WORDS
from reachmap.review import attach as review_attach
from reachmap.review import carry, lock, revisions, store
from reachmap.review.formats import FINGERPRINT, SHA, TARGET_ID, TIME
from reachmap.review.store import Problem, StoreError
from reachmap.runner.files import NAME as REPORT_NAME

__all__ = [
    "Client", "Entry", "KINDS", "KIND_WORDS", "Loaded", "NOTE_ID", "NOTES_LABEL", "MAX_CLIENT", "MAX_NOTES", "MAX_REVISIONS", "MAX_TEXT", "Names", "NoReader", "Note", "NoteError", "Problem", "REASON_WORDS",
    "STAMP", "STATE_WORDS", "Unparsed", "append", "attach", "changed_names", "check_target", "client_of", "code_lines", "listing", "new_note", "notes_path", "parse_row", "read",
    "revisions_read", "stamp", "strict_object", "unreadable_record", "verify_file",
]

VERSION = 1
KINDS = ("observation", "question", "proposed_test", "proposed_file")
STATES = ("current", "stale", "orphaned")
MAX_TEXT = 2000  # characters of a note's text (what a rendering shows whole: report.text.CONTENT_LIMIT)
MAX_NOTES = 500  # notes kept for one change
MAX_CLIENT = 100  # characters of a client's name or version as it is sent, before odd characters are written out
DIR = (".reachmap", "notes")
_ROW_KEYS = (
    "v", "id", "report", "key", "revision", "head_sha", "target_id", "fingerprint", "kind", "text", "created_at", "client", "stamp", "verification", "proposal",
)
NOTE_ID = re.compile(r"^n-[0-9a-f]{16}$")
_VERIFICATION_KEYS = ("status", "reason", "read", "sha", "symbol", "lines", "detail")
_READS = ("found", "absent", "unreadable", "not_attempted")
_PROPOSAL_KEYS = ("repo", "path", "symbol")


class NoteError(ValueError):
    """A note that cannot be made. `code` names the rule and `words` says it in this tool's words: never a piece of what the caller sent."""

    def __init__(self, code: str, words: str) -> None:
        super().__init__(words)
        self.code, self.words = code, words


# ------------------------------------------------------------------------------ the client and its stamp


@dataclass(frozen=True, slots=True)
class Client:
    """What the client said about itself at `initialize`: capped, with control and direction characters written out. It is the client's own claim."""

    name: str
    version: str


def _visible(value: str) -> str:
    return shown.visible(value.replace("\n", "\\x0a").replace("\t", "\\x09"))


def client_of(name: Any, version: Any = "") -> Client:
    """The client as recorded: the name and the version as sent, each cut to `MAX_CLIENT` characters, with every control character written out
    (a newline too), so that a name cannot start a line of its own. Anything that is not text is no name."""
    def one(value: Any) -> str:
        return _visible(value[:MAX_CLIENT]) if isinstance(value, str) else ""

    return Client(one(name) if isinstance(name, str) and name[:MAX_CLIENT].strip() else "", one(version))


def stamp(client: Client) -> str:
    """The stamp of a note: built here and nowhere else."""
    return f"{STAMP} {client.name}"


# ------------------------------------------------------------------------------ a note


@dataclass(frozen=True, slots=True)
class Note:
    id: str
    report: str
    key: Mapping[str, str]
    revision: int
    head_sha: str
    target_id: str | None
    fingerprint: str | None
    kind: str
    text: str
    created_at: str
    client: Client
    verification: Mapping[str, Any] | None = None
    proposal: Mapping[str, str] | None = None

    @property
    def stamp(self) -> str:
        return stamp(self.client)

    def to_row(self) -> dict[str, Any]:
        return {
            "v": VERSION, "id": self.id, "report": self.report, "key": dict(self.key), "revision": self.revision, "head_sha": self.head_sha,
            "target_id": self.target_id, "fingerprint": self.fingerprint, "kind": self.kind, "text": self.text, "created_at": self.created_at,
            "client": {"name": self.client.name, "version": self.client.version}, "stamp": self.stamp,
            "verification": None if self.verification is None else dict(self.verification),
            "proposal": None if self.proposal is None else dict(self.proposal),
        }


def _text_ok(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:  # a lone surrogate: no file and no client can hold it
        return False
    return True


def _about(report_name: str, report: Mapping[str, Any], target_id: str | None, client: Client) -> tuple[Mapping[str, str], int, str, str | None]:
    """What a note is tied to: the key, the revision, the head commit and the target's fingerprint (None for the report as a whole). `NoteError`
    when the report cannot take a note, the target is not in it or the client has no name."""
    if not isinstance(report_name, str) or not REPORT_NAME.fullmatch(report_name):
        raise NoteError("report", "the report name is not a plain report name")
    if report.get("status") != "completed":
        raise NoteError("not_completed", "a run that did not complete takes no note: there is no revision to tie it to")
    if not client.name.strip():
        raise NoteError("client", "the client did not report a name, so the note cannot be stamped")
    try:
        key = review_attach.key_of(report)
        targets = carry.targets_of(report)
    except carry.ReviewError:
        raise NoteError("no_review", "the report has no review data (it was made before decisions existed): rerun reachmap analyse") from None
    revision = report["review"].get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise NoteError("no_revision", "the report has no revision number")
    fingerprint = None
    if target_id is not None:
        if not isinstance(target_id, str) or target_id not in targets:
            raise NoteError("target", "the target is not the id of an area or a finding of this report")
        fingerprint = targets[target_id].fingerprint
    head = report.get("ref")
    if not isinstance(head, str) or not SHA.fullmatch(head):
        raise NoteError("no_review", "the report has no head commit")
    return key, revision, head, fingerprint


def check_target(report_name: str, report: Mapping[str, Any], target_id: str | None, client: Client) -> None:
    """Refuse, as `new_note` would, a report that cannot take a note, a target it does not have or a client with no name, without making a note
    (so that nothing is read for a claim that would be refused anyway)."""
    _about(report_name, report, target_id, client)


def new_note(
    report_name: str, report: Mapping[str, Any], target_id: str | None, kind: str, text: str, client: Client, created_at: str,
    *, verification: Mapping[str, Any] | None = None, proposal: Mapping[str, str] | None = None,
) -> Note:
    """A note about `report` (the report named `report_name`): its key, revision, head commit and the target's fingerprint are read from the
    report, the stamp is built from `client`. `NoteError` for anything that is not a note."""
    if kind not in KINDS:
        raise NoteError("kind", "the kind is not one of observation, question, proposed_test, proposed_file")
    if not _text_ok(text):
        raise NoteError("text", f"the text is needed, at most {MAX_TEXT} characters, and cannot hold a lone surrogate")
    key, revision, head, fingerprint = _about(report_name, report, target_id, client)
    return Note(
        "n-" + secrets.token_hex(8), report_name, key, revision, head, target_id, fingerprint, kind, text, created_at, client,
        None if verification is None else dict(verification), None if proposal is None else dict(proposal),
    )


# ------------------------------------------------------------------------------ the store


def notes_path(base: str | Path, key: Mapping[str, str]) -> Path:
    return Path(base).joinpath(*DIR, store.key_slug(key) + ".jsonl")


@dataclass(frozen=True, slots=True)
class Loaded:
    items: tuple[Note, ...]  # in the order written; empty when there is a problem
    problem: Problem | None


def _line(note: Note) -> bytes:
    return (json.dumps(note.to_row(), ensure_ascii=True, separators=(",", ":"), allow_nan=False) + "\n").encode("ascii")


def _is_hex_sha(value: Any) -> bool:
    return isinstance(value, str) and bool(SHA.fullmatch(value))


def _verification_ok(v: Any) -> bool:
    if not isinstance(v, dict) or tuple(sorted(v)) != tuple(sorted(_VERIFICATION_KEYS)):
        return False
    return (
        v["status"] in ("verified", "not_verified") and v["reason"] in REASON_WORDS and (v["status"] == "verified") == (v["reason"] == "verified")
        and v["read"] in _READS and (v["sha"] is None or _is_hex_sha(v["sha"])) and (v["symbol"] is None or (isinstance(v["symbol"], str) and len(v["symbol"]) <= 200))
        and isinstance(v["lines"], list) and len(v["lines"]) <= 5 and all(isinstance(n, int) and not isinstance(n, bool) and n >= 1 for n in v["lines"])
        and (v["detail"] is None or (isinstance(v["detail"], str) and len(v["detail"]) <= 500))
    )


def _proposal_ok(p: Any) -> bool:
    return isinstance(p, dict) and tuple(sorted(p)) == tuple(sorted(_PROPOSAL_KEYS)) and all(isinstance(p[k], str) and 0 < len(p[k]) <= 500 for k in _PROPOSAL_KEYS)


def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """`object_pairs_hook` for JSON that is read as a record: a key that is there twice is refused (`ValueError`), never resolved by taking the last
    one, so what a reader of the line and a reader of the same line elsewhere see cannot differ."""
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("a key is there twice")
        out[key] = value
    return out


def parse_row(raw: bytes, number: int) -> Note | Problem:
    """One line of the store as a note, or the problem with it (in words of this tool: never a piece of the line)."""
    if not raw:
        return Problem(number, "the line is empty")
    try:
        row = json.loads(raw.decode("utf-8"), object_pairs_hook=strict_object)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return Problem(number, "the line is not JSON (a last line may be unfinished)")
    if not raw.isascii():
        return Problem(number, "the line is not ASCII")
    if not isinstance(row, dict) or tuple(sorted(row)) != tuple(sorted(_ROW_KEYS)):
        return Problem(number, "the line does not have the keys of a note")
    if row["v"] != VERSION or isinstance(row["v"], bool):
        return Problem(number, "the line is of a version this reachmap does not read")
    bad = Problem(number, "the line is not a valid note")
    key, client = row["key"], row["client"]
    if not (isinstance(key, dict) and set(key) == {"workspace", "repo", "change"}):
        return bad
    if not all(isinstance(key[k], str) and key[k] for k in ("workspace", "repo", "change")):
        return bad
    if not (isinstance(client, dict) and set(client) == {"name", "version"} and all(isinstance(client[k], str) and len(client[k]) <= MAX_CLIENT * 10 for k in client) and client["name"].strip()):
        return bad
    c = Client(client["name"], client["version"])
    ok = (
        isinstance(row["id"], str) and NOTE_ID.fullmatch(row["id"]) and isinstance(row["report"], str) and REPORT_NAME.fullmatch(row["report"])
        and isinstance(row["revision"], int) and not isinstance(row["revision"], bool) and row["revision"] >= 1 and _is_hex_sha(row["head_sha"])
        and ((row["target_id"] is None and row["fingerprint"] is None)
             or (isinstance(row["target_id"], str) and TARGET_ID.fullmatch(row["target_id"]) and isinstance(row["fingerprint"], str) and FINGERPRINT.fullmatch(row["fingerprint"])))
        and row["kind"] in KINDS and _text_ok(row["text"]) and isinstance(row["created_at"], str) and TIME.fullmatch(row["created_at"])
        and row["stamp"] == stamp(c)
    )
    if not ok:
        return bad
    if row["kind"] == "proposed_file":
        if not _verification_ok(row["verification"]) or not (row["proposal"] is None or _proposal_ok(row["proposal"])):
            return bad
    elif row["verification"] is not None or row["proposal"] is not None:
        return bad
    return Note(row["id"], row["report"], dict(key), row["revision"], row["head_sha"], row["target_id"], row["fingerprint"], row["kind"], row["text"],
                row["created_at"], c, row["verification"], row["proposal"])


def read(base: str | Path, key: Mapping[str, str]) -> Loaded:
    """Every note of `key` in the order written, or a problem (and no note) when any line cannot be read: a later line may not be shown without
    the lines before it. No file is no notes."""
    path = notes_path(base, key)
    try:
        raw = store.read_file(path)
    except (FileNotFoundError, NotADirectoryError):
        return Loaded((), None)
    except OSError as exc:
        return Loaded((), Problem(0, f"the file cannot be read ({exc.strerror or type(exc).__name__})"))
    if not raw:
        return Loaded((), None)
    parts = raw.split(b"\n")
    tail = parts.pop()
    items: list[Note] = []
    for number, part in enumerate(parts, 1):
        got = parse_row(part, number)
        if isinstance(got, Problem):
            return Loaded((), got)
        if dict(got.key) != {k: key[k] for k in ("workspace", "repo", "change")}:  # a line of another change put in this file
            return Loaded((), Problem(number, "the line is a note about another change"))
        items.append(got)
    if tail:
        return Loaded((), Problem(len(parts) + 1, "the last line is unfinished (no newline at its end)"))
    return Loaded(tuple(items), None)


def append(base: str | Path, key: Mapping[str, str], note: Note) -> None:
    """Add a note at the end of the store of `key`, under the lock. An unreadable store, and a store that holds `MAX_NOTES` already, are refused
    with nothing written."""
    line = _line(note)
    path = notes_path(base, key)
    with lock.held(base):  # the read and the write are one step
        current = read(base, key)
        if current.problem is not None:
            raise StoreError(f"{path.name}: line {current.problem.line}: {current.problem.why}; repair the file before adding to it (nothing was written)")
        if len(current.items) >= MAX_NOTES:
            raise StoreError(f"{path.name}: this change has {MAX_NOTES} notes already, which is the most kept (nothing was written)")
        store.append_lines(path, [line], "the note")


def unreadable_record(key: Mapping[str, str], problem: Problem) -> dict[str, Any]:
    """The record for the notes view when the store cannot be read: the shape of an incompleteness record, blocking for the notes view only, in
    this tool's words."""
    where = "the file as a whole" if problem.line == 0 else f"line {problem.line}"
    record = {
        "kind": "note_store_unreadable", "subject": store.key_slug(key) + ".jsonl", "not_established": "which notes were recorded for this change",
        "detail": f"{where}: {problem.why}", "blocks_selection": True,
    }
    for field in ("not_established", "detail"):
        wording.require_clean(record[field], f"note store record {field}")
    return record


# ------------------------------------------------------------------------------ staleness


@dataclass(frozen=True, slots=True)
class Entry:
    """A note with where it stands against the latest revision, and the label of its target (from the latest report, else this one, else the
    report it was made on) when there is one to show."""

    note: Note
    state: str
    label: str | None


def _reference(base: Path, report: Mapping[str, Any], key: Mapping[str, str]) -> tuple[int, Mapping[str, carry.Target] | None, Mapping[str, carry.Target], Callable[[int], Any]]:
    """The latest revision of the key and its targets (None when its report file is not at hand), this report's own targets, and a loader of
    the targets of any revision."""
    viewed = report["review"].get("revision")
    viewed = viewed if isinstance(viewed, int) and not isinstance(viewed, bool) else 0
    try:
        log = revisions.read(base, key)
    except StoreError:
        log = []
    latest = max(log[-1].number if log else 0, viewed)
    try:
        own: Mapping[str, carry.Target] = carry.targets_of(report)
    except carry.ReviewError:
        own = {}
    earlier = review_attach.revision_targets(base, log)
    return latest, (own if viewed == latest else earlier(latest)), own, earlier


def state_of(note: Note, latest: int, reference: Mapping[str, carry.Target] | None) -> str:
    """`orphaned` when the latest report no longer has the note's target, `stale` when the note is not on the latest revision or the claim of its
    target is not the one it was written against, else `current`. With no latest report at hand nothing is called orphaned."""
    tid = note.target_id
    if tid is not None and reference is not None and tid not in reference:
        return "orphaned"
    if note.revision != latest:
        return "stale"
    if tid is not None and reference is not None and reference[tid].fingerprint != note.fingerprint:
        return "stale"
    return "current"


def listing(base: str | Path, report: Mapping[str, Any]) -> tuple[list[Entry], Problem | None]:
    """The notes of the report's key with their states, in the order written, or the problem that kept them from being read. A report with no
    review data has none."""
    base = Path(base)
    try:
        key = review_attach.key_of(report)
    except carry.ReviewError:
        return [], None
    loaded = read(base, key)
    if loaded.problem is not None:
        return [], loaded.problem
    if not loaded.items:
        return [], None
    latest, reference, own, earlier = _reference(base, report, key)
    out = []
    for note in loaded.items:
        state = state_of(note, latest, reference)
        label = None
        tid = note.target_id
        if tid is not None:
            for source in (reference, own):
                if source and tid in source:
                    label = source[tid].label
                    break
            else:
                made_on = earlier(note.revision)
                label = made_on[tid].label if made_on and tid in made_on else None
        out.append(Entry(note, state, label))
    return out, None


def _entry_json(e: Entry, viewed: Any) -> dict[str, Any]:
    n = e.note
    return {
        "id": n.id, "kind": n.kind, "state": e.state, "revision": n.revision, "head_sha": n.head_sha, "report": n.report, "target_id": n.target_id,
        "target_label": e.label, "text": n.text, "client": {"name": n.client.name, "version": n.client.version}, "created_at": n.created_at,
        "verification": None if n.verification is None else dict(n.verification), "proposal": None if n.proposal is None else dict(n.proposal),
        "viewed": viewed,
    }


def attach(report: dict[str, Any], base: str | Path, as_of: str | None = None) -> None:
    """Give `report` the `model_notes` object that the renderers show in the Model notes section: `notes` (each with its state, its stamp
    fields and its text), `records` (a blocking `note_store_unreadable` record when the store could not be read) and `as_of`. Nothing is changed
    for a report with no review data, one that did not complete, or one with no note and no problem, except that a `model_notes` the report
    held (a hand edited file, a report saved through an API) is always removed first: it was not made by the store and carries no stamp the server built.
    Nothing else in the report is touched."""
    report.pop("model_notes", None)  # notes a report file holds were not made by the store: only what the store says now is shown
    if not review_attach.has_review(report) or report.get("status") != "completed":
        return
    try:
        entries, problem = listing(base, report)
        key = review_attach.key_of(report)
    except (carry.ReviewError, OSError, StoreError):
        return
    if not entries and problem is None:
        return
    stamp_time = as_of or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    viewed = report["review"].get("revision")
    report["model_notes"] = {
        "notes": [_entry_json(e, viewed) for e in entries],
        "records": [] if problem is None else [unreadable_record(key, problem)],
        "as_of": stamp_time,
    }


# ------------------------------------------------------------------------------ a proposed file (rule 4.7)

MAX_REVISIONS = 6  # revisions of a repository tried for one proposed file
MAX_LINES = 5  # lines named in a verification
MAX_DETAIL = 500


@dataclass(frozen=True, slots=True)
class Names:
    """The names by which the change can be linked to other code: `distinct` are those the analysis counted and found defined few enough times
    (`limits.distinct_max`), `rejected` those it found defined too often or could not count. Both are content of the report."""

    distinct: set[str]
    rejected: set[str]
    full: dict[str, set[str]] = field(default_factory=dict)  # the qualified names (`Class.method`, `Model.field`) each distinct name was found under


def _dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


def _key(path: str, name: str) -> str | None:
    """The name the analysis counts definitions of for a changed symbol: the last part for Python (a dunder is never a name to link code by), the
    whole name otherwise (`reachmap.adapters`, `_distinct_name`); None for the module itself."""
    if name == "<module>":
        return None
    if path.endswith(".py"):
        leaf = name.rsplit(".", 1)[-1]
        return None if _dunder(leaf) else leaf
    return name


def _d(v: Any) -> dict[str, Any]:
    return v if isinstance(v, dict) else {}


def _l(v: Any) -> list[Any]:
    return v if isinstance(v, list) else []


def _propagation(report: Mapping[str, Any]) -> dict[str, Any]:
    return _d(_l(report.get("propagations"))[0]) if _l(report.get("propagations")) else {}


def changed_names(report: Mapping[str, Any]) -> Names:
    """The changed symbols of the change itself (the first pass: the passes that follow work from seed lines, whose names were not counted for
    distinctiveness) and the contract fields the analysis counted, split by what the analysis found out about each. Read from the report only."""
    distinct: set[str] = set()
    rejected: set[str] = set()
    full: dict[str, set[str]] = {}
    for p in _l(_propagation(report).get("passes")):
        p = _d(p)
        if p.get("depth") != 0 or p.get("candidate"):
            continue
        for impact in _l(_d(p.get("analysis")).get("impacts")):
            impact = _d(impact)
            path = impact.get("path") if isinstance(impact.get("path"), str) else ""
            for s in _l(impact.get("symbols")):
                name = _d(s).get("name")
                key = _key(path, name) if isinstance(name, str) else None
                if key:
                    distinct.add(key)
                    full.setdefault(key, set()).add(name)
            for field in ("not_distinctive", "names_unreadable"):
                rejected.update(n for n in _l(impact.get(field)) if isinstance(n, str) and n)
    for c in _l(_propagation(report).get("contract_changes")):
        c = _d(c)
        name = c.get("name")
        if not isinstance(name, str) or not name or _dunder(name):
            continue
        if c.get("distinct") is True and c.get("counted") is True:
            distinct.add(name)
            if isinstance(c.get("symbol"), str):
                full.setdefault(name, set()).add(c["symbol"])
        else:
            rejected.add(name)
    return Names(distinct - rejected, rejected, {k: v for k, v in full.items() if k not in rejected})


def _sha_list(*sources: Any) -> list[str]:
    out: list[str] = []
    for sha in sources:
        if isinstance(sha, str) and SHA.fullmatch(sha) and sha not in out:
            out.append(sha)
    return out


def revisions_read(report: Mapping[str, Any], repo: str) -> list[str]:
    """The full commit ids at which the report read `repo`, in the order to try them: the revisions of the walk's passes in it, the head of the
    change when it is the changed repository, the pins of the hops and consumers that lead to it and the revisions of its contract reads and, for the
    changed repository, its branches. At most `MAX_REVISIONS`."""
    prop = _propagation(report)
    found: list[Any] = [_d(p).get("sha") for p in _l(prop.get("passes")) if _d(p).get("repo") == repo]
    change = _d(report.get("change"))
    if change.get("repo") == repo:
        found.append(change.get("head_sha"))
    for h in _l(prop.get("hops")):
        h = _d(h)
        if h.get("to_repo") == repo:
            found += [_d(pin).get("sha") for pin in _l(h.get("pins")) if _d(pin).get("repo") == repo]
    found += [_d(c).get("sha") for c in _l(prop.get("consumers")) if _d(c).get("repo") == repo]
    found += [_d(r).get("sha") for r in _l(prop.get("contract_reads")) + _l(prop.get("contract_candidates")) if _d(r).get("repo") == repo]
    if change.get("repo") == repo:
        found += [_d(b).get("commit") for b in _l(report.get("branches"))]
    return _sha_list(*found)[:MAX_REVISIONS]


class NoReader(ValueError):
    """The file is of a kind this version does not read as code."""


class Unparsed(ValueError):
    """The file could not be read as code (it does not parse)."""


_PY_BINDERS = tuple(t for t in (getattr(ast, n, None) for n in ("TypeVar", "ParamSpec", "TypeVarTuple")) if t is not None)


def _py_hit(node: ast.AST, name: str) -> bool:
    if isinstance(node, ast.Name):
        return node.id == name
    if isinstance(node, ast.Attribute):
        return node.attr == name
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.ExceptHandler, ast.MatchAs, ast.MatchStar) + _PY_BINDERS):
        return getattr(node, "name", None) == name
    if isinstance(node, (ast.arg, ast.keyword)):
        return node.arg == name
    if isinstance(node, ast.alias):
        return name in node.name.split(".") or node.asname == name
    if isinstance(node, (ast.Global, ast.Nonlocal)):
        return name in node.names
    if isinstance(node, ast.MatchMapping):
        return node.rest == name
    return False


def _py_lines(text: str, name: str) -> list[int]:
    parsed = py_syntax.parse(text)
    if parsed.problems or parsed.tree is None:
        raise Unparsed("the file does not parse")
    lines: set[int] = set()
    stack: list[ast.AST] = [parsed.tree]
    while stack:  # iterative: a deep tree must not exhaust the recursion limit
        node = stack.pop()
        if _py_hit(node, name):
            line = getattr(node, "lineno", None)
            if isinstance(line, int):
                lines.add(line)
        stack.extend(ast.iter_child_nodes(node))
    return sorted(lines)


def code_lines(path: str, text: str, name: str) -> list[int]:
    """The lines (at most `MAX_LINES`, the first ones) where `name` is a name in the code of the file: found by the lexer of the language (the
    JavaScript tokenizer, the Python parser), so a comment, a docstring and the words of a string or a template are not code, while the expression
    of an f-string or of a template substitution is. `NoReader` for a kind of file with no reader, `Unparsed` for one that does not parse."""
    if path.endswith(".py"):
        return _py_lines(text, name)[:MAX_LINES]
    if is_js_path(path):
        lex = js_tokens(text)
        return sorted({t.line for t in lex.tokens if t.kind == "ident" and t.value == name})[:MAX_LINES]
    raise NoReader("no reader for this kind of file")


Reader = Callable[[str, str, str], ReadResult]


def _verdict(reason: str, read: str = "not_attempted", sha: str | None = None, symbol: str | None = None, lines: Sequence[int] = (), detail: str | None = None) -> dict[str, Any]:
    return {
        "status": "verified" if reason == "verified" else "not_verified", "reason": reason, "read": read, "sha": sha, "symbol": symbol, "lines": list(lines),
        "detail": None if detail is None else _visible(detail)[:MAX_DETAIL],
    }


def unchecked() -> dict[str, Any]:
    """The verification of a proposed file that was given as plain note text: it was not checked."""
    return _verdict("unchecked_text")


def verify_file(
    report: Mapping[str, Any], repo: str, path: str, symbol: str, read: Reader | None, known: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    """Check a model's claim that `path` in `repo` is relevant because it names `symbol`. Verified only when all of these hold (nothing else is):
    `symbol` is the name of a symbol or contract field of the change that the analysis found distinctive (given with a class or module, as
    `Model.field`, it needs the very qualifier the changed symbol has; a bare name matches by its last part); the report read `repo` at a revision
    (`revisions_read`); the file is found there by `read` (the git layer: found, absent or unreadable, and an unreadable read is never an
    absence); and the symbol is a name in the code of the file (`code_lines`), not in a comment or a string. The result is the stored verification:
    `status`, a `reason` code (words in `REASON_WORDS`), what the read answered, the revision it was made at, the symbol and the lines, and the
    git layer's own words when the read was unreadable. It says nothing about whether the file is affected: a verified claim is still a note."""
    names = changed_names(report)
    leaf = symbol.rsplit(".", 1)[-1] if isinstance(symbol, str) else ""
    matched = next((c for c in (symbol, leaf) if c in names.distinct), None)
    if matched is None:
        rejected = isinstance(symbol, str) and (symbol in names.rejected or leaf in names.rejected)
        return _verdict("symbol_not_distinctive" if rejected else "symbol_not_changed")
    if "." in symbol and matched != symbol and symbol not in names.full.get(matched, set()):  # a claim with a class or module needs that of the changed symbol
        return _verdict("symbol_qualifier")
    if read is None:
        return _verdict("no_config")
    if known is not None and not known(repo):
        return _verdict("unknown_repo")
    shas = revisions_read(report, repo)
    if not shas:
        return _verdict("repo_not_read")
    unreadable: str | None = None
    for sha in shas:
        got = read(repo, path, sha)
        if got.state == Read.FOUND:
            try:
                lines = code_lines(path, got.text, matched.rsplit(".", 1)[-1])
            except NoReader:
                return _verdict("no_reader", "found", sha)
            except Unparsed:
                return _verdict("unparsed", "found", sha)
            if lines:
                return _verdict("verified", "found", sha, matched, lines)
            return _verdict("not_in_code", "found", sha)
        if got.state == Read.UNREADABLE and unreadable is None:
            unreadable = got.detail or "the read failed"
    if unreadable is not None:
        return _verdict("file_unreadable", "unreadable", None, None, (), unreadable)
    return _verdict("file_absent", "absent")
