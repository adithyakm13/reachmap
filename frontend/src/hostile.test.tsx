import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { fixture } from "./test/fixtures";
import { expectPageWordsClean, pageText, renderReport, tagNames } from "./test/render";

const TABS = ["", "/areas", "/findings", "/branches", "/cross-repo", "/tests", "/records", "/author"];
const FORBIDDEN = ["script", "img", "iframe", "object", "embed", "link", "meta", "base", "form", "foreignobject", "audio", "video", "canvas"];

/** Everything in the page that could be made from content: no element, attribute, link or style comes from a report. */
function expectInert(root: ParentNode = document.body): void {
  for (const tag of FORBIDDEN) {
    // the page itself has a form (a decision) and a canvas of no kind: only content that came from a report matters, so a tag is looked for
    // inside the places the content is drawn
    for (const el of root.querySelectorAll(`[data-c] ${tag}, [data-c]${tag}`)) {
      throw new Error(`a ${el.tagName} inside content`);
    }
  }
  for (const el of root.querySelectorAll("*")) {
    for (const attr of el.getAttributeNames()) {
      expect(attr.startsWith("on"), `${el.tagName} ${attr}`).toBe(false);
    }
  }
  for (const a of root.querySelectorAll("a")) {
    const href = a.getAttribute("href") ?? "";
    expect(href.startsWith("/") || href.startsWith("#"), href).toBe(true);
  }
  // the one style elements are the UI kit's own (they carry its mark): none holds a word of a report
  for (const style of root.querySelectorAll("style")) {
    expect(style.hasAttribute("data-mantine-styles") || style.hasAttribute("data-mantine-disable-transition")).toBe(true);
    expect(style.textContent).not.toMatch(/alert\(1\)|onerror|<\/script>/);
  }
  for (const el of root.querySelectorAll("[href], [src], [xlink\\:href]")) {
    const v = el.getAttribute("href") ?? el.getAttribute("src") ?? el.getAttribute("xlink:href") ?? "";
    expect(/^(\/|#)/.test(v), `${el.tagName} ${v}`).toBe(true);
  }
}

describe("a hostile report", () => {
  it("is shown as text on every tab: no element, attribute, link or style comes from its content", async () => {
    const benign = await renderReport("complete");
    const allowed = tagNames(document.body);
    benign.unmount();
    for (const tail of TABS) {
      const { unmount } = await renderReport("hostile", tail);
      for (const tag of tagNames(document.body)) {
        // the elements of the UI kit and of the graph library: the same as for a report with nothing hostile in it, plus the ones a tab adds
        if (!allowed.has(tag)) {
          expect(["tr", "td", "th", "thead", "tbody", "table", "pre", "code", "section", "ol", "li", "ul", "label", "select", "option", "input", "textarea", "form", "button", "svg", "path", "g", "text", "circle", "line", "polyline", "rect", "marker", "defs", "pattern", "p", "div", "span", "h1", "h2", "h3", "h4", "a", "nav", "kbd", "caption", "b", "strong", "style"].includes(tag), `${tail}: <${tag}>`).toBe(true);
        }
      }
      expectInert();
      unmount();
    }
  });

  it("shows the hostile strings verbatim as text", async () => {
    await renderReport("hostile", "/findings");
    const text = pageText();
    for (const piece of ["</script><script>alert(1)</script>", "<!--", "<img src=x onerror=alert(1)>", '"><svg onload=alert(1)>', "javascript:alert(1)", "]]>", "[x](javascript:alert(1))", "<details open>"]) {
      expect(text).toContain(piece);
    }
    expect(document.querySelector("img")).toBeNull();
    expect(document.querySelector("details")).toBeNull();
  });

  it("shows the evidence of an area and a finding as text in the drawer, with the odd characters written out", async () => {
    const report = fixture("hostile");
    const area = report.areas![0];
    const drawer = await renderReport(report, `/areas/${area.id ?? ""}`).catch(() => null);
    drawer?.unmount();
    // the hostile fixture has no ids (it is an M10 fixture): the drawer is reached through a copy that has them
    const withIds = fixture("hostile");
    withIds.areas![0].id = "a-0123456789abcdef";
    withIds.findings![0].id = "f-0123456789abcdef";
    const opened = await renderReport(withIds, "/areas/a-0123456789abcdef");
    const dialog = await screen.findByRole("dialog");
    expect(dialog.querySelectorAll(".rm-evidence").length).toBeGreaterThan(1);
    const text = dialog.textContent ?? "";
    expect(text).toContain("\\u202e");
    expect(text).toContain("\\u2028");
    expect(text).toContain("\\ud800");
    expect(text).toContain("\\x1b[31m");
    for (const raw of ["\u202e", "\u2028", "\u2029", "\ud800", "\u001b"]) {
      expect(document.body.textContent).not.toContain(raw);
    }
    expectInert();
    expectPageWordsClean();
    opened.unmount();
    const finding = await renderReport(withIds, "/findings/f-0123456789abcdef");
    const fd = await screen.findByRole("dialog");
    expect(within(fd).getAllByText(/alert\(1\)/).length).toBeGreaterThan(0);
    expectInert();
    finding.unmount();
  });

  it("shows the chain as a list of steps whose labels are text and nothing else", async () => {
    const withIds = fixture("hostile");
    withIds.areas![0].id = "a-0123456789abcdef";
    await renderReport(withIds, "/areas/a-0123456789abcdef");
    const dialog = await screen.findByRole("dialog");
    const chain = within(dialog).getByRole("list", { name: "Chain 1" });
    expect(chain.querySelectorAll(".rm-step-target").length).toBeGreaterThan(0);
    expect(chain.querySelector("img, script, iframe")).toBeNull();
    expectInert(chain);
    for (const box of chain.querySelectorAll("[data-c]")) {
      expect(box.querySelector("img, script, iframe, a, style")).toBeNull();
    }
  });

  it("draws the overview graph with the hostile repository and area labels as text", async () => {
    await renderReport("hostile");
    const graph = await screen.findByRole("group", { name: "What this change reaches" });
    expect(graph.querySelectorAll(".rm-repo").length).toBeGreaterThan(0);
    for (const box of graph.querySelectorAll("[data-c]")) {
      expect(box.children.length).toBe(0);
    }
    expect(graph.querySelector("img, script, iframe")).toBeNull();
    expectInert(graph);
    expect((graph.textContent ?? "").includes("\u202e")).toBe(false);
  });

  it("writes odd characters out as escapes in the filters and the lists", async () => {
    await renderReport("hostile", "/areas");
    for (const option of document.querySelectorAll("option")) {
      expect(option.textContent).not.toMatch(new RegExp(`[${[0x202e, 0x2028, 0x2029, 0x1b, 0xd800].map((c) => String.fromCharCode(c)).join('')}]`));
    }
    expect(pageText()).toContain("\\u202e");
  });

  it("cuts a 2 MB line", async () => {
    const report = fixture("s13");
    report.areas![0].id = "a-0123456789abcdef";
    (report.findings ?? [])[0].evidence[0].detail = "X".repeat(2_000_000) + "</script>";
    report.areas![0].chains[0][0].detail = "Y".repeat(2_000_000) + "</script>";
    await renderReport(report, "/findings/" + report.findings![0].id);
    expect(pageText().length).toBeLessThan(400_000);
    expect(pageText()).toContain("[cut: 1998009 more characters in the report JSON]");
  });

  it("shows a hostile reason as text, quoted and attributed", async () => {
    const report = fixture("s13");
    const area = (report.areas ?? [])[0];
    const reason = '</script><img src=x onerror=alert(1)> "safe"\n# heading';
    report.review!.decisions!.current = [
      { target_id: area.id as string, kind: "area", label: area.label, verdict: "skip", reason, author: "<b>Quinn</b>", channel: "runner", revision: 1, created_at: "2026-01-02T03:04:05Z", fingerprint: "a".repeat(64) },
    ];
    await renderReport(report, `/areas/${area.id}`);
    const text = pageText();
    expect(text).toContain("risk accepted, not tested (reason from <b>Quinn</b>: " + JSON.stringify(reason) + ")");
    expect(document.querySelector("img")).toBeNull();
    expect([...document.querySelectorAll("b")].filter((b) => (b.textContent ?? "").includes("Quinn"))).toEqual([]); // the author is text, not a bold element
    expectPageWordsClean();
  });
});
