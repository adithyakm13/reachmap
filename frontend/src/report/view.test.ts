import { describe, expect, it } from "vitest";
import { KIND_ORDER, allAreas, allFindings, build, shellQuote } from "./view";
import { flatSection, noteSection } from "./notes";
import { show } from "./text";
import { NAMES, ORDER, fixture, type FixtureName } from "../test/fixtures";

// `fixtures/order.json` is what the Python view model (`reachmap.report.model`) makes of each report. The TypeScript view model must
// put the same things in the same order (regenerate with `python3.12 frontend/fixtures/make_reports.py`).

type Order = {
  status: string;
  recordsFirst: boolean;
  records: [string, boolean][];
  mustTest: string[];
  sample: string[];
  candidates: string[];
  areaFindings: Record<string, number[]>;
  areaComplete: Record<string, boolean | null>;
  groups: [string, number[]][];
  exclusions: number[];
  matrix: [string, string, number[]][];
  hops: [string, string, string, number][];
  consumers: [string, string, string][];
  http: [string, string, string][];
  contractChanges: string[];
  contractReads: [string, string, string][];
  contractCandidates: [string, string, string][];
  backend: [string, string][];
  suggested: string;
  suggestedFull: {
    items: [string, string, number | null, number][];
    records: [string, boolean, boolean, string][];
    partial: string[];
    sources: [string, string][];
  };
  recordScopes: [string, boolean, string][];
  counts: [string, number][] | null;
  modelNotes: null | { notes: [string, string, number, string | null][]; records: [string, boolean][]; section: unknown[] };
  mapped: number;
  author: null | {
    repos: [string, string[]][];
    files: [string, string][];
    branches: [string, string][];
    tests: [string, string][];
  };
};

