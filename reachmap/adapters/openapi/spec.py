"""The OpenAPI document as the analyser needs it: operations with the line of their method key, the base paths of their
servers, the schemas each operation reaches through `$ref` (with cycles ended), and a signature that a spec diff compares.

Only JSON is read (`jsonpos`); a document that is not OpenAPI 3 or Swagger 2, or not valid JSON, is a `SpecError` and never
an empty spec. What is read but not understood is kept as a stated gap in `Spec.problems` (a path that does not start with
`/`, a server with no URL, a `$ref` that points outside the document), and the operation that depends on it is marked
`known=False`: a diff treats an operation whose signature is not known as changed (undecidable means affected).

Signature of an operation. Four parts, each a digest of a canonical form, and the signature of all of them:

- `parameters`: the path-level and operation-level parameters, the operation's overriding the path's by (in, name);
- `request`: the request body (OpenAPI 3); a Swagger 2 body is one of the parameters;
- `responses`: every response by status code;
- `schemas`: every object the operation reaches through `$ref`, each with a digest of its own canonical form.

A reference to a parameter, a request body or a response is followed inline (so a change to that component shows in its part,
and in `schemas`); a reference inside a schema is kept as the name it points at, and what it points at is in `schemas`. The
closure of references is found by a breadth first search with a visited set, so a schema that refers to itself, two schemas that
refer to each other or a graph in which every level refers to the next twice cost one visit each. Documentation (`description`,
`summary`, `title`, `example`, `examples`, `externalDocs`) is left out of every canonical form, except where the word is a
name (a property called `description` is a property).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable
from urllib.parse import unquote

from reachmap.adapters import normalize_path
from reachmap.adapters.openapi.jsonpos import JsonError, Node, parse

__all__ = ["Base", "Changed", "Operation", "Spec", "SpecDiff", "SpecError", "SpecRead", "diff", "read", "read_file"]

METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
DOC_KEYS = frozenset({"description", "summary", "title", "example", "examples", "externalDocs"})
NAME_MAPS = frozenset({"properties", "patternProperties", "responses", "content", "headers", "links", "callbacks", "variables", "encoding",
                       "definitions", "$defs", "dependentSchemas", "mapping", "scopes"})
SET_KEYS = frozenset({"required", "enum", "type"})
PARTS = ("operation_id", "parameters", "request", "responses", "schemas")
_AUTHORITY = re.compile(r"^(?:[A-Za-z][A-Za-z0-9+.\-]*|\{[^}/]*\})?://[^/]*")
_VARIABLE = re.compile(r"\{([^{}]*)\}")


class SpecError(ValueError):
    """The text is not an OpenAPI document this adapter reads."""


@dataclass(frozen=True, slots=True)
class Base:
    """The base path an operation is served under: where it came from (`line` of the server URL, 0 when there is no server) and
    whether a part of it could not be evaluated (`partial`; the unknown part is written `<unknown:NAME>` in `path`)."""

    path: str
    line: int
    partial: bool = False


@dataclass(frozen=True, slots=True)
class Operation:
    """One operation: `line` is the line of its method key (`"get": {`), `path_line` the line of its path key, `start` and `end` the
    lines of its object. `bases` are the base paths of its servers; `parts` and `signature` are described in the module docstring;
    `refs` is every `$ref` it reaches (sorted), `unresolved` those that point nowhere this adapter can read, `shared` the
    lines of the path item's own `parameters` and `servers` (a change there is a change to every operation of the path)."""

    method: str
    template: str
    operation_id: str | None
    line: int
    path_line: int
    start: int
    end: int
    bases: tuple[Base, ...]
    parts: dict[str, str]
    signature: str
    refs: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()
    shared: tuple[tuple[int, int], ...] = ()
    tags: tuple[str, ...] = ()

    @property
    def known(self) -> bool:
        """False when part of what the operation says could not be read (a `$ref` outside the document)."""
        return not self.unresolved

    def routes(self) -> list[tuple[str, Base]]:
        """The full path of the operation under each of its servers, with the base it came from; one route for one full path."""
        out: dict[str, Base] = {}
        for base in self.bases:
            out.setdefault(normalize_path(base.path + self.template), base)
        return list(out.items())


