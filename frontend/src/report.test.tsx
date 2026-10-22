import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "./api/client";
import type { Report } from "./api/types";
import { fakeClient } from "./test/fakeClient";
import { fixture, NAMES } from "./test/fixtures";
import { expectOwnWordsClean, expectPageWordsClean, headings, pageText, renderAt, renderReport } from "./test/render";
import { build } from "./report/view";

const TABS = ["Overview", "Areas", "Findings", "Branches", "Cross-repo", "Tests", "Records", "For the author"];

describe("a report: the frame and the tabs", () => {
  it("has a header card, tabs in the order a QA reader needs them, and the tab of the address selected", async () => {
    const { container, router } = await renderReport("s13");
    const tabs = screen.getAllByRole("tab").map((t) => (t.textContent ?? "").replace(/\d+$/, ""));
    expect(tabs).toEqual(TABS);
    expect(screen.getByRole("tab", { name: /Overview/ }).getAttribute("aria-selected")).toBe("true");
    expect(headings(container, 1)[0]).toContain("acme/api");
    expect(router.state.location.pathname).toBe(`/reports/${"acme-api-compare-main-x-0123abcd-0123456789ab-r1"}`);
  });

  it("keeps the tab in the address: a click goes to its path, a path selects its tab, and the back button comes back", async () => {
    const { router } = await renderReport("s13");
    await userEvent.click(screen.getByRole("tab", { name: /Findings/ }));
    expect(router.state.location.pathname.endsWith("/findings")).toBe(true);
    expect(screen.getByRole("tab", { name: /Findings/ }).getAttribute("aria-selected")).toBe("true");
    expect(await screen.findByRole("heading", { level: 2, name: /direct/ })).toBeTruthy();
    await userEvent.click(screen.getByRole("tab", { name: /Branches/ }));
    expect(router.state.location.pathname.endsWith("/branches")).toBe(true);
    await act(async () => {
      await router.navigate(-1);
    });
    await waitFor(() => expect(screen.getByRole("tab", { name: /Findings/ }).getAttribute("aria-selected")).toBe("true"));
  });

  it("opens the tab an address names, and says so for a tab or an id that is not there", async () => {
    for (const tail of ["/areas", "/findings", "/branches", "/cross-repo", "/tests", "/records", "/author"]) {
      const { unmount } = await renderReport("s13", tail);
      const name = { "/cross-repo": "Cross-repo", "/author": "For the author" }[tail] ?? tail.slice(1).replace(/^./, (c) => c.toUpperCase());
      expect(screen.getByRole("tab", { name: new RegExp(`^${name}`) }).getAttribute("aria-selected"), tail).toBe("true");
      unmount();
    }
    for (const tail of ["/nothing", "/areas/not-an-id", "/areas/f-0123456789abcdef", "/findings/a-0123456789abcdef", "/areas/a-0123456789abcdef/extra", "/notes"]) {
      const client = (await import("./test/render")).runnerWith("x-r1", fixture("s13"));
      const view = renderAt(client, `/reports/x-r1${tail}`);
      const heading = await screen.findByRole("heading", { level: 1 });
      expect(heading.textContent, tail).toMatch(/Page not found|acme\/api/);
      view.unmount();
    }
  });

  it("has a Run notes tab only when the report has notes", async () => {
    const without = await renderReport("s13");
    expect(screen.queryByRole("tab", { name: /Run notes/ })).toBeNull();
    without.unmount();
    const report = fixture("s13");
    report.notes = ["the run read 3 files", "<b>bold</b> note"];
    await renderReport(report, "/notes");
    expect(screen.getByRole("tab", { name: /Run notes/ }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByText("<b>bold</b> note")).toBeTruthy();
    expect(document.querySelector("b")).toBeNull();
  });

  it("has one h1, landmarks, headings that never skip a level and tables with headers", async () => {
    for (const tail of ["", "/areas", "/findings", "/branches", "/records", "/author"]) {
      const { container, unmount } = await renderReport("complete", tail);
      expect(headings(container, 1).length, tail).toBe(1);
      const levels = [...container.querySelectorAll("h1,h2,h3,h4")].map((h) => Number(h.tagName[1]));
      levels.forEach((level, i) => {
        if (i > 0) {
          expect(level, tail).toBeLessThanOrEqual(levels[i - 1] + 1);
        }
      });
      expect(screen.getByRole("main")).toBeTruthy();
      expect(screen.getByRole("banner")).toBeTruthy();
      expect(screen.getByRole("navigation", { name: "Workspace" })).toBeTruthy();
      for (const table of container.querySelectorAll("table")) {
        expect(table.querySelectorAll("thead th").length, tail).toBeGreaterThan(0);
      }
      unmount();
    }
  });
});

describe("a report: the header and the banner", () => {
  it("shows the commits with a short id, the whole one in the title and a button that copies the whole one", async () => {
    const { container, report } = await renderReport("s13");
    const head = report.change.head_sha as string;
    const code = container.querySelector(`code[title="${head}"]`) as HTMLElement;
    expect(code.textContent).toBe(head.slice(0, 12));
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    await userEvent.click(within(code.closest(".rm-meta") as HTMLElement).getByRole("button", { name: "Copy commit id" }));
    expect(writeText).toHaveBeenCalledWith(head);
    expect(await screen.findByText("Copied")).toBeTruthy();
  });

  it("says when the commit id could not be copied", async () => {
    await renderReport("s13");
    Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true });
    await userEvent.click(screen.getAllByRole("button", { name: "Copy commit id" })[0]);
    expect(await screen.findAllByText("Could not copy")).toBeTruthy();
  });

  it("puts a banner above the tabs, with a link to the records, when a record blocks selection", async () => {
    const { container } = await renderReport("complete");
    const banner = container.querySelector('[data-banner="incomplete"]') as HTMLElement;
    expect(banner.textContent).toContain("1 blocking record");
    expect(banner.textContent).toContain("The lists in this report may be incomplete");
    const link = within(banner).getByRole("link", { name: "See what the run could not establish" });
    expect(link.getAttribute("href")).toMatch(/\/records$/);
    const text = pageText();
    expect(text).not.toMatch(/nothing to test|complete coverage|no impact/i);
  });

  it("has no such banner for a run that read everything it set out to read", async () => {
    const report = fixture("s13");
    report.incompleteness = [];
    report.propagations![0] = { ...(report.propagations![0] as object), complete: true, reasons: [] };
    const { container } = await renderReport(report);
    expect(container.querySelector('[data-banner="incomplete"]')).toBeNull();
  });

  it("shows a run that did not complete with its header, a banner and its records, and no count, no tab, no list, no form", async () => {
    for (const [name, words] of [["failed", "The run failed."], ["timed_out", "The run timed out."]] as const) {
      const { container, unmount } = await renderReport(name);
      expect(container.textContent).toContain(words);
      expect(screen.queryAllByRole("tab")).toEqual([]);
      expect(container.querySelector("table")).toBeNull();
      expect(container.querySelector("form")).toBeNull();
      expect(headings(container, 2)).toEqual(["What the run could not establish"]);
      expect(container.textContent).not.toMatch(/Must test|Areas to test|Findings by kind|Candidates/);
      expect(container.querySelectorAll(".rm-stat").length).toBe(0);
      expect(build(fixture(name)).header.counts).toBeNull();
      expectPageWordsClean();
      unmount();
    }
  });

  it("sends a tab of a run that did not complete to its records", async () => {
    const { router } = await renderReport("failed", "/areas");
    expect(router.state.location.pathname.endsWith("/areas")).toBe(false);
    expect(screen.queryAllByRole("tab")).toEqual([]);
  });

  it("shows a status and a fixed message when the report cannot be read, and a message when it is not a report", async () => {
    const failing = fakeClient({ report: () => { throw new ApiError(404, "not_found"); } });
    const first = renderAt(failing, `/reports/${NAME_FOR_ERRORS}`);
    expect((await screen.findByRole("alert")).textContent).toContain("Status 404");
    first.unmount();
    const odd = fakeClient({ report: () => ({ schema: 2 }) as unknown as Report });
    renderAt(odd, `/reports/${NAME_FOR_ERRORS}`);
    expect((await screen.findByRole("alert")).textContent).toContain("not a report this page reads");
  });

  it("shows a page that says so for a name that cannot be a report", async () => {
    renderAt(fakeClient(), "/reports/..%2fsecret");
    expect(await screen.findByRole("heading", { level: 1, name: "Page not found" })).toBeTruthy();
    renderAt(fakeClient(), "/nowhere/at/all");
    expect((await screen.findAllByRole("heading", { level: 1, name: "Page not found" })).length).toBe(2);
  });
});

