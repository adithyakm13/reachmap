import { describe, expect, it } from "vitest";
import { layoutCross, layoutReach } from "../graphs/layout";
import type { Report } from "../api/types";
import { fixture } from "../test/fixtures";
import { branchGrid, MAX_GRID_FILES } from "./grid";
import { chainGraph, crossGraph, reachGraph, REACH_AREAS_PER_REPO } from "./graph";
import { languageOf } from "./lang";
import { statsOf } from "./stats";
import { allAreas, build, type Ev } from "./view";
import { duration, relative } from "../time";
import { publishNonce, readNonce } from "../nonce";
import { isTyping, takesEnter, takesSpace } from "../keys";

function ev(over: Partial<Ev>): Ev {
  return { repo: "acme/api", path: "a.py", line: 1, ref: "r".repeat(40), short: "rrrrrrrrrrrr", detail: "x", locator: "a.py:1", claim: "", ...over };
}

describe("the chain graph", () => {
  const cross = build(fixture("s13")).cross;

  it("makes a node per step and an edge between steps, left to right in the order of the report", () => {
    const g = chainGraph([ev({ path: "a.py" }), ev({ path: "b.py" }), ev({ path: "c.py" })], cross);
    expect(g.nodes.map((n) => n.id)).toEqual(["h0", "h1", "h2"]);
    expect(g.edges.map((e) => [e.source, e.target])).toEqual([["h0", "h1"], ["h1", "h2"]]);
  });

  it("names an edge only when the report names its kind: by the claim of the step it arrives at", () => {
    const g = chainGraph([ev({ claim: "import:x" }), ev({ claim: "route:GET /x:y" }), ev({ claim: "" }), ev({ claim: "package:p" }), ev({ claim: "weird:z" })], cross);
    expect(g.edges.map((e) => e.label)).toEqual(["route", null, "package", null]);
  });

  it("never reads a claim such as __proto__ or constructor as a kind", () => {
    const g = chainGraph([ev({}), ev({ claim: "__proto__:x" }), ev({ claim: "constructor" }), ev({ claim: "toString:y" })], cross);
    expect(g.edges.map((e) => e.label)).toEqual([null, null, null]);
  });

  it("has a lane for each repository in the order the repositories first appear, and names the edge that crosses repositories from the hops the run followed", () => {
    const hops = [{ kind: "http", fromRepo: "acme/web", toRepo: "acme/api", depth: 1, detail: "", candidate: false, complete: true, reasons: [], pins: [] }];
    const g = chainGraph([ev({ repo: "acme/web" }), ev({ repo: "acme/api" }), ev({ repo: "acme/api" }), ev({ repo: "acme/web" })], { ...cross, hops });
    expect(g.lanes).toEqual(["acme/web", "acme/api"]);
    expect(g.nodes.map((n) => n.lane)).toEqual([0, 1, 1, 0]);
    expect(g.edges.map((e) => e.label)).toEqual(["http", null, "http"]);
  });

  it("is empty for an empty chain", () => {
    const g = chainGraph([], cross);
    expect([g.nodes.length, g.edges.length, g.lanes.length]).toEqual([0, 0, 0]);
  });
});

describe("the reach graph", () => {
  it("has a group for the repository of the change and each repository it reaches, with the areas inside and the edges between", () => {
    const view = build(fixture("complete"));
    const g = reachGraph(view, allAreas(view));
    expect(g.repos.map((r) => r.repo)).toContain("acme/api");
    expect(g.repos.find((r) => r.repo === view.header.repo)?.origin).toBe(true);
    expect(g.repos.flatMap((r) => r.areas).length).toBe(allAreas(view).length);
    expect(g.edges.length).toBeGreaterThan(0);
    expect(g.edges.every((e) => g.repos.some((r) => r.repo === e.from) && g.repos.some((r) => r.repo === e.to))).toBe(true);
  });

  it("marks what was matched by name as a candidate", () => {
    const view = build(fixture("complete"));
    const g = reachGraph(view, allAreas(view));
    expect(g.repos.flatMap((r) => r.areas).filter((a) => a.candidate).length).toBe(view.candidates.length);
  });

  it("shows at most a few areas of a repository and counts the rest", () => {
    const report = fixture("s13") as Report;
    const base = report.areas![0];
    report.areas = Array.from({ length: 12 }, (_, i) => ({ ...base, label: `/screen-${i}`, id: `a-${i.toString(16).padStart(16, "0")}` }));
    const view = build(report);
    const g = reachGraph(view, allAreas(view));
    const mine = g.repos.find((r) => r.repo === "acme/api")!;
    expect(mine.areas.length).toBe(REACH_AREAS_PER_REPO);
    expect(mine.hidden).toBe(12 - REACH_AREAS_PER_REPO);
  });

  it("is laid out with the areas inside their group", () => {
    const view = build(fixture("complete"));
    const laid = layoutReach(reachGraph(view, allAreas(view)));
    const groups = laid.nodes.filter((n) => n.type === "repo");
    const areas = laid.nodes.filter((n) => n.type === "area");
    expect(groups.length).toBeGreaterThan(0);
    for (const a of areas) {
      expect(groups.some((g) => g.id === a.parentId)).toBe(true);
      expect(a.extent).toBe("parent");
    }
    for (const e of laid.edges) {
      expect(groups.some((g) => g.id === e.source)).toBe(true);
    }
  });
});

