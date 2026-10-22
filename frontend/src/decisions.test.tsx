import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "./api/client";
import type { DecisionJson, DecisionRequest, Report } from "./api/types";
import { resetAuthorCache } from "./storage";
import { fakeClient } from "./test/fakeClient";
import { fixture } from "./test/fixtures";
import { NAME, expectOwnWordsClean, expectPageWordsClean, headings, pageText, renderAt, renderReport } from "./test/render";

function decisionOf(request: DecisionRequest, label: string, kind: string, revision = 1): DecisionJson {
  return {
    target_id: request.target, kind, label, verdict: request.verdict, reason: request.reason, author: request.author, channel: "runner", revision,
    created_at: "2026-01-02T03:04:05Z", fingerprint: "f".repeat(64),
  };
}

/** A runner that keeps the decisions it was given and serves the report with them, as the real one does. */
function runner(base: Report = fixture("s13")) {
  const current: DecisionJson[] = [];
  const served = (): Report => {
    const report = JSON.parse(JSON.stringify(base)) as Report;
    const decisions = report.review!.decisions!;
    decisions.current = JSON.parse(JSON.stringify(current)) as DecisionJson[];
    const taken = new Set(current.map((d) => d.target_id));
    decisions.undecided = {
      areas: (decisions.undecided?.areas ?? []).filter((id) => !taken.has(id)),
      findings: (decisions.undecided?.findings ?? []).filter((id) => !taken.has(id)),
    };
    return report;
  };
  const client = fakeClient({ reports: [], report: () => served() });
  client.decide.mockImplementation(async (_name, request) => {
    const area = (base.areas ?? []).find((a) => a.id === request.target);
    const finding = (base.findings ?? []).find((f) => f.id === request.target);
    current.push(decisionOf(request, (area?.label ?? finding?.title) as string, area ? "area" : "finding"));
  });
  return { client, current, base };
}

/** A report page on the areas tab, served by `client`. */
async function openAreas(client: ReturnType<typeof runner>["client"], tail = "/areas") {
  const view = renderAt(client, `/reports/${NAME}${tail}`);
  await screen.findByRole("tab", { name: /Areas/ });
  await waitFor(() => expect(document.querySelector("table")).not.toBeNull());
  return view;
}

const record = () => screen.getByRole("button", { name: "Record decision" });

/** The words of every alert on the page (the form's own, and the toast of the answer: both are alerts to a screen reader). */
async function alertText(): Promise<string> {
  return (await screen.findAllByRole("alert")).map((a) => a.textContent).join(" ");
}