const NAME_FOR_ERRORS = "x-r1";

describe("the overview", () => {
  it("shows the counts of a completed run, risk and confidence as two separate distributions, never one scale", async () => {
    const { container } = await renderReport("complete");
    const stats = [...container.querySelectorAll(".rm-stat")].map((c) => c.textContent);
    expect(stats).toEqual(["Must test2", "Sample1", "Candidates, matched by name1", "Findings10"]);
    const risk = within(container).getByText("Risk", { selector: ".rm-distribution *" }).closest(".rm-distribution") as HTMLElement;
    const confidence = within(container).getByText("Confidence", { selector: ".rm-distribution *" }).closest(".rm-distribution") as HTMLElement;
    expect(risk).not.toBe(confidence);
    expect(risk.textContent).toContain("A different scale from confidence");
    expect(confidence.textContent).toContain("A different scale from risk");
    // the two bars are made of different colours, so that a high risk is never read as a high confidence
    expect(risk.innerHTML).not.toBe(confidence.innerHTML);
    expect(container.textContent).toContain("Findings by kind");
  });

  it("shows how far the decisions have got as a bar of its own, and says it changes no count", async () => {
    const { container } = await renderReport("s13");
    const card = within(container).getByText("Decisions", { selector: "p" }).closest(".mantine-Card-root") as HTMLElement;
    expect(card.textContent).toContain("A decision is risk acceptance. It changes no count, priority or gap in this report.");
    expect(card.textContent).toContain("Not decided");
    expect(card.querySelector('[role="progressbar"], .mantine-Progress-root')).not.toBeNull();
  });

  it("does not offer decisions for a report that has no place among the revisions of its change", async () => {
    const report = fixture("complete");
    const { container } = await renderReport(report);
    expect(container.textContent).toContain("Decisions cannot be recorded for this report.");
  });

  it("lists the blocking records first, with a link to all of them", async () => {
    const { container } = await renderReport("complete");
    const card = within(container).getByText("Blocking records", { selector: "p" }).closest(".mantine-Card-root") as HTMLElement;
    expect(card.querySelectorAll('[data-blocking="true"]').length).toBe(1);
    expect(within(card).getByRole("link", { name: "All records" }).getAttribute("href")).toMatch(/\/records$/);
  });

  it("says no blocking record was made without saying the lists are complete", async () => {
    const { container } = await renderReport("s13");
    expect(container.textContent).toContain("No blocking record was made by this run. That does not say the lists are complete.");
  });

  it("draws what the change reaches as a graph with the repositories as groups, loaded when the page needs it", async () => {
    const { container } = await renderReport("complete");
    const graph = await within(container).findByRole("group", { name: "What this change reaches" });
    expect(graph.querySelector(".react-flow")).not.toBeNull();
    expect(graph.textContent).toContain("acme/api");
    expect(graph.querySelectorAll(".rm-repo").length).toBeGreaterThan(0);
    expect(graph.querySelectorAll(".rm-area").length).toBeGreaterThan(0);
    expect(graph.querySelector(".rm-area-candidate")).not.toBeNull();
  });
});