describe("the cross-repository graph", () => {
  it("has the repositories and the edges the run followed, packages with the pin and adoption of each branch", () => {
    const view = build(fixture("s11"));
    const g = crossGraph(view);
    expect(g.repos.length).toBeGreaterThan(1);
    const kinds = new Set(g.edges.map((e) => e.kind));
    expect(kinds.has("contract") || kinds.has("package") || kinds.has("http")).toBe(true);
    for (const e of g.edges) {
      expect(g.repos).toContain(e.from);
      expect(g.repos).toContain(e.to);
    }
    const withNotes = g.edges.filter((e) => e.notes.length > 0);
    expect(withNotes.length).toBeGreaterThan(0);
    expect(withNotes[0].notes[0]).toHaveProperty("adoption");
  });

  it("keeps a candidate apart from an edge the run followed, even between the same repositories", () => {
    const report = fixture("s11");
    const prop = report.propagations![0] as { contract_reads: Record<string, unknown>[]; contract_candidates: Record<string, unknown>[] };
    prop.contract_candidates = prop.contract_reads.map((r) => ({ ...r }));
    const g = crossGraph(build(report));
    const pairs = g.edges.filter((e) => e.kind === "contract");
    expect(pairs.some((e) => e.candidate)).toBe(true);
    expect(pairs.some((e) => !e.candidate)).toBe(true);
    const laid = layoutCross(g);
    expect(laid.edges.some((e) => String(e.className).includes("rm-edge-candidate"))).toBe(true);
    expect(laid.edges.some((e) => e.className === "rm-edge")).toBe(true);
  });

  it("has nothing for a report that reaches no other repository", () => {
    const report = fixture("s13");
    const prop = report.propagations![0] as Record<string, unknown>;
    for (const key of ["hops", "consumers", "http_edges", "contract_changes", "contract_reads", "contract_candidates"]) {
      prop[key] = [];
    }
    expect(crossGraph(build(report)).edges).toEqual([]);
  });
});

describe("the branch grid", () => {
  it("has a row per branch and a column per changed file, with the state of each file read from the words of the report", () => {
    const g = branchGrid(build(fixture("s13")));
    expect(g.rows.map((r) => r.branch)).toEqual(["main", "release/1.2", "release/1.x"]);
    expect(g.files).toEqual(["services/billing/app/services/totals.py"]);
    expect(g.rows.map((r) => r.cells[0].state)).toEqual(["pre_change", "present", "present"]);
    expect(g.rows[1].cells[0].evidence.length).toBe(2);
    expect(g.rows[1].found).toBe(2);
  });

  it("reads a file's state only from the exact words a row writes for it, and says nothing for a text that says two things", () => {
    const report = fixture("s13");
    report.change.files = [
      { path: "a.py", old_path: null, status: "modified", role: "source", added: 1, removed: 1 },
      { path: "b.py", old_path: null, status: "modified", role: "source", added: 1, removed: 1 },
      { path: "c.py", old_path: null, status: "modified", role: "source", added: 1, removed: 1 },
    ];
    report.branches![1].detail = "a.py: present (2 of 2 added lines found); b.py: diverged (0 of 2 added lines found); c.py: partial: present";
    report.branches![1].state = "partial";
    const row = branchGrid(build(report)).rows[1];
    expect(row.cells.map((c) => c.state)).toEqual(["present", "diverged", null]);
  });

  it("keeps a state out of a cell when the path only appears inside another name", () => {
    const report = fixture("s13");
    report.change.files = [{ path: "a.py", old_path: null, status: "modified", role: "source", added: 1, removed: 1 }, { path: "b.py", old_path: null, status: "modified", role: "source", added: 1, removed: 1 }];
    report.branches![1].detail = "xa.py: present (1 of 1); b.py: partial (1 of 2)";
    const row = branchGrid(build(report)).rows[1];
    expect(row.cells.map((c) => c.state)).toEqual([null, "partial"]);
  });

  it("puts an unreadable file in its cell, and one branch with several rows in one grid row", () => {
    const g = branchGrid(build(fixture("complete")));
    const old = g.rows.find((r) => r.branch === "release/old")!;
    expect(old.states).toEqual(["unreadable"]);
    expect(old.cells[0].state).toBeNull(); // the words of this fixture do not name the file the way the analyser does
    const report = fixture("s13");
    const extra = { ...report.branches![1], state: "unreadable", evidence: [], detail: "services/billing/app/services/totals.py: could not be read" };
    report.branches!.splice(2, 0, extra);
    const rows = branchGrid(build(report)).rows;
    expect(rows.filter((r) => r.branch === "release/1.2").length).toBe(1);
    expect(rows.find((r) => r.branch === "release/1.2")!.states).toEqual(["present", "unreadable"]);
    expect(rows.find((r) => r.branch === "release/1.2")!.cells[0].state).toBe("present");
  });

  it("limits the columns and says how many files were left out", () => {
    const report = fixture("s13");
    report.change.files = Array.from({ length: 40 }, (_, i) => ({ path: `f${i}.py`, old_path: null, status: "modified", role: "source", added: 1, removed: 1 }));
    const g = branchGrid(build(report));
    expect(g.files.length).toBe(MAX_GRID_FILES);
    expect(g.hiddenFiles).toBe(10);
  });
});

