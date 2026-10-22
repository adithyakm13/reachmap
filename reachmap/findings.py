"""Finding builders: one function per kind of finding, each built from the evidence objects the earlier steps found.

A title is never free text: it is generated from the identity (what the claim is about) and counts, and it passes the
banned wording check (anything quoted from code, such as a route or a path, is defused first: `clean`). The identity
holds stable parts only (kind, repository, path, symbol, route, endpoint, branch), never a line number or a count,
so decisions attach to the same claim across runs; the title may carry counts because it is not hashed.

Risk is how bad it is if the claim is true; confidence is how well the evidence supports it. They are separate fields
set by the rules below and never averaged or combined. A confidence is the weakest of the things the claim rests on (the
weakest link decides, nothing is blended), and `confidence_basis` says why, starting with the weakest:
`import-backed`, `typed read`, `http edge`, `by name`, `guard proven`, `undecidable`, `partial read`, `pin read`.

| kind | risk | confidence |
|---|---|---|
| `direct` | medium | high when the chain is import-backed and read whole; medium for an undecidable call site (the argument cannot be decided, so it is treated as affected) or a partial read; low by name |
| `cross-repo` | medium | as `direct`, and medium for an http match that is a literal in a parameter, a tie or of unknown method, and for a contract read by name; low by name |
| `exclusion` | low | high: the importer's own import line shows it names only other exports |
| `guard-excluded` | low | high: the guard line and the call line show the call cannot enter the changed code |
| `contract-tightened` | high | per consumer: high when every read of the field is typed (the receiver is proven), medium when one is by name, low when no read was found or the name is not distinctive |
| `contract-relaxed` | medium | as `contract-tightened` |
| `removed_route_still_called` | high | low (the README values, from the http edge) |
| `added_route_uncalled` | low | medium (the README values, from the http edge) |
| `operation-changed` | medium | high when both versions of the spec were read and the signature of the operation compared; medium when a reference of the operation points outside the spec (whether it changed is not known) |
| `release` | high when the consumer has adopted the change, medium on adoption or when not decided | high when adoption was decided from the pin and the tag's ancestry, low when it was not |
| `branch-missing` | high | by how many changed files agree: all high, at least half medium, fewer low |

`duplicate` needs a parallel-implementation detector and is not built; nothing registers a finding nobody builds.
Every area has one `direct` (changed repository) or `cross-repo` finding; the areas themselves carry the priority.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from reachmap import redact
from reachmap.adapters import Exclusion
from reachmap.edges import FindingInput
from reachmap.edges.contract import ContractChange, ContractRead
from reachmap.edges.package import Consumer
from reachmap.model import Area, Confidence, Evidence, Finding, Risk

__all__ = ["Backing", "TABLE_KINDS", "added_route_uncalled", "branch_missing", "build", "clean", "contract_change", "cross_repo",
           "direct", "exclusion", "from_input", "operation_changed", "release", "removed_route_still_called"]

TABLE_KINDS = (
    "direct", "cross-repo", "exclusion", "guard-excluded", "contract-tightened", "contract-relaxed",
    "removed_route_still_called", "added_route_uncalled", "operation-changed", "release", "branch-missing",
)

_NOUN = {"screen": "the screen", "api_route": "the API route", "task": "the task", "job": "the job", "topic": "the topic"}


def clean(text: object, limit: int = 400) -> str:
    """Text that quotes code (a route, a path, a symbol), fit for a title or a basis: scrubbed, on one line, and with any
    banned phrase defused (a route called `/safe` stays readable as `/s_afe`; the identity keeps the real name)."""
    return redact.for_record(text, limit)


# ------------------------------------------------------------------------------ what a claim rests on


@dataclass(frozen=True, slots=True)
class Backing:
    """The confidence of a claim and the reason, written out. `combine` keeps the weakest. For an area, `chain` is the chain
    the confidence is that of (the best one), and the one the finding cites."""

    confidence: Confidence
    text: str
    chain: tuple[Evidence, ...] | None = None

    @staticmethod
    def whole(area: Area) -> "Backing":
        n = len(area.chains[0]) if area.chains else 0
        return Backing(Confidence.HIGH, f"import-backed: {n} cited line(s) from the change to {clean(area.label, 120)}")

    @staticmethod
    def by_name(reason: str) -> "Backing":
        return Backing(Confidence.LOW, f"by name: {clean(reason)}")


def combine(parts: Sequence[Backing]) -> Backing:
    """The weakest confidence, with every reason, the weakest first (ties keep the order given)."""
    ordered = sorted(parts, key=lambda p: p.confidence.rank)
    return Backing(ordered[0].confidence, "; ".join(p.text for p in ordered))


# ------------------------------------------------------------------------------ areas


def _chain_title(area: Area) -> str:
    extra = f", through {len(area.chains)} chains" if len(area.chains) > 1 else ""
    mounted = f"; also mounted at {len(area.also_mounted)} other route(s)" if area.also_mounted else ""
    return extra + mounted


def _evidence(area: Area, backing: Backing, definitions: Sequence[Evidence] = (), changed: Iterable[Evidence] = ()) -> tuple[Evidence, ...]:
    """What an area finding cites: the definition lines of the changed symbols (when they are known), then the chain that reaches
    the area (the best one; a candidate found by name has no chain, and its route line is what there is to cite). A hop of the
    chain that is one of the changed lines (a change inside the entry file starts its chain at the first changed line) is left
    out when a definition is cited: the claim must not go stale when the body of the function is edited again."""
    chain = backing.chain if backing.chain is not None else (area.chains[0] if area.chains else (area.route_line,))
    if definitions:
        drop = {e for e in changed if e not in definitions}
        kept = tuple(e for e in chain if e not in drop)
        chain = kept or chain
    return tuple(dict.fromkeys((*definitions, *chain)))


def direct(area: Area, backing: Backing, definitions: Sequence[Evidence] = (), changed: Iterable[Evidence] = ()) -> Finding:
    """A changed symbol reaching an area in the changed repository."""
    word = "may reach" if area.by_name else "reaches"
    return Finding(
        kind="direct", title=clean(f"The change {word} {_NOUN.get(area.kind, 'the area')} {area.label} in {area.repo}{_chain_title(area)}"),
        workflow=clean(area.module), risk=Risk.MEDIUM, confidence=backing.confidence, confidence_basis=clean(backing.text, 600),
        evidence=_evidence(area, backing, definitions, changed), identity=("direct", area.repo, area.kind, area.label),
    )


def cross_repo(area: Area, origin_repo: str, backing: Backing, definitions: Sequence[Evidence] = (), changed: Iterable[Evidence] = ()) -> Finding:
    """An area (a screen, a route) in another repository, reached through edges from the changed one."""
    word = "may reach" if area.by_name else "reaches"
    return Finding(
        kind="cross-repo", title=clean(f"The change in {origin_repo} {word} {_NOUN.get(area.kind, 'the area')} {area.label} in {area.repo}{_chain_title(area)}"),
        workflow=clean(area.module), risk=Risk.MEDIUM, confidence=backing.confidence, confidence_basis=clean(backing.text, 600),
        evidence=_evidence(area, backing, definitions, changed), identity=("cross-repo", area.repo, area.kind, area.label),
    )


def _area_backing(area: Area, reached: Any, prop: Any, undecidable: set[tuple[str, str]]) -> Backing:
    """The confidence of an area: the best of its chains, each as sure as its weakest link (a chain that passes an undecidable
    call site, a partial read, a loose http match or a read by name is medium; by name only, low). The chain that gives it is
    in the result."""
    if area.by_name:
        return Backing(Confidence.LOW, Backing.by_name("a candidate found through a name or a partial URL, not through an import; shown apart and never must test").text,
                       area.chains[0] if area.chains else None)
    best: Backing | None = None
    for chain in area.chains or ((area.route_line,),):
        got = _chain_backing(area, chain, reached, prop, undecidable)
        if best is None or got.confidence.rank > best.confidence.rank:  # a tie keeps the first chain
            best = got
    assert best is not None
    return best


def _chain_backing(area: Area, chain: tuple[Evidence, ...], reached: Any, prop: Any, undecidable: set[tuple[str, str]]) -> Backing:
    parts = [Backing(Confidence.HIGH, f"import-backed: {len(chain)} cited line(s) from the change to {clean(area.label, 120)}")]
    if not reached.complete:
        why = list(reached.reasons)
        parts.append(Backing(Confidence.MEDIUM, "partial read: " + clean((why[0] if why else "something along the chain was not established completely") + (f" (and {len(why) - 1} more)" if len(why) > 1 else ""), 300)))
    if any((e.repo, e.path) in undecidable for e in chain):
        parts.append(Backing(Confidence.MEDIUM, "undecidable: a call site passes an argument the guard cannot decide, so it is treated as affected"))
    members = set(chain)
    for m in prop.http_edges:
        if m.client in members:
            if m.candidate:
                parts.append(Backing(Confidence.LOW, "by name: the client URL is partial"))
            elif m.literal_in_parameter or m.ambiguous or m.method_unknown:
                what = [w for w, on in (("a literal in a parameter", m.literal_in_parameter), ("a tie between routes", m.ambiguous), ("a method that is not known", m.method_unknown)) if on]
                parts.append(Backing(Confidence.MEDIUM, "http edge: the URL fits the route as " + " and ".join(what)))
            else:
                parts.append(Backing(Confidence.HIGH, "http edge: the URL fits the route exactly"))
    for r in prop.contract_reads:
        if any(e in members for e in r.evidence):
            if r.basis == "typed":
                parts.append(Backing(Confidence.HIGH, "typed read: the receiver of the field is proven"))
            else:
                parts.append(Backing(Confidence.MEDIUM, "by name: a read of a distinctive field whose receiver is not proven"))
    got = combine(_dedupe_parts(parts))
    return Backing(got.confidence, got.text, chain)


def _dedupe_parts(parts: Iterable[Backing]) -> list[Backing]:
    return list(dict.fromkeys(parts))


# ------------------------------------------------------------------------------ exclusions


_EXCLUSION_WORDS = {
    "named_export_narrowing": ("exclusion", "it imports only names the change does not touch", "import-backed: the importer's own import line names only other exports"),
    "guard": ("guard-excluded", "the change sits under a guard its call cannot enter", "guard proven: the guard line and the call line show the call cannot enter the changed code"),
}


def exclusion(repo: str, changed_path: str, ex: Exclusion) -> Finding:
    """An importer or a call site that is not reached, with the evidence that shows it."""
    kind, why, basis = _EXCLUSION_WORDS[ex.reason]
    subject, changed = redact.display(ex.subject_path), redact.display(changed_path)  # a name that is not UTF-8 is shown escaped
    return Finding(
        kind=kind, title=clean(f"{subject} is not reached through {changed}: {why}"), workflow="", risk=Risk.LOW,
        confidence=Confidence.HIGH, confidence_basis=clean(basis), evidence=ex.evidence,
        identity=(kind, repo, changed, subject, ex.reason),
    )


# ------------------------------------------------------------------------------ contracts and releases


def contract_change(kind: str, change: ContractChange, consumer: str, reads: Sequence[ContractRead], consumers: Sequence[Consumer]) -> Finding:
    """A tightened or relaxed model member, for one consumer: the reads of it in that consumer, or its pin when none was found."""
    mine = [r for r in reads if r.repo == consumer and r.owner == change.owner and r.field == change.name]
    word = "required" if kind == "contract-tightened" else "optional"
    if not change.distinct:
        backing = Backing(Confidence.LOW, f"name not distinctive: {clean(change.name, 80)} is defined {change.definitions} times, so reads of it in {clean(consumer, 80)} were not linked by the name")
    elif not mine:
        backing = Backing(Confidence.LOW, f"no read of {clean(change.name, 80)} in {clean(consumer, 80)} was found; its pin is cited")
    elif all(r.basis == "typed" for r in mine):
        backing = Backing(Confidence.HIGH, f"typed read: {len(mine)} read(s) whose receiver is proven")
    else:
        backing = Backing(Confidence.MEDIUM, f"by name: {sum(r.basis != 'typed' for r in mine)} of {len(mine)} read(s) have a receiver that is not proven")
    evidence: list[Evidence] = list(change.evidence)
    for r in mine:
        evidence.extend(r.evidence)
    if not mine:
        pins = [c for c in consumers if c.repo == consumer and c.evidence]
        if pins:
            evidence.extend(pins[0].evidence)
    places = f"{consumer} reads it in {len(mine)} place(s)" if mine else f"no read of it was found in {consumer}"
    return Finding(
        kind=kind, title=clean(f"{change.symbol} is now {word}: {places}"), workflow="",
        risk=Risk.HIGH if kind == "contract-tightened" else Risk.MEDIUM, confidence=backing.confidence, confidence_basis=clean(backing.text, 600),
        evidence=tuple(dict.fromkeys(evidence)), identity=(kind, change.repo, change.path, change.symbol, consumer),
    )


def release(c: Consumer) -> Finding | None:
    """Whether a consumer on a branch has the change (adopted) or will be exposed to it on adoption. None when the
    consumer pins nothing the run could cite."""
    if not c.evidence:
        return None
    where = f"{c.repo} on {c.branch}"
    if c.adoption == "adopted":
        title, risk, backing = f"{where} pins {c.pin}, which has the change: it is adopted", Risk.HIGH, Backing(Confidence.HIGH, f"pin read: {c.manifest} names {c.pin} and the ancestry of its tag contains the change")
    elif c.adoption == "on_adoption":
        title, risk, backing = f"{where} pins {c.pin} and will be exposed to the change on adoption", Risk.MEDIUM, Backing(Confidence.HIGH, f"pin read: {c.manifest} names {c.pin} and that tag does not contain the change")
    else:
        title, risk, backing = f"Whether {where} has the change was not decided", Risk.MEDIUM, Backing(Confidence.LOW, "pin read: " + (c.detail or "adoption was not decided"))
    return Finding(
        kind="release", title=clean(title), workflow="", risk=risk, confidence=backing.confidence, confidence_basis=clean(backing.text, 600),
        evidence=c.evidence, identity=("release", c.producer, c.repo, c.branch),
    )


# ------------------------------------------------------------------------------ http findings


def from_input(inp: FindingInput) -> Finding:
    """The finding of an http edge input (`removed_route_still_called`, `added_route_uncalled`), with the README values."""
    if inp.kind == "removed_route_still_called":
        return removed_route_still_called(inp)
    if inp.kind == "added_route_uncalled":
        return added_route_uncalled(inp)
    if inp.kind == "operation-changed":
        return operation_changed(inp)
    raise ValueError(f"no builder for the finding input {inp.kind!r}")


def _basis(inp: FindingInput) -> str:
    return clean(inp.confidence_basis + (f"; {inp.detail}" if inp.detail else ""), 600)


def removed_route_still_called(inp: FindingInput) -> Finding:
    ident = inp.identity
    if len(ident) >= 6:
        title = f"Route {ident[2]} {ident[3]} is removed and {ident[4]} still calls it ({ident[5]})"
    else:
        title = f"A removed route is still called ({' '.join(ident[1:])})"
    return Finding(kind="removed_route_still_called", title=clean(title), workflow="", risk=Risk(inp.risk), confidence=Confidence(inp.confidence),
                   confidence_basis=_basis(inp), evidence=inp.evidence, identity=inp.identity)


def added_route_uncalled(inp: FindingInput) -> Finding:
    ident = inp.identity
    title = f"Route {ident[2]} {ident[3]} is added and no client names it yet" if len(ident) >= 4 else f"An added route has no caller ({' '.join(ident[1:])})"
    return Finding(kind="added_route_uncalled", title=clean(title), workflow="", risk=Risk(inp.risk), confidence=Confidence(inp.confidence),
                   confidence_basis=_basis(inp), evidence=inp.evidence, identity=inp.identity)


def operation_changed(inp: FindingInput) -> Finding:
    """An operation of a spec that the change alters (parameters, request body, responses or a schema they reach), with the spec line of
    the operation at both versions as evidence. What follows for the clients is not claimed: the claim is that the declared contract changed."""
    ident = inp.identity
    title = f"The spec changes the contract of {ident[2]} {ident[3]} in {ident[1]}" if len(ident) >= 4 else f"An operation changes in the spec ({' '.join(ident[1:])})"
    return Finding(kind="operation-changed", title=clean(title), workflow="", risk=Risk(inp.risk), confidence=Confidence(inp.confidence),
                   confidence_basis=_basis(inp), evidence=inp.evidence, identity=inp.identity)


# ------------------------------------------------------------------------------ branches


def branch_missing(matrix: Any, repo: str) -> list[Finding]:
    """The change is on a release branch and not on the primary branch (a fix that never reached `main`), or the primary
    branch has it and a configured release branch does not (a merged change that was not taken there). Confidence is
    how many of the changed files agree. Nothing is said when the primary branch has no readable row, or no branch
    shows the change somewhere else."""
    if matrix is None:
        return []
    primary = matrix.primary
    row = {r.branch: r for r in matrix.rows if r.state != "unreadable"}
    kind = {b: matrix.kind_of(b) for b in row}  # by the files, so that a branch whose row has nothing to cite still counts as old
    if kind.get(primary) is None:
        return []
    per_file: dict[str, dict[str, str]] = {}
    for fs in matrix.files:
        if fs.state != "not_compared":
            per_file.setdefault(fs.branch, {})[fs.path] = fs.state
    out: list[Finding] = []
    others = [b for b in row if b != primary]

    def agreement(branches: Sequence[str], primary_state: str, other_state: str) -> tuple[int, int]:
        paths = sorted(per_file.get(primary, {}))
        agree = sum(1 for p in paths if per_file[primary][p] == primary_state and all(per_file.get(b, {}).get(p) == other_state for b in branches))
        return agree, len(paths)

    def confidence(agree: int, total: int) -> Confidence:
        if total and agree == total:
            return Confidence.HIGH
        return Confidence.MEDIUM if agree and agree * 2 >= total else Confidence.LOW

    if kind[primary] == "pre_change":
        holders = [b for b in others if kind[b] in ("present", "partial")]
        if holders:
            agree, total = agreement(holders, "pre_change", "present")
            names = ", ".join(holders)
            out.append(Finding(
                kind="branch-missing", title=clean(f"The change is on {names} and not on {primary}"), workflow="", risk=Risk.HIGH,
                confidence=confidence(agree, total),
                confidence_basis=clean(f"{agree} of {total} changed file(s) are unchanged on {primary} and carry the change on every one of {names}"),
                evidence=tuple(dict.fromkeys(e for b in (primary, *holders) for e in row[b].evidence)),
                identity=("branch-missing", repo, primary, "on-release-not-primary"),
            ))
    elif kind[primary] == "present":
        for b in others:
            if kind[b] == "pre_change":
                agree, total = agreement([b], "present", "pre_change")
                out.append(Finding(
                    kind="branch-missing", title=clean(f"The change is on {primary} and not on {b}"), workflow="", risk=Risk.HIGH,
                    confidence=confidence(agree, total),
                    confidence_basis=clean(f"{agree} of {total} changed file(s) carry the change on {primary} and are unchanged on {b}"),
                    evidence=tuple(dict.fromkeys((*row[primary].evidence, *row[b].evidence))),
                    identity=("branch-missing", repo, b, "on-primary-not-release"),
                ))
    return out


# ------------------------------------------------------------------------------ the definition of a changed symbol

DEFINITION_LINE = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:def|class|function\*?|const|let|var|interface|type|enum|abstract\s+class)\b")


def definition_line_number(lines: Sequence[str], start: int) -> int:
    """The 1-based line of the `def`, `class` or `function` of a declaration that begins at line `start` (a declaration's span
    begins at its decorators): the first line from `start` that looks like one, within a few lines; `start` itself if none does."""
    for number in range(start, min(start + 12, len(lines) + 1)):
        if DEFINITION_LINE.match(lines[number - 1]):
            return number
    return start


def origin_symbols(prop: Any) -> list[Any]:
    """The changed symbols (not `<module>`) of the changed repository's own pass, in a fixed order."""
    origin = next((p for p in prop.passes if p.depth == 0 and p.repo == prop.repo), None)
    if origin is None:
        return []
    return sorted((s for i in origin.analysis.impacts for s in i.symbols if not s.is_module), key=lambda s: (s.path, s.start, s.name))


