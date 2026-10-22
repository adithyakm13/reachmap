"""Reports with decisions for the M11 tests: a real report (s01, four screens and four findings) given the `decisions` object the review
layer would write, with the text a person typed (the reason, the author) as the test chooses, hostile or not."""

from __future__ import annotations

import copy
from typing import Any

from tests import support_report as sr

S01 = ("--repo", "acme/web", "--compare", "main...scenario/s01-shared-component")
WHEN = "2026-01-02T03:04:05Z"


def s01() -> dict[str, Any]:
    return sr.real(*S01)


def entry(report: dict[str, Any], kind: str, index: int, verdict: str, reason: str = "", author: str = "Quinn", channel: str = "cli", revision: int = 1, **more: Any) -> dict[str, Any]:
    t = report[kind + "s"][index]
    label = t["label"] if kind == "area" else t["title"]
    return {
        "target_id": t["id"], "kind": kind, "label": label, "verdict": verdict, "reason": reason, "author": author, "channel": channel,
        "fingerprint": t["fingerprint"], "revision": revision, "created_at": WHEN, **more,
    }


def decided(reason: str = "retired in this release", author: str = "Quinn") -> dict[str, Any]:
    """s01 with: area 0 skipped and finding 0 accepted (current), area 1 marked to test on a claim that has since changed (stale, with what
    changed), a decision about an area that is gone (orphaned); areas 2 and 3 and findings 1 to 3 are undecided."""
    r = s01()
    stale = entry(r, "area", 1, "test", "", author, fingerprint="b" * 64, now_fingerprint=r["areas"][1]["fingerprint"],
                  changes={"removed": [{"repo": "acme/web", "path": "src/a.tsx", "detail": 'import { Badge } from "@components/Badge";'}],
                           "added": [{"repo": "acme/web", "path": "src/a.tsx", "detail": 'import { Badge } from "../Badge";'}]})
    orphan = {
        "target_id": "a-" + "7" * 16, "kind": "area", "label": "/cases/old", "verdict": "skip", "reason": reason, "author": author, "channel": "html-import",
        "fingerprint": "7" * 64, "revision": 1, "created_at": WHEN,
    }
    r["review"]["decisions"] = {
        "current": [entry(r, "area", 0, "skip", reason, author), entry(r, "finding", 0, "accept", "", author)],
        "stale": [stale], "orphaned": [orphan],
        "undecided": {"areas": [r["areas"][i]["id"] for i in (2, 3)], "findings": [r["findings"][i]["id"] for i in (1, 2, 3)]},
        "records": [], "as_of": WHEN,
    }
    return r


def unreadable() -> dict[str, Any]:
    r = s01()
    r["review"]["decisions"] = {
        "current": [], "stale": [], "orphaned": [], "undecided": {"areas": [], "findings": []}, "as_of": WHEN,
        "records": [{"kind": "decision_store_unreadable", "subject": "acme--acme-web--x--12345678.jsonl", "not_established": "which decisions were recorded for this change",
                     "detail": "line 2: the line is not JSON (a last line may be unfinished)", "blocks_selection": True}],
    }
    return r


def without_decisions(report: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(report)
    out["review"]["decisions"] = {"current": [], "stale": [], "orphaned": [], "undecided": {"areas": [], "findings": []}, "records": [], "as_of": WHEN}
    return out
