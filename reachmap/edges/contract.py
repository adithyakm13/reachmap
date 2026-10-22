"""The contract edge: a changed schema field, and the consumer code that reads it (context 7.4).

The producer's language analysis names what changed: `Class.field` symbols of the files under `contract.root`. This edge
says how each field changed (optional to required is `contract-tightened`, required to optional or a removed default is
`contract-relaxed`, removed, added, type changed, default changed; classified with `ast`, never by text), and then looks
in each consumer for the code that reads it:

- the consumer is searched on its primary branch, in files that import the producer package (`import_marker`): an
  attribute read `.field`, a keyword `field=`, a subscript `["field"]` and `getattr(x, "field")`, found with `ast`, so a
  comment or a string elsewhere never counts (a word in a comment is not a read);
- a read in a file that does not import the package is a candidate (by name), listed apart and not followed;
- a name defined more than `limits.distinct_max` times in the producer (by definitions, through the language adapter) is
  not distinctive: it links nothing and `not_distinctive` says so; a name whose definitions could not be counted is never
  taken as distinctive;
- each read is a `ContractRead` with the import line and the read line as evidence, and its line is handed to the
  consumer's own pass as the changed line.

A search that was cut or failed, a file that did not parse and a consumer that cannot be pinned make the edge
incomplete (recorded): a short list of reads is never "the" list then.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any

from reachmap.adapters.base import evidence_at, not_distinctive_detail, record_quoted
from reachmap.edges import Hop, Pin, Seed, edge_incomplete, incomplete_hop
from reachmap.edges.receivers import ModelRefs, construct_sites, defines_name, read_sites
from reachmap.incomplete import record
from reachmap.model import Evidence, Read

__all__ = [
    "ContractChange", "ContractEdgeAdapter", "ContractRead", "FieldShape", "ModelRefs", "classify", "defines_name", "field_reads",
    "field_shape", "read_sites",
]

TIGHTENED, RELAXED = "contract-tightened", "contract-relaxed"


@dataclass(frozen=True, slots=True)
class FieldShape:
    """A field of a model as declared: its annotation and default as source text, whether it has a default, whether
    its annotation accepts None. `kind` is `field`, `method` (a def) or `other`."""

    kind: str
    annotation: str | None
    default: str | None
    has_default: bool
    nullable: bool
    core: str | None = None  # the annotation without its None alternatives


@dataclass(frozen=True, slots=True)
class ContractChange:
    """How one member of a model changed: `kinds` lists what changed, the first being the main one. `distinct` is
    False for a name defined too often to link code by (nothing is matched by it); `definitions` is the count."""

    repo: str
    path: str
    symbol: str
    owner: str
    name: str
    change: str  # modified | added | removed (the symbol's own)
    kinds: tuple[str, ...]
    before: FieldShape | None
    after: FieldShape | None
    evidence: tuple[Evidence, ...]
    distinct: bool = True
    definitions: int | None = None
    counted: bool = True  # the definitions were counted completely (a name that was not is never distinctive)

    @property
    def kind(self) -> str:
        return self.kinds[0]

    def all_evidence(self) -> tuple[Evidence, ...]:
        return self.evidence


@dataclass(frozen=True, slots=True)
class ContractRead:
    """A line of consumer code that reads a changed field: `evidence` is the line that imports the producer package
    (absent for a candidate: the file does not import it) and the read line. `form` is how the field is named; `pin`
    and `adoption` are those of the consumer on the branch the read was found on, once its package edge has been read."""

    repo: str  # the consumer
    producer: str
    path: str
    line: int
    symbol: str  # the top-level declaration the read sits in
    owner: str
    field: str
    form: str
    sha: str
    branch: str
    evidence: tuple[Evidence, ...]
    candidate: bool = False
    pin: str | None = None  # what the consumer pins on that branch and whether it has the change (from its package edge)
    adoption: str | None = None
    basis: str = "name"  # `typed`: the receiver is provably the model; `name`: a distinctive name the consumer defines nowhere

    def all_evidence(self) -> tuple[Evidence, ...]:
        return self.evidence


# ------------------------------------------------------------------------------ how a field changed


def _is_none(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _nullable(node: ast.AST | None) -> bool:
    """Whether an annotation accepts None: `X | None`, `Optional[X]`, `Union[X, None]`, `None`, a quoted one, and
    `Annotated[X | None, ...]`."""
    if node is None:
        return False
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            return _nullable(ast.parse(node.value, mode="eval").body)
        except (SyntaxError, ValueError, RecursionError):
            return False
    if _is_none(node):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _nullable(node.left) or _nullable(node.right)
    if isinstance(node, ast.Subscript):
        base = node.value.attr if isinstance(node.value, ast.Attribute) else node.value.id if isinstance(node.value, ast.Name) else ""
        items = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
        if base == "Optional":
            return True
        if base == "Union":
            return any(_nullable(i) for i in items)
        if base == "Annotated" and items:
            return _nullable(items[0])
    return False


def _strip_none(node: ast.AST | None) -> str | None:
    """An annotation as source without its None alternatives (`str | None` and `Optional[str]` are `str`): whether a
    field may be None is `nullable`, not a change of its type."""
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            return _strip_none(ast.parse(node.value, mode="eval").body)
        except (SyntaxError, ValueError, RecursionError):
            return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        left, right = _strip_none(node.left), _strip_none(node.right)
        if _is_none(node.left):
            return right
        if _is_none(node.right):
            return left
        return f"{left} | {right}"
    if isinstance(node, ast.Subscript):
        base = node.value.attr if isinstance(node.value, ast.Attribute) else node.value.id if isinstance(node.value, ast.Name) else ""
        items = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
        if base == "Optional" and len(items) == 1:
            return _strip_none(items[0])
        if base == "Union":
            kept = [_strip_none(i) for i in items if not _is_none(i)]
            return " | ".join(k for k in kept if k)
        if base == "Annotated" and isinstance(node.slice, ast.Tuple) and items:
            return f"Annotated[{_strip_none(items[0])}, {', '.join(ast.unparse(i) for i in items[1:])}]"
    return ast.unparse(node)


def _callee(call: ast.Call) -> str:
    return call.func.attr if isinstance(call.func, ast.Attribute) else call.func.id if isinstance(call.func, ast.Name) else ""


def _default(value: ast.expr | None) -> tuple[bool, str | None]:
    """(has a default, its source): `= None` is a default, `Field(default=...)` and `Field(default_factory=...)` are, `Field(...)`
    and `Field()` are not."""
    if value is None:
        return False, None
    if isinstance(value, ast.Call) and _callee(value) == "Field":
        for kw in value.keywords:
            if kw.arg in ("default", "default_factory"):
                return True, ast.unparse(kw)
        if value.args and not (isinstance(value.args[0], ast.Constant) and value.args[0].value is Ellipsis):
            return True, ast.unparse(value.args[0])
        return False, None
    if isinstance(value, ast.Constant) and value.value is Ellipsis:
        return False, None
    return True, ast.unparse(value)


def _class_body(tree: ast.Module | None, path: list[str]) -> list[ast.stmt] | None:
    body: list[ast.stmt] | None = tree.body if tree is not None else None
    for name in path:
        found = next((s for s in body or () if isinstance(s, ast.ClassDef) and s.name == name), None)
        if found is None:
            return None
        body = found.body
    return body


def field_shape(tree: ast.Module | None, path: list[str], name: str) -> FieldShape | None:
    """The declaration of member `name` of the class at `path` (None when the class or the member is not there)."""
    for stmt in _class_body(tree, path) or ():
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.target.id == name:
            has, default = _default(stmt.value)
            return FieldShape("field", ast.unparse(stmt.annotation), default, has, _nullable(stmt.annotation), _strip_none(stmt.annotation))
        if isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in stmt.targets):
            has, default = _default(stmt.value)
            return FieldShape("field", None, default, has, _is_none(stmt.value), None)
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and stmt.name == name:
            return FieldShape("method", None, None, False, False)
        if isinstance(stmt, ast.ClassDef) and stmt.name == name:
            return FieldShape("other", None, None, False, False)
    return None


def classify(before: FieldShape | None, after: FieldShape | None) -> tuple[str, ...]:
    """What changed between two shapes of a member, the main kind first. Optional to required (a default, or the
    option of None, is gone) is `contract-tightened`; required to optional is `contract-relaxed`; both can hold for one
    field (then the annotation changed too)."""
    if before is None and after is None:
        return ("unclassified",)
    if before is None:
        return ("added",) if after is not None and (after.has_default or after.nullable or after.kind != "field") else ("added_required",)
    if after is None:
        return ("removed",)
    if before.kind != "field" or after.kind != "field":
        return ("member_changed",)
    kinds: list[str] = []
    tighter = (before.has_default and not after.has_default) or (before.nullable and not after.nullable)
    looser = (not before.has_default and after.has_default) or (not before.nullable and after.nullable)
    if tighter and not looser:
        kinds.append(TIGHTENED)
    elif looser and not tighter:
        kinds.append(RELAXED)
    elif tighter and looser:
        kinds += [TIGHTENED, RELAXED]
    if before.core != after.core:
        kinds.append("type_changed")
    if before.default != after.default and before.has_default and after.has_default:
        kinds.append("default_changed")
    return tuple(kinds) or ("modified",)


# ------------------------------------------------------------------------------ the reads in a consumer file


def field_reads(parsed: Any, name: str, *, as_class: bool = False) -> list[tuple[int, str]]:
    """The (line, form) of every use of `name` as a field in the code of a parsed Python file, whatever its receiver (see
    `read_sites` for the proven ones): `.name`, `name=` as a keyword, `["name"]` and `getattr(x, "name")`; for a class,
    every `name` identifier and the import of it. Comments and other strings never count (the tree has none). A file that
    did not parse has no tree: the caller treats it as unread, not as empty."""
    return sorted({(line, form) for line, form, _proven in read_sites(parsed, name, None, as_class=as_class)})


# ------------------------------------------------------------------------------ the adapter


def _under(path: str, root: str) -> bool:
    root = (root or "").strip("/")
    return not root or path == root or path.startswith(root + "/")


def _import_lines(parsed: Any) -> set[int]:
    """The lines of the import statements of a parsed Python file."""
    if parsed is None or parsed.tree is None:
        return set()
    return {n for node in ast.walk(parsed.tree) if isinstance(node, (ast.Import, ast.ImportFrom)) for n in range(node.lineno, (node.end_lineno or node.lineno) + 1)}


class ContractEdgeAdapter:
    kind = "contract"

    # ---- what changed

    def _symbols(self, run: Any, p: Any) -> list[tuple[Any, Any]]:
        cfg = run.repo(p.repo)
        root = (cfg.contract or {}).get("root", "")
        out = []
        for impact in p.analysis.impacts:
            if impact.role == "test" or not impact.path.endswith(".py") or not _under(impact.path, root):
                continue
            for s in impact.symbols:
                out.append((impact, s))
        return out

    def applies(self, run: Any, p: Any, edge: Any) -> bool:
        return p.repo == edge.from_repo and p.seed is None and p.depth == 0 and bool(self._symbols(run, p))

    @staticmethod
    def _only_imports(lang: Any, p: Any, s: Any) -> bool:
        """Whether every changed line of a module-level change is part of an import statement (before and after)."""
        pre = p.change.pre_change_sha
        after = lang.parsed(p.change.head_sha, s.path) if s.added_lines else None
        before_path = next((e.path for e in s.evidence if pre is not None and e.ref == pre), s.path)
        before = lang.parsed(pre, before_path) if s.removed_lines and pre is not None else None
        if (s.added_lines and after is None) or (s.removed_lines and before is None) or (s.removed_lines and pre is None):
            return False
        return set(s.added_lines) <= _import_lines(after) and set(s.removed_lines) <= _import_lines(before)

    def changes(self, run: Any, p: Any) -> list[ContractChange]:
        """The members of the producer's models that the change touches, classified (once per run)."""
        def make() -> list[ContractChange]:
            lang = run.language(p.repo)
            out: list[ContractChange] = []
            pre = p.change.pre_change_sha
            for impact, s in self._symbols(run, p):
                if s.is_module:
                    if self._only_imports(lang, p, s):
                        continue  # an import added to or removed from a models file changes no field
                    record_quoted(run.target, "contract_change_unscoped", s.path,
                                  "a statement outside every class changed, so which consumer code it affects cannot be told from the change", s.path, once=True)
                    run.problem(f"{s.path}: a change outside every model cannot be followed into the consumers")
                    continue
                parts = s.name.split(".")
                owner, name = ".".join(parts[:-1]), parts[-1]
                before = after = None
                if s.change != "removed":
                    pa = lang.parsed(p.change.head_sha, s.path)
                    after = field_shape(pa.tree, parts[:-1], name) if pa is not None and owner else None
                if pre is not None and s.change != "added":
                    before_path = next((e.path for e in s.evidence if e.ref == pre), s.path)
                    pb = lang.parsed(pre, before_path)
                    before = field_shape(pb.tree, parts[:-1], name) if pb is not None and owner else None
                leaf = name
                kinds = classify(before, after) if owner else (("removed",) if s.change == "removed" else ("added",) if s.change == "added" else ("class_changed",))
                sha = p.change.head_sha if s.side == "after" else pre
                dunder = leaf.startswith("__") and leaf.endswith("__")  # never a name to link code by
                found = lang.definitions(leaf, sha) if sha is not None and not dunder else None
                limit = run.limits.distinct_max
                distinct = found is not None and found.distinctive(limit)
                counted = dunder or (found is not None and found.state == "found" and found.complete)
                if found is not None and found.state == "found" and found.count is not None and found.count > limit:
                    record(run.target, "not_distinctive", leaf, detail=not_distinctive_detail(found, sha, limit))
                out.append(ContractChange(
                    p.repo, s.path, s.name, owner, leaf, s.change, kinds, before, after, tuple(s.evidence), distinct,
                    found.count if found is not None else None, counted,
                ))
            run.contract_changes.extend(out)
            return out
        return run.memo(("contract-changes", p.repo, p.change.head_sha), make)

    # ---- where it is read

    @staticmethod
    def _pins(run: Any, consumer: str, primary: Any) -> list[Pin]:
        """The revisions of the consumer to read: its primary branch, and for a released change every other branch the
        package edge found adopted (the code that meets the new contract is on them too)."""
        pins = [Pin(consumer, primary.ref, primary.sha, "primary")]
        for c in sorted(run.consumers, key=lambda c: c.branch):
            if c.repo == consumer and c.adoption == "adopted" and all(c.sha != x.sha for x in pins):
                pins.append(Pin(consumer, c.branch, c.sha, "branch"))
        return pins

    def follow(self, run: Any, p: Any, edge: Any) -> list[Hop]:
        producer, consumer = edge.from_repo, edge.to_repo
        changes = self.changes(run, p)
        primary = run.primary(consumer)
        if primary is None:
            return [incomplete_hop(run.target, edge, producer, p.depth, [f"the primary branch of {consumer} could not be read: its reads of {producer} were not found"])]
        marker = self._marker(run, edge)
        lang = run.language(consumer)
        reasons: list[str] = []
        wanted = [c for c in changes if c.kinds != ("added",)]  # an optional new field has no reader yet
        for c in wanted:
            if not c.counted:  # never silently dropped: the name is followed by its typed reads only, and the edge says it is not whole
                reasons.append(f"the definitions of {c.name} could not be counted completely, so it is not taken as distinctive: only reads of a receiver that is provably the model were followed")
        if any(c.kinds[0] == "added_required" for c in wanted):
            record(run.target, "contract_construction_unseen", consumer,
                   detail=f"a required field was added: the places that build the model were looked for in the files that import the package, and a factory or a call through another name is not seen")
            run.problem(f"{consumer}: the constructions of the model with the new required field were looked for by name only")
        groups: dict[tuple[str, str, str], list[tuple[ContractChange, ContractRead]]] = {}
        pin_of: dict[str, Pin] = {}
        seen: set[tuple[str, str, str]] = set()
        for pin in self._pins(run, consumer, primary):
            pin_of[pin.ref] = pin
            for c in sorted(wanted, key=lambda c: (c.path, c.symbol)):
                found, why = self._reads(run, consumer, producer, lang, pin, marker, c)
                reasons += [w for w in why if w not in reasons]
                for read in found:
                    key = (read.path, read.evidence[-1].detail, read.field, read.candidate)  # the same line of text on another branch is the same read
                    if key in seen:
                        continue
                    seen.add(key)
                    if read.candidate:
                        run.contract_candidates.append(read)
                        continue
                    run.contract_reads.append(read)
                    groups.setdefault((pin.ref, read.path, read.symbol), []).append((c, read))
        hops: list[Hop] = []
        for (branch, path, symbol), items in sorted(groups.items()):
            items.sort(key=lambda x: x[1].line)
            c0, first = items[0]
            lines = tuple(sorted({r.line for _c, r in items}))
            evidence = tuple(dict.fromkeys(e for _c, r in items for e in r.evidence[-1:]))
            pin = pin_of[branch]
            seed = Seed(consumer, path, lines, pin.sha, evidence)
            hops.append(Hop(
                "contract", producer, consumer, (producer, consumer), "forward", p.depth + 1, (pin,), c0.evidence, first.evidence,
                (*p.prefix, *first.evidence), (seed,), detail=f"{c0.symbol} is read as {first.form} in {path} on {branch}", complete=not reasons, reasons=tuple(reasons),
            ))
        for why in reasons:
            edge_incomplete(run.target, edge, why)
        if reasons and not hops:
            hops.append(incomplete_hop(run.target, edge, producer, p.depth, reasons))
        return hops

    @staticmethod
    def _marker(run: Any, edge: Any) -> str | None:
        """The import name of the producer package in the consumer: the `package` edge of the same pair says it, else
        the producer's `contract.package`."""
        for e in run.workspace.edges:
            if e.kind == "package" and (e.from_repo, e.to_repo) == (edge.from_repo, edge.to_repo) and e.import_marker:
                return e.import_marker
        return (run.repo(edge.from_repo).contract or {}).get("package")

    def _reads(self, run: Any, consumer: str, producer: str, lang: Any, pin: Any, marker: str | None, c: ContractChange) -> tuple[list[ContractRead], list[str]]:
        """The reads of one changed member in one revision of the consumer. A read is `typed` when its receiver is provably the
        model (always followed); an unproven one is `name` based and followed only when the name is distinctive and the consumer
        defines it nowhere; otherwise it is a candidate (a distinctive name) or dropped (a name the producer defines too
        often: the `not_distinctive` record says it links nothing). Constructions are the reads of a new required field."""
        why: list[str] = []
        construct = c.kinds[0] == "added_required"
        needle = c.owner.split(".")[-1] if construct else c.name
        res = lang.search(pin.sha, needle)
        if res is None:
            return [], [f"{consumer} could not be checked out at {pin.sha[:12]}: the reads of {c.name} were not searched"]
        if res.state == Read.UNREADABLE:
            return [], [f"the search for {c.name} in {consumer} failed: {res.detail}"]
        if res.truncated_by == "examine_cap":
            why.append(f"the search for {c.name} in {consumer} was cut after {res.examined} of {res.total} files")
        for link, kind in res.not_searched:
            if link.endswith(".py"):
                why.append(f"{link} is a {kind}: git grep does not search it, so a read of {c.name} in it may be missed")
        paths = sorted({m.path for m in res.partial_matches if m.path.endswith(".py") and not lang.is_test_path(m.path)})
        as_class = not c.owner
        refs = ModelRefs(c.owner.split(".")[-1] if c.owner else c.name, marker)
        parsed_files: dict[str, Any] = {}
        for path in paths:
            parsed = lang.parsed(pin.sha, path)
            if parsed is None:
                why.append(f"{path} could not be read: a read of {c.name} in it was not looked for")
            elif parsed.problems:
                why.append(f"{path} did not parse ({parsed.problems[0]}): a read of {c.name} in it may be missed")
            else:
                parsed_files[path] = parsed
        defined = 0 if construct else sum(defines_name(parsed, c.name) for parsed in parsed_files.values())
        out: list[ContractRead] = []
        for path, parsed in parsed_files.items():
            if construct:
                sites = [(line, "construct", True) for line in construct_sites(parsed, refs)]
            else:
                sites = read_sites(parsed, c.name, refs, as_class=as_class)
            if not sites:
                continue
            imp = self._import_line(lang, parsed, path, pin.sha, marker, c)
            for line, form, proven in sites:
                if proven or as_class:
                    basis, candidate = "typed", imp is None
                elif form != "subscript" and c.distinct and defined == 0:
                    basis, candidate = "name", imp is None
                elif c.distinct:
                    basis, candidate = "name", True  # not backed: shown apart, never followed
                else:
                    continue  # a name too many models define, on a receiver nothing proves: it links nothing
                ev = evidence_at(consumer, path, pin.sha, parsed, line)
                if ev is None:
                    continue
                out.append(ContractRead(
                    consumer, producer, path, line, parsed.enclosing(line), c.owner, c.name, form, pin.sha, pin.ref,
                    (imp, ev) if imp is not None and imp != ev else (ev,), candidate=candidate, basis=basis,
                ))
        return sorted(out, key=lambda r: (r.path, r.line, r.form)), why

    @staticmethod
    def _import_line(lang: Any, parsed: Any, path: str, sha: str, marker: str | None, c: ContractChange) -> Evidence | None:
        """The line that imports the producer package in the file, preferring the import of the class that holds the
        changed field (`from acme_contracts.models.invoice import Invoice` for `Invoice.settlement_reference`); None when
        the file does not import the package."""
        if not marker:
            return None
        owner = c.owner.split(".")[0] if c.owner else c.name
        best: tuple[int, int, Any] | None = None
        for rec in parsed.imports:
            if rec.level or rec.kind == "dynamic":
                continue
            if not (rec.module == marker or rec.module.startswith(marker + ".")):
                continue
            named = any(n.name == owner or n.asname == owner for n in rec.names)
            rank = (0 if named else 1, rec.line)
            if best is None or rank < best[:2]:
                best = (rank[0], rank[1], rec)
        if best is None:
            return None
        return evidence_at(lang.repo, path, sha, parsed, best[2].line)
