"""Small builders for model tests."""

from reachmap.identity import fingerprint
from reachmap.incomplete import record
from reachmap.model import (
    Analysis, Area, BranchState, Decision, Evidence, Finding, MappedTest, Suggestion,
)

SHA = "a" * 40
SHA2 = "b" * 40


def ev(path="src/a.ts", line=3, ref=SHA, detail="import x from './y'", repo="acme/web", claim=None):
    return Evidence(repo=repo, path=path, line=line, ref=ref, detail=detail, claim=claim)


def finding(**kw):
    fields = dict(
        kind="direct",
        title="StatusBadge reaches 2 screens",
        workflow="billing",
        risk="medium",
        confidence="high",
        confidence_basis="import found at the pinned commit",
        evidence=[ev()],
        identity=["direct", "acme/web", "src/a.ts", "StatusBadge"],
    )
    fields.update(kw)
    return Finding(**fields)


def area(**kw):
    fields = dict(
        label="/cases",
        kind="screen",
        repo="acme/web",
        module="cases",
        route_line=ev(path="src/routes.tsx", line=9, detail='<Route path="/cases" element={<CaseListPage />} />'),
        chains=[[ev(), ev(path="src/b.ts", line=1, detail="import a from './a'")]],
        also_mounted=[],
        priority="must_test",
        by_name=False,
    )
    fields.update(kw)
    return Area(**fields)


def screens(count, **kw):
    return [area(label=f"/screen-{i}", **kw) for i in range(count)]


def branch(**kw):
    fields = dict(branch="main", commit=SHA, state="present", added_lines_found=2, evidence=[ev()], detail="")
    fields.update(kw)
    return BranchState(**fields)


def decision(target, verdict="skip", reason="owner agreed", **kw):
    fields = dict(
        target_id=target.id if hasattr(target, "id") else target,
        verdict=verdict,
        reason=reason,
        author="qa@example.invalid",
        channel="workspace",
        fingerprint=fingerprint(target) if hasattr(target, "all_evidence") else "c" * 64,
        revision=1,
    )
    fields.update(kw)
    return Decision(**fields)


def suggestion(**kw):
    fields = dict(source="qase", case_id="42", title="Open the invoice list", matched_words=["invoice"])
    fields.update(kw)
    return Suggestion(**fields)


def mapped(a, **kw):
    fields = dict(area_id=a.id, case_id="7", title="Invoice list loads", source="qase", mapping="areas.Billing.suites=12")
    fields.update(kw)
    return MappedTest(**fields)


def analysis(**kw):
    """A completed analysis, or (status timed_out or failed) one with the required record and no results."""
    status = kw.get("status", "completed")
    fields = dict(
        change={"kind": "compare", "base": "main", "head": "feature"},
        ref=SHA,
        branches=[branch()],
        findings=[finding()],
        areas=[area()],
        propagations=[{"from": "acme/contracts", "to": "acme/api"}],
        backend_calls=[{"method": "GET", "route": "/api/cases"}],
        suggestions=[suggestion()],
        mapped_tests=[],
        incompleteness=[],
        notes=["Run on a fixture"],
        revision=1,
        status=status,
    )
    if status != "completed":
        fields.update(findings=[], areas=[], branches=[], propagations=[], backend_calls=[], suggestions=[], incompleteness=[record([], {"timed_out": "timed_out", "failed": "run_failed"}.get(status, "timed_out"), SHA)])
    fields.update(kw)
    return Analysis(**fields)