describe("the areas", () => {
  it("groups the areas by priority, with the candidates labelled as such and counted nowhere as areas", async () => {
    const { container } = await renderReport("complete", "/areas");
    expect(headings(container, 2)).toEqual(expect.arrayContaining(["Must test", "Sample", "Candidates"]));
    const must = within(container).getByRole("heading", { level: 2, name: "Must test" }).closest("section") as HTMLElement;
    expect(must.querySelectorAll("tbody tr").length).toBe(2);
    const candidates = within(container).getByRole("heading", { level: 2, name: "Candidates" }).closest("section") as HTMLElement;
    expect(candidates.textContent).toContain("matched by name, not import-backed");
    expect(candidates.querySelector(".rm-dashed")).not.toBeNull();
  });

  it("labels an area matched by name as a candidate whatever priority the report gives it", async () => {
    const report = fixture("s13");
    const area = (report.areas ?? [])[0];
    area.by_name = true;
    area.priority = "must_test";
    const { container, unmount } = await renderReport(report, "/areas");
    const candidates = within(container).getByRole("heading", { level: 2, name: "Candidates" }).closest("section") as HTMLElement;
    expect(candidates.textContent).toContain(area.label);
    expect(within(container).queryByRole("heading", { level: 3, name: "Must test" })).toBeNull();
    unmount();
    await renderReport(report, `/areas/${area.id}`);
    expect(pageText()).toContain("Priority:candidate, matched by name, not import-backed");
    expect(pageText()).not.toContain("Priority:must test");
  });

  it("filters by repository, kind, decision state and text, and says when nothing matches", async () => {
    const report = fixture("unordered");
    const { container } = await renderReport(report, "/areas");
    const rows = () => container.querySelectorAll("tbody tr").length;
    const all = rows();
    expect(all).toBe((report.areas ?? []).length);
    await userEvent.selectOptions(screen.getByLabelText("Repository"), "acme/zeta");
    expect(rows()).toBe(1);
    await userEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(rows()).toBe(all);
    await userEvent.type(screen.getByLabelText("Search areas"), "sample-a");
    expect(rows()).toBe(1);
    await userEvent.clear(screen.getByLabelText("Search areas"));
    await userEvent.selectOptions(screen.getByLabelText("Kind"), "screen");
    expect(rows()).toBeLessThan(all);
    await userEvent.type(screen.getByLabelText("Search areas"), "no area is called this");
    expect(await screen.findByText("No area matches these filters.")).toBeTruthy();
  });

  it("orders the areas as the view model does, inside each priority", async () => {
    const { container } = await renderReport("unordered", "/areas");
    const must = within(container).getByRole("heading", { level: 2, name: "Must test" }).closest("section") as HTMLElement;
    const labels = [...must.querySelectorAll("tbody tr")].map((tr) => tr.querySelector("a")?.textContent);
    expect(labels).toEqual(build(fixture("unordered")).mustTest.map((a) => a.label));
  });

  it("says why there is no area when none was established", async () => {
    const report = fixture("complete");
    report.areas = [];
    await renderReport(report, "/areas");
    expect(screen.getByText("No areas")).toBeTruthy();
  });
});

