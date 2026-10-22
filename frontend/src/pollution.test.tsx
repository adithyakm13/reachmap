import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { layoutCross, layoutReach } from "./graphs/layout";
import type { Report } from "./api/types";
import type { CrossGraph, ReachGraph } from "./report/graph";
import { crossGraph, reachGraph } from "./report/graph";
import { allAreas, build } from "./report/view";
import { RAW } from "./test/fixtures";
import { renderReport } from "./test/render";

// Report content is untrusted, and a repository can be called anything. dagre keeps nodes and edges in plain objects keyed by the id it is
// given, so an id taken from a report (`__proto__`, `constructor`) would write into `Object.prototype` and break every object of the page.

const NAMES = ["__proto__", "constructor", "toString", "hasOwnProperty", "valueOf", "prototype", "__defineGetter__"];

function prototypeState(): string {
  return JSON.stringify(Object.getOwnPropertyNames(Object.prototype).sort());
}

function expectPrototypeIntact(before: string): void {
  expect(prototypeState()).toBe(before);
  const plain = {} as Record<string, unknown>;
  for (const key of [...NAMES, "x", "polluted", "1", "a"]) {
    if (!["__proto__", "constructor", "toString", "hasOwnProperty", "valueOf", "__defineGetter__"].includes(key)) {
      expect(plain[key], key).toBeUndefined();
    }
  }
  expect(Object.keys(plain)).toEqual([]);
  expect(typeof ({}).hasOwnProperty).toBe("function");
  expect(({} as { polluted?: unknown }).polluted).toBeUndefined();
}

function reachModel(): ReachGraph {
  return {
    repos: NAMES.map((repo, i) => ({ repo, origin: i === 0, areas: [{ id: null, label: repo, kind: repo, candidate: false }], hidden: 0 })),
    edges: NAMES.slice(1).map((to, i) => ({ id: `r${i}`, from: NAMES[i], to, kind: NAMES[i], candidate: false })),
  };
}

function crossModel(): CrossGraph {
  return {
    repos: [...NAMES],
    edges: NAMES.slice(1).map((to, i) => ({ id: `x${i}`, from: NAMES[i], to, kind: "package" as const, rawKind: NAMES[i], candidate: i % 2 === 0, notes: [{ branch: NAMES[i], pin: null, adoption: NAMES[i] }] })),
  };
}

describe("report content never becomes a key of an object that dagre keeps", () => {
  it("lays out the reach graph of repositories called __proto__, constructor and toString", () => {
    const before = prototypeState();
    (Object.prototype as Record<string, unknown>).polluted = undefined;
    delete (Object.prototype as Record<string, unknown>).polluted;
    const laid = layoutReach(reachModel());
    expect(laid.nodes.filter((n) => n.type === "repo").length).toBe(NAMES.length);
    expect(laid.edges.length).toBe(NAMES.length - 1);
    // the names are in the data of the boxes, as text, and in no id
    expect(laid.nodes.map((n) => n.id).join(" ")).not.toMatch(/proto|constructor|toString|valueOf/);
    expect(laid.nodes.filter((n) => n.type === "repo").map((n) => (n.data as { repo: string }).repo)).toEqual(NAMES);
    expectPrototypeIntact(before);
  });

  it("lays out the cross-repository graph of such repositories, with their edges between them", () => {
    const before = prototypeState();
    const laid = layoutCross(crossModel());
    expect(laid.nodes.length).toBe(NAMES.length);
    expect(laid.edges.length).toBeGreaterThan(0);
    expect(laid.nodes.map((n) => n.id).join(" ")).not.toMatch(/proto|constructor|toString|valueOf/);
    for (const e of laid.edges) {
      expect(laid.nodes.some((n) => n.id === e.source)).toBe(true);
      expect(laid.nodes.some((n) => n.id === e.target)).toBe(true);
    }
    expectPrototypeIntact(before);
  });

  it("lays out a graph made from a report whose repositories and hops are called such names, and the next graph is still laid out", () => {
    const before = prototypeState();
    for (const name of NAMES) {
      const raw = RAW.s11.replaceAll("acme/api", name).replaceAll("acme/web", name + "x").replaceAll("acme/worker", "constructor");
      const view = build(JSON.parse(raw) as Report);
      layoutReach(reachGraph(view, allAreas(view)));
      layoutCross(crossGraph(view));
      expectPrototypeIntact(before);
    }
    // an ordinary graph afterwards: nothing was broken for it
    const view = build(JSON.parse(RAW.s11) as Report);
    expect(layoutCross(crossGraph(view)).nodes.length).toBeGreaterThan(1);
    expectPrototypeIntact(before);
  });

  it("draws the overview and the cross-repository tab of such a report and leaves Object.prototype as it was", async () => {
    const before = prototypeState();
    for (const name of ["__proto__", "constructor", "toString"]) {
      const raw = RAW.s11.replaceAll("acme/api", name).replaceAll("acme/web", name + "x").replaceAll("acme/worker", "hasOwnProperty");
      const report = JSON.parse(raw) as Report;
      const overview = await renderReport(report);
      await screen.findByRole("group", { name: "What this change reaches" });
      overview.unmount();
      const cross = await renderReport(report, "/cross-repo");
      await screen.findByRole("group", { name: "Repositories and the edges between them" });
      cross.unmount();
      expectPrototypeIntact(before);
    }
  });
});