@dataclass(frozen=True, slots=True)
class Changed:
    """An operation present in both versions whose signature differs, or is not known. `why` names the parts that differ
    (`unresolved` first when a signature is not known)."""

    method: str
    path: str
    before: Operation
    after: Operation
    why: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SpecDiff:
    removed: list[tuple[Operation, str]] = field(default_factory=list)
    added: list[tuple[Operation, str]] = field(default_factory=list)
    changed: list[Changed] = field(default_factory=list)


class Spec:
    """A spec read at one commit: `operations`, the stated gaps in `problems`, and `unread`: the path templates (and methods) that the
    reader could not turn into operations, which a diff must never call removed or added."""

    def __init__(self, path: str, text: str, root: Node, version: str) -> None:
        self.path = path
        self.lines = text.split("\n")
        self.root = root
        self.version = version
        self.operations: tuple[Operation, ...] = ()
        self.problems: tuple[str, ...] = ()  # everything that was read but not followed
        self.ref_problems: tuple[str, ...] = ()  # of those, the `$ref`s that point outside the document or at nothing
        self.unread: tuple[tuple[str, str | None, str], ...] = ()  # (path template as written, METHOD or None for the whole path item, why)

    def is_unread(self, template: str, method: str) -> bool:
        """Whether the operation `method` of `template` was not read (its whole path item, or that method, could not be)."""
        return any(t == template and m in (None, method.upper()) for t, m, _why in self.unread)

    def line_text(self, n: int) -> str:
        """The text of line `n` (1-based) as git has it, without a trailing carriage return."""
        return self.lines[n - 1].rstrip("\r") if 1 <= n <= len(self.lines) else ""

    def locate(self, ref: str) -> tuple[Node, int] | None:
        """The node a local reference points at and the line of the key that holds it (the line of the node for an array item and
        for the whole document); None for a reference outside the document or one that points at nothing."""
        if not ref.startswith("#"):
            return None
        fragment = unquote(ref[1:])
        if fragment == "":
            return self.root, 1
        if not fragment.startswith("/"):
            return None
        node, line = self.root, 1
        for part in fragment[1:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if node.kind == "object":
                member = node.member(part)
                if member is None:
                    return None
                node, line = member.node, member.key_line
            elif node.kind == "array" and part.isdigit() and int(part) < len(node.items or ()):
                node = (node.items or [])[int(part)]
                line = node.line
            else:
                return None
        return node, line

    def span_of(self, ref: str) -> tuple[int, int] | None:
        """The first and last line of what a local reference points at (from its key), or None."""
        found = self.locate(ref)
        return (found[1], found[0].end_line) if found is not None else None


# ------------------------------------------------------------------------------ reading


def read(text: str, path: str) -> Spec:
    """Read a spec. Raises `SpecError` for text that is not valid JSON or not an OpenAPI 3 or Swagger 2 document."""
    try:
        root = parse(text)
    except JsonError as exc:
        raise SpecError(f"{path} is not valid JSON: {exc}") from None
    if root.kind != "object":
        raise SpecError(f"{path} is not an OpenAPI document: the top level is not an object")
    version = _version(root)
    if version is None:
        raise SpecError(f"{path} is not an OpenAPI 3 or Swagger 2 document: no `openapi` (3.x) or `swagger` (2.0) member")
    spec = Spec(path, text, root, version)
    _Builder(spec).build()
    return spec


def _version(root: Node) -> str | None:
    for key, ok in (("openapi", lambda v: v.startswith("3.")), ("swagger", lambda v: v == "2.0")):
        node = root.get(key)
        if node is not None and node.kind == "string" and ok(node.value):
            return node.value
    return None


def canon(node: Node, names_map: bool = False) -> Any:
    """The plain Python form a digest is made of: documentation left out, references kept as written, the lists that are sets
    (`required`, `enum`, `type`) sorted, a whole number written `1.0` the number `1`."""
    if node.kind == "object":
        out: dict[str, Any] = {}
        for m in node.members or ():
            if not names_map and m.key in DOC_KEYS:
                continue
            value = canon(m.node, not names_map and m.key in NAME_MAPS)
            if not names_map and m.key in SET_KEYS and isinstance(value, list) and all(not isinstance(v, (dict, list)) for v in value):
                value = sorted(value, key=lambda v: json.dumps(v, sort_keys=True))
            out[m.key] = value
        return out
    if node.kind == "array":
        return [canon(n) for n in node.items or ()]
    if node.kind == "number" and isinstance(node.value, float) and node.value.is_integer():
        return int(node.value)
    return node.value


def param_form(form: Any) -> Any:
    """A parameter in its canonical form (an object with `in` and `name`): an absent `required` is false, and a path parameter is always required."""
    if isinstance(form, dict) and isinstance(form.get("in"), str) and isinstance(form.get("name"), str):
        form = {**form, "required": form.get("required") is True or form["in"] == "path"}
    return form


def scan_refs(node: Node, names_map: bool = False) -> list[str]:
    """Every `$ref` string under a node (a property called `$ref` is a name, not a reference)."""
    out: list[str] = []
    if node.kind == "object":
        for m in node.members or ():
            if m.key == "$ref" and not names_map and m.node.kind == "string":
                out.append(m.node.value)
            else:
                out += scan_refs(m.node, not names_map and m.key in NAME_MAPS)
    elif node.kind == "array":
        for n in node.items or ():
            out += scan_refs(n)
    return out


def _shared(item: Node) -> tuple[tuple[int, int], ...]:
    """The lines of the path item's own `parameters` and `servers`: they belong to every operation of the path."""
    return tuple((m.key_line, m.node.end_line) for key in ("parameters", "servers") if (m := item.member(key)) is not None)


def _tags(node: Node) -> tuple[str, ...]:
    tags = node.get("tags")
    return tuple(n.value for n in (tags.items if tags is not None and tags.kind == "array" else ()) or () if n.kind == "string")


def digest(value: Any) -> str:
    return hashlib.sha1(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8", "backslashreplace")).hexdigest()[:16]


class _Builder:
    def __init__(self, spec: Spec) -> None:
        self.spec = spec
        self.problems: list[str] = []
        self.ref_problems: list[str] = []
        self.unread: list[tuple[str, str | None, str]] = []
        self._children: dict[str, tuple[str, ...]] = {}
        self._own: dict[str, str] = {}
        self._reported: set[str] = set()

    def problem(self, why: str) -> None:
        if why not in self.problems:
            self.problems.append(why)

    # ---- references

    def children(self, ref: str) -> tuple[str, ...] | None:
        """The references inside what `ref` points at; None when it points at nothing readable."""
        if ref not in self._children:
            found = self.spec.locate(ref)
            self._children[ref] = None if found is None else tuple(scan_refs(found[0]))  # type: ignore[assignment]
        return self._children[ref]

    def own(self, ref: str) -> str:
        if ref not in self._own:
            found = self.spec.locate(ref)
            self._own[ref] = "unresolved" if found is None else digest(param_form(canon(found[0])))
        return self._own[ref]

    def closure(self, roots: Iterable[str]) -> tuple[list[str], list[str]]:
        """Every reference reachable from `roots` (sorted) and those that point nowhere (sorted): a breadth first search with a
        visited set, so a cycle ends."""
        seen: set[str] = set()
        queue = list(roots)
        unresolved: set[str] = set()
        while queue:
            ref = queue.pop()
            if ref in seen:
                continue
            seen.add(ref)
            kids = self.children(ref)
            if kids is None:
                unresolved.add(ref)
                continue
            queue.extend(k for k in kids if k not in seen)
        return sorted(seen), sorted(unresolved)

    def inline(self, node: Node, unresolved: set[str]) -> Node | None:
        """A parameter, a request body, a response or a path item: followed through `$ref` until it is the thing itself. A reference
        that points nowhere (or a loop of references) is recorded in `unresolved` and the answer is None."""
        seen: list[str] = []
        while node.kind == "object":
            ref = node.get("$ref")
            if ref is None or ref.kind != "string":
                return node
            if ref.value in seen:
                unresolved.add(ref.value)
                return None
            seen.append(ref.value)
            found = self.spec.locate(ref.value)
            if found is None:
                unresolved.add(ref.value)
                return None
            node = found[0]
        return node

    # ---- servers

    def bases_of(self, owner: Node | None, root: Node) -> list[Base] | None:
        if owner is None:
            return None
        member = owner.member("servers")
        if member is None:
            return None
        node = member.node
        if node.kind != "array":
            self.problem(f"`servers` at line {member.key_line} is not a list")
            return None
        out: list[Base] = []
        for server in node.items or ():
            out.append(self.base_of(server))
        return out

    def base_of(self, server: Node) -> Base:
        url = server.get("url") if server.kind == "object" else None
        if url is None or url.kind != "string":
            self.problem(f"a server at line {server.line} has no `url`: its base path is not known")
            return Base("/<unknown:server-url>", server.line, True)
        line = server.member("url").key_line  # type: ignore[union-attr]
        variables = server.get("variables")
        defaults: dict[str, str] = {}
        if variables is not None and variables.kind == "object":
            for m in variables.members or ():
                d = m.node.get("default") if m.node.kind == "object" else None
                if d is not None and d.kind in ("string", "number", "bool"):
                    defaults[m.key] = str(d.value).lower() if d.kind == "bool" else str(d.value)
        text = _AUTHORITY.sub("", url.value, count=1) if _AUTHORITY.match(url.value) else url.value
        if text and not text.startswith("/"):
            self.problem(f"the server URL at line {line} is relative: its base path is not known")
            return Base("/<unknown:relative-server-url>", line, True)
        partial = False

        def fill(m: re.Match[str]) -> str:
            nonlocal partial
            if m.group(1) in defaults:
                return defaults[m.group(1)]
            partial = True
            return f"<unknown:{m.group(1)}>"

        path = normalize_path(_VARIABLE.sub(fill, text.split("?", 1)[0].split("#", 1)[0]) or "/")
        return Base("" if path == "/" else path, line, partial)

    # ---- the document

    def build(self) -> None:
        spec, root = self.spec, self.spec.root
        paths = root.get("paths")
        ops: list[Operation] = []
        if paths is not None and paths.kind != "object":
            raise SpecError(f"{spec.path}: `paths` is not an object")
        root_bases = self.root_bases(root)
        for pm in (paths.members if paths is not None else ()) or ():
            if pm.key.startswith("x-"):
                continue
            if not pm.key.startswith("/"):
                self.problem(f"the path {pm.key!r} at line {pm.key_line} does not start with /: it is not read as a route")
                self.unread.append((pm.key, None, "the path does not start with /"))
                continue
            unresolved: set[str] = set()
            item = self.inline(pm.node, unresolved)
            if item is None or item.kind != "object":
                self.problem(f"the path item {pm.key!r} at line {pm.key_line} cannot be resolved: its operations were not read")
                self.unread.append((pm.key, None, "the path item is a reference that cannot be resolved here"))
                continue
            path_bases = self.bases_of(item, root)
            for method in METHODS:
                m = item.member(method)
                if m is None:
                    continue
                if m.node.kind != "object":
                    self.problem(f"{method} of {pm.key!r} at line {m.key_line} is not an object: it is not read as an operation")
                    self.unread.append((pm.key, method.upper(), "the method is not an object"))
                    continue
                ops.append(self.operation(method, pm.key, pm.key_line, m.key_line, m.node, item, path_bases, root_bases, root))
        seen: dict[tuple[str, str], Operation] = {}
        for op in ops:
            for full, _base in op.routes():
                first = seen.setdefault((op.method, full), op)
                if first is not op:
                    self.problem(f"two operations have the method and path {op.method} {full} ({first.template} and {op.template}): the first is read")
        spec.operations = tuple(ops)
        spec.problems = tuple(self.problems)
        spec.ref_problems = tuple(self.ref_problems)
        spec.unread = tuple(self.unread)

    def root_bases(self, root: Node) -> list[Base]:
        if root.get("swagger") is not None:
            bp = root.member("basePath")
            if bp is None or bp.node.kind != "string":
                return [Base("", 0)]
            path = normalize_path(bp.node.value or "/")
            return [Base("" if path == "/" else path, bp.key_line)]
        found = self.bases_of(root, root)
        return found if found else [Base("", 0)]

    def operation(self, method: str, template: str, path_line: int, line: int, node: Node, item: Node, path_bases: list[Base] | None,
                  root_bases: list[Base], root: Node) -> Operation:
        bases = self.bases_of(node, root) or path_bases or root_bases
        unresolved: set[str] = set()
        # ---- parameters: the path item's, then the operation's, which replace those with the same (in, name)
        raw: list[Node] = []
        params: dict[tuple[str, str], Any] = {}
        for owner in (item, node):
            plist = owner.get("parameters")
            for n in (plist.items if plist is not None and plist.kind == "array" else ()) or ():
                raw.append(n)
                p = self.inline(n, unresolved)
                if p is None or p.kind != "object":
                    continue
                name, where = p.get("name"), p.get("in")
                key = (where.value if where is not None and where.kind == "string" else "", name.value if name is not None and name.kind == "string" else f"#{p.line}")
                params[key] = param_form(canon(p))
        request = node.get("requestBody")
        request_form: Any = None
        if request is not None:
            raw.append(request)
            r = self.inline(request, unresolved)
            request_form = canon(r) if r is not None else None
        responses = node.get("responses")
        responses_form: dict[str, Any] = {}
        if responses is not None and responses.kind == "object":
            raw.append(responses)
            for m in responses.members or ():
                r = self.inline(m.node, unresolved) if m.node.kind == "object" else None
                responses_form[m.key] = canon(r) if r is not None else None
        # ---- the closure of references the operation reaches
        roots = [ref for n in raw for ref in scan_refs(n)]
        refs, gone = self.closure(roots)
        unresolved |= set(gone)
        for ref in sorted(unresolved):
            self.report_unresolved(ref, f"{method.upper()} {template}")
        oid = node.get("operationId")
        parts = {
            "operation_id": oid.value if oid is not None and oid.kind == "string" else "",
            "parameters": digest([[list(k), v] for k, v in sorted(params.items())]),
            "request": digest(request_form),
            "responses": digest(responses_form),
            "schemas": digest([[ref, self.own(ref)] for ref in refs]),
        }
        return Operation(
            method=method.upper(), template=template, operation_id=parts["operation_id"] or None, line=line, path_line=path_line,
            start=node.line, end=node.end_line, bases=tuple(bases), parts=parts, signature=digest(parts), refs=tuple(refs),
            unresolved=tuple(sorted(unresolved)), shared=_shared(item), tags=_tags(node),
        )

    def report_unresolved(self, ref: str, where: str) -> None:
        if ref in self._reported:
            return
        self._reported.add(ref)
        why = f"the $ref {ref!r} of {where} cannot be resolved here (only references inside the document are followed): what the operation says is not fully known"
        self.ref_problems.append(why)
        self.problem(why)


# ------------------------------------------------------------------------------ a spec file at a commit


@dataclass(frozen=True, slots=True)
class SpecRead:
    """The answer of reading the configured spec at a commit, the three ways every read answers and what is not read: `state` is
    `found` (`spec` is set), `absent` (the tree says the path is not there), `unreadable` (the read failed: the git layer
    recorded it), `invalid` (not JSON, not OpenAPI), `yaml` (not read). `why` says which, without quoting the file."""

    state: str
    spec: Spec | None = None
    why: str = ""


def read_file(language: Any, path: str, sha: str) -> SpecRead:
    """Read the spec at `path` at `sha` through a language adapter (`file_text`), keeping the three-way answer."""
    res = language.file_text(sha, path)
    if res.is_absent:
        return SpecRead("absent", why=f"the spec {path} is not in the tree at {sha[:12]}")
    if res.is_unreadable:
        return SpecRead("unreadable", why=f"the spec {path} could not be read at {sha[:12]}: {res.detail}")
    if path.lower().endswith((".yaml", ".yml")):
        return SpecRead("yaml", why=f"the spec {path} is YAML, which this adapter does not read")
    try:
        return SpecRead("found", read(res.text, path))
    except SpecError as exc:
        return SpecRead("invalid", why=str(exc))


# ------------------------------------------------------------------------------ diff


def _routes(spec: Spec) -> dict[tuple[str, str], Operation]:
    out: dict[tuple[str, str], Operation] = {}
    for op in spec.operations:
        for path, _base in op.routes():
            out.setdefault((op.method, path), op)
    return out


def diff(before: Spec, after: Spec) -> SpecDiff:
    """What changed between two versions of a spec, by method and full path: operations removed, added, and present in both with
    another signature (or with one that could not be read, which is a change: undecidable means affected). Sorted."""
    old, new = _routes(before), _routes(after)
    result = SpecDiff()
    for key in sorted(old.keys() - new.keys()):
        result.removed.append((old[key], key[1]))
    for key in sorted(new.keys() - old.keys()):
        result.added.append((new[key], key[1]))
    for key in sorted(old.keys() & new.keys()):
        a, b = old[key], new[key]
        parts = tuple(k for k in PARTS if a.parts.get(k) != b.parts.get(k))
        if not a.known or not b.known:
            result.changed.append(Changed(key[0], key[1], a, b, ("unresolved", *parts)))
        elif a.signature != b.signature:
            result.changed.append(Changed(key[0], key[1], a, b, parts))
    return result