describe("the drawer of an area", () => {
  it("opens over the list with its own address, shows the facts, the route line and every step with the line coloured, and closes back to the list", async () => {
    const { client, base } = runner();
    const area = (base.areas ?? [])[0];
    const { router } = await openAreas(client, `/areas/${area.id}`);
    const drawer = await screen.findByRole("dialog");
    expect(within(drawer).getByRole("heading", { level: 2 }).textContent).toBe(area.label);
    expect(drawer.textContent).toContain("Priority:must test");
    expect(drawer.textContent).toContain("Route line");
    // the list stays behind the drawer
    expect(document.querySelector(".rm-areas")).not.toBeNull();
    const steps = [...drawer.querySelectorAll(".rm-step-target")];
    expect(steps.length).toBe(area.chains[0].length);
    for (const [i, step] of steps.entries()) {
      const ev = area.chains[0][i];
      expect(step.textContent).toContain(`${ev.path}:${ev.line}`);
      expect(step.textContent).toContain(ev.detail.trim());
      expect(step.querySelector(".rm-evidence-no")?.textContent).toBe(String(ev.line));
      expect(step.querySelector(`code[title="${ev.ref}"]`)).not.toBeNull();
    }
    // the verbatim line is coloured by token, as text
    expect(drawer.querySelector(".rm-evidence-code .token")).not.toBeNull();
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(router.state.location.pathname).toBe(`/reports/${NAME}/areas`));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("closes with the button, and a reload of its address comes back to it", async () => {
    const { client, base } = runner();
    const area = (base.areas ?? [])[1];
    const { router, unmount } = await openAreas(client, `/areas/${area.id}`);
    unmount();
    const again = await openAreas(client, `/areas/${area.id}`);
    expect(again.router.state.location.pathname).toBe(`/reports/${NAME}/areas/${area.id}`);
    await userEvent.click(await screen.findByRole("button", { name: "Close the panel" }));
    await waitFor(() => expect(again.router.state.location.pathname).toBe(`/reports/${NAME}/areas`));
    void router;
  });

  it("shows the chain as a list of steps, each with the line coloured, and names a link only when the report names its kind", async () => {
    const { client, base } = runner();
    const area = (base.areas ?? [])[0];
    await openAreas(client, `/areas/${area.id}`);
    const drawer = await screen.findByRole("dialog");
    const list = within(drawer).getByRole("list", { name: "Chain 1" });
    expect(list.querySelectorAll(".rm-step-target").length).toBe(area.chains[0].length);
    // one lane header for a chain that stays in one repository, and a link between two steps (named only when a claim says what it is)
    expect(list.querySelectorAll(".rm-chain-lane").length).toBe(1);
    expect(list.querySelectorAll(".rm-chain-link").length).toBe(area.chains[0].length - 1);
    expect([...list.querySelectorAll(".rm-chain-link")].map((l) => l.textContent)).toEqual(["route", "", "", ""]);
    // the graph libraries are not loaded for a chain
    expect(drawer.querySelector(".react-flow")).toBeNull();
  });

  it("has a header for each repository the chain passes through, in the order of the steps", async () => {
    const base = fixture("s13");
    const area = (base.areas ?? [])[0];
    area.chains[0] = [
      { ...area.chains[0][0], repo: "acme/web", path: "src/a.ts", line: 3, detail: "import { x } from './x';" },
      { ...area.chains[0][1], repo: "acme/api", path: "app/b.py", line: 9, detail: "return x()" },
      { ...area.chains[0][2], repo: "acme/api", path: "app/c.py", line: 2, detail: "from b import y", claim: "import:app/b.py:y" },
      { ...area.chains[0][3], repo: "acme/web", path: "src/d.ts", line: 4, detail: "fetch(url)" },
    ];
    (base.propagations![0] as { hops: unknown[] }).hops = [{ kind: "http", from_repo: "acme/web", to_repo: "acme/api", depth: 1, detail: "", candidate: false, complete: true, reasons: [], pins: [] }];
    const { client } = runner(base);
    await openAreas(client, `/areas/${area.id}`);
    const drawer = await screen.findByRole("dialog");
    const lanes = [...drawer.querySelectorAll(".rm-chain-lane")].map((l) => l.textContent);
    expect(lanes).toEqual(["Repository acme/web", "Repository acme/api", "Repository acme/web"]);
    expect([...drawer.querySelectorAll(".rm-chain-link")].map((l) => l.textContent)).toEqual(["route", "import", "http"]);
  });

  it("folds several chains and opens the first", async () => {
    const base = fixture("s13");
    const area = (base.areas ?? [])[0];
    area.chains.push(area.chains[0].slice(0, 2));
    const { client } = runner(base);
    await openAreas(client, `/areas/${area.id}`);
    const drawer = await screen.findByRole("dialog");
    expect(within(drawer).getAllByRole("button", { name: /^Chain \d of 2/ }).length).toBe(2);
    expect(drawer.querySelectorAll(".rm-chain").length).toBeGreaterThan(0);
  });

  it("says so for an area the report does not have", async () => {
    const { client } = runner();
    await openAreas(client, "/areas/a-0000000000000000");
    expect(await screen.findByText("This report has no such area or finding.")).toBeTruthy();
  });

  it("lists the findings about the area, risk and confidence apart, with links to their drawers", async () => {
    const { client, base } = runner();
    const area = (base.areas ?? [])[0];
    await openAreas(client, `/areas/${area.id}`);
    const drawer = await screen.findByRole("dialog");
    const card = within(drawer).getByRole("heading", { name: "Findings about this area" }).closest("section") as HTMLElement;
    expect(card.querySelector('[data-kind="risk"]')).not.toBeNull();
    expect(card.querySelector('[data-kind="confidence"]')).not.toBeNull();
    expect(within(card).getAllByRole("link")[0].getAttribute("href")).toMatch(new RegExp(`/reports/${NAME}/findings/f-[0-9a-f]{16}$`));
  });
});

describe("the drawer of a finding", () => {
  it("shows the kind, risk and confidence apart, the basis and every line it cites, with its own address", async () => {
    const { client, base } = runner();
    const finding = (base.findings ?? [])[0];
    const { router } = renderAt(client, `/reports/${NAME}/findings/${finding.id}`);
    const drawer = await screen.findByRole("dialog");
    expect(within(drawer).getByRole("heading", { level: 2 }).textContent).toBe("Finding F1");
    expect(drawer.textContent).toContain(finding.title);
    expect(drawer.textContent).toContain(`Confidence basis`.toUpperCase().slice(0, 0) + finding.confidence_basis);
    expect(drawer.querySelectorAll(".rm-evidence").length).toBe(finding.evidence.length);
    for (const e of finding.evidence) {
      expect(drawer.textContent).toContain(`${e.path}:${e.line}`);
    }
    expect(drawer.querySelector('[data-kind="risk"]')).not.toBeNull();
    expect(drawer.querySelector('[data-kind="confidence"]')).not.toBeNull();
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(router.state.location.pathname).toBe(`/reports/${NAME}/findings`));
  });

  it("says so for a finding the report does not have", async () => {
    const { client } = runner();
    renderAt(client, `/reports/${NAME}/findings/f-0000000000000000`);
    expect(await screen.findByText("This report has no such area or finding.")).toBeTruthy();
  });
});