describe.each(NAMES)("the view of %s agrees with the Python view model", (name: FixtureName) => {
  const want = ORDER[name] as unknown as Order;
  const view = build(fixture(name));

  it("has the same status, records and areas in the same order", () => {
    expect(view.status).toBe(want.status);
    expect(view.recordsFirst).toBe(want.recordsFirst);
    expect(view.records.map((r) => [show(r.kind), r.blocking])).toEqual(want.records);
    expect(view.records.map((r) => [show(r.kind), r.blocksSuggestions, r.scope])).toEqual(want.recordScopes);
    expect(view.header.counts).toEqual(want.counts);
    expect(view.mustTest.map((a) => show(a.label))).toEqual(want.mustTest);
    expect(view.sample.map((a) => show(a.label))).toEqual(want.sample);
    expect(view.candidates.map((a) => show(a.label))).toEqual(want.candidates);
    const areas = [...view.mustTest, ...view.sample, ...view.candidates];
    expect(Object.fromEntries(areas.map((a) => [show(a.label), a.findings]))).toEqual(want.areaFindings);
    expect(Object.fromEntries(areas.map((a) => [show(a.label), a.complete]))).toEqual(want.areaComplete);
  });

  it("has the same finding groups, the same exclusions and the same branch matrix", () => {
    expect(view.groups.map((g) => [show(g.kind), g.findings.map((f) => f.number)])).toEqual(want.groups);
    expect(view.exclusions.map((f) => f.number)).toEqual(want.exclusions);
    expect(view.matrix.map((b) => [show(b.branch), show(b.state), b.findings])).toEqual(want.matrix);
  });

  it("has the same cross-repository reach and backend calls", () => {
    expect(view.cross.hops.map((h) => [show(h.fromRepo), show(h.toRepo), show(h.kind), h.depth])).toEqual(want.hops);
    expect(view.cross.consumers.map((c) => [show(c.producer), show(c.repo), show(c.branch)])).toEqual(want.consumers);
    expect(view.cross.http.map((e) => [show(e.clientRepo), show(e.serverRepo), show(e.route)])).toEqual(want.http);
    expect(view.cross.changes.map((c) => show(c.symbol))).toEqual(want.contractChanges);
    expect(view.cross.reads.map((r) => [show(r.repo), show(r.owner), show(r.field)])).toEqual(want.contractReads);
    expect(view.cross.candidates.map((r) => [show(r.repo), show(r.owner), show(r.field)])).toEqual(want.contractCandidates);
    expect(view.backend.map((b) => [show(b.client.locator), show(b.path)])).toEqual(want.backend);
    expect(view.suggested.state).toBe(want.suggested);
    expect(view.suggested.items.map((x) => [show(x.source), show(x.caseId), x.area, x.evidence.length])).toEqual(want.suggestedFull.items);
    expect(view.suggested.records.map((r) => [show(r.kind), r.blocking, r.blocksSuggestions, r.scope])).toEqual(want.suggestedFull.records);
    expect(view.suggested.partial.map((r) => show(r.kind))).toEqual(want.suggestedFull.partial);
    expect(view.suggested.sources.map((x) => [show(x.source), x.state])).toEqual(want.suggestedFull.sources);
    expect(view.mapped.length).toBe(want.mapped);
  });

  it("has the same model notes: the same notes in the same states, and the section of the Python layout, line by line", () => {
    const m = view.modelNotes;
    if (want.modelNotes === null) {
      expect(m).toBeNull();
      return;
    }
    expect(m).not.toBeNull();
    expect(m?.notes.map((n) => [show(n.id), show(n.state), n.revision, n.targetId === null ? null : show(n.targetId)])).toEqual(want.modelNotes.notes);
    expect(m?.records.map((r) => [show(r.kind), r.blocking])).toEqual(want.modelNotes.records);
    expect(flatSection(noteSection(m!))).toEqual(want.modelNotes.section);
  });

  it("has the same view for the author", () => {
    if (want.author === null) {
      expect(view.author).toBeNull();
      return;
    }
    expect(view.author?.repos.map((r) => [show(r.repo), r.edges.map((e) => show(e.text))])).toEqual(want.author.repos);
    expect(view.author?.files.map((f) => [show(f.repo), show(f.path)])).toEqual(want.author.files);
    expect(view.author?.branches.map((b) => [show(b.branch), show(b.state)])).toEqual(want.author.branches);
    expect(view.author?.tests.map((t) => [show(t.repo), show(t.text)])).toEqual(want.author.tests);
  });
});

describe("the order of the areas", () => {
  it("is made by sorting, not by the order of the report (the fixture lists its areas out of order)", () => {
    const report = fixture("unordered");
    const inReport = (report.areas ?? []).map((a) => a.label);
    const v = build(report);
    const shown = [...v.mustTest, ...v.sample, ...v.candidates].map((a) => a.label);
    expect(shown.length).toBe(inReport.length);
    expect(shown).not.toEqual(inReport);
    expect(v.mustTest.map((a) => a.label)).toEqual(["GET /a/first", "GET /api/billing/invoices/{invoice_id}", "GET /m/middle", "/billing/invoices/:invoiceId", "/zz/last"]);
    expect(v.sample.map((a) => a.label)).toEqual(["/s/sample-a", "/s/sample-b"]);
    expect(v.candidates.map((a) => a.label)).toEqual(["/c/candidate-a", "/c/candidate-b"]);
  });
});

