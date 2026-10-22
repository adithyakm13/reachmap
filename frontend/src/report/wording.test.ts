import { describe, expect, it } from "vitest";
import cases from "../../fixtures/wording_cases.json";
import { BANNED, WordingError, check, clean, requireClean } from "./wording";

const PHRASES = ["covered by", "already covers", "no need to test", "nothing to test", "safe", "is safe", "safe to release", "fully covered", "no impact"];

describe("the banned wording (a mirror of reachmap/wording.py)", () => {
  it("has every phrase of the brief", () => {
    for (const phrase of PHRASES) {
      expect(BANNED).toContain(phrase);
    }
  });

  it("finds each banned phrase", () => {
    for (const phrase of PHRASES) {
      expect(check(`The change is ${phrase} here`)).toContain(phrase);
    }
  });

  it("is case-insensitive and tolerates extra whitespace inside a phrase", () => {
    expect(check("SAFE")).toContain("safe");
    expect(check("Covered By a test")).toContain("covered by");
    expect(check("No Need To Test this")).toContain("no need to test");
    expect(check("no  need\tto test")).toContain("no need to test");
  });

  it("matches on word boundaries only", () => {
    for (const text of ["unsafe", "safety", "safely", "safeguard", "unsafely"]) {
      expect(check(text)).toEqual([]);
    }
    expect(check("a safe place")).toEqual(["safe"]);
  });

  it("counts a hyphen as a space inside a phrase", () => {
    const cases: [string, string][] = [
      ["fully-covered", "fully covered"],
      ["no-impact", "no impact"],
      ["covered-by tests", "covered by"],
      ["no-need-to-test", "no need to test"],
      ["safe-to-release", "safe to release"],
    ];
    for (const [text, phrase] of cases) {
      expect(check(text)).toContain(phrase);
    }
  });

  it("returns nothing for clean text", () => {
    expect(check("Callers of formatAmount were not established")).toEqual([]);
    expect(check("")).toEqual([]);
  });

  it("names the field and the phrase", () => {
    expect(() => requireClean("This is covered by tests", "title")).toThrow(WordingError);
    expect(() => requireClean("This is covered by tests", "title")).toThrow(/title.*covered by/);
    expect(requireClean("Screens reached through StatusBadge", "title")).toBe("Screens reached through StatusBadge");
  });

  it("gives what reachmap/wording.py gives for every shared case (frontend/fixtures/wording_cases.json, also tested in Python)", () => {
    const shared = (cases as { cases: { text: string; found: string[] }[] }).cases;
    expect(shared.length).toBeGreaterThan(50);
    for (const c of shared) {
      expect(check(c.text), JSON.stringify(c.text)).toEqual(c.found);
    }
  });

  it("counts the whitespace of Python, not of JavaScript, between the words of a phrase", () => {
    expect(check("no\u001cimpact")).toEqual(["no impact"]);
    expect(check("no\u0085impact")).toEqual(["no impact"]);
    expect(check("no\ufeffimpact")).toEqual([]);
  });

  it("checks every value of a table of words", () => {
    expect(() => clean({ a: "fine", b: "nothing to test" }, "table")).toThrow(/table\.b/);
    expect(clean({ a: "fine" }, "table")).toEqual({ a: "fine" });
  });
});
