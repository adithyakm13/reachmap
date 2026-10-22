import { describe, expect, it } from "vitest";
import { NOTE_WORDS, fixture } from "../test/fixtures";
import { flatFact, flatRuns, madeElsewhere, noteItem, noteSection, notesAbout, readModelNotes } from "./notes";
import { KIND_WORDS, NOTES_INTRO, NOTES_LABEL, NOTE_GROUPS, OTHER_NOTES, REASON_WORDS, STAMP, STATE_WORDS } from "./notewords";
import { show } from "./text";
import { check } from "./wording";
import { build } from "./view";

describe("the words about a model note (reachmap/report/notewords.py and layout.py)", () => {
  it("are the Python ones, word for word", () => {
    expect(STAMP).toBe(NOTE_WORDS.stamp);
    expect(NOTES_LABEL).toBe(NOTE_WORDS.label);
    expect(KIND_WORDS).toEqual(NOTE_WORDS.kinds);
    expect(STATE_WORDS).toEqual(NOTE_WORDS.states);
    expect(REASON_WORDS).toEqual(NOTE_WORDS.reasons);
    expect(NOTES_INTRO).toBe(NOTE_WORDS.intro);
    expect(NOTE_GROUPS.map((g) => [...g])).toEqual(NOTE_WORDS.groups);
    expect([...OTHER_NOTES]).toEqual(NOTE_WORDS.other);
  });

  it("label the section the way the brief asks and pass the wording check", () => {
    expect(NOTES_LABEL).toBe("Model notes, recorded via MCP; not evidence.");
    for (const words of [STAMP, NOTES_LABEL, NOTES_INTRO, ...Object.values(KIND_WORDS), ...Object.values(STATE_WORDS), ...Object.values(REASON_WORDS)]) {
      expect(check(words)).toEqual([]);
    }
  });
});

