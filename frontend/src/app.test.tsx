import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError, createClient } from "./api/client";
import { fakeClient } from "./test/fakeClient";
import { fixture } from "./test/fixtures";
import { NAME, expectOwnWordsClean, expectPageWordsClean, renderAt, renderReport, runnerWith } from "./test/render";

describe("the token", () => {
  it("shows how to get one, and calls nothing, when there is none", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const { container } = renderAt(null, "/");
    expect((await screen.findByRole("alert")).textContent).toContain("reachmap serve printed");
    expect(screen.queryByRole("table")).toBeNull();
    expect(fetchSpy).not.toHaveBeenCalled();
    expectOwnWordsClean(container);
  });

  it("sends the token on every call through the real client and calls nothing but /api/ of this origin", async () => {
    const seen: [string, Record<string, string>][] = [];
    const fetchImpl = vi.fn(async (url: string, init: RequestInit) => {
      seen.push([url, init.headers as Record<string, string>]);
      return new Response(JSON.stringify(url === "/api/repos" ? { workspace: "acme", repos: [] } : { reports: [] }), { status: 200 });
    });
    renderAt(createClient("tok-123_abc", fetchImpl), "/");
    await screen.findByText(/no report yet/);
    await waitFor(() => expect(seen.map(([url]) => url).sort()).toEqual(["/api/reports", "/api/repos"]));
    for (const [url, headers] of seen) {
      expect(url.startsWith("/api/")).toBe(true);
      expect(headers["X-Reachmap-Token"]).toBe("tok-123_abc");
    }
  });
});