describe("the findings", () => {
  it("shows kind, title, risk and confidence in separate columns with two different badge styles, and the exclusions apart", async () => {
    const { container } = await renderReport("complete", "/findings");
    expect(headings(container, 2)).toContain("Not reached (with evidence)");
    const first = container.querySelector("tbody tr") as HTMLElement;
    const risk = first.querySelector('[data-kind="risk"]') as HTMLElement;
    const confidence = first.querySelector('[data-kind="confidence"]') as HTMLElement;
    expect(risk).not.toBeNull();
    expect(confidence).not.toBeNull();
    expect(risk.closest("td")).not.toBe(confidence.closest("td"));
    expect(risk.getAttribute("data-variant")).not.toBe(confidence.getAttribute("data-variant"));
    // inside a group of one kind the column of the kind would say the same on every row: it is not there
    const heads = [...first.closest("table")!.querySelectorAll("thead th")].map((th) => th.textContent);
    expect(heads).toEqual(["Finding", "Risk", "Confidence", "Confidence basis", "Evidence", "Decision"]);
    const excluded = within(container).getByRole("heading", { level: 2, name: "Not reached (with evidence)" }).closest("section") as HTMLElement;
    expect(excluded.querySelectorAll("tbody tr").length).toBe(2);
    // the exclusions are of several kinds and keep the column
    expect([...excluded.querySelectorAll("thead th")].map((th) => th.textContent)).toEqual(["Finding", "Kind", "Risk", "Confidence", "Confidence basis", "Evidence", "Decision"]);
  });

  it("filters by kind, risk and confidence", async () => {
    const { container } = await renderReport("complete", "/findings");
    const total = container.querySelectorAll("tbody tr").length;
    await userEvent.selectOptions(screen.getByLabelText("Kind"), "direct");
    expect(container.querySelectorAll("tbody tr").length).toBeLessThan(total);
    await userEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    await userEvent.selectOptions(screen.getByLabelText("Risk"), "high");
    for (const row of container.querySelectorAll("tbody tr")) {
      expect(row.querySelector('[data-kind="risk"]')?.textContent).toBe("high");
    }
  });
});

