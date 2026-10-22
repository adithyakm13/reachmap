import { describe, expect, it } from "vitest";
import { CONTENT_LIMIT, length, show, visible } from "./text";

describe("text.visible (a port of reachmap/report/text.py)", () => {
  it("leaves plain text, tab and newline alone", () => {
    expect(visible("def f(x):\n\treturn x")).toBe("def f(x):\n\treturn x");
    expect(visible("café 中")).toBe("café 中");
  });

  it("writes out control, direction, zero-width and separator characters", () => {
    expect(visible("a\u202eb")).toBe("a\\u202eb");
    expect(visible("a\u2028b\u2029c")).toBe("a\\u2028b\\u2029c");
    expect(visible("\u001b[31m")).toBe("\\x1b[31m");
    expect(visible("a\u200bb\ufeffc")).toBe("a\\u200bb\\ufeffc");
    expect(visible("a\u0000b")).toBe("a\\x00b");
    expect(visible("\u0085")).toBe("\\x85");
  });

  it("writes out a lone surrogate and keeps a pair", () => {
    expect(visible("x\ud800y")).toBe("x\\ud800y");
    expect(visible("smile 😀")).toBe("smile 😀");
    expect(visible("\u{e0001}")).toBe("\\U000e0001"); // a tag character (above U+FFFF)
  });

  it("keeps the replacement character, which is a plain letter-like symbol", () => {
    expect(visible("a�b")).toBe("a�b");
  });
});

describe("text.show", () => {
  it("cuts a long piece first and says how much was left out", () => {
    const text = "x".repeat(CONTENT_LIMIT + 5);
    expect(show(text)).toBe("x".repeat(CONTENT_LIMIT) + " [cut: 5 more characters in the report JSON]");
    expect(show("x".repeat(CONTENT_LIMIT + 1))).toBe("x".repeat(CONTENT_LIMIT) + " [cut: 1 more character in the report JSON]");
  });

  it("counts characters, not UTF-16 units", () => {
    const astral = "\u{1f600}".repeat(CONTENT_LIMIT);
    expect(astral.length).toBe(2 * CONTENT_LIMIT);
    expect(show(astral)).toBe(astral);
    expect(length(astral)).toBe(CONTENT_LIMIT);
  });

  it("makes a 2 MB line a few thousand characters and ends the odd ones as escapes", () => {
    const big = "X".repeat(2_000_000) + "</script><script>alert(1)</script>";
    const shown = show(big);
    expect(shown.length).toBeLessThan(2100);
    expect(shown).toContain("[cut: 1998034 more characters in the report JSON]");
    expect(show("\u202e" + "y".repeat(10))).toBe("\\u202e" + "y".repeat(10));
  });
});
