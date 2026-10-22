import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Report } from "./api/types";
import { build } from "./report/view";
import { fixture } from "./test/fixtures";
import { expectPageWordsClean, pageText, renderReport } from "./test/render";

// The suggested cases and the records of the test sources (M15): per area, with what became of each source, and never "None suggested."
// unless every source was read completely. What the view model holds is held to the Python one by `view.test.ts`; this is what the page says.

const sectionOf = (container: HTMLElement) => container.querySelector('section[aria-labelledby="suggested-h"]') as HTMLElement;

describe("the Tests tab with suggestions", () => {
  it("groups the cases per area, with the spec line, and lists the cases tied to no area apart", async () => {
    const { container, report } = await renderReport("sources", "/tests");
    const section = sectionOf(container);
    const headings = [...section.querySelectorAll("h3")].map((h) => h.textContent);
    const areas = report.areas ?? [];
    expect(headings.slice(0, 2)).toEqual([`${areas[0].label} in ${areas[0].repo}`, `${areas[1].label} in ${areas[1].repo}`]);
    expect(headings).toContain("Not tied to an area of this report");
    const first = section.querySelector('[data-suggested-area="0"]') as HTMLElement;
    expect(first.textContent).toContain("billing.spec.ts:5");
    expect(first.textContent).toContain("Spec line:");
    expect(first.textContent).toContain("await page.goto('/billing/invoices/42');");
    const second = section.querySelector('[data-suggested-area="1"]') as HTMLElement;
    expect(second.textContent).toContain("Q-12");
    expect(second.textContent).not.toContain("Spec line:");
    expect(section.textContent).not.toContain("None suggested.");
    expectPageWordsClean();
  });

  it("says what each source that was not read completely says, and what a spec source could not use", async () => {
    const { container } = await renderReport("sources", "/tests");
    const text = sectionOf(container).textContent ?? "";
    expect(text).toContain("A test source did not answer, so its suggestions are missing:");
    expect(text).toContain("A test source was read in part, so this list is incomplete:");
    expect(text).toContain("A test source is not configured, so its suggestions were not looked up:");
    expect(text).toContain("The spec sources could not use everything they read: 1 visit has an address that is not a literal and some visits match no screen route:");
    expect(text).toContain("spec_url_partial");
  });

  it.each([
    ["sources_unavailable", "A test source did not answer, so no suggestions are listed."],
    ["sources_incomplete", "A test source was read in part, and nothing it listed matched an area, so the list is incomplete and no suggestions are listed."],
    ["sources_unconfigured", "Some test sources are not configured, and the others were read and suggested nothing."],
    ["sources_none", "None suggested from what could be read; 2 visits have an address that is not a literal and some visits match no screen route:"],
    ["s13", "No test source was read by this run."],
  ] as const)("says the state of %s in its own words and never that nothing was suggested when a source was not read completely", async (name, sentence) => {
    const { container } = await renderReport(name, "/tests");
    const text = sectionOf(container).textContent ?? "";
    expect(text).toContain(sentence);
    expect(text).not.toContain("None suggested.");
  });

  it("says None suggested only when every source was read completely and nothing was set aside", async () => {
    const report = fixture("s13");
    (report.inputs as Record<string, unknown>).test_sources = [{ source: "qase", kind: "qase", state: "read", read: true }];
    (report.inputs as Record<string, unknown>).test_source_read = true;
    const { container } = await renderReport(report, "/tests");
    expect(sectionOf(container).textContent).toContain("None suggested.");
  });

  it("shows the words of a case as text: nothing in a title, a source or a spec line becomes an element", async () => {
    const report = fixture("sources");
    const s = (report.suggestions ?? []) as Record<string, unknown>[];
    s[0].title = "<img src=x onerror=alert(1)>";
    s[0].source = "<script>alert(1)</script>";
    (s[0].evidence as Record<string, unknown>[])[0].detail = "<iframe src=javascript:alert(1)>";
    const { container } = await renderReport(report, "/tests");
    expect(container.querySelector("img, script, iframe")).toBeNull();
    expect(sectionOf(container).textContent).toContain("<img src=x onerror=alert(1)>");
  });
});