describe("the statistics of the overview", () => {
  it("counts findings by kind, risk and confidence apart, from the findings that reach something", () => {
    const view = build(fixture("complete"));
    const s = statsOf(view);
    expect(s.byKind.map((k) => k.key)).toEqual(expect.arrayContaining(["direct"]));
    expect(s.risk.reduce((n, x) => n + x.n, 0)).toBe(8);
    expect(s.confidence.reduce((n, x) => n + x.n, 0)).toBe(8);
    expect(s.risk.map((x) => x.key)).toEqual(expect.arrayContaining(["high"]));
    // two separate lists: risk and confidence are never one scale
    expect(s.risk).not.toBe(s.confidence);
  });

  it("orders levels high, medium, low and leaves an unknown level last", () => {
    const report = fixture("complete");
    const plain = report.findings!.filter((f) => !f.is_exclusion);
    ["low", "high", "zz", "medium", "high", "low", "medium", "high"].forEach((risk, i) => {
      plain[i].risk = risk;
    });
    expect(statsOf(build(report)).risk).toEqual([{ key: "high", n: 3 }, { key: "medium", n: 2 }, { key: "low", n: 2 }, { key: "zz", n: 1 }]);
  });

  it("has no decisions to count for a report that cannot take any, and counts them for one that can", () => {
    expect(statsOf(build(fixture("complete"))).progress).toBeNull();
    const p = statsOf(build(fixture("s13"))).progress!;
    expect(p.total).toBe(fixture("s13").areas!.length + fixture("s13").findings!.length);
    expect([p.decided, p.stale, p.undecided, p.orphaned]).toEqual([0, 0, p.total, 0]);
  });

  it("counts current, stale and orphaned decisions apart", () => {
    const report = fixture("s13");
    const [a0, a1] = report.areas!;
    const d = (id: string) => ({ target_id: id, kind: "area", label: "x", verdict: "test", reason: "", author: "Q", channel: "runner", revision: 1, created_at: "t", fingerprint: "f".repeat(64) });
    report.review!.decisions!.current = [d(a0.id as string)];
    report.review!.decisions!.stale = [d(a1.id as string)];
    report.review!.decisions!.orphaned = [d("a-" + "9".repeat(16))];
    const p = statsOf(build(report)).progress!;
    expect([p.decided, p.stale, p.orphaned]).toEqual([1, 1, 1]);
    expect(p.undecided).toBe(p.total - 2);
  });

  it("says nothing about how many are decided when the store of decisions could not be read", () => {
    const report = fixture("s13");
    report.review!.decisions!.records = [{ kind: "decision_store_unreadable", subject: "d", not_established: "x", detail: "", blocks_selection: true }];
    expect(statsOf(build(report)).progress).toBeNull();
  });
});

describe("the language of a cited line", () => {
  it("is taken from the extension, and anything else is plain text", () => {
    expect(languageOf("a/b/c.py")).toBe("python");
    expect(languageOf("src/App.tsx")).toBe("tsx");
    expect(languageOf("x.JSON")).toBe("json");
    expect(languageOf("Makefile")).toBe("text");
    expect(languageOf(".gitignore")).toBe("text");
    expect(languageOf("a.unknownext")).toBe("text");
    expect(languageOf("dir.py/file")).toBe("text");
  });

  it("does not take a path such as __proto__ or constructor for a language", () => {
    for (const path of ["a.__proto__", "a.constructor", "a.toString", "__proto__", "a.hasOwnProperty"]) {
      expect(languageOf(path)).toBe("text");
    }
  });
});

