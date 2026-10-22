"""A small report and a reader that needs no repository, for the unit tests of the citation audit (M18)."""

from __future__ import annotations

from typing import Any

from reachmap.model import ReadResult

SHA = "a" * 40
OTHER = "b" * 40
LINES = {
    "src/pages/Invoices.tsx": ['import { Badge } from "@c/Badge";', "", "export const Page = () => <Badge />;"],
    "src/routes.tsx": ["import { Page } from './pages/Invoices';", "", "const r = [", '  { path: "/billing", element: <Page /> },'],
    "src/c/Badge.tsx": ["export function Badge() {", "  return null;", "}"],
}


def ev(path: str, line: int, claim: str | None = None, repo: str = "acme/web", ref: str = SHA, detail: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"repo": repo, "path": path, "line": line, "ref": ref, "detail": LINES[path][line - 1] if detail is None else detail}
    if claim is not None:
        out["claim"] = claim
    return out


def mini_report() -> dict[str, Any]:
    """A small completed report: one screen, one finding about it, a branch row and a propagation, with evidence that `LINES` has."""
    from reachmap import findings as _findings  # noqa: F401  (registers the kinds)
    from reachmap.model import Area, Evidence, Finding

    imp = Evidence(**ev("src/pages/Invoices.tsx", 1, "import:src/c/Badge.tsx:Badge"))
    route = Evidence(**ev("src/routes.tsx", 4, "route:/billing:src/pages/Invoices.tsx"))
    definition = Evidence(**ev("src/c/Badge.tsx", 1, "def:Badge"))
    area = Area("/billing", "screen", "acme/web", "src/pages/Invoices.tsx", route, ((imp, route),), (), "must_test", False)
    finding = Finding("direct", "The change reaches the screen /billing in acme/web", "", "medium", "high", "import-backed chain", (definition, imp, route),
                      ("acme/web", "direct", "/billing"))
    return {
        "schema": 1, "status": "completed", "ref": SHA, "change": {"head_sha": SHA, "files": []}, "findings": [finding.to_dict()], "areas": [area.to_dict()],
        "branches": [{"branch": "main", "commit": SHA, "state": "present", "detail": "d", "evidence": [ev("src/c/Badge.tsx", 3)]}],
        "propagations": [{
            "passes": [{"analysis": {"impacts": [{"importers": [{"evidence": imp.to_dict()}]}]}}],
            # a contract read has repo, path and line of its own and a piece of evidence inside it
            "contract_reads": [{"repo": "acme/web", "path": "src/c/Badge.tsx", "line": 1, "sha": SHA, "evidence": ev("src/c/Badge.tsx", 1)}],
        }],
        "backend_calls": [{"client": {"path": "src/pages/Invoices.tsx"}, "evidence": [ev("src/pages/Invoices.tsx", 1)]}],
        "suggestions": [{"source": "playwright", "case_id": "a:1", "title": "t", "matched_words": [], "area_id": None, "evidence": [ev("src/routes.tsx", 4)], "label": "l"}],
        "incompleteness": [], "notes": [], "review": None,
    }


class FakeReader:
    """What the audit's reader answers, from a dict of files, with the calls kept: {(repo, ref): {path: text}}. A path that is not in
    `files` is absent; `broken` names (repo, ref) pairs, or (repo, ref, path) triples, that read as unreadable."""

    def __init__(self, files: dict | None = None, broken: set | None = None) -> None:
        self.files = files if files is not None else {("acme/web", SHA): {p: "\n".join(v) + "\n" for p, v in LINES.items()}}
        self.broken = broken or set()
        self.calls: list[tuple[str, str, list[str]]] = []

    def read(self, repo: str, ref: str, paths):
        self.calls.append((repo, ref, list(paths)))
        out = {}
        for p in paths:
            if (repo, ref) in self.broken or (repo, ref, p) in self.broken:
                out[p] = ReadResult.unreadable("the object is missing")
            elif p in self.files.get((repo, ref), {}):
                out[p] = ReadResult.found(self.files[(repo, ref)][p])
            else:
                out[p] = ReadResult.absent()
        return out