describe("the records of the test sources", () => {
  it("are blocking records that block suggestions only: the banner, the tab count and the overview count the selection", async () => {
    const { container } = await renderReport("sources", "/records");
    const items = [...container.querySelectorAll("[data-scope]")] as HTMLElement[];
    const chips = items.filter((i) => i.querySelector('[data-chip="suggestions-only"]') !== null);
    expect(chips.length).toBe(5);
    expect(items.filter((i) => i.getAttribute("data-blocking") === "true").length).toBe(2);
    expect(screen.getByText("A blocking record of a test source means the list of suggested cases may be incomplete. The areas come from the code and are not affected.")).toBeTruthy();
    expect(screen.queryByText("A blocking record means the lists in this report may be incomplete.")).toBeNull();
    // the red badge is for a record that blocks the selection: none does
    expect(container.querySelector(".rm-record-blocking")).toBeNull();
    const banner = container.querySelector('[data-banner="incomplete"]');
    if (banner !== null) {
      expect(banner.textContent).not.toContain("blocking record");
    }
    const tab = screen.getByRole("tab", { name: /Records/ });
    expect(tab.querySelector(".mantine-Badge-root")?.getAttribute("data-variant")).toBe("default");
  });

  it("keep the red for a record that blocks the selection, and say both things when both exist", async () => {
    const report = fixture("sources");
    (report.incompleteness as unknown[]).push({ kind: "run_failed", subject: "x", not_established: "The analysis of x was not completed, because the run failed", detail: "", blocks_selection: true, scope: "selection", blocks_suggestions: false });
    const { container } = await renderReport(report, "/records");
    expect(container.querySelectorAll(".rm-record-blocking").length).toBe(1);
    expect(screen.getByText("A blocking record means the lists in this report may be incomplete.")).toBeTruthy();
    expect(screen.getByText("A blocking record of a test source means the list of suggested cases may be incomplete. The areas come from the code and are not affected.")).toBeTruthy();
  });

  it("show on the overview a count of their own, apart from the blocking records of the selection", async () => {
    const { container } = await renderReport("sources", "");
    const small = container.querySelector(".rm-counts-small") as HTMLElement;
    expect(small.textContent).toContain("Blocking records: 0");
    expect(small.textContent).toContain("Blocking records about suggestions: 2");
    expect(container.textContent).toContain("Records about the suggested cases are on the Tests tab and in the records.");
  });

  it("do not show a count of their own when no test source made one", async () => {
    const { container } = await renderReport("s13", "");
    expect((container.querySelector(".rm-counts-small") as HTMLElement).textContent).not.toContain("about suggestions");
  });
});

describe("a report written before test sources said what their records were about", () => {
  it("reads a record of a test source that blocked selection as blocking suggestions", () => {
    const report = fixture("s13");
    report.incompleteness = [
      { kind: "source_unavailable", subject: "qase", not_established: "Suggestions from qase were not looked up, because the source did not answer", detail: "", blocks_selection: true },
      { kind: "hub_cutoff", subject: "x", not_established: "What imports x was not established, because the walk stopped at this hub", detail: "", blocks_selection: true },
    ] as Report["incompleteness"];
    delete (report as { counts?: unknown }).counts;
    const view = build(report);
    expect(view.records.map((r) => [r.kind, r.blocking, r.blocksSuggestions, r.scope])).toEqual([
      ["source_unavailable", false, true, "suggestions"], // blocking of either scope comes first, in the order of the report
      ["hub_cutoff", true, false, "selection"],
    ]);
    expect(view.recordsFirst).toBe(true);
    expect(Object.fromEntries(view.header.counts ?? [])).toMatchObject({ records: 2, blocking_records: 1, blocking_suggestion_records: 1 });
    expect(view.suggested.state).toBe("unavailable");
  });
});

describe("the suggestions in the drawer of an area", () => {
  it("lists the cases suggested for that area, with the spec line", async () => {
    const { report } = await renderReport("sources", "/areas/" + ((fixture("sources").areas ?? [])[0].id as string));
    const drawer = await screen.findByRole("dialog");
    const box = drawer.querySelector("[data-suggested-for]") as HTMLElement;
    expect(box.textContent).toContain("billing.spec.ts:5");
    expect(box.textContent).toContain("Spec line:");
    expect(box.textContent).not.toContain("Q-12");
    expect(report.areas?.length).toBeGreaterThan(1);
  });

  it("says it is not known when a source was not read completely and nothing was suggested for the area", async () => {
    const id = (fixture("sources_unavailable").areas ?? [])[0].id as string;
    await renderReport("sources_unavailable", `/areas/${id}`);
    const drawer = await screen.findByRole("dialog");
    const text = (drawer.querySelector("[data-suggested-for]") as HTMLElement).textContent ?? "";
    expect(text).toContain("Whether a case would be suggested for this area is not known");
    expect(text).not.toContain("None suggested");
  });

  it("says none was suggested for an area only when every source was read completely", async () => {
    const report = fixture("s13");
    (report.inputs as Record<string, unknown>).test_sources = [{ source: "qase", kind: "qase", state: "read", read: true }];
    (report.inputs as Record<string, unknown>).test_source_read = true;
    await renderReport(report, `/areas/${(report.areas ?? [])[0].id as string}`);
    const drawer = await screen.findByRole("dialog");
    expect(within(drawer).getByText("None suggested for this area.")).toBeTruthy();
  });

  it("has no section for the suggestions when no test source was read", async () => {
    await renderReport("s13", `/areas/${(fixture("s13").areas ?? [])[0].id as string}`);
    const drawer = await screen.findByRole("dialog");
    expect(drawer.querySelector("[data-suggested-for]")).toBeNull();
    expect(pageText()).not.toContain("None suggested");
  });
});