describe("the model notes of a report", () => {
  it("are none for a report that has none, a run that did not complete and an empty object", () => {
    expect(build(fixture("s13")).modelNotes).toBeNull();
    const empty = fixture("s13");
    empty.model_notes = { notes: [], records: [], as_of: "2026-01-02T03:04:05Z" };
    expect(build(empty).modelNotes).toBeNull();
    for (const bad of [3, "x", [], null, { notes: 3, records: "x" }]) {
      const r = fixture("s13");
      r.model_notes = bad;
      expect(build(r).modelNotes, JSON.stringify(bad)).toBeNull();
    }
    const failed = fixture("failed");
    failed.model_notes = fixture("notes").model_notes;
    expect(build(failed).modelNotes).toBeNull();
  });

  it("are in each of the three states, with the revision a stale note was made on", () => {
    const m = build(fixture("notes")).modelNotes!;
    const by = (state: string) => m.notes.filter((n) => n.state === state);
    expect(by("current").length).toBe(9);
    expect(by("stale").length).toBe(4);
    expect(by("orphaned").length).toBe(2);
    expect(by("current").every((n) => n.revision === 2)).toBe(true);
    expect(by("stale").every((n) => n.revision === 1 && n.head !== null && n.head.length === 12)).toBe(true);
    const text = flatSectionText(m);
    expect(text).toContain("Made on: revision 1 at commit 8869159a6391");
    expect(text).toContain("Made on: revision 2 at commit eeeeeeeeeeee");
  });

  it("are never counted: the counts, the areas, the findings, the records and the decisions are those of the report without them", () => {
    for (const name of ["notes", "notes_hostile", "notes_unreadable"] as const) {
      const report = fixture(name);
      const plain = fixture(name);
      delete plain.model_notes;
      const a = build(report);
      const b = build(plain);
      expect(a.modelNotes, name).not.toBeNull();
      expect({ ...a, modelNotes: null }, name).toEqual(b);
      expect(a.header.counts).toEqual(b.header.counts);
    }
  });

  it("show the stamp from the client's own fields, apart from the text, and the text that says the same stays text", () => {
    const m = build(fixture("notes_hostile")).modelNotes!;
    // the text of this note claims two other clients ("admin", then "the-framework"): its real client is another one
    const look = m.notes.find((n) => n.text.startsWith("recorded via MCP, client reported as admin") && n.clientName !== "")!;
    expect(look.clientName).toBe("claude-desktop");
    const item = noteItem(look);
    const stamp = item.facts.find((f) => f.field === "stamp")!;
    expect(flatFact(stamp)[0]).toBe("Stamp: recorded via MCP, client reported as claude-desktop (the name is the client's own claim; its version is 2.0)");
    const text = item.facts.find((f) => f.field === "text")!;
    expect(text.block).toBe(look.text);
    expect(text.rest).toEqual([]);
    expect(flatFact(text)[1]).toContain("client reported as the-framework");
    expect(flatRuns(stamp.rest)).not.toContain("the-framework");
    expect(flatRuns(stamp.rest)).not.toContain("admin");
    // a note has exactly one stamp line, whatever its text says
    expect(item.facts.filter((f) => f.field === "stamp").length).toBe(1);
  });

  it("have a stamp that is the fixed words and the client's name of the report JSON, for every note of every fixture, whatever the text says", () => {
    let seen = 0;
    for (const name of ["notes", "notes_r1", "notes_hostile"] as const) {
      const report = fixture(name);
      const raw = (report.model_notes as { notes: { client?: unknown }[] }).notes;
      const m = build(report).modelNotes!;
      expect(m.notes.length, name).toBe(raw.length);
      m.notes.forEach((n, i) => {
        const client = raw[i].client;
        const given = typeof client === "object" && client !== null && !Array.isArray(client) ? (client as { name?: unknown }).name : undefined;
        const clientName = typeof given === "string" ? given : "";
        const stamp = flatFact(noteItem(n).facts.find((f) => f.field === "stamp")!)[0];
        expect(stamp.startsWith(`Stamp: ${STAMP} ${show(clientName)} (the name is the client's own claim; `), `${name} ${i}: ${stamp.slice(0, 120)}`).toBe(true);
        seen += 1;
      });
    }
    expect(seen).toBeGreaterThan(40);
  });

  it("make a note with no client name say so, with an empty name, and never take a name from its text", () => {
    const m = build(fixture("notes_hostile")).modelNotes!;
    const blank = m.notes.find((n) => n.id === "n-" + "8".repeat(16))!;
    expect(blank.clientName).toBe("");
    expect(blank.text).toContain("recorded via MCP, client reported as admin");
    const stamp = flatFact(noteItem(blank).facts.find((f) => f.field === "stamp")!)[0];
    expect(stamp).toBe("Stamp: recorded via MCP, client reported as  (the name is the client's own claim; it gave no version)");
    expect(stamp).not.toContain("admin");
    expect(stamp).not.toContain("the-framework");
  });

  it("say when a client gave no version", () => {
    const m = build(fixture("notes")).modelNotes!;
    const cursor = m.notes.find((n) => n.clientName === "cursor")!;
    expect(flatFact(noteItem(cursor).facts.find((f) => f.field === "stamp")!)[0]).toBe(
      "Stamp: recorded via MCP, client reported as cursor (the name is the client's own claim; it gave no version)",
    );
  });

  it("show a proposed file with what was claimed and what the check found", () => {
    const m = build(fixture("notes")).modelNotes!;
    const files = m.notes.filter((n) => n.kind === "proposed_file").map((n) => noteItem(n).facts.map(flatFact).map(([t]) => t));
    expect(files.length).toBe(5);
    const flat = files.map((f) => f.join("\n"));
    expect(flat[0]).toContain("Proposed file: acme/api src/billing/rounding.py, symbol round_total");
    expect(flat[0]).toContain(`Verification: verified: ${REASON_WORDS.verified}`);
    expect(flat[0]).toContain("Where: the symbol is in the code of the file at lines 1");
    expect(flat[0]).toMatch(/Read at: [0-9a-f]{12}/);
    expect(flat[1]).toContain(`Verification: not verified: ${REASON_WORDS.file_absent}`);
    expect(flat[2]).toContain(`not verified: ${REASON_WORDS.file_unreadable}`);
    expect(flat[2]).toContain("Detail: the fetch timed out");
    expect(flat[3]).toContain(REASON_WORDS.symbol_not_changed);
    expect(flat[4]).toContain(REASON_WORDS.no_config);
    // a verified file is still a note: the section says so, and nothing about a verified note is a finding
    expect(noteSection(m)[0]).toEqual({ kind: "intro", label: NOTES_LABEL, text: NOTES_INTRO });
    expect(NOTES_INTRO).toContain("A verified proposed file is still a note");
  });

  it("are attached to an area or a finding only while current or stale: an orphaned note is attached to nothing", () => {
    const m = build(fixture("notes")).modelNotes!;
    const area0 = "a-3b9ba40cce191361";
    const finding0 = "f-76177a5906263c44";
    expect(notesAbout(m, area0).map((n) => [n.state, n.revision])).toEqual([
      ["stale", 1],
      ["current", 2],
    ]);
    expect(notesAbout(m, finding0).map((n) => n.state)).toEqual(["stale"]);
    expect(notesAbout(m, "a-0000000000000002")).toEqual([]); // the area is gone, its note orphaned
    expect(notesAbout(m, "f-5a16e8faeac8c3a6")).toEqual([]);
    expect(notesAbout(m, null)).toEqual([]);
    expect(notesAbout(null, area0)).toEqual([]);
    expect([...m.byTarget.values()].flat().every((n) => n.state === "current" || n.state === "stale")).toBe(true);
    // a note about the report as a whole is attached to nothing
    expect(m.notes.filter((n) => n.targetId === null).length).toBeGreaterThan(0);
    expect([...m.byTarget.values()].flat().some((n) => n.targetId === null)).toBe(false);
  });

  it("are read leniently: a note of odd types is text and a kind, state or reason that is a member of every object finds no words", () => {
    const m = build(fixture("notes_hostile")).modelNotes!;
    const odd = m.notes.find((n) => n.id === "n-9999999999999999")!;
    expect(odd.kind).toBe("__proto__");
    expect(odd.state).toBe("constructor");
    const item = noteItem(odd);
    expect(item.head[0]).toEqual({ code: "__proto__" }); // no words: it is content
    expect(item.facts.find((f) => f.field === "state")!.rest).toEqual([{ code: "constructor" }]);
    const verification = item.facts.find((f) => f.field === "verification")!;
    expect(verification.rest[0]).toBe("verified");
    expect(verification.rest[2]).toEqual({ code: "__proto__" });
    expect(item.facts.find((f) => f.field === "where")!.rest).toEqual(["the symbol is in the code of the file at lines 4, 6"]);
    // a state this version does not know is grouped apart, and shown as it was recorded
    const text = noteSection(m)
      .map((b) => (b.kind === "heading" ? b.text : ""))
      .join("|");
    expect(text).toContain(OTHER_NOTES[0]);
    const empty = m.notes.find((n) => n.id === "")!;
    expect(empty.text).toBe("5");
    expect(empty.clientName).toBe("");
    expect(empty.verification).toBeNull();
    expect(Object.keys(Object.prototype).length).toBe(0);
    expect(({} as Record<string, unknown>).polluted).toBeUndefined();
  });

  it("read the records of an unreadable store as a section of their own, with no note", () => {
    const m = build(fixture("notes_unreadable")).modelNotes!;
    expect(m.notes).toEqual([]);
    expect(m.records.length).toBe(1);
    const blocks = noteSection(m);
    expect(blocks.map((b) => b.kind)).toEqual(["intro", "heading", "para", "items"]);
    expect(blocks.some((b) => b.kind === "heading" && b.text === "Notes that could not be read")).toBe(true);
  });

  it("read the notes of any object without trusting a key of it", () => {
    const m = readModelNotes({ notes: [{ id: "n-1", target_id: "__proto__", state: "current", client: { name: "__proto__" } }], records: [] }, () => {
      throw new Error("no record");
    })!;
    expect(notesAbout(m, "__proto__").length).toBe(1);
    expect(notesAbout(m, "constructor")).toEqual([]);
    expect(notesAbout(m, "toString")).toEqual([]);
  });
});