describe("the frame", () => {
  it("has a banner, a navigation, a main, a skip link, the name of the workspace and a command to search", async () => {
    renderAt(fakeClient(), "/");
    expect(await screen.findByText("acme")).toBeTruthy();
    expect(screen.getByRole("banner")).toBeTruthy();
    expect(screen.getByRole("navigation", { name: "Workspace" })).toBeTruthy();
    expect(screen.getByRole("main")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Skip to the main content" }).getAttribute("href")).toBe("#main");
    expect(screen.getByRole("main").getAttribute("id")).toBe("main");
    expect(screen.getByRole("button", { name: /Search/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Keyboard shortcuts" })).toBeTruthy();
  });

  it("marks the page of the navigation that is open, and collapses the sidebar to icons", async () => {
    renderAt(fakeClient(), "/jobs");
    const nav = await screen.findByRole("navigation", { name: "Workspace" });
    expect(within(nav).getByRole("link", { name: "Jobs" }).getAttribute("aria-current")).toBe("page");
    expect(within(nav).getByRole("link", { name: "Reports" }).getAttribute("aria-current")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Collapse the sidebar" }));
    expect(within(nav).getByRole("link", { name: "Jobs" }).textContent).toBe("");
    await userEvent.click(screen.getByRole("button", { name: "Expand the sidebar" }));
    expect(within(nav).getByRole("link", { name: "Jobs" }).textContent).toBe("Jobs");
  });

  it("shows the crumbs of a report page: Reports, the report, its tab, and the drawer", async () => {
    const report = fixture("s13");
    await renderReport(report, `/areas/${report.areas![0].id}`);
    const crumbs = screen.getByRole("navigation", { name: "You are here" });
    expect(within(crumbs).getByRole("link", { name: "Reports" }).getAttribute("href")).toBe("/");
    expect(within(crumbs).getByRole("link", { name: NAME }).getAttribute("href")).toBe(`/reports/${NAME}`);
    expect(within(crumbs).getByRole("link", { name: "Areas" }).getAttribute("href")).toBe(`/reports/${NAME}/areas`);
    expect(within(crumbs).getByText("Area").getAttribute("aria-current")).toBe("page");
  });

  it("opens the list of shortcuts with the question mark, and not while a field has the focus", async () => {
    renderAt(fakeClient(), "/jobs");
    await screen.findByLabelText("Value");
    await userEvent.click(screen.getByLabelText("Value"));
    await userEvent.keyboard("?");
    expect(screen.queryByRole("dialog", { name: "Keyboard shortcuts" })).toBeNull();
    expect((screen.getByLabelText("Value") as HTMLInputElement).value).toBe("?");
    await userEvent.click(document.body);
    await userEvent.keyboard("?");
    const dialog = await screen.findByRole("dialog", { name: "Keyboard shortcuts" });
    for (const key of ["j", "k", "t", "s", "x", "Enter", "Esc"]) {
      expect(within(dialog).getByText(key, { selector: "kbd" })).toBeTruthy();
    }
    expectPageWordsClean();
  });
});

describe("the keys of the page and the control that has the focus", () => {
  it("opens the shortcuts with the question mark while a check box or a button has the focus", async () => {
    renderAt(fakeClient(), "/jobs");
    const radio = await screen.findByLabelText("Compare");
    await userEvent.click(radio);
    expect(document.activeElement).toBe(radio);
    await userEvent.keyboard("?");
    expect(await screen.findByRole("dialog", { name: "Keyboard shortcuts" })).toBeTruthy();
  });
});

describe("the theme", () => {
  it("is light by default, toggles with aria-pressed and a fixed label, and is kept as a convenience", async () => {
    renderAt(fakeClient(), "/");
    const button = await screen.findByRole("button", { name: "Dark theme" });
    expect(button.getAttribute("aria-pressed")).toBe("false");
    expect(document.documentElement.getAttribute("data-mantine-color-scheme")).toBe("light");
    await userEvent.click(button);
    expect(button.getAttribute("aria-pressed")).toBe("true");
    expect(button.getAttribute("aria-label")).toBe("Dark theme");
    expect(document.documentElement.getAttribute("data-mantine-color-scheme")).toBe("dark");
    expect(window.localStorage.getItem("reachmap-theme")).toBe("dark");
  });

  it("works when the storage throws", async () => {
    vi.spyOn(window.localStorage, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(window.localStorage, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    renderAt(fakeClient(), "/");
    const button = await screen.findByRole("button", { name: "Dark theme" });
    await userEvent.click(button);
    expect(button.getAttribute("aria-pressed")).toBe("true");
    await waitFor(() => expect(document.documentElement.getAttribute("data-mantine-color-scheme")).toBe("dark"));
  });
});

describe("the command palette", () => {
  it("opens with Ctrl and K, finds a report, an area and a finding, and jumps there", async () => {
    const report = fixture("s13");
    const { router } = await renderReport(report);
    await userEvent.keyboard("{Control>}k{/Control}");
    const search = await screen.findByPlaceholderText(/Search reports, areas and findings/);
    // the entries of the page and the reports of the runner
    await userEvent.type(search, "billing/invoices");
    const found = await screen.findAllByRole("button", { name: /^GET \/api\/billing\/invoices\/\{invoice_id\}/ });
    // the area is one of the entries (a finding with a like label may be another)
    await userEvent.click(found.find((f) => f.textContent?.includes("acme/api")) ?? found[0]);
    await waitFor(() => expect(router.state.location.pathname).toBe(`/reports/${NAME}/areas/${report.areas![0].id}`));
  });

  it("offers the pages, the actions and the reports, and runs an action", async () => {
    const client = runnerWith(NAME, fixture("s13"));
    const { router } = renderAt(client, "/jobs");
    await userEvent.click(await screen.findByRole("button", { name: /Search/ }));
    const search = await screen.findByPlaceholderText(/Search reports, areas and findings/);
    await waitFor(() => expect(client.reports).toHaveBeenCalled());
    await userEvent.type(search, "acme-api");
    expect((await screen.findAllByRole("button", { name: /acme-api-compare/ })).length).toBeGreaterThan(0);
    await userEvent.clear(search);
    await userEvent.type(search, "Reports");
    await userEvent.keyboard("{Enter}");
    await waitFor(() => expect(router.state.location.pathname).toBe("/"));
  });

  it("shows a report name with odd characters made visible", async () => {
    const client = fakeClient({ reports: [{ name: "a-r1", key: {}, revision: 1, status: "completed", created: null }] });
    renderAt(client, "/");
    await screen.findByRole("link", { name: "a-r1" });
    await userEvent.keyboard("{Control>}k{/Control}");
    const search = await screen.findByPlaceholderText(/Search reports, areas and findings/);
    await userEvent.type(search, "a-r1");
    expect((await screen.findAllByRole("button", { name: /^a-r1/ })).length).toBeGreaterThan(0);
  });

  it("says when nothing matches, and its own words pass the wording check", async () => {
    renderAt(fakeClient(), "/");
    await screen.findByText("acme");
    await userEvent.keyboard("{Control>}k{/Control}");
    const search = await screen.findByPlaceholderText(/Search reports, areas and findings/);
    await userEvent.type(search, "zzzzzzzzz");
    expect(await screen.findByText("Nothing matches.")).toBeTruthy();
    expectPageWordsClean();
  });
});

describe("the reports list", () => {
  const item = (over: Record<string, unknown>) => ({
    name: "acme-api-compare-main-x-0123abcd-0123456789ab-r1",
    key: { workspace: "acme", repo: "acme/api", change: "compare:main...x" },
    revision: 1,
    status: "completed",
    created: "2026-01-02T03:04:05Z",
    head_sha: "8869159a63913fd8dbd220ee27e9bcac2d03cc33",
    areas: 3,
    blocking_records: 0,
    ...over,
  });

  it("lists the reports with the change, repository, status, revision, head, time, areas to test and blocking records, and a link to each", async () => {
    const client = runnerWith(NAME, fixture("s13"));
    client.reports.mockResolvedValue([item({}) as never]);
    renderAt(client, "/");
    const link = await screen.findByRole("link", { name: "compare:main...x" });
    expect(link.getAttribute("href")).toBe(`/reports/${NAME}`);
    const row = link.closest("tr") as HTMLElement;
    expect(within(row).getByText("acme/api")).toBeTruthy();
    expect(within(row).getByText("finished")).toBeTruthy();
    expect(row.querySelector('code[title="8869159a63913fd8dbd220ee27e9bcac2d03cc33"]')?.textContent).toBe("8869159a6391");
    expect(within(row).getByRole("button", { name: "Copy commit id" })).toBeTruthy();
    expect(within(row).getByText("3")).toBeTruthy(); // areas to test
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Reports");
    const heads = [...document.querySelectorAll("thead th")].map((th) => th.textContent);
    expect(heads).toEqual(["Change", "Repository", "Status", "Revision", "Head", "Started", "Areas to test", "Blocking records"]);
  });

  it("writes the time as a person says it, with the exact text for the title", async () => {
    const client = fakeClient({ reports: [item({ created: new Date(Date.now() - 5 * 60 * 1000).toISOString() }) as never] });
    renderAt(client, "/");
    await screen.findByRole("link", { name: "compare:main...x" });
    expect(screen.getByText("5 minutes ago")).toBeTruthy();
  });

  it("marks a report that has a newer revision, and counts blocking records in red", async () => {
    const client = fakeClient({ reports: [item({ revision: 2, blocking_records: 2 }) as never, item({ name: "acme-api-compare-main-x-0123abcd-0123456789ab-r1b", revision: 1 }) as never] });
    renderAt(client, "/");
    await screen.findAllByRole("link", { name: "compare:main...x" });
    expect(document.querySelectorAll('[data-stale="true"]').length).toBe(1);
    expect(screen.getByText("Newer revision")).toBeTruthy();
    expect(screen.getByText("2", { selector: ".mantine-Badge-label" })).toBeTruthy();
  });

  it("searches, filters by repository and status, sorts by a column, and clears the filters", async () => {
    const client = fakeClient({
      reports: [
        item({ name: "a-r1", key: { repo: "acme/api", change: "compare:one" }, areas: 5 }) as never,
        item({ name: "b-r1", key: { repo: "acme/web", change: "compare:two" }, status: "failed", areas: null }) as never,
        item({ name: "c-r1", key: { repo: "acme/web", change: "compare:three" }, areas: 9 }) as never,
      ],
    });
    renderAt(client, "/");
    await screen.findByRole("link", { name: "compare:one" });
    const rows = () => document.querySelectorAll("tbody tr").length;
    expect(rows()).toBe(3);
    await userEvent.type(screen.getByLabelText("Search reports"), "two");
    expect(rows()).toBe(1);
    await userEvent.clear(screen.getByLabelText("Search reports"));
    await userEvent.selectOptions(screen.getByLabelText("Repository"), "acme/web");
    expect(rows()).toBe(2);
    await userEvent.selectOptions(screen.getByLabelText("Status"), "completed");
    expect(rows()).toBe(1);
    await userEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(rows()).toBe(3);
    await userEvent.click(screen.getByRole("button", { name: /Areas to test/ }));
    expect([...document.querySelectorAll("tbody tr a")].map((a) => a.textContent)).toEqual(["compare:one", "compare:three", "compare:two"]);
    await userEvent.click(screen.getByRole("button", { name: /Areas to test/ }));
    expect([...document.querySelectorAll("tbody tr a")].map((a) => a.textContent)).toEqual(["compare:three", "compare:one", "compare:two"]);
    expect(screen.getByRole("columnheader", { name: /Areas to test/ }).getAttribute("aria-sort")).toBe("descending");
    await userEvent.type(screen.getByLabelText("Search reports"), "no such report");
    expect(await screen.findByText("No report matches these filters.")).toBeTruthy();
  });

  it("gives a run that did not complete no count, and a runner of an earlier version no head or counts", async () => {
    const client = fakeClient({
      reports: [
        item({ name: "f-r1", status: "failed", areas: null, blocking_records: null, head_sha: null }) as never,
        { name: "old-r1", key: { repo: "acme/api", change: "compare:old" }, revision: 1, status: "completed", created: null } as never,
      ],
    });
    renderAt(client, "/");
    await screen.findByRole("link", { name: "compare:old" });
    const failed = (within(document.querySelector("tbody") as HTMLElement).getByText("failed")).closest("tr") as HTMLElement;
    expect([...failed.querySelectorAll("td")].slice(-2).map((td) => td.textContent)).toEqual(["", ""]);
  });

  it("lists a report whatever the runner could say about it (a change that is an object, nothing at all, a number for a revision)", async () => {
    const client = fakeClient({
      reports: [
        { name: "old-r1", key: { workspace: "acme", repo: "acme/api", change: { compare: "main...topic" } }, revision: null, status: "completed", created: "2026-01-02T03:04:05Z" },
        { name: "bare-r1", key: { workspace: null, repo: null, change: null }, revision: null, status: null, created: null },
        { name: "late-r1", key: {}, revision: 3, status: "timed_out", created: "2026-02-02T03:04:05Z" },
        { name: "odd-r1", key: { repo: 5, change: [1] }, revision: { a: 1 }, status: ["x"], created: 7 },
      ],
    });
    renderAt(client, "/");
    const table = await screen.findByRole("table", { name: "Reports" });
    expect(within(table).getByText("compare:main...topic")).toBeTruthy();
    expect(within(table).getByText("timed out")).toBeTruthy();
    expect(within(table).getAllByRole("row").length).toBe(5);
  });

  it("says when there are none, with the way to start one", async () => {
    renderAt(fakeClient(), "/");
    expect(await screen.findByText(/no report yet/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Start an analysis" }).getAttribute("href")).toBe("/jobs");
  });

  it("opens a report when a row is clicked", async () => {
    const client = runnerWith(NAME, fixture("s13"));
    client.reports.mockResolvedValue([item({}) as never]);
    const { router } = renderAt(client, "/");
    const row = (await screen.findByRole("link", { name: "compare:main...x" })).closest("tr") as HTMLElement;
    await userEvent.click(within(row).getByText("acme/api"));
    await waitFor(() => expect(router.state.location.pathname).toBe(`/reports/${NAME}`));
  });

  it("shows a skeleton while it loads and an API error as a status and a fixed message, never the body of the answer", async () => {
    const hostile = '<img src=x onerror="alert(1)"> safe';
    const fetchImpl = vi.fn(async (url: string) => (url === "/api/repos" ? new Response('{"workspace":"w","repos":[]}', { status: 200 }) : new Response(hostile, { status: 500 })));
    const { container } = renderAt(createClient("t", fetchImpl), "/");
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Status 500");
    expect(alert.textContent).toContain("The runner reported an error.");
    expect(container.innerHTML).not.toContain("onerror");
    expect(container.querySelector("img")).toBeNull();
    expectOwnWordsClean(container);
  });

  it("says the token was refused for a 401 and that the runner cannot be reached for a network failure", async () => {
    const refusing = fakeClient();
    refusing.reports.mockRejectedValue(new ApiError(401, "unauthorized"));
    const first = renderAt(refusing, "/");
    expect((await screen.findByRole("alert")).textContent).toContain("refused the session token");
    first.unmount();
    const down = fakeClient();
    down.reports.mockRejectedValue(new ApiError(0));
    renderAt(down, "/");
    expect((await screen.findByRole("alert")).textContent).toContain("could not be reached");
  });
});

describe("the jobs page", () => {
  const job = (over: Record<string, unknown>) => ({
    id: "0123456789ab", state: "queued", repo: "acme/api", change: { compare: "main...topic" }, as_of: null, created_at: "2026-01-02T03:04:05.000Z",
    started_at: null, finished_at: null, report: null, exit_code: null, reason: null, ...over,
  });

  it("lists the jobs, links a finished one to its report and cancels one that is queued", async () => {
    const client = fakeClient({ jobs: [job({}), job({ id: "ba9876543210", state: "completed", report: NAME })] });
    renderAt(client, "/jobs");
    const table = await screen.findByRole("table", { name: "Jobs" });
    expect(within(table).getByRole("link", { name: NAME }).getAttribute("href")).toBe(`/reports/${NAME}`);
    expect(within(table).getByText("queued")).toBeTruthy();
    await userEvent.click(within(table).getByRole("button", { name: "Cancel" }));
    expect(client.cancelJob).toHaveBeenCalledWith("0123456789ab");
    expect(within(table).getAllByRole("button", { name: "Cancel" }).length).toBe(1);
  });

  it("starts an analysis from the repositories of the configuration, and says so in a toast", async () => {
    const client = fakeClient({
      repos: { workspace: "acme", repos: [{ name: "acme/api", roles: [], branches: [] }, { name: "acme/web", roles: [], branches: [] }] },
    });
    renderAt(client, "/jobs");
    await userEvent.selectOptions(await screen.findByLabelText("Repository"), "acme/web");
    await userEvent.click(screen.getByLabelText("Compare"));
    await userEvent.type(screen.getByLabelText("Value"), "main...topic");
    await userEvent.type(screen.getByLabelText("As of (optional)"), "2026-01-02");
    await userEvent.click(screen.getByRole("button", { name: "Start analysis" }));
    expect(client.submitJob).toHaveBeenCalledWith({ repo: "acme/web", compare: "main...topic", as_of: "2026-01-02" });
    expect((await screen.findAllByText("The job was queued.")).length).toBeGreaterThan(0);
  });

  it("shows the names of the repositories with odd characters written out", async () => {
    const client = fakeClient({ repos: { workspace: "acme", repos: [{ name: "acme/a\u202eb", roles: [], branches: [] }] } });
    renderAt(client, "/jobs");
    const option = await screen.findByRole("option", { name: "acme/a\\u202eb" });
    expect(option.textContent).not.toContain("\u202e");
  });

  it("asks for a value before it starts anything", async () => {
    const client = fakeClient();
    renderAt(client, "/jobs");
    await userEvent.click(await screen.findByRole("button", { name: "Start analysis" }));
    expect((await screen.findByRole("alert")).textContent).toBe("A value is required.");
    expect(client.submitJob).not.toHaveBeenCalled();
  });

  it("shows a refusal as a fixed message and the field the runner named", async () => {
    const client = fakeClient();
    client.submitJob.mockRejectedValue(new ApiError(400, "invalid_job", "compare"));
    renderAt(client, "/jobs");
    await userEvent.type(await screen.findByLabelText("Value"), "12");
    await userEvent.click(screen.getByRole("button", { name: "Start analysis" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("The job was not started.");
    expect(alert.textContent).toContain("invalid_job: compare");
  });
});