def definition_lines(git: Any, remote: str, change: Any, prop: Any) -> dict[tuple[str, str, int], Evidence]:
    """The definition line of every changed symbol of the change, as Evidence: (path, side, start of the declaration) to the
    line (`def`, `class`, `function`) read at the head (a removed symbol: at the pre-change commit). A symbol whose file cannot
    be read or whose line cannot be cited is left out: the finding then cites its chain alone."""
    wanted: dict[str, dict[str, set[int]]] = {}
    names: dict[tuple[str, str, int], str] = {}  # the symbol a declaration is (the first, in the fixed order of `origin_symbols`)
    for s in origin_symbols(prop):
        sha = change.head_sha if s.side == "after" else change.pre_change_sha
        if sha:
            wanted.setdefault(sha, {}).setdefault(s.path, set()).add(s.start)
            names.setdefault((s.path, s.side, s.start), s.name)
    out: dict[tuple[str, str, int], Evidence] = {}
    for sha, files in sorted(wanted.items()):
        reads = git.files_at(remote, sorted(files), sha)
        side = "after" if sha == change.head_sha else "before"
        for path, starts in files.items():
            read = reads[path]
            if not read.is_found:
                continue
            lines = read.text.split("\n")
            for start in starts:
                if 1 <= start <= len(lines):
                    number = definition_line_number(lines, start)
                    text = lines[number - 1].rstrip("\r")
                    if text.strip():
                        out[(path, side, start)] = Evidence(prop.repo, path, number, sha, text, claim=f"def:{names[(path, side, start)]}")
    return out