describe("the branches", () => {
  it("shows a grid of branches by files with a state chip in each cell, and the lines the report cites when a cell is chosen", async () => {
    const { container } = await renderReport("s13", "/branches");
    const grid = container.querySelector(".rm-grid") as HTMLElement;
    const branches = [...grid.querySelectorAll("tbody th")].map((th) => th.textContent);
    expect(branches).toEqual(["main", "release/1.2", "release/1.x"]);
    const path = "services/billing/app/services/totals.py";
    expect(grid.querySelector("thead")?.textContent).toContain(path);
    const cells = [...grid.querySelectorAll(".rm-grid-cell")].map((c) => c.textContent);
    expect(cells[0]).toContain("pre_change");
    expect(cells[1]).toContain("present");
    await userEvent.click(within(grid).getByRole("button", { name: `Evidence for release/1.2 ${path}` }));
    const card = (await screen.findByRole("heading", { level: 3, name: "Evidence for" })).closest(".rm-pick") as HTMLElement;
    expect(card.textContent).toContain("ROUND_HALF_UP");
    expect(card.textContent).toContain(`${path}:6`);
  });

  it("says a cell has no state of its own when the report does not itemise it", async () => {
    const report = fixture("s13");
    report.change.files = [
      { path: "a.py", old_path: null, status: "modified", role: "source", added: 1, removed: 1 },
      { path: "b.py", old_path: null, status: "modified", role: "source", added: 1, removed: 1 },
    ];
    report.branches![1].detail = "free text the report did not itemise";
    const { container } = await renderReport(report, "/branches");
    expect(container.textContent).toContain("not itemised");
  });

  it("links the findings about a branch that is missing the change", async () => {
    const { container } = await renderReport("s13", "/branches");
    const row = container.querySelector('[data-branch="0"]') as HTMLElement;
    expect(within(row).getAllByRole("link").some((a) => /^F\d+$/.test(a.textContent ?? ""))).toBe(true);
  });

  it("says when no branch was compared", async () => {
    const report = fixture("s13");
    report.branches = [];
    await renderReport(report, "/branches");
    expect(screen.getByText("No branch of the repository was compared.")).toBeTruthy();
  });
});

describe("the cross-repository reach", () => {
  it("draws the repositories and the edges between them, and lists the same edges with the candidates apart", async () => {
    const { container } = await renderReport("s11", "/cross-repo");
    const graph = await within(container).findByRole("group", { name: "Repositories and the edges between them" });
    expect(graph.querySelectorAll(".rm-plain").length).toBeGreaterThan(1);
    const table = container.querySelector(".rm-edges") as HTMLElement;
    expect(table.querySelectorAll("tbody tr").length).toBeGreaterThan(0);
    expect(table.textContent).toContain("Pins and adoption per branch");
    expect(container.textContent).toContain("Backend calls");
  });

  it("marks what was matched by name as a candidate, dashed", async () => {
    const report = fixture("s11");
    const prop = report.propagations![0] as { contract_candidates: unknown[]; contract_reads: Record<string, unknown>[] };
    prop.contract_candidates = prop.contract_reads.map((r) => ({ ...r, repo: "acme/other" }));
    const { container } = await renderReport(report, "/cross-repo");
    const rows = [...container.querySelectorAll('.rm-edges tbody tr[data-candidate="true"]')];
    expect(rows.length).toBeGreaterThan(0);
    expect(rows[0].querySelector(".rm-dashed")).not.toBeNull();
  });

  it("says when no other repository was reached", async () => {
    const report = fixture("s13");
    const prop = report.propagations![0] as Record<string, unknown>;
    for (const key of ["hops", "consumers", "http_edges", "contract_changes", "contract_reads", "contract_candidates"]) {
      prop[key] = [];
    }
    await renderReport(report, "/cross-repo");
    expect(screen.getAllByText("No other repository was reached by this change.").length).toBeGreaterThan(0);
  });
});

