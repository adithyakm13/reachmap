import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createClient } from "./api/client";
import type { Report } from "./api/types";
import { fixture } from "./test/fixtures";
import { NAME, renderAt, renderReport } from "./test/render";
import { Component, type ReactNode } from "react";
import { render } from "@testing-library/react";
import { SectionBoundary } from "./components/Boxes";
import { AppProviders } from "./providers";
import { build } from "./report/view";
import { crossGraph, type CrossGraph } from "./report/graph";
import { layoutCross } from "./graphs/layout";

const TOKEN = "Zk3_-abcDEF0123456789abcdefABCDEF0123456789";
const METHODS = ["log", "info", "warn", "error", "debug", "trace"] as const;

function spyConsole() {
  return METHODS.map((m) => vi.spyOn(console, m).mockImplementation(() => undefined));
}

function everythingLogged(spies: ReturnType<typeof spyConsole>): string {
  return spies
    .flatMap((s) => s.mock.calls)
    .map((args) =>
      args
        .map((a) => {
          try {
            return a instanceof Error ? `${a.name} ${a.message} ${a.stack ?? ""}` : typeof a === "string" ? a : JSON.stringify(a);
          } catch {
            return String(a);
          }
        })
        .join(" "),
    )
    .join("\n");
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("what the page writes to the console", () => {
  it("never holds the token, whatever fails: a refused request, a network error, an answer that is not JSON", async () => {
    const spies = spyConsole();
    const answers: (() => Promise<Response>)[] = [
      async () => new Response("{}", { status: 401 }),
      async () => {
        throw new TypeError(`Failed to fetch /api/reports with ${TOKEN}`);
      },
      async () => new Response(`not json ${TOKEN}`, { status: 200, headers: { "Content-Type": "application/json" } }),
      async () => new Response("{}", { status: 500 }),
    ];
    for (const next of answers) {
      const client = createClient(TOKEN, async () => next());
      const { container } = renderAt(client, "/");
      await waitFor(() => expect(container.textContent?.length ?? 0).toBeGreaterThan(0));
      await screen.findByRole("alert").catch(() => undefined);
      await client.reports().catch(() => undefined);
      await client.report(NAME).catch(() => undefined);
      await client.decide(NAME, { target: "a-0123456789abcdef", verdict: "skip", reason: "x", author: "Quinn" }).catch(() => undefined);
      container.remove();
    }
    expect(everythingLogged(spies)).not.toContain(TOKEN);
  });

  it("holds no text of a report: a page that fails to draw logs one fixed line, not the message", async () => {
    const spies = spyConsole();
    const report = fixture("hostile");
    const view = await renderReport(report);
    view.unmount();
    expect(everythingLogged(spies)).not.toMatch(/onerror|alert\(1\)|<script/i);
  });
});

class Throws extends Component<{ children?: ReactNode }> {
  render(): ReactNode {
    throw new Error("<img src=x onerror=alert(1)> hostile message");
  }
}

describe("a section that fails to draw", () => {
  it("shows fixed words in its place, leaves its neighbours alone, and never shows the error", () => {
    // React itself logs a caught error in development: that is not the page's own
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const { container } = render(
      <AppProviders>
        <p>the neighbour</p>
        <SectionBoundary>
          <Throws />
        </SectionBoundary>
      </AppProviders>,
    );
    expect(screen.getByText("the neighbour")).toBeTruthy();
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("could not be drawn");
    expect(container.textContent).not.toContain("hostile message");
    expect(container.querySelector("img")).toBeNull();
  });
});

describe("a report whose counts are missing", () => {
  it("counts what the report lists when it completed, and says no count at all when it did not", async () => {
    const complete = fixture("complete");
    delete (complete as { counts?: unknown }).counts;
    const view = build(complete);
    expect(view.header.counts).not.toBeNull();
    expect(Object.fromEntries(view.header.counts ?? []).areas).toBe((complete.areas ?? []).filter((a) => !(a as { by_name?: boolean }).by_name).length);
    const none: Report = fixture("complete");
    none.areas = [];
    none.findings = [];
    none.incompleteness = [];
    delete (none as { counts?: unknown }).counts;
    const zero = build(none);
    expect(zero.header.counts?.every(([, n]) => n === 0)).toBe(true);
    const failed = build(fixture("failed"));
    expect(failed.header.counts).toBeNull();
    const timedOut = build(fixture("timed_out"));
    expect(timedOut.header.counts).toBeNull();
  });
});

describe("an edge between repositories", () => {
  it("keeps the kind the report named, as text, next to the kind the page understood", () => {
    const report = fixture("s11");
    const view = build(report);
    const graph = crossGraph(view);
    expect(graph.edges.length).toBeGreaterThan(0);
    for (const e of graph.edges) {
      expect(typeof e.rawKind).toBe("string");
    }
    const hostile = JSON.parse(JSON.stringify(report)) as Report;
    for (const p of hostile.propagations ?? []) {
      for (const hop of (p as { hops?: { kind?: string }[] }).hops ?? []) {
        hop.kind = "<img src=x onerror=alert(1)>";
      }
    }
    const edges = crossGraph(build(hostile)).edges;
    expect(edges.some((e) => e.rawKind.includes("onerror"))).toBe(true);
    expect(edges.filter((e) => e.rawKind.includes("onerror")).every((e) => e.kind === "edge")).toBe(true);
  });
});

describe("repositories that call each other", () => {
  it("are one line with an arrow at each end, named by both kinds, not two lines on one another", () => {
    const note = { branch: "main", pin: null, adoption: "" };
    const model: CrossGraph = {
      repos: ["acme/api", "acme/web", "acme/other"],
      edges: [
        { id: "a", from: "acme/api", to: "acme/web", kind: "http", rawKind: "http", candidate: false, notes: [note] },
        { id: "b", from: "acme/web", to: "acme/api", kind: "http", rawKind: "http", candidate: false, notes: [note] },
        { id: "c", from: "acme/api", to: "acme/other", kind: "package", rawKind: "package", candidate: true, notes: [note] },
      ],
    };
    const laid = layoutCross(model);
    expect(laid.edges.length).toBe(2);
    const mutual = laid.edges.find((e) => (e.data as { both: boolean }).both)!;
    expect(mutual.label).toBe("http x2");
    expect(laid.edges.filter((e) => !(e.data as { both: boolean }).both).length).toBe(1);
  });
});

describe("the palette on a hostile report", () => {
  it("lists every label as visible plain text: nothing is an element, no control or direction character is raw", async () => {
    await renderReport("hostile", "/areas");
    await userEvent.keyboard("{Control>}k{/Control}");
    const search = await screen.findByPlaceholderText(/Search reports, areas and findings/);
    const dialog = search.closest("[role=dialog]") as HTMLElement;
    expect(dialog).not.toBeNull();
    await waitFor(() => expect(dialog.querySelectorAll("button").length).toBeGreaterThan(3));
    const raw = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]/;
    for (const button of dialog.querySelectorAll("button")) {
      expect(button.querySelector("img, script, iframe, a, style, svg[onload]")).toBeNull();
      expect(raw.test(button.textContent ?? "")).toBe(false);
      expect((button.textContent ?? "").length).toBeLessThan(400);
    }
    // a query that is markup is searched for as text
    await userEvent.type(search, "<img src=x onerror=alert(1)>");
    expect(dialog.querySelector("img")).toBeNull();
  });
});
