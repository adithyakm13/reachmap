"""The OpenAPI HTTP client adapter (JavaScript and TypeScript): the calls a repository makes through a client generated from a spec.

Configured as `http_client = { kind = "openapi", spec = "api/openapi.json", client = "src/api/generated" }` on a repository that
keeps the spec its client was generated from (`spec`) and says where the generated code is (`client`: a file, a folder, a glob, or
a list of them). Every operation of the spec with an operationId is a method name of the generated client (`getInvoice`), and a
call of that name is a call of the operation: its method and URL (the server base path and the path of the operation, with its
parameters) are the spec's, not read from the call.

A call is a use of the name in a file that imports the generated code (through any specifier the JavaScript adapter resolves, or
through a barrel that re-exports it, followed to a fixed point). Two kinds. A resolved one: a named import used (`getInvoice(`, also
aliased) or a member of a namespace import (`api.getInvoice(` with `import * as api`): the import says which function it is, whatever
its name. A match by name alone: the name as a member of anything else in that file (`new InvoicesApi().getInvoice(`, a default import's
member). The generated code itself and test files are not callers; a name in a comment or a string is not code.

Distinctiveness (context 5, shape B) applies to the match by name alone, not to a resolved import. A name links code by itself only
when it is distinctive: not made only of generic words (`get`, `list`, `getAll`, `health`: see `names.is_generic`) and not defined more
often than `limits.distinct_max` in non-test code (the JavaScript adapter's `definitions`: definitions, never references). A name that
fails is dropped from member matching and recorded (`not_distinctive`, not blocking); an import of it from the generated client is
still a call. A name two operations carry is no name at all. A count that could not be made is never distinctive and makes the list
incomplete.

The list says when it could be short (`ClientCalls.complete`, `reasons`): a spec that is absent, unreadable, YAML or not OpenAPI;
generated files that are not in the tree; an operation with no operationId (calls to it cannot be found); a file that does not lex;
a dynamic import of the generated code with no binding; an importer search that was cut; a re-export under another name. The http
edge turns every reason into a blocking `edge_incomplete`. Limits are in docs/briefs/M16-openapi.md.
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from reachmap import config
from reachmap.adapters.base import evidence_at, not_distinctive_detail, record_quoted
from reachmap.adapters.http_client import ClientCall, ClientCalls, ClientUrl, url_from_parts
from reachmap.adapters.openapi import names
from reachmap.adapters.openapi.options import as_list
from reachmap.adapters.openapi.spec import Operation, Spec, read_file
from reachmap.adapters.react_router import glob_regex
from reachmap.model import Evidence

__all__ = ["OpenApiClient"]

_GLOB = re.compile(r"[*?{]")
_ATOMS = re.compile(r"(\{[^{}]*\}|<unknown:[^<>]*>)")
DEFAULT_DISTINCT_MAX = 10


class OpenApiClient:
    """The calls of one repository at one commit, through a client generated from its spec. One adapter per run: the answer for a
    commit is kept (a commit never changes)."""

    kind = "openapi"

    def __init__(self, repo: str, language: Any, spec_path: str, client: Sequence[str] = ()) -> None:
        self.repo = repo
        self.language = language
        self.spec_path = spec_path
        self.client = tuple(client)
        self._calls: dict[str, ClientCalls] = {}

    @classmethod
    def from_config(cls, repo: config.Repo, language: Any) -> "OpenApiClient":
        options = repo.http_client or {}
        return cls(repo.name, language, str(options.get("spec") or ""), as_list(options.get("client")))

    @property
    def target(self) -> Any:
        return self.language.target

    def calls(self, sha: str) -> ClientCalls:
        if sha not in self._calls:
            self._calls[sha] = self._scan(sha)
        return self._calls[sha]

    # ---- the scan

    def _scan(self, sha: str) -> ClientCalls:
        got = read_file(self.language, self.spec_path, sha)
        if got.spec is None:
            self._record_unread(got.state, got.why)
            return ClientCalls(self.repo, sha, (), False, (f"{got.why}: the calls of {self.repo} through its generated client were not found",))
        spec = got.spec
        reasons: list[str] = []
        self._gaps(spec, reasons)
        ops = [op for op in spec.operations if op.operation_id]
        missing = [op for op in spec.operations if not op.operation_id]
        if missing:
            reasons.append(f"{len(missing)} operation(s) of {self.spec_path} have no operationId (first: {missing[0].method} {missing[0].template}): "
                           "calls to them through the generated client cannot be found by name")
        bound, wanted = self._distinctive(ops, sha, reasons)
        files = self._generated(sha)
        if not files:
            reasons.append(f"no file of the generated client ({', '.join(self.client)}) is in the tree at {sha[:12]}: the calls through it were not found")
            return ClientCalls(self.repo, sha, (), False, tuple(dict.fromkeys(reasons)), len(bound))
        users = self._users(files, bound, sha, reasons)
        calls: list[ClientCall] = []
        for path in sorted(users):
            calls += self._calls_in(path, users[path], bound, wanted, spec, sha, reasons)
        calls.sort(key=lambda c: (c.path, c.line, c.name, c.url.text))
        return ClientCalls(self.repo, sha, tuple(calls), not reasons, tuple(dict.fromkeys(reasons)), len(bound))

    def _record_unread(self, state: str, why: str) -> None:
        if state == "yaml":  # blocking, like any spec that cannot be read: a removed operation of a YAML spec gives no finding
            record_quoted(self.target, "spec_unreadable", f"{self.repo}:{self.spec_path}",
                          "the OpenAPI spec is YAML and this adapter reads JSON only: the operations it declares are not known, so no call is matched to one and nothing is said to be absent from it", once=True)
        else:
            record_quoted(self.target, "spec_unreadable", f"{self.repo}:{self.spec_path}", "no call is matched to an operation and nothing is said to be absent from it", why, once=True)

    def _gaps(self, spec: Spec, reasons: list[str]) -> None:
        if spec.problems:
            more = f" (and {len(spec.problems) - 3} more)" if len(spec.problems) > 3 else ""
            record_quoted(self.target, "spec_gap", f"{self.repo}:{self.spec_path}",
                          "the operations that depend on these parts may be read incompletely", "; ".join(spec.problems[:3]) + more, once=True)
        refs = set(spec.ref_problems)
        reasons += [f"{self.spec_path}: {p}" for p in spec.problems if p not in refs]  # a reference outside the document does not hide a call

    def _generated(self, sha: str) -> set[str]:
        tree = self.language.tree(sha)
        out: set[str] = set()
        for pattern in self.client:
            glob = glob_regex(pattern) if _GLOB.search(pattern) else None
            prefix = pattern.rstrip("/") + "/"
            out |= {p for p in tree if p == pattern or p.startswith(prefix) or (glob is not None and glob.match(p))}
        return out

    # ---- which names link code

    def _distinctive(self, ops: Sequence[Operation], sha: str, reasons: list[str]) -> tuple[dict[str, Operation], dict[str, Operation]]:
        """(the operations a name of the generated client stands for, those among them whose name also links code by itself). A name an
        import resolves (a named import, a namespace) is a resolved edge and needs no cap; a member call (`x.name(`) is a match by name
        alone, and only a distinctive name makes one: not made only of generic words, defined at most `limits.distinct_max` times (definitions,
        never references). A name two operations carry is no name at all. The names dropped from member matching are recorded."""
        by_name: dict[str, list[Operation]] = {}
        for op in ops:
            by_name.setdefault(op.operation_id or "", []).append(op)
        limit = getattr(self.language, "distinct_max", None) or DEFAULT_DISTINCT_MAX
        prefix = getattr(self.language, "subject_prefix", "")  # `<repo>:` when the run is in another repository: the subject names the repository, as a hub's does
        bound: dict[str, Operation] = {}
        named: dict[str, Operation] = {}
        for name in sorted(by_name):
            group = by_name[name]
            if len(group) > 1:
                record_quoted(self.target, "not_distinctive", prefix + name, f"{len(group)} operations of the spec carry this operationId, so the name is not used to link code", once=True)
                continue
            bound[name] = group[0]
            if names.is_generic(name):
                record_quoted(self.target, "not_distinctive", prefix + name, "the name is made only of generic words, so no member call is matched by it (an import of it from the generated client still is)", once=True)
                continue
            found = self.language.definitions(name, sha)
            if found.state != "found" or found.count is None:
                reasons.append(f"how often {name} is defined could not be counted: member calls of that name were not looked for")
            elif found.count > limit:
                record_quoted(self.target, "not_distinctive", prefix + name, not_distinctive_detail(found, sha, limit) + "; no member call is matched by it (an import of it from the generated client still is)", once=True)
            elif not found.complete:
                reasons.append(f"how often {name} is defined could not be counted completely: member calls of that name were not looked for")
            else:
                named[name] = group[0]
        return bound, named

    # ---- the files that use the generated code

    def _users(self, files: set[str], wanted: dict[str, Operation], sha: str, reasons: list[str]) -> dict[str, list[Any]]:
        """The files that import the generated code, with the import edges that do (a barrel that re-exports it is followed: its
        users are users of the generated code)."""
        lang = self.language
        known = set(files)
        frontier = sorted(known)
        users: dict[str, list[Any]] = {}
        prefetch = getattr(lang, "prefetch_importers", None)
        while frontier:
            if prefetch is not None:
                prefetch(frontier, sha)
            nxt: list[str] = []
            for target in frontier:
                found = lang.importers(target, sha)
                if not found.complete:
                    reasons += [f"the users of {target} were not found completely: {why}" for why in found.reasons]
                for e in found.edges:
                    path = e.importer_path
                    if path in known and e.kind not in ("reexport", "export_star"):
                        continue  # generated code uses generated code
                    if e.kind in ("reexport", "export_star"):
                        for b in e.bindings:
                            if b.imported in wanted and b.local != b.imported:
                                reasons.append(f"{path} re-exports {b.imported} under another name: calls through that name were not found")
                        if path not in known:
                            known.add(path)
                            nxt.append(path)
                        continue
                    if lang.is_test_path(path):
                        continue
                    users.setdefault(path, []).append(e)
            frontier = sorted(nxt)
        return users

    # ---- the calls in one file

    def _calls_in(self, path: str, edges: Sequence[Any], bound: dict[str, Operation], named: dict[str, Operation], spec: Spec, sha: str,
                  reasons: list[str]) -> list[ClientCall]:
        lang = self.language
        parsed = lang.parsed(sha, path)
        if parsed is None:
            reasons.append(f"{path} could not be read: the calls in it were not found")
            return []
        if parsed.problems:
            reasons.append(f"{path} did not lex cleanly ({parsed.problems[0]}): a call in it may be missed")
        uses: dict[tuple[str, int], tuple[str, str, Any]] = {}  # (operation, line): the use, the resolved one first
        for e in edges:
            if e.kind in ("dynamic", "require") and not e.bindings:
                reasons.append(f"{path} loads the generated client dynamically: which operations it calls was not established")
            for b in e.bindings:
                if b.type_only:
                    continue
                if b.imported == "*":  # a namespace import: `api.name(` is the module's member, resolved
                    for name in sorted(bound):
                        for u in parsed.find_uses(b.local, member=name):
                            uses.setdefault((name, u.line), (name, name, u))
                elif b.imported in bound:  # a named import, aliased or not: resolved
                    for u in parsed.find_uses(b.local):
                        uses.setdefault((b.imported, u.line), (b.imported, b.local, u))
        for u in parsed.member_uses(named):  # by name alone, in a file that imports the generated code: distinctive names only
            uses.setdefault((u.local, u.line), (u.local, u.local, u))
        out: list[ClientCall] = []
        for name, local, use in uses.values():
            ev = evidence_at(self.repo, path, sha, parsed, use.line)
            if ev is None:
                continue
            op = bound[name]
            for full, base in op.routes():
                out.append(ClientCall(
                    repo=self.repo, path=path, line=use.line, name=name, local=local, url=self._url(full), method=op.method, evidence=ev,
                    via=self._via(spec, op, sha, base), enclosing=use.enclosing, form=use.form,
                ))
        return out

    def _via(self, spec: Spec, op: Operation, sha: str, base: Any = None) -> tuple[Evidence, ...]:
        """The lines of the spec that define the URL of the call: the method key, the path key and the server URL."""
        lines = [op.line, op.path_line]
        if base is not None and base.line and base.path:
            lines.append(base.line)
        out = []
        for n in lines:
            detail = spec.line_text(n)
            if detail.strip():
                out.append(Evidence(self.repo, self.spec_path, n, sha, detail))
        return tuple(out)

    @staticmethod
    def _url(full: str) -> ClientUrl:
        parts: list[tuple[str, str]] = []
        for segment in full.strip("/").split("/"):
            if not segment:
                continue
            parts.append(("lit", "/"))
            for piece in _ATOMS.split(segment):
                if not piece:
                    continue
                if piece.startswith("{") and piece.endswith("}"):
                    parts.append(("param", piece[1:-1]))
                elif piece.startswith("<unknown:"):
                    parts.append(("unknown", piece[len("<unknown:"):-1]))
                else:
                    parts.append(("lit", piece))
        return url_from_parts(parts)
