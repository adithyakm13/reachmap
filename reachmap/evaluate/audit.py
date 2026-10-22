"""The citation audit (context section 9, "Citation integrity"): every line a report cites is read again at the commit it names and
compared with the text the report holds.

    reachmap audit REPORT.json... [--config reachmap.toml] [--show N] [--allow unreadable]

What is audited, per report:

- **Every piece of evidence anywhere in it**, found by shape (an object with `repo`, `path`, `line`, `ref` and `detail`), not by
  place: findings, areas (route line and chains), branch rows, every pass and hop of the propagation, call sites, consumers, contract
  reads, http edges, suggestions, mapped tests. Each distinct locator (repo, commit, path, line, text) is read once, through
  `Git.files_at` (one `ls-tree` per 200 paths and one `cat-file --batch` per commit, whatever the number of paths), and gets one answer:

  - `ok`          the file has that line and its text equals `detail` (a trailing carriage return of a CRLF line is not part of the text,
                  as the adapters cut it);
  - `mismatch`    the file is there and the line is not: another text, or no such line (both are shown);
  - `absent`      the path is not in the commit (git read the tree and it is not there);
  - `unreadable`  nothing was established: the repository is not in the configuration, the commit could not be fetched, the file could
                  not be read or decoded. Never counted as ok and never as a mismatch.
- **The model notes** the store beside the report holds (when there is a store): a verified proposed file is re-read at the commit it was
  verified at, and each line it names must still hold the symbol as a name; a note about a target the report lacks is not a failure
  by itself (it is the note's own `orphaned` state), but a `current` note whose target is not in the report is. A store that could not be
  read (`note_store_unreadable`, or `decision_store_unreadable` for the decisions) means its notes were never audited: each such record is
  counted as one `unreadable` with its detail.
- **A run that did not complete** (`failed`, `timed_out`) cites nothing to read and is never a pass: it is `not audited` and the exit code is 1.
- **Structure, from the report alone**: every finding has evidence; every finding and area reads back as one (so its id and fingerprint
  are what they hash to); no id is repeated; each claim (`import`, `route`, `def`, `wraps`, `operation`) is well formed and, where the
  cited line can say so, agrees with it (a `def:` symbol, or the model it is a field of, is a name on its line, an `import:` line is an import that names what the
  claim says, a `route:` claim on an area's route line carries the area's label, a `wraps:` claim names the file it is in; Java and Kotlin
  files get two more shapes, see `claim_problem`); a decision that is `stale` shows what changed, and what it says was added is evidence
  the report holds; a completed report has every list a completed report has; an object that has the look of evidence and lacks a key is a
  problem, never skipped.

The output is counts, never percentages. The exit code is 0 only when mismatch, absent, unreadable and structure problems are all zero and
every report was audited (`--allow unreadable` reports the unreadable ones on their own line and does not fail on them: for an audit run
without the network). A file that is not a report reachmap can read is exit 2.
A pass says that the citations resolve; it does not say the analysis found everything (rule 4.2).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from reachmap import payload
from reachmap.errors import ModelError
from reachmap.evaluate import remote_of
from reachmap.model import Area, Evidence, Finding, ReadResult
from reachmap.report import text as shown

__all__ = [
    "ABSENT", "Audit", "Citation", "Failure", "GitReader", "MISMATCH", "OK", "UNREADABLE", "audit_report", "load", "render", "run_audit",
]

OK, MISMATCH, ABSENT, UNREADABLE = "ok", "mismatch", "absent", "unreadable"
STATES = (OK, MISMATCH, ABSENT, UNREADABLE)
CLAIM_ROLES = ("import", "route", "def", "wraps", "operation")
SCHEMA = 1
DEFAULT_SHOW = 10
_SHOWN = 160  # characters of a cited or found line shown in the output
JVM_FILES = (".java", ".kt", ".kts")  # the files the Java conventions of the claim check apply to
COMPLETED_KEYS = ("change", "ref", "incompleteness", "notes", "findings", "areas", "branches", "propagations", "backend_calls", "suggestions")
UNREADABLE_STORES = ("note_store_unreadable", "decision_store_unreadable")
ANNOTATION_LOOKAHEAD = 12  # lines below an annotation line in which the declaration it belongs to names the symbol


class Reader(Protocol):
    """What the audit reads through: the answers of `Git.files_at` for the paths of one commit of one repository. A reader never raises
    for a read that failed: the answer is `ReadResult.unreadable(why)`."""

    def read(self, repo: str, ref: str, paths: Sequence[str]) -> dict[str, ReadResult]: ...


@dataclass(frozen=True)
class Citation:
    """One locator to re-read. `detail` is the text the report holds (None for a note's line, which holds a symbol instead)."""

    repo: str
    ref: str
    path: str
    line: int
    detail: str | None
    site: str  # where in the report (`areas[1].chains[0][2]`)
    symbol: str | None = None

    @property
    def key(self) -> tuple[Any, ...]:
        return (self.repo, self.ref, self.path, self.line, self.detail, self.symbol)

    def locator(self) -> str:
        return f"{self.repo} {self.path}:{self.line} at {self.ref[:12]}"


@dataclass(frozen=True)
class Failure:
    state: str
    citation: Citation | None  # None for a store that could not be read (what it holds was never audited)
    found: str | None  # the text of the line as the commit has it, or why there is none
    occurrences: int = 1
    what: str = ""  # what could not be read, when there is no citation


@dataclass
class Audit:
    """The answer for one report."""

    name: str
    status: str
    counts: dict[str, int] = field(default_factory=lambda: {s: 0 for s in STATES})
    occurrences: int = 0  # every place a distinct locator is cited
    failures: list[Failure] = field(default_factory=list)  # mismatch, absent and unreadable, in the order cited
    structure: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # what was not checked, said
    note_citations: int = 0

    @property
    def distinct(self) -> int:
        return sum(self.counts.values())

    @property
    def not_audited(self) -> bool:
        """A run that did not complete has nothing to read: that is not a pass, it is a report that was not audited."""
        return self.status != "completed"

    def passed(self, allow_unreadable: bool = False) -> bool:
        if self.not_audited:
            return False
        return not self.counts[MISMATCH] and not self.counts[ABSENT] and not self.structure and (allow_unreadable or not self.counts[UNREADABLE])


# ----------------------------------------------------------------------------- finding the citations


_EVIDENCE_KEYS = ("repo", "path", "line", "ref", "detail")


def collect(report: Any, problems: list[str]) -> list[Citation]:
    """Every piece of evidence of the report, in document order, found by the walker the evidence readers share (`payload.evidence_walk`). An
    object that has the look of evidence and lacks a key, or has all five and is not valid evidence (a ref that is not a commit, a path that
    leaves the repository, another key), is a structure problem, never a locator."""
    out: list[Citation] = []
    for site, node, complete in payload.evidence_walk(report):
        if not complete:
            problems.append(f"{site}: looks like evidence and lacks {sorted({'ref', 'detail'} - node.keys())}")
            continue
        try:
            extra = sorted(set(node) - set(_EVIDENCE_KEYS) - {"claim"})
            if extra:
                raise ModelError(f"unexpected keys {extra}")
            ev = Evidence(**{k: node[k] for k in (*_EVIDENCE_KEYS, *(("claim",) if "claim" in node else ()))})
        except (ModelError, TypeError, ValueError) as exc:
            problems.append(f"{site}: not valid evidence: {shown.one_line(str(exc))[:200]}")
            continue
        out.append(Citation(ev.repo, ev.ref, ev.path, ev.line, ev.detail, site))
    return out


# ----------------------------------------------------------------------------- reading


class GitReader:
    """The reader of `reachmap audit`: the repositories of a workspace through the git layer (the cache is the one `analyse` fills; a
    commit that is not in it is fetched). A repository the configuration lacks, a remote that cannot be made and any exception are
    answers of `unreadable`."""

    def __init__(self, workspace: Any | None, git: Any) -> None:
        self.workspace, self.git = workspace, git

    def read(self, repo: str, ref: str, paths: Sequence[str]) -> dict[str, ReadResult]:
        """All the paths of one commit through one `files_at` call: it lists the tree in chunks of its own and reads every blob with one
        `cat-file --batch`, so a commit costs a handful of processes whatever the number of paths."""
        try:
            remote = remote_of(self.workspace, repo)
        except LookupError as exc:
            return {p: ReadResult.unreadable(str(exc)) for p in paths}
        try:
            return dict(self.git.files_at(remote, list(paths), ref))
        except Exception as exc:  # whatever went wrong, the lines were not read: never an absence
            why = f"the read failed ({type(exc).__name__})"
            return {p: ReadResult.unreadable(why) for p in paths}


# ----------------------------------------------------------------------------- the checks of one line


def _lines_of(text: str) -> list[str]:
    """The lines of a file as the adapters count them: split on newlines only (not on the other separators `str.splitlines` knows), and the
    empty piece after a final newline is no line."""
    parts = text.split("\n")
    if parts and parts[-1] == "":
        parts.pop()
    return parts


def _same(detail: str, line: str) -> bool:
    """Equal after the documented carriage return handling: the adapters cut the trailing carriage return(s) of a CRLF line."""
    return detail == line.rstrip("\r") or detail == line.removesuffix("\r")


_WORD = "(?<![\\w$])%s(?![\\w$])"


def has_name(line: str, name: str) -> bool:
    return bool(name) and re.search(_WORD % re.escape(name), line) is not None


# ----------------------------------------------------------------------------- claims


def _split(claim: str, parts: int) -> list[str]:
    return claim.split(":", 1)[1].rsplit(":", parts - 1) if parts > 1 else [claim.split(":", 1)[1]]


def _route_of(claim: str) -> str:
    pieces = _split(claim, 2)
    return pieces[0] if len(pieces) == 2 else ""


def _jvm(ev: Mapping[str, Any]) -> bool:
    return isinstance(ev.get("path"), str) and ev["path"].endswith(JVM_FILES)


def annotation_symbol(ev: Mapping[str, Any]) -> str | None:
    """The symbol of a `def:` claim that a Java or Kotlin file cites at the annotation line above the declaration (the adapter cites a declaration
    from its first annotation, which names nothing): to be checked against the lines below it once the file is read. None for any other claim."""
    claim = ev.get("claim")
    if isinstance(claim, str) and claim.startswith("def:") and _jvm(ev) and isinstance(ev.get("detail"), str) and ev["detail"].lstrip().startswith("@"):
        return claim.split(":", 1)[1]
    return None


def claim_problem(ev: Mapping[str, Any]) -> str | None:
    """Why the claim of a piece of evidence does not agree with the line it is on (or is not well formed), or None. Two shapes are allowed
    for Java and Kotlin files only (`.java`, `.kt`, `.kts`): a definition cited at its annotation line (checked later, against the lines
    below it: `annotation_symbol`) and an import claim cited at a use of the name, because a class of the same package is used without an import."""
    claim = ev.get("claim")
    if claim is None:
        return None
    if not isinstance(claim, str) or ":" not in claim:
        return "the claim is not role:value"
    role = claim.split(":", 1)[0]
    if role not in CLAIM_ROLES:
        return f"unknown claim role {role!r}"
    detail = ev["detail"]
    if role == "def":
        symbol = claim.split(":", 1)[1]  # a field of a model is claimed as Model.field and cited at the line of the class: any part counts
        if annotation_symbol(ev) is None and not any(has_name(detail, part) for part in symbol.split(".")):
            return f"the claimed definition {symbol!r} is not a name on its line"
    elif role == "import":
        target_names = _split(claim, 2)
        if len(target_names) != 2 or not target_names[0] or not target_names[1]:
            return "an import claim is import:<file>:<names>"
        statement = re.search(r"\b(import|from|require|export)\b", detail) is not None
        if not statement and not _jvm(ev):
            return "the line of an import claim is not an import"
        incomplete = detail.rstrip().endswith(("(", "{", "\\", ","))  # the statement goes on below: the names are on the lines that follow
        for name in () if incomplete and statement else target_names[1].split(","):
            if name not in ("*", "default", "") and not has_name(detail, name):
                return f"the import claim names {name!r}, which the line does not" if statement else f"the line of the import claim neither imports nor uses {name!r}"
    elif role == "route":
        pieces = _split(claim, 2)
        if len(pieces) != 2 or not pieces[0] or not pieces[1]:
            return "a route claim is route:<route>:<file>"
    elif role == "wraps":
        if claim.split(":", 1)[1] != ev["path"]:
            return "a wraps claim names the file its line is in"
    elif role == "operation":
        pieces = _split(claim, 2)
        if len(pieces) != 2 or " " not in pieces[0]:
            return "an operation claim is operation:<METHOD path>:<signature>"
        method = pieces[0].split(" ", 1)[0].lower()
        if re.search(rf"(?<![A-Za-z]){re.escape(method)}(?![A-Za-z])", detail, re.IGNORECASE) is None:
            return f"the operation claim says {method!r}, which its line does not hold as a word"
    return None


# ----------------------------------------------------------------------------- structure


def _structure(report: Mapping[str, Any], problems: list[str]) -> list[tuple[str, Mapping[str, Any], str]]:
    """The structure problems of a report, from the report alone, and the annotation claims (site, evidence, symbol) that need the file."""
    from reachmap import findings as _findings  # noqa: F401  (registers the finding kinds a report may hold)

    if report.get("status") == "completed":
        missing = [k for k in COMPLETED_KEYS if report.get(k) is None]
        if missing:
            problems.append(f"a completed report has {', '.join(missing)}: it is missing or null")
    seen: dict[str, str] = {}
    for name, cls in (("findings", Finding), ("areas", Area)):
        items = report.get(name)
        if items is None:
            continue
        if not isinstance(items, list):
            problems.append(f"{name}: not a list")
            continue
        for i, item in enumerate(items):
            where = f"{name}[{i}]"
            if not isinstance(item, dict):
                problems.append(f"{where}: not an object")
                continue
            if name == "findings" and not item.get("evidence"):
                problems.append(f"{where}: a finding with no evidence")
                continue
            if cls is Area:
                _area_routes(item, where, problems)
            try:
                found = cls.from_dict(item)
            except (ModelError, TypeError, KeyError, ValueError, AttributeError) as exc:
                problems.append(f"{where}: does not read back as {'a finding' if cls is Finding else 'an area'}: {shown.one_line(str(exc))[:200]}")
                continue
            if found.id in seen:
                problems.append(f"{where}: repeats the id of {seen[found.id]}")
            seen[found.id] = where
    deferred: list[tuple[str, Mapping[str, Any], str]] = []
    for site, ev, complete in payload.evidence_walk(report):  # every claim on every piece of evidence, wherever it is
        if not complete or not isinstance(ev.get("detail"), str) or not isinstance(ev.get("path"), str):
            continue  # said once, by `collect`
        if bad := claim_problem(ev):
            problems.append(f"{site}: {bad}")
        elif (symbol := annotation_symbol(ev)) is not None:
            deferred.append((site, ev, symbol))
    _decisions(report, problems)
    return deferred


def _area_routes(item: Mapping[str, Any], where: str, problems: list[str]) -> None:
    """An area's route line carries the route claim of its own label, and the last hop of each chain (the route line of one mount) one
    of the routes the area is mounted at. Said whether or not the area reads back, so an edited claim is found as such."""
    label, mounted = item.get("label"), item.get("also_mounted")
    chains, line = item.get("chains"), item.get("route_line")
    if not (isinstance(label, str) and isinstance(mounted, list) and isinstance(chains, list) and isinstance(line, dict)):
        return
    routes = (label, *(m for m in mounted if isinstance(m, str)))
    for j, chain in enumerate(chains):
        last = chain[-1] if isinstance(chain, list) and chain and isinstance(chain[-1], dict) else {}
        if isinstance(last.get("claim"), str) and last["claim"].startswith("route:") and _route_of(last["claim"]) not in routes:
            problems.append(f"{where}.chains[{j}][{len(chain) - 1}]: the route claim says {_route_of(last['claim'])!r}, which is not a route of the area")
    claim = line.get("claim")
    if claim is not None and (not isinstance(claim, str) or not claim.startswith("route:") or _route_of(claim) != label):
        problems.append(f"{where}.route_line: the claim of a route line is the route claim of the area's own label")


def _decisions(report: Mapping[str, Any], problems: list[str]) -> None:
    """A stale decision's `changes` list what was added and removed from the claim's evidence; what was added is evidence of this report."""
    review = report.get("review")
    decisions = review.get("decisions") if isinstance(review, dict) else None
    if not isinstance(decisions, dict):
        return
    held: set[tuple[str, str, str]] = set()
    for name in ("areas", "findings"):
        for item in report.get(name) or []:
            if isinstance(item, dict):
                evidence = [item.get("route_line"), *(e for c in item.get("chains") or [] for e in c)] if name == "areas" else item.get("evidence") or []
                held.update((e["repo"], e["path"], e["detail"].strip()) for e in evidence if isinstance(e, dict) and isinstance(e.get("detail"), str)
                            and isinstance(e.get("path"), str) and isinstance(e.get("repo"), str))
    for i, entry in enumerate(decisions.get("stale") or []):
        changes = entry.get("changes") if isinstance(entry, dict) else None
        if not isinstance(changes, dict):
            continue
        for j, item in enumerate(changes.get("added") or []):
            try:
                triple = (item["repo"], item["path"], item["detail"].strip())
            except (KeyError, TypeError, AttributeError):
                problems.append(f"review.decisions.stale[{i}].changes.added[{j}]: not a (repo, path, detail) entry")
                continue
            if triple not in held:
                problems.append(f"review.decisions.stale[{i}].changes.added[{j}]: says a line was added that no evidence of the report holds")


# ----------------------------------------------------------------------------- notes


def _note_citations(report: Mapping[str, Any], problems: list[str]) -> list[Citation]:
    block = report.get("model_notes")
    if not isinstance(block, dict):
        return []
    ids = {item.get("id") for name in ("areas", "findings") for item in report.get(name) or [] if isinstance(item, dict)}
    out: list[Citation] = []
    for i, note in enumerate(block.get("notes") or []):
        where = f"model_notes.notes[{i}]"
        if not isinstance(note, dict):
            continue
        if note.get("state") == "current" and note.get("target_id") is not None and note["target_id"] not in ids:
            problems.append(f"{where}: a current note about a target the report does not have")
        verification, proposal = note.get("verification"), note.get("proposal")
        if not (isinstance(verification, dict) and verification.get("status") == "verified" and isinstance(proposal, dict)):
            continue
        sha, symbol, lines = verification.get("sha"), verification.get("symbol"), verification.get("lines")
        if not (isinstance(sha, str) and isinstance(symbol, str) and isinstance(lines, list)
                and isinstance(proposal.get("repo"), str) and isinstance(proposal.get("path"), str)):
            problems.append(f"{where}: a verified proposal without the commit, symbol or lines it was verified at")
            continue
        for n in lines:
            if isinstance(n, int) and not isinstance(n, bool) and n >= 1:
                try:
                    Evidence(proposal["repo"], proposal["path"], n, sha, "x")
                except (ModelError, TypeError, ValueError):
                    problems.append(f"{where}: the locator of a verified proposal is not valid")
                    break
                out.append(Citation(proposal["repo"], sha, proposal["path"], n, None, f"{where}.verification.lines", symbol.rsplit(".", 1)[-1]))
    return out


# ----------------------------------------------------------------------------- the audit of one report


def audit_report(report: Mapping[str, Any], reader: Reader, name: str = "report") -> Audit:
    """The audit of a report held in memory, reading through `reader`."""
    result = Audit(name, str(report.get("status")))
    cited = collect(report, result.structure) + _note_citations(report, result.structure)
    deferred = _structure(report, result.structure)
    if result.not_audited:
        result.notes.append(f"the run status is {result.status}: the run did not complete, so the report was not audited")
    for site, detail in _unreadable_stores(report):  # a store that could not be read: what it holds was never shown, so it was never audited
        result.counts[UNREADABLE] += 1
        result.failures.append(Failure(UNREADABLE, None, detail, what=site))
    distinct: dict[tuple[Any, ...], tuple[Citation, int]] = {}
    for c in cited:
        held = distinct.get(c.key)
        distinct[c.key] = (c, 1) if held is None else (held[0], held[1] + 1)
    result.occurrences = len(cited)
    result.note_citations = sum(1 for c, _ in distinct.values() if c.symbol is not None)
    by_commit: dict[tuple[str, str], list[str]] = {}
    for c, _ in distinct.values():
        paths = by_commit.setdefault((c.repo, c.ref), [])
        if c.path not in paths:
            paths.append(c.path)
    texts: dict[tuple[str, str], dict[str, ReadResult]] = {}
    for (repo, ref), paths in by_commit.items():  # one batch of reads per commit
        texts[(repo, ref)] = reader.read(repo, ref, paths)
    split: dict[tuple[str, str, str], list[str] | None] = {}
    for c, count in distinct.values():
        got = texts[(c.repo, c.ref)].get(c.path)
        state, found = _answer(c, got, split)
        result.counts[state] += 1
        if state != OK:
            result.failures.append(Failure(state, c, found, count))
    for site, ev, symbol in deferred:  # an annotation line names nothing: the declaration below it must
        body = texts.get((ev["repo"], ev["ref"]), {}).get(ev["path"])
        if body is None or not body.is_found:
            continue  # the citation itself did not resolve and is counted above
        lines = split.get((ev["repo"], ev["ref"], ev["path"])) or _lines_of(body.text)
        below = lines[ev["line"]:ev["line"] + ANNOTATION_LOOKAHEAD]
        if not any(has_name(text, part) for text in below for part in symbol.split(".")):
            result.structure.append(f"{site}: the annotation at line {ev['line']} is not followed, within {ANNOTATION_LOOKAHEAD} lines, by a declaration that names {symbol!r}")
    return result


def _unreadable_stores(report: Mapping[str, Any]) -> list[tuple[str, str]]:
    """(where, detail) of every record that says a store of notes or decisions could not be read: the notes or decisions it holds were not shown
    and cannot be audited, which is not the same as there being none."""
    out: list[tuple[str, str]] = []
    review = report.get("review")
    decisions = review.get("decisions") if isinstance(review, dict) else None
    for name, block in (("model_notes", report.get("model_notes")), ("review.decisions", decisions)):
        records = block.get("records") if isinstance(block, dict) else None
        for i, record in enumerate(records if isinstance(records, list) else []):
            if isinstance(record, dict) and record.get("kind") in UNREADABLE_STORES:
                detail = record.get("detail")
                out.append((f"{name}.records[{i}] ({record['kind']})", detail if isinstance(detail, str) else "the store could not be read"))
    return out


def _answer(c: Citation, got: ReadResult | None, split: dict[tuple[str, str, str], list[str] | None]) -> tuple[str, str | None]:
    if got is None:
        return UNREADABLE, "the reader gave no answer for the path"
    if got.is_absent:
        return ABSENT, "the path is not in the commit"
    if got.is_unreadable:
        return UNREADABLE, got.detail or "the read failed"
    key = (c.repo, c.ref, c.path)
    if key not in split:
        split[key] = _lines_of(got.text)
    lines = split[key]
    assert lines is not None
    if not 1 <= c.line <= len(lines):
        return MISMATCH, f"no line {c.line}: the file has {len(lines)} line{'s' if len(lines) != 1 else ''}"
    line = lines[c.line - 1]
    if c.detail is not None:
        return (OK, None) if _same(c.detail, line) else (MISMATCH, line)
    return (OK, None) if c.symbol and has_name(line, c.symbol) else (MISMATCH, line)


# ----------------------------------------------------------------------------- files and output


class AuditError(ValueError):
    """A file that is not a report this audit reads (the command's exit 2)."""


def load(path: str | Path) -> dict[str, Any]:
    """A report file as JSON, through the same check the renderers make (`report.check`): a file that is not a report of schema 1, or a report
    that the renderers could not read (a completed one without its result lists, a finding without evidence), is an `AuditError`, the
    command's exit 2. What is wrong inside a report that does read is the audit's to say."""
    from reachmap import report as reports

    try:
        raw = Path(path).read_bytes()
        data = json.loads(raw.decode("utf-8"))
    except OSError as exc:
        raise AuditError(f"{Path(path).name}: cannot be read: {exc.strerror or type(exc).__name__}") from None
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise AuditError(f"{Path(path).name}: is not a JSON report") from None
    if not isinstance(data, dict) or data.get("schema") != SCHEMA or not isinstance(data.get("status"), str):
        raise AuditError(f"{Path(path).name}: is not a report of schema {SCHEMA}")
    try:
        reports.check(data)
    except reports.ReportError as exc:
        raise AuditError(f"{Path(path).name}: not a report reachmap can read: {exc}") from None
    return data


def _clip(text: str | None) -> str:
    """A line of the report or of a commit as the output shows it: cut first (a 2 MB line is never scanned), then made visible and one line."""
    full = text or ""
    cut = f" ... ({len(full) - _SHOWN} more characters)" if len(full) > _SHOWN else ""
    return shown.one_line(shown.visible(full[:_SHOWN])) + cut


def _counts_line(a: Audit) -> str:
    c = a.counts
    return f"ok {c[OK]}, mismatch {c[MISMATCH]}, absent {c[ABSENT]}, unreadable {c[UNREADABLE]}"


def render(results: Sequence[Audit], show: int = DEFAULT_SHOW, allow_unreadable: bool = False) -> list[str]:
    """The text of an audit: per report its counts, the first `show` failures with their locators (both texts for a mismatch) and the structure
    problems, then the totals. No percentages."""
    lines: list[str] = []
    for a in results:
        verdict = "NOT AUDITED: the run did not complete" if a.not_audited else "passed" if a.passed(allow_unreadable) else "FAILED"
        lines.append(f"{a.name}: {verdict}")
        lines.append(f"  citations: {a.distinct} distinct ({_counts_line(a)}); {a.occurrences} places cite them" + (f"; {a.note_citations} are model note lines" if a.note_citations else ""))
        lines.append(f"  structure problems: {len(a.structure)}")
        shown_failures = 0
        for state in (MISMATCH, ABSENT, UNREADABLE):
            for f in (x for x in a.failures if x.state == state):
                if shown_failures >= show:
                    break
                shown_failures += 1
                if f.citation is None:  # a store of notes or decisions that could not be read
                    lines.append(f"  {state}: {f.what}")
                    lines.append(f"    why: {_clip(f.found)}")
                    continue
                lines.append(f"  {state}: {f.citation.locator()} ({f.citation.site})")
                if f.citation.detail is not None:
                    lines.append(f"    report: {_clip(f.citation.detail)}")
                elif f.citation.symbol is not None:
                    lines.append(f"    symbol: {_clip(f.citation.symbol)}")
                lines.append(f"    commit: {_clip(f.found)}" if state == MISMATCH else f"    why: {_clip(f.found)}")
        left = len(a.failures) - shown_failures
        if left > 0:
            lines.append(f"  ... {left} more failure{'s' if left != 1 else ''} not shown (--show N)")
        for text in a.structure[:show]:
            lines.append(f"  structure: {_clip(text)}")
        if len(a.structure) > show:
            lines.append(f"  ... {len(a.structure) - show} more structure problem{'s' if len(a.structure) - show != 1 else ''} not shown")
        for text in a.notes:
            lines.append(f"  note: {text}")
    total = {s: sum(a.counts[s] for a in results) for s in STATES}
    lines.append(
        f"{len(results)} report{'s' if len(results) != 1 else ''}: {sum(a.distinct for a in results)} distinct citations "
        f"(ok {total[OK]}, mismatch {total[MISMATCH]}, absent {total[ABSENT]}, unreadable {total[UNREADABLE]}), "
        f"{sum(len(a.structure) for a in results)} structure problems; {sum(1 for a in results if not a.passed(allow_unreadable))} report(s) failed"
        + (f" ({sum(a.not_audited for a in results)} not audited)" if any(a.not_audited for a in results) else ""))
    if allow_unreadable and total[UNREADABLE]:
        lines.append(f"unreadable (allowed, not ok): {total[UNREADABLE]} citations were not read, so nothing is said about them")
    lines.append("A pass says every citation resolves at its commit; it does not say the analysis found everything.")
    return lines


def run_audit(paths: Sequence[str], reader: Reader, attach_notes: Any = None) -> list[Audit]:
    """The audit of report files, in the order given. `attach_notes(data, base_dir)`, when given, adds the model notes of the store beside the report."""
    results = []
    for path in paths:
        data = load(path)
        if attach_notes is not None:
            attach_notes(data, Path(path).parent)
        results.append(audit_report(data, reader, Path(path).name))
    return results


def command(ns: Any, env: Mapping[str, str], out: Any, git_env: Mapping[str, str] | None) -> int:
    """`reachmap audit`: exit 0 when every citation of every report resolves, 1 when one does not (or could not be read), 2 for a usage error."""
    from reachmap import config, gitlayer, notes
    from reachmap.evaluate import EXIT_FAILED, EXIT_OK, UsageError, emit

    if ns.show < 0:
        raise UsageError("--show: a number of failures, 0 or more")
    try:
        workspace = config.load(ns.config, env=env)
    except config.ConfigError as exc:
        raise UsageError(f"config error: {exc}") from None
    reader = GitReader(workspace, gitlayer.Git(gitlayer.default_cache(env), env=git_env))
    try:
        results = run_audit(ns.reports, reader, attach_notes=notes.attach)
    except AuditError as exc:
        raise UsageError(str(exc)) from None
    allow = ns.allow == "unreadable"
    emit(out, "\n".join(render(results, ns.show, allow)))
    return EXIT_OK if all(a.passed(allow) for a in results) else EXIT_FAILED