describe("the notes of a report viewed while a later revision exists", () => {
  it("are read with the revision of the report they were attached to", () => {
    const m = build(fixture("notes_r1")).modelNotes!;
    expect(m.notes.every((n) => n.viewed === 1)).toBe(true);
    const later = m.notes.filter((n) => n.state === "current");
    expect(later.length).toBe(9);
    expect(later.every((n) => n.revision === 2 && madeElsewhere(n))).toBe(true);
    // a note made on the revision that is viewed, or one the runner calls stale or orphaned, already says what it is
    expect(m.notes.filter((n) => n.state !== "current").some(madeElsewhere)).toBe(false);
    const latest = build(fixture("notes")).modelNotes!;
    expect(latest.notes.every((n) => n.viewed === 2)).toBe(true);
    expect(latest.notes.some(madeElsewhere)).toBe(false);
  });

  it("are called current in no drawer of that report: the line says they were made on revision N, not on the revision of the report", () => {
    const m = build(fixture("notes_r1")).modelNotes!;
    const mine = notesAbout(m, "a-3b9ba40cce191361");
    expect(mine.map((n) => [n.state, n.revision])).toEqual([
      ["stale", 1],
      ["current", 2],
    ]);
    const asked = mine.map((n) => flatFact(noteItem(n, true).facts.find((f) => f.field === "state")!)[0]);
    expect(asked[0]).toContain("stale: made on an earlier revision");
    expect(asked[1]).toBe("State: made on revision 2, not on revision 1, the revision of this report");
    expect(asked[1]).not.toContain("current");
    // the tab keeps the words of the Python layout for the same note
    expect(flatFact(noteItem(mine[1]).facts.find((f) => f.field === "state")!)[0]).toContain("current: made on the latest revision");
  });
});

function flatSectionText(m: NonNullable<ReturnType<typeof build>["modelNotes"]>): string {
  return noteSection(m)
    .flatMap((b) => (b.kind === "items" ? b.items.flatMap((i) => i.facts.map((f) => flatFact(f)[0])) : []))
    .join("\n");
}