describe("the view model", () => {
  it("gives a run that did not complete no count and no list", () => {
    for (const name of ["failed", "timed_out"] as const) {
      const v = build(fixture(name));
      expect(v.completed).toBe(false);
      expect(v.header.counts).toBeNull();
      expect([v.mustTest, v.sample, v.candidates, v.groups, v.exclusions, v.matrix, v.backend]).toEqual([[], [], [], [], [], [], []]);
      expect(v.author).toBeNull();
      expect(v.records.length).toBeGreaterThan(0);
    }
  });

  it("keeps the counts of a report that has none out of a failed run even when the file carries them", () => {
    const report = fixture("failed");
    (report as unknown as Record<string, unknown>).counts = { areas: 9, must_test: 9, sample: 0, candidates: 0, findings: 9, exclusions: 0, records: 1, blocking_records: 1 };
    expect(build(report).header.counts).toBeNull();
  });

  it("counts from the lists when a completed report has no counts", () => {
    const report = fixture("complete");
    delete (report as unknown as Record<string, unknown>).counts;
    const given = build(fixture("complete")).header.counts;
    expect(build(report).header.counts).toEqual(given);
  });

  it("puts the blocking records before the areas when one blocks selection, after the findings otherwise", () => {
    expect(build(fixture("complete")).recordsFirst).toBe(true);
    expect(build(fixture("s13")).recordsFirst).toBe(false);
  });

  it("orders finding kinds as the reader meets them and keeps a kind it does not know after them", () => {
    const report = fixture("s13");
    const extra = JSON.parse(JSON.stringify((report.findings as unknown[])[0]));
    extra.kind = "zz-new-kind";
    extra.identity = ["zz-new-kind", "x", "y", "z"];
    (report.findings as unknown[]).push(extra);
    const kinds = build(report).groups.map((g) => g.kind);
    expect(kinds[kinds.length - 1]).toBe("zz-new-kind");
    const known = kinds.slice(0, -1);
    expect(known).toEqual([...known].sort((a, b) => KIND_ORDER.indexOf(a) - KIND_ORDER.indexOf(b)));
  });

  it("numbers findings as the report does, whatever the grouping", () => {
    const v = build(fixture("s13"));
    expect(allFindings(v).map((f) => f.number)).toEqual(allFindings(v).map((_, i) => i + 1));
    expect(allAreas(v).map((a) => a.index)).toEqual(allAreas(v).map((_, i) => i));
  });

  it("shows nothing about decisions in a report that has none, and a line on every target in one that has", () => {
    const report = fixture("s13");
    const none = build(report);
    expect(none.decisions).toBeNull();
    expect(allAreas(none).every((a) => a.decision === null)).toBe(true);
    const areaId = report.areas?.[0].id as string;
    report.review!.decisions!.current = [
      { target_id: areaId, kind: "area", label: "x", verdict: "skip", reason: "retired", author: "Quinn", channel: "runner", revision: 1, created_at: "2026-01-02T03:04:05Z", fingerprint: "a".repeat(64) },
    ];
    const some = build(report);
    expect(some.decisions?.current.length).toBe(1);
    const states = allAreas(some).map((a) => a.decision?.state);
    expect(states).toContain("current");
    expect(states).toContain("none");
  });

  it("makes no target say it has no decision when the store could not be read", () => {
    const report = fixture("s13");
    report.review!.decisions!.records = [{ kind: "decision_store_unreadable", subject: "store", not_established: "x", detail: "", blocks_selection: true }];
    const v = build(report);
    expect(v.decisions?.records.length).toBe(1);
    expect(allAreas(v).every((a) => a.decision === null)).toBe(true);
    expect(allFindings(v).every((f) => f.decision === null)).toBe(true);
  });

  it("is tolerant of a deep payload that is missing or of another type", () => {
    const report = fixture("complete") as unknown as Record<string, unknown>;
    report.propagations = [{ hops: "nope", consumers: 5, http_edges: [null, 3], contract_reads: {}, unit_test_commands: [{ paths: 1 }] }];
    report.backend_calls = [null, 7, { route: 3, modules: [4] }];
    report.suggestions = [3, null];
    report.branches = [null];
    const v = build(report as never);
    expect(v.completed).toBe(true);
    expect(v.author).not.toBeNull();
  });

  it("quotes paths for a shell as shlex.quote does", () => {
    expect(shellQuote("tests/test_a.py")).toBe("tests/test_a.py");
    expect(shellQuote("a b")).toBe("'a b'");
    expect(shellQuote("it's")).toBe(`'it'"'"'s'`);
    expect(shellQuote("")).toBe("''");
    expect(shellQuote("$(rm -rf x)")).toBe("'$(rm -rf x)'");
  });
});
