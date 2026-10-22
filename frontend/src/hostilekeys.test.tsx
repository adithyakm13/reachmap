import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Report } from "./api/types";
import { fakeClient } from "./test/fakeClient";
import { fixture } from "./test/fixtures";
import { expectPageWordsClean, pageText, renderAt, renderReport } from "./test/render";
import { build } from "./report/view";

// A report can hold any text where the page looks a word up in a table (a kind, a state, an adoption, a basis, a verdict, an edge kind,
// a priority): `__proto__`, `constructor`, `toString` and `hasOwnProperty` must find nothing, not a member every object has.
const KEYS = ["__proto__", "constructor", "toString", "hasOwnProperty", "valueOf", "prototype"];
const REPLACED = new Set(["kind", "state", "adoption", "basis", "form", "verdict", "role", "mapping", "source", "channel"]);
const TABS = ["", "/areas", "/findings", "/branches", "/cross-repo", "/tests", "/records", "/author"];

function replaceKeys(value: unknown, key: string, top = true): unknown {
  if (Array.isArray(value)) {
    return value.map((v) => replaceKeys(v, key, false));
  }
  if (typeof value === "object" && value !== null) {
    return Object.fromEntries(
      Object.entries(value).map(([k, v]) => [k, typeof v === "string" && REPLACED.has(k) && !(top && k === "kind") ? key : replaceKeys(v, key, false)]),
    );
  }
  return value;
}

describe.each(KEYS)("a report whose kinds, states and words are %s", (key) => {
  it("is shown whole on every tab, in its own words", async () => {
    const report = replaceKeys(fixture("complete"), key) as Report;
    for (const tail of TABS) {
      const { unmount } = await renderReport(report, tail);
      expect(screen.getAllByRole("tab").length, tail).toBeGreaterThan(5);
      expectPageWordsClean();
      unmount();
    }
    const { unmount } = await renderReport(report, "/findings");
    expect(pageText()).toContain(key);
    unmount();
  });

  it("is shown whole when the priority and the identity are such words too", async () => {
    const report = replaceKeys(fixture("s13"), key) as Report;
    for (const a of report.areas ?? []) {
      a.priority = key as never;
    }
    for (const f of report.findings ?? []) {
      f.identity = f.identity.map(() => key);
    }
    for (const tail of ["", "/areas", "/findings", "/branches"]) {
      const { unmount } = await renderReport(report, tail);
      expectPageWordsClean();
      unmount();
    }
    expect(build(report).completed).toBe(true);
  });

  it("shows a decision whose verdict is such a word, and a list and jobs with such states", async () => {
    const report = fixture("s13");
    const area = (report.areas ?? [])[0];
    report.review!.decisions!.current = [
      { target_id: area.id as string, kind: key, label: key, verdict: key, reason: "r", author: "Quinn", channel: key, revision: 1, created_at: "t", fingerprint: "a".repeat(64) },
    ];
    const { unmount } = await renderReport(report, "/areas");
    expect(pageText()).toContain(key);
    unmount();
    const drawer = await renderReport(report, `/areas/${area.id}`);
    expect(pageText()).toContain(key);
    drawer.unmount();
    const client = fakeClient({
      reports: [{ name: "x-r1", key: { repo: key, change: key }, revision: 1, status: key, created: key }],
      jobs: [{ id: "0123456789ab", state: key, repo: key, change: { compare: key }, as_of: null, created_at: key, started_at: null, finished_at: null, report: null, exit_code: null, reason: null }],
    });
    const list = renderAt(client, "/");
    await screen.findByRole("table", { name: "Reports" });
    expect(list.container.textContent).toContain(key);
    list.unmount();
    const jobs = renderAt(client, "/jobs");
    await screen.findByRole("table", { name: "Jobs" });
    expect(jobs.container.textContent).toContain(key);
  });
});