describe("the records, the tests and the author", () => {
  it("puts the blocking records first, red, with what could not be established", async () => {
    const { container } = await renderReport("complete", "/records");
    const items = [...container.querySelectorAll(".rm-record")];
    expect(items[0].getAttribute("data-blocking")).toBe("true");
    expect(items[0].textContent).toContain("Not established:");
    expect(headings(container, 3)[0]).toBe("Blocking records");
    expect(headings(container, 2)).toEqual(["What the run could not establish"]);
  });

  it("labels suggestions as suggested, not verified coverage, apart from the mapped tests", async () => {
    const report = fixture("s13");
    report.suggestions = [{ source: "tcms", case_id: "C-1", title: "Round totals", matched_words: ["totals"] }];
    report.mapped_tests = [{ area_id: "a-0123456789abcdef", case_id: "C-9", title: "Mapped one", source: "tcms", mapping: "config" }];
    const { container } = await renderReport(report, "/tests");
    const h2 = headings(container, 2);
    expect(h2).toEqual(["Mapped tests", "Suggested, not verified coverage"]);
    const suggested = within(container).getByRole("heading", { name: "Suggested, not verified coverage" }).closest("section") as HTMLElement;
    expect(suggested.textContent).toContain("Round totals");
    expect(suggested.textContent).not.toContain("Mapped one");
    expect(suggested.textContent).toContain("not coverage");
    // rule 4.3: the two are different fields, each section holds its own and never the other's
    const mapped = within(container).getByRole("heading", { name: "Mapped tests" }).closest("section") as HTMLElement;
    expect(mapped.textContent).toContain("Mapped one");
    expect(mapped.textContent).not.toContain("Round totals");
    expect(mapped.querySelectorAll(".mantine-Card-root").length).toBe(1);
    expect(suggested.querySelectorAll(".mantine-Card-root").length).toBe(1);
    const view = build(report);
    expect(view.mapped.length).toBe(1);
    expect(view.suggested.items.length).toBe(1);
  });

  it("says that no test source was read when none was, and that none is configured when the record says so", async () => {
    const plain = await renderReport("s13", "/tests");
    expect(plain.container.textContent).toContain("No test source was read by this run.");
    plain.unmount();
    const report = fixture("s13");
    report.incompleteness.push({ kind: "source_unconfigured", subject: "tests", not_established: "No test source is set up.", detail: "", blocks_selection: false });
    const configured = await renderReport(report, "/tests");
    expect(configured.container.textContent).toContain("No test source is configured.");
  });

  it("shows the unit test commands of each repository as text that can be copied", async () => {
    const { container } = await renderReport("complete", "/author");
    expect(headings(container, 2)).toEqual(["Unit tests to run", "Other repositories reached", "Other branches", "Files beyond the diff"]);
    expect(container.querySelector("pre")).not.toBeNull();
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    await userEvent.click(within(container).getAllByRole("button", { name: "Copy" })[0]);
    expect(writeText).toHaveBeenCalledTimes(1);
  });
});

describe("the wording of the page", () => {
  it("never claims safety or coverage, on any tab, for any fixture", async () => {
    // the fixtures with model notes are walked on their own tab and in their drawers by notes.test.tsx (this loop was near its time limit)
    for (const name of NAMES.filter((n) => !n.startsWith("notes"))) {
      for (const tail of ["", "/areas", "/findings", "/branches", "/cross-repo", "/tests", "/records", "/author"]) {
        const { unmount } = await renderReport(name, tail);
        expectPageWordsClean();
        unmount();
      }
    }
    const jobs = renderAt(fakeClient(), "/jobs");
    await screen.findByRole("heading", { name: "Start an analysis" });
    expectOwnWordsClean(jobs.container);
  });

  it("checks that the check itself would catch a claim in the page's own words", async () => {
    const { container } = await renderReport("s13");
    const p = document.createElement("p");
    p.textContent = "The change is safe to release";
    container.appendChild(p);
    expect(() => expectOwnWordsClean(container)).toThrow();
  });
});