def _definitions_for(area: Area, chain: tuple[Evidence, ...], prop: Any, definitions: Mapping[tuple[str, str, int], Evidence]) -> list[Evidence]:
    """The definition lines of the changed symbols behind an area: those whose file the chain starts from (the importer of the
    changed file, or the changed file itself), or for a contract change the symbols of the model fields the chain reads; with
    no way to tell, the first changed symbols of the change (at most three)."""
    if not definitions:
        return []
    symbols = origin_symbols(prop)
    origin = next(p for p in prop.passes if p.depth == 0 and p.repo == prop.repo)
    picked: list[Any] = []
    first = chain[0] if chain else None
    if first is not None and first.repo == prop.repo:
        for i in origin.analysis.impacts:
            if i.path == first.path or any(e.importer_path == first.path for e in i.importers) or any(c.path == first.path for c in i.callers):
                picked += [s for s in i.symbols if s.path == i.path and not s.is_module]
    else:
        members = set(chain)
        for r in prop.contract_reads:
            if any(e in members for e in r.evidence):
                for c in prop.contract_changes:
                    if (c.owner, c.name) == (r.owner, r.field):
                        picked += [s for s in symbols if s.name == c.symbol and s.path == c.path]
    chosen = picked or symbols
    out: list[Evidence] = []
    for s in chosen[:3] if not picked else chosen[:3]:
        e = definitions.get((s.path, s.side, s.start))
        if e is not None and e not in out:
            out.append(e)
    return out