describe("time", () => {
  const now = Date.parse("2026-01-02T12:00:00Z");
  it("says how long ago, in the largest whole unit", () => {
    expect(relative("2026-01-02T11:59:50Z", now)).toBe("just now");
    expect(relative("2026-01-02T11:55:00Z", now)).toBe("5 minutes ago");
    expect(relative("2026-01-02T11:59:00Z", now)).toBe("1 minute ago");
    expect(relative("2026-01-02T09:00:00Z", now)).toBe("3 hours ago");
    expect(relative("2025-12-31T12:00:00Z", now)).toBe("2 days ago");
    expect(relative("2025-10-01T12:00:00Z", now)).toBe("2025-10-01");
    expect(relative("2026-01-03T12:00:00Z", now)).toBe("in the future");
  });

  it("gives nothing for a text that is not a time", () => {
    expect(relative("not a time", now)).toBeNull();
    expect(relative("", now)).toBeNull();
  });

  it("writes a duration", () => {
    expect(duration(1.5)).toBe("1.5 s");
    expect(duration(125)).toBe("2 min 5 s");
    expect(duration(-1)).toBe("");
  });
});

describe("the style nonce", () => {
  function page(content: string): Document {
    const doc = document.implementation.createHTMLDocument("x");
    doc.head.innerHTML = `<meta name="reachmap-style-nonce" content="${content}">`;
    return doc;
  }

  it("is read from the meta element the runner wrote, and the element is taken out", () => {
    const doc = page("AbCdEfGhIjKlMnOpQrStUvWx");
    expect(readNonce(doc)).toBe("AbCdEfGhIjKlMnOpQrStUvWx");
    expect(doc.querySelector("meta")).toBeNull();
  });

  it("is nothing when the runner left the placeholder, or wrote something that is not a nonce", () => {
    expect(readNonce(page("__REACHMAP_STYLE_NONCE__"))).toBeUndefined();
    expect(readNonce(page("short"))).toBeUndefined();
    expect(readNonce(page('"><script>alert(1)</script>'.replace(/"/g, "&quot;")))).toBeUndefined();
    expect(readNonce(document.implementation.createHTMLDocument("x"))).toBeUndefined();
  });

  it("is given to the libraries that make their own style element", () => {
    const g = globalThis as { __webpack_nonce__?: string };
    delete g.__webpack_nonce__;
    publishNonce(undefined);
    expect(g.__webpack_nonce__).toBeUndefined();
    publishNonce("AbCdEfGhIjKlMnOpQrStUvWx");
    expect(g.__webpack_nonce__).toBe("AbCdEfGhIjKlMnOpQrStUvWx");
    delete g.__webpack_nonce__;
  });
});

describe("who has the key", () => {
  function el(html: string): HTMLElement {
    const host = document.createElement("div");
    host.innerHTML = html;
    return host.firstElementChild as HTMLElement;
  }

  it("is a text field, a text area, a list of choices or an editable element when a person is typing", () => {
    for (const html of ['<input type="text">', "<input>", '<input type="search">', '<input type="number">', "<textarea></textarea>", "<select></select>", '<div contenteditable="true"></div>']) {
      expect(isTyping(el(html)), html).toBe(true);
    }
    for (const html of ['<input type="checkbox">', '<input type="radio">', "<button></button>", "<a href=\"#x\">x</a>", "<div></div>"]) {
      expect(isTyping(el(html)), html).toBe(false);
    }
    expect(isTyping(null)).toBe(false);
  });

  it("leaves Enter to a button, a link, a summary and a check box, and Space to all of them but a link", () => {
    for (const html of ["<button></button>", '<a href="#x">x</a>', "<summary></summary>", '<input type="checkbox">', '<input type="radio">']) {
      expect(takesEnter(el(html)), html).toBe(true);
    }
    for (const html of ["<button></button>", "<summary></summary>", '<input type="checkbox">', '<input type="radio">']) {
      expect(takesSpace(el(html)), html).toBe(true);
    }
    // Space does nothing on a link: the shortcut of the page may take it
    expect(takesSpace(el('<a href="#x">x</a>'))).toBe(false);
    for (const html of ["<div></div>", '<input type="text">', "<tr></tr>"]) {
      expect(takesEnter(el(html)), html).toBe(false);
      expect(takesSpace(el(html)), html).toBe(false);
    }
  });
});