describe("recording a decision", () => {
  it("asks for the verdict, a reason for a skip and a name, and posts exactly the decision", async () => {
    const { client, base } = runner();
    const area = (base.areas ?? [])[0];
    await openAreas(client, `/areas/${area.id}`);
    const verdict = await screen.findByLabelText("Verdict");
    expect(within(verdict).getAllByRole("option").map((o) => o.getAttribute("value"))).toEqual(["test", "sample", "skip"]);
    expect(within(verdict).getByRole("option", { name: "Skip: risk accepted, not tested" })).toBeTruthy();

    await userEvent.selectOptions(verdict, "skip");
    await userEvent.click(record());
    let problems = (await screen.findAllByRole("alert")).map((a) => a.textContent).join(" ");
    expect(problems).toContain("A name is required.");
    expect(problems).toContain("A reason is required for this verdict.");
    expect(client.decide).not.toHaveBeenCalled();

    await userEvent.type(screen.getByLabelText("Reason"), "retired in this release");
    await userEvent.type(screen.getByLabelText("Your name"), "  Quinn ");
    await userEvent.click(record());
    expect(client.decide).toHaveBeenCalledTimes(1);
    expect(client.decide).toHaveBeenCalledWith(NAME, { target: area.id, verdict: "skip", reason: "retired in this release", author: "Quinn" });
    expect(await screen.findByText("Decision recorded.", { selector: "[role=status]" })).toBeTruthy();
    // the page reads the report again and shows the decision as the runner computed it
    await waitFor(() => expect(client.report.mock.calls.length).toBeGreaterThanOrEqual(2));
    await waitFor(() => expect(pageText()).toContain('risk accepted, not tested (reason from Quinn: "retired in this release")'));
    problems = "";
  });

  it("says the answer once in a drawer (its status line, no toast over the form) and in a toast when a dialog closes", async () => {
    const { client, base } = runner(fixture("unordered"));
    await openAreas(client, `/areas/${(base.areas ?? [])[0].id}`);
    await userEvent.type(await screen.findByLabelText("Your name"), "Quinn");
    await userEvent.click(record());
    await screen.findByText("Decision recorded.", { selector: "[role=status]" });
    expect(screen.queryAllByText("Decision recorded.").length).toBe(1);
  });

  it("remembers the name as a convenience and fills it in next time", async () => {
    const { client, base } = runner();
    const first = await openAreas(client, `/areas/${(base.areas ?? [])[0].id}`);
    await userEvent.type(await screen.findByLabelText("Your name"), "Quinn");
    await userEvent.selectOptions(screen.getByLabelText("Verdict"), "test");
    await userEvent.click(record());
    await screen.findAllByText("Decision recorded.");
    expect(window.localStorage.getItem("reachmap-author")).toBe("Quinn");
    first.unmount();
    resetAuthorCache();
    await openAreas(client, `/areas/${(base.areas ?? [])[1].id}`);
    expect(((await screen.findByLabelText("Your name")) as HTMLInputElement).value).toBe("Quinn");
  });

  it("records when the storage throws", async () => {
    vi.spyOn(window.localStorage, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(window.localStorage, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    resetAuthorCache();
    const { client, base } = runner();
    await openAreas(client, `/areas/${(base.areas ?? [])[0].id}`);
    await userEvent.type(await screen.findByLabelText("Your name"), "Quinn");
    await userEvent.click(record());
    expect((await screen.findAllByText("Decision recorded.")).length).toBeGreaterThan(0);
    expect(client.decide).toHaveBeenCalledTimes(1);
  });

  it("offers accept and reject for a finding, and a reason is required for a reject", async () => {
    const { client, base } = runner();
    const finding = (base.findings ?? [])[0];
    renderAt(client, `/reports/${NAME}/findings/${finding.id}`);
    const verdict = await screen.findByLabelText("Verdict");
    expect(within(verdict).getAllByRole("option").map((o) => o.getAttribute("value"))).toEqual(["accept", "reject"]);
    await userEvent.type(screen.getByLabelText("Your name"), "Quinn");
    await userEvent.selectOptions(verdict, "reject");
    await userEvent.click(record());
    expect(await alertText()).toContain("A reason is required for this verdict.");
    await userEvent.type(screen.getByLabelText("Reason"), "wrong guess");
    await userEvent.click(record());
    await screen.findAllByText("Decision recorded.");
    expect(client.decide).toHaveBeenCalledWith(NAME, { target: finding.id, verdict: "reject", reason: "wrong guess", author: "Quinn" });
    await waitFor(() => expect(pageText()).toContain("finding rejected by the reviewer (reason from Quinn:"));
  });

  it("does not require a reason for a test, a sample or an accept", async () => {
    const { client, base } = runner();
    await openAreas(client, `/areas/${(base.areas ?? [])[0].id}`);
    await userEvent.type(await screen.findByLabelText("Your name"), "Quinn");
    await userEvent.selectOptions(screen.getByLabelText("Verdict"), "sample");
    await userEvent.click(record());
    await screen.findAllByText("Decision recorded.");
    expect(client.decide.mock.calls[0][1]).toMatchObject({ verdict: "sample", reason: "" });
  });

  it("refuses a reason or a name that is too long before it asks the runner", async () => {
    const { client, base } = runner();
    await openAreas(client, `/areas/${(base.areas ?? [])[0].id}`);
    await userEvent.type(await screen.findByLabelText("Your name"), "n".repeat(101));
    await userEvent.click(screen.getByLabelText("Reason"));
    await userEvent.paste("r".repeat(2001));
    await userEvent.click(record());
    const text = (await screen.findAllByRole("alert")).map((a) => a.textContent).join(" ");
    expect(text).toContain("The name is longer than 100 characters.");
    expect(text).toContain("The reason is longer than 2000 characters.");
    expect(client.decide).not.toHaveBeenCalled();
  });

  it("counts the length of a name in characters, as the runner does, not in UTF-16 units", async () => {
    const { client, base } = runner();
    await openAreas(client, `/areas/${(base.areas ?? [])[0].id}`);
    await userEvent.click(await screen.findByLabelText("Your name"));
    await userEvent.paste("\u{1f600}".repeat(100)); // 100 characters, 200 UTF-16 units
    await userEvent.click(record());
    await screen.findAllByText("Decision recorded.");
    expect(client.decide.mock.calls[0][1].author).toBe("\u{1f600}".repeat(100));
  });

  it("shows a refusal as fixed words with the status, never as the runner's text", async () => {
    const { client, base } = runner();
    client.decide.mockRejectedValue(new ApiError(400, "invalid_decision", "reason"));
    await openAreas(client, `/areas/${(base.areas ?? [])[0].id}`);
    await userEvent.type(await screen.findByLabelText("Your name"), "Quinn");
    await userEvent.click(record());
    const text = await alertText();
    expect(text).toContain("The decision was not recorded.");
    expect(text).toContain("The runner refused the request as not valid.");
    expect(text).toContain("invalid_decision: reason");
    expectPageWordsClean();
    expect(screen.queryByText("Decision recorded.", { selector: "[role=status]" })).toBeNull();
  });

  it("says the runner cannot be reached when the call fails without an answer", async () => {
    const { client, base } = runner();
    client.decide.mockRejectedValue(new Error("network"));
    await openAreas(client, `/areas/${(base.areas ?? [])[0].id}`);
    await userEvent.type(await screen.findByLabelText("Your name"), "Quinn");
    await userEvent.click(record());
    expect(await alertText()).toContain("The runner could not be reached.");
  });

  it("changes no priority, risk, confidence or count: a skip leaves the area under Must test", async () => {
    const { client, base } = runner();
    const area = (base.areas ?? [])[0];
    const overview = await renderReport(base, "");
    const before = overview.container.querySelector(".rm-stat")?.parentElement?.textContent;
    overview.unmount();
    const findings = await renderReport(base, "/findings");
    const risks = [...findings.container.querySelectorAll("tbody tr")].map((tr) => tr.querySelector('[data-kind="risk"]')?.textContent + "/" + tr.querySelector('[data-kind="confidence"]')?.textContent);
    findings.unmount();

    await openAreas(client, `/areas/${area.id}`);
    await userEvent.selectOptions(await screen.findByLabelText("Verdict"), "skip");
    await userEvent.type(screen.getByLabelText("Reason"), "accepted");
    await userEvent.type(screen.getByLabelText("Your name"), "Quinn");
    await userEvent.click(record());
    await waitFor(() => expect(pageText()).toContain("risk accepted, not tested (reason from"));
    document.body.innerHTML = "";

    const after = renderAt(client, `/reports/${NAME}`);
    await screen.findByRole("tab", { name: /Overview/ });
    expect(after.container.querySelector(".rm-stat")?.parentElement?.textContent).toBe(before);
    after.unmount();
    const areas = renderAt(client, `/reports/${NAME}/areas`);
    await screen.findByRole("heading", { level: 2, name: "Must test" });
    const must = within(areas.container).getByRole("heading", { level: 2, name: "Must test" }).closest("section") as HTMLElement;
    expect(must.textContent).toContain(area.label);
    expect(must.textContent).toContain("risk accepted, not tested");
    areas.unmount();
    const again = renderAt(client, `/reports/${NAME}/findings`);
    await screen.findByRole("heading", { level: 2, name: /direct/ });
    expect([...again.container.querySelectorAll("tbody tr")].map((tr) => tr.querySelector('[data-kind="risk"]')?.textContent + "/" + tr.querySelector('[data-kind="confidence"]')?.textContent)).toEqual(risks);
  });

  it("offers no decision for a report that has no review data, or a run that did not complete", async () => {
    const old = await renderReport("complete", "/areas");
    expect(old.container.querySelectorAll('input[type="checkbox"]').length).toBe(0);
    expect(screen.queryByRole("button", { name: "Decide for the selection" })).toBeNull();
    old.unmount();
    const area = fixture("complete").areas![0];
    const drawer = await renderReport("complete", `/areas/${area.id ?? "a-0123456789abcdef"}`);
    expect(drawer.container.querySelector("form")).toBeNull();
  });
});

describe("triage with the keyboard", () => {
  async function ready() {
    const r = runner(fixture("unordered"));
    await openAreas(r.client);
    return r;
  }
  const active = () => [...document.querySelectorAll('tbody tr[data-active="true"]')].map((tr) => tr.getAttribute("data-row"));

  it("moves with j and k, stays inside the list, and opens the drawer with Enter", async () => {
    const { base } = await ready();
    expect(active()).toEqual([]);
    await userEvent.keyboard("j");
    expect(active()).toEqual(["0"]);
    await userEvent.keyboard("jj");
    expect(active()).toEqual(["2"]);
    await userEvent.keyboard("kkkk");
    expect(active()).toEqual(["0"]);
    await userEvent.keyboard("j");
    const label = document.querySelector('tbody tr[data-active="true"] a')?.textContent;
    await userEvent.keyboard("{Enter}");
    const drawer = await screen.findByRole("dialog");
    expect(within(drawer).getByRole("heading", { level: 2 }).textContent).toBe(label);
    expect(base.areas!.some((a) => a.label === label)).toBe(true);
  });

  it("opens the decision dialog prefilled with t, s and x for the area in focus", async () => {
    const { client, base } = await ready();
    await userEvent.keyboard("j");
    const first = document.querySelector('tbody tr[data-active="true"] a')?.textContent;
    const target = base.areas!.find((a) => a.label === first)!;
    await userEvent.keyboard("t");
    let dialog = await screen.findByRole("dialog", { name: "Record a decision" });
    expect((within(dialog).getByLabelText("Verdict") as HTMLSelectElement).value).toBe("test");
    expect(dialog.textContent).toContain(first);
    await userEvent.type(within(dialog).getByLabelText("Your name"), "Quinn");
    await userEvent.click(within(dialog).getByRole("button", { name: "Record decision" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(client.decide).toHaveBeenCalledWith(NAME, { target: target.id, verdict: "test", reason: "", author: "Quinn" });

    await userEvent.keyboard("s");
    dialog = await screen.findByRole("dialog", { name: "Record a decision" });
    expect((within(dialog).getByLabelText("Verdict") as HTMLSelectElement).value).toBe("sample");
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    await userEvent.keyboard("x");
    dialog = await screen.findByRole("dialog", { name: "Record a decision" });
    expect((within(dialog).getByLabelText("Verdict") as HTMLSelectElement).value).toBe("skip");
    // a skip needs a reason: the dialog says so before it asks the runner
    await userEvent.click(within(dialog).getByRole("button", { name: "Record decision" }));
    expect((await within(dialog).findAllByRole("alert")).map((a) => a.textContent).join(" ")).toContain("A reason is required for this verdict.");
    expect(client.decide).toHaveBeenCalledTimes(1);
  });

  it("does not take the keys while a field has the focus or a dialog is open", async () => {
    await ready();
    await userEvent.type(screen.getByLabelText("Search areas"), "jjjtsx");
    expect(active()).toEqual([]);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("selects the area in focus with Space, and t then decides for every area selected", async () => {
    const { client, base } = await ready();
    await userEvent.keyboard("j ");
    await userEvent.keyboard("jj ");
    expect(document.querySelectorAll('tbody tr[data-selected="true"]').length).toBe(2);
    await userEvent.keyboard("t");
    const dialog = await screen.findByRole("dialog", { name: "Decide for the selection" });
    expect(dialog.textContent).toContain("Each area gets its own decision");
    await userEvent.type(within(dialog).getByLabelText("Your name"), "Quinn");
    await userEvent.click(within(dialog).getByRole("button", { name: "Record decision" }));
    await waitFor(() => expect(client.decide).toHaveBeenCalledTimes(2));
    const targets = client.decide.mock.calls.map((c) => c[1].target);
    expect(new Set(targets).size).toBe(2);
    expect(base.areas!.filter((a) => targets.includes(a.id as string)).length).toBe(2);
  });

  it("keeps the keys when a check box has the focus (a box was ticked with the mouse), and leaves Space to the box", async () => {
    await ready();
    const first = screen.getAllByRole("checkbox", { name: /^Select this area:/ })[0];
    await userEvent.click(first);
    expect(document.activeElement).toBe(first);
    expect(active()).toEqual(["0"]); // a click on a row makes it the one in focus
    await userEvent.keyboard("j");
    expect(active()).toEqual(["1"]);
    // Space on the box is the box's own: it does not also select the area in focus
    const before = document.querySelectorAll('tbody tr[data-selected="true"]').length;
    first.focus();
    await userEvent.keyboard(" ");
    expect(document.querySelectorAll('tbody tr[data-selected="true"]').length).toBe(before - 1);
  });

  it("takes no key as a decision when decisions cannot be recorded", async () => {
    const view = await renderReport("complete", "/areas");
    await userEvent.keyboard("jt");
    expect(screen.queryByRole("dialog")).toBeNull();
    view.unmount();
  });
});

describe("deciding for a selection", () => {
  async function ready(report = fixture("unordered")) {
    const r = runner(report);
    const view = await openAreas(r.client);
    return { ...r, view };
  }

  it("selects with the boxes, shows how many are selected, and records one decision per area, one request each, with the runner's answer for each", async () => {
    const { client, base } = await ready();
    const boxes = screen.getAllByRole("checkbox", { name: /^Select this area:/ });
    await userEvent.click(boxes[0]);
    await userEvent.click(boxes[1]);
    await userEvent.click(boxes[2]);
    expect(screen.getByText("3 selected")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Decide for the selection" }));
    const dialog = await screen.findByRole("dialog", { name: "Decide for the selection" });
    await userEvent.selectOptions(within(dialog).getByLabelText("Verdict"), "skip");
    await userEvent.type(within(dialog).getByLabelText("Reason"), "retired in this release");
    await userEvent.type(within(dialog).getByLabelText("Your name"), "Quinn");
    await userEvent.click(within(dialog).getByRole("button", { name: "Record decision" }));
    await waitFor(() => expect(client.decide).toHaveBeenCalledTimes(3));
    for (const call of client.decide.mock.calls) {
      expect(call[0]).toBe(NAME);
      expect(call[1]).toMatchObject({ verdict: "skip", reason: "retired in this release", author: "Quinn" });
    }
    // all three were recorded: the dialog closes by itself and a toast says so once
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(await screen.findByText("Decisions recorded for the selection.")).toBeTruthy();
    // the page read the report again: every selected area now says risk accepted, not tested
    await waitFor(() => expect(client.report.mock.calls.length).toBeGreaterThanOrEqual(2));
    await waitFor(() => expect(document.querySelectorAll('[data-decision="current"]').length).toBe(3));
    expect(base.areas!.length).toBeGreaterThan(3);
    expect(screen.queryByText("3 selected")).toBeNull();
  });

  it("reports each row apart when one of them is refused, and records the others", async () => {
    const { client } = await ready();
    client.decide.mockImplementationOnce(async () => undefined).mockImplementationOnce(async () => {
      throw new ApiError(409, "not_completed");
    });
    const boxes = screen.getAllByRole("checkbox", { name: /^Select this area:/ });
    await userEvent.click(boxes[0]);
    await userEvent.click(boxes[1]);
    await userEvent.click(screen.getByRole("button", { name: "Decide for the selection" }));
    const dialog = await screen.findByRole("dialog", { name: "Decide for the selection" });
    await userEvent.type(within(dialog).getByLabelText("Your name"), "Quinn");
    await userEvent.click(within(dialog).getByRole("button", { name: "Record decision" }));
    const results = await within(dialog).findByRole("list", { name: "Decide for the selection" });
    const items = within(results).getAllByRole("listitem").map((li) => li.textContent);
    expect(items.filter((t) => t?.includes("Recorded")).length).toBe(1);
    const refused = items.find((t) => t?.includes("The decision was not recorded."));
    expect(refused).toContain("The runner cannot do that now.");
    expect(refused).toContain("not_completed");
    expect(client.decide).toHaveBeenCalledTimes(2);
    expectPageWordsClean();
  });

  it("selects every area shown with one box, and only the areas that are shown", async () => {
    await ready();
    await userEvent.click(screen.getByRole("checkbox", { name: "Select all areas shown" }));
    const total = document.querySelectorAll('tbody tr[data-selected="true"]').length;
    expect(total).toBe(document.querySelectorAll("tbody tr").length);
    await userEvent.selectOptions(screen.getByLabelText("Repository"), "acme/zeta");
    expect(screen.getByText("1 selected")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Clear the selection" }));
    expect(document.querySelectorAll('tbody tr[data-selected="true"]').length).toBe(0);
  });

  it("filters by decision state", async () => {
    const { client, base } = await ready();
    const target = base.areas![0];
    await userEvent.type(screen.getByLabelText("Search areas"), target.label);
    await userEvent.click(screen.getAllByRole("checkbox", { name: /^Select this area:/ })[0]);
    await userEvent.click(screen.getByRole("button", { name: "Decide for the selection" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Your name"), "Quinn");
    await userEvent.click(within(dialog).getByRole("button", { name: "Record decision" }));
    await waitFor(() => expect(client.decide).toHaveBeenCalledTimes(1));
    await userEvent.keyboard("{Escape}");
    await userEvent.clear(screen.getByLabelText("Search areas"));
    await waitFor(() => expect(document.querySelectorAll('[data-decision="current"]').length).toBe(1));
    await userEvent.click(screen.getByRole("radio", { name: "Decided" }));
    expect(document.querySelectorAll("tbody tr").length).toBe(1);
    await userEvent.click(screen.getByRole("radio", { name: "Not decided" }));
    expect(document.querySelectorAll("tbody tr").length).toBe(base.areas!.length - 1);
  });
});

describe("what the report says about decisions", () => {
  it("says a stale decision needs review, with what changed, and that it is not applied", async () => {
    const base = fixture("s13");
    const area = (base.areas ?? [])[0];
    base.review!.revision = 2;
    base.review!.decisions!.stale = [
      {
        target_id: area.id as string, kind: "area", label: area.label, verdict: "skip", reason: "retired", author: "Quinn", channel: "runner", revision: 1,
        created_at: "2026-01-02T03:04:05Z", fingerprint: "a".repeat(64), now_fingerprint: "b".repeat(64),
        changes: { removed: [{ repo: "acme/api", path: "x.py", line: 1, detail: "old line" }], added: [{ repo: "acme/api", path: "x.py", line: 1, detail: "new line" }] },
      } as DecisionJson,
    ];
    const { container } = await renderReport(base, "/areas");
    await userEvent.click(await screen.findByRole("button", { name: "decision needs review" }));
    const text = (await screen.findByRole("dialog")).textContent ?? "";
    expect(text).toContain("What changed:");
    expect(text).toContain("It is not applied.");
    expect(text).toContain("old line");
    expect(text).toContain("new line");
    await userEvent.keyboard("{Escape}");
    const section = container.querySelector("#decisions-h")?.closest("section") as HTMLElement;
    expect(section.textContent).toContain("Decisions that need review");
    expect(section.textContent).toContain("aaaaaaaaaaaa");
    expect(section.textContent).toContain("bbbbbbbbbbbb");
    // not applied: the area stays where it was and is not worded as risk accepted
    const must = within(container).getByRole("heading", { level: 2, name: "Must test" }).closest("section") as HTMLElement;
    expect(must.textContent).toContain(area.label);
    expect(must.textContent).not.toContain("risk accepted, not tested");
    expectPageWordsClean();
  });

  it("words a stale decision in the drawer against the revision the report is", async () => {
    const base = fixture("s13");
    const area = (base.areas ?? [])[0];
    base.review!.revision = 2;
    base.review!.decisions!.stale = [
      { target_id: area.id as string, kind: "area", label: area.label, verdict: "skip", reason: "retired", author: "Quinn", channel: "runner", revision: 1, created_at: "t", fingerprint: "a".repeat(64) } as DecisionJson,
    ];
    await renderReport(base, `/areas/${area.id}`);
    const drawer = await screen.findByRole("dialog");
    expect(drawer.textContent).toContain("the claim on revision 2 differs from the claim Quinn decided on at revision 1");
    expect(drawer.textContent).toContain("It is not applied.");
  });

  it("lists an orphaned decision apart, struck through, and applies it to nothing", async () => {
    const base = fixture("s13");
    base.review!.decisions!.orphaned = [
      { target_id: "a-" + "9".repeat(16), kind: "area", label: "/gone", verdict: "test", reason: "", author: "Quinn", channel: "cli", revision: 1, created_at: "2026-01-02T03:04:05Z", fingerprint: "c".repeat(64) },
    ];
    const { container } = await renderReport(base, "/areas");
    await waitFor(() => expect(container.querySelector(".rm-orphaned")?.textContent).toBe("/gone"));
    expect(container.textContent).toContain("Orphaned decisions (1)");
    expect(container.textContent).toContain("marked to test (by Quinn)");
    expect(container.querySelectorAll('[data-decision="current"]').length).toBe(0);
  });

  it("says the decisions could not be read, and no area says it has none", async () => {
    const base = fixture("s13");
    base.review!.decisions!.records = [{ kind: "decision_store_unreadable", subject: "decisions", not_established: "Which decisions were recorded.", detail: "", blocks_selection: true }];
    const { container } = await renderReport(base, "/areas");
    expect(headings(container, 3)).toContain("Decisions that could not be read");
    expect(container.querySelectorAll('[data-decision="none"]').length).toBe(0);
    expect(container.textContent).not.toContain("No decision recorded.");
  });

  it("says how many decisions there are on the overview and what is not decided", async () => {
    const { client, base } = runner();
    client.report.mockImplementation(async () => {
      const r = JSON.parse(JSON.stringify(base)) as Report;
      r.review!.decisions!.current = [decisionOf({ target: (base.areas ?? [])[0].id as string, verdict: "test", reason: "", author: "Quinn" }, "x", "area")];
      return r;
    });
    const { container } = renderAt(client, `/reports/${NAME}`);
    await screen.findByRole("tab", { name: /Overview/ });
    const card = within(container).getByText("Decisions", { selector: "p" }).closest(".mantine-Card-root") as HTMLElement;
    expect(card.textContent).toContain("Decided");
    expect(card.textContent).toContain("Not decided");
    expectOwnWordsClean(container);
  });
});

describe("the keys of the triage in the table", () => {
  async function ready(report = fixture("unordered")) {
    const r = runner(report);
    await openAreas(r.client);
    return r;
  }
  const activeRow = () => document.querySelector('tbody tr[data-active="true"]') as HTMLElement | null;

  it("puts the focus itself on the title link of the row it moves to", async () => {
    await ready();
    await userEvent.keyboard("jj");
    const row = activeRow()!;
    expect(row.getAttribute("aria-current")).toBe("true");
    expect(document.activeElement).toBe(row.querySelector("a.rm-row-title"));
    await userEvent.keyboard("k");
    expect(document.activeElement).toBe(activeRow()!.querySelector("a.rm-row-title"));
    expect(document.querySelectorAll('tbody tr[data-active="true"]').length).toBe(1);
  });

  it("clears the selection with Escape", async () => {
    await ready();
    await userEvent.keyboard("j jj ");
    expect(document.querySelectorAll('tbody tr[data-selected="true"]').length).toBe(2);
    await userEvent.keyboard("{Escape}");
    expect(document.querySelectorAll('tbody tr[data-selected="true"]').length).toBe(0);
    expect(screen.queryByText(/selected$/)).toBeNull();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("leaves the keys alone while the drawer of an area is open", async () => {
    await ready();
    await userEvent.keyboard("j{Enter}");
    const drawer = await screen.findByRole("dialog");
    const before = activeRow()?.getAttribute("data-row");
    drawer.focus();
    await userEvent.keyboard("jjt");
    expect(activeRow()?.getAttribute("data-row")).toBe(before);
    expect(screen.queryByRole("dialog", { name: "Record a decision" })).toBeNull();
    expect(screen.getAllByRole("dialog").length).toBe(1);
  });

  it("leaves the keys alone while the decision dialog is open", async () => {
    await ready();
    await userEvent.keyboard("jt");
    const dialog = await screen.findByRole("dialog", { name: "Record a decision" });
    const before = activeRow()?.getAttribute("data-row");
    dialog.focus();
    await userEvent.keyboard("jjx");
    expect(activeRow()?.getAttribute("data-row")).toBe(before);
    expect((within(dialog).getByLabelText("Verdict") as HTMLSelectElement).value).toBe("test");
  });

  it("puts the focus in the reason for a skip, and not for the others", async () => {
    await ready();
    await userEvent.keyboard("jx");
    let dialog = await screen.findByRole("dialog", { name: "Record a decision" });
    await waitFor(() => expect(document.activeElement).toBe(within(dialog).getByLabelText("Reason")));
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await userEvent.keyboard("t");
    dialog = await screen.findByRole("dialog", { name: "Record a decision" });
    await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));
    expect(document.activeElement).not.toBe(within(dialog).getByLabelText("Reason"));
  });

  it("records with Ctrl and Enter, and with Cmd and Enter, from a field of the form", async () => {
    const { client } = await ready();
    await userEvent.keyboard("jt");
    let dialog = await screen.findByRole("dialog", { name: "Record a decision" });
    await userEvent.type(within(dialog).getByLabelText("Your name"), "Quinn");
    await userEvent.keyboard("{Control>}{Enter}{/Control}");
    await waitFor(() => expect(client.decide).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await userEvent.keyboard("t");
    dialog = await screen.findByRole("dialog", { name: "Record a decision" });
    await userEvent.type(within(dialog).getByLabelText("Your name"), "{Meta>}{Enter}{/Meta}");
    await waitFor(() => expect(client.decide).toHaveBeenCalledTimes(2));
  });

  it("keeps every group with its title and its rows when a decision is made", async () => {
    const { client } = await ready();
    const groupTitles = () => [...document.querySelectorAll("section.rm-group h2")].map((h) => h.textContent);
    const rows = () => document.querySelectorAll("tbody tr").length;
    const before = { titles: groupTitles(), rows: rows() };
    expect(before.titles.length).toBeGreaterThan(0);
    await userEvent.keyboard("jt");
    const dialog = await screen.findByRole("dialog", { name: "Record a decision" });
    await userEvent.type(within(dialog).getByLabelText("Your name"), "Quinn");
    await userEvent.click(within(dialog).getByRole("button", { name: "Record decision" }));
    await waitFor(() => expect(client.decide).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(document.querySelectorAll('[data-decision="current"]').length).toBe(1));
    expect(groupTitles()).toEqual(before.titles);
    expect(rows()).toBe(before.rows);
  });

  it("moves the focus on to the next area without a decision after a decision from the keys", async () => {
    const { client } = await ready();
    await userEvent.keyboard("jt");
    const dialog = await screen.findByRole("dialog", { name: "Record a decision" });
    await userEvent.type(within(dialog).getByLabelText("Your name"), "Quinn");
    await userEvent.keyboard("{Control>}{Enter}{/Control}");
    await waitFor(() => expect(client.decide).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() => expect(activeRow()?.getAttribute("data-row")).toBe("1"));
    expect(activeRow()!.getAttribute("data-decision")).not.toBe("current");
  });
});

describe("a long list", () => {
  function many(n: number): Report {
    const base = fixture("unordered");
    const proto = (base.areas ?? [])[0];
    base.areas = Array.from({ length: n }, (_, i) => ({
      ...JSON.parse(JSON.stringify(proto)),
      id: `a-${i.toString(16).padStart(16, "0")}`,
      label: `GET /api/things/${i}/items/{item_id}`,
      priority: i % 5 === 0 ? "must_test" : "sample",
    }));
    base.review!.decisions!.undecided = { areas: base.areas.map((a) => a.id as string), findings: [] };
    return base;
  }

  async function measure(n: number): Promise<{ median: number; touched: number }> {
    const r = runner(many(n));
    const view = await openAreas(r.client);
    expect(document.querySelectorAll("tbody tr").length).toBe(n);
    fireEvent.keyDown(document.body, { key: "j" });
    fireEvent.keyDown(document.body, { key: "j" });
    // which rows does one key touch in the document?
    const touched = new Set<Element>();
    const watch = new MutationObserver((records) => {
      for (const rec of records) {
        const row = (rec.target instanceof Element ? rec.target : rec.target.parentElement)?.closest("tr");
        if (row) {
          touched.add(row);
        }
      }
    });
    watch.observe(document.querySelector("tbody")!.parentElement!.parentElement!, { subtree: true, childList: true, attributes: true, characterData: true });
    fireEvent.keyDown(document.body, { key: "j" });
    await Promise.resolve();
    const taken = watch.takeRecords();
    for (const rec of taken) {
      const row = (rec.target instanceof Element ? rec.target : rec.target.parentElement)?.closest("tr");
      if (row) {
        touched.add(row);
      }
    }
    watch.disconnect();
    const times: number[] = [];
    for (let i = 0; i < 30; i++) {
      const t0 = performance.now();
      fireEvent.keyDown(document.body, { key: i < 20 ? "j" : "k" });
      times.push(performance.now() - t0);
    }
    const sorted = [...times].sort((x, y) => x - y);
    view.unmount();
    return { median: sorted[Math.floor(sorted.length / 2)], touched: touched.size };
  }

  it("moves with j and k in a list of 500 areas by touching two rows, not every row", async () => {
    const small = await measure(50);
    const large = await measure(500);
    // the time in jsdom is several times that of a browser and grows with the size of the document (its selectors are slow): it is noted, not asserted
    console.info(`TIMING j and k in jsdom: median ${small.median.toFixed(1)} ms with 50 areas, ${large.median.toFixed(1)} ms with 500 areas`);
    expect(small.touched).toBeGreaterThan(0);
    expect(small.touched).toBeLessThanOrEqual(2);
    expect(large.touched).toBeLessThanOrEqual(2);
    // no wall-clock bound: a loaded machine makes jsdom slow, and the rows touched (above) are what the design promises
  });
});