# ------------------------------------------------------------------------------ all of them


def build(prop: Any, matrix: Any = None, definitions: Mapping[tuple[str, str, int], Evidence] | None = None) -> list[Finding]:
    """Every finding of a run, in a fixed order: the areas (one each), the exclusions, the contract changes per consumer, the
    releases, the http inputs, the branches. `prop` is the `Propagation`; `matrix` the branch matrix (or None); `definitions`
    (from `definition_lines`) the definition lines an area finding cites before its chain."""
    out: dict[str, Finding] = {}

    def add(f: Finding | None) -> None:
        if f is None:
            return
        if f.id in out:  # the same claim from two places: one finding, the evidence of both
            old = out[f.id]
            more = tuple(e for e in f.evidence if e not in old.evidence)
            if more:
                out[f.id] = Finding(old.kind, old.title, old.workflow, old.risk, old.confidence, old.confidence_basis, old.evidence + more, old.identity)
            return
        out[f.id] = f

    changed = [e for s in origin_symbols(prop) for e in s.evidence]  # the changed lines: no area finding cites one next to a definition
    undecidable = {(i.repo, c.path) for p in prop.passes if not p.candidate for i in p.analysis.impacts for c in i.callers if c.reason == "undecidable"}
    for r in prop.reached:
        backing = _area_backing(r.area, r, prop, undecidable)
        chain = backing.chain if backing.chain is not None else (r.area.chains[0] if r.area.chains else ())
        cited = [] if r.area.by_name else _definitions_for(r.area, chain, prop, definitions or {})
        add(direct(r.area, backing, cited, changed) if r.area.repo == prop.repo else cross_repo(r.area, prop.repo, backing, cited, changed))
    for p in prop.passes:
        if p.candidate:
            continue
        for impact in p.analysis.impacts:
            for ex in impact.exclusions:
                add(exclusion(p.repo, impact.path, ex))
    for change in prop.contract_changes:
        consumers = sorted({c.repo for c in prop.consumers if c.producer == change.repo} | {r.repo for r in prop.contract_reads if r.producer == change.repo})
        for kind in ("contract-tightened", "contract-relaxed"):
            if kind in change.kinds:
                for consumer in consumers:
                    add(contract_change(kind, change, consumer, prop.contract_reads, prop.consumers))
    for c in prop.consumers:
        add(release(c))
    for inp in prop.findings:
        add(from_input(inp))
    for f in branch_missing(matrix, prop.repo):
        add(f)
    return list(out.values())
