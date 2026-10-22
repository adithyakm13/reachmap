// Showing text that came from somewhere else (a source line, a path, a record's detail) without sending it: the port of
// `reachmap/report/text.py`, so that the page, the Markdown and the page of `reachmap report` write the same escapes.
//
// Control, bidi and zero-width characters, line separators and lone surrogates are written out as `\xNN` or `\uNNNN`, so that they can
// be read and cannot act (a right-to-left override that reorders a path, a surrogate no file can hold), and a very long line is cut
// and says so. The full text stays in the report JSON.

export const CONTENT_LIMIT = 2000;

const ODD = /[\p{Cc}\p{Cf}\p{Cs}\p{Zl}\p{Zp}]/u;
const ODD_ALL = /[\p{Cc}\p{Cf}\p{Cs}\p{Zl}\p{Zp}]/gu;
const PLAIN = /^[\x20-\x7e\t\n]*$/;

function escapeChar(ch: string): string {
  const n = ch.codePointAt(0) ?? 0;
  if (n < 0x100) {
    return "\\x" + n.toString(16).padStart(2, "0");
  }
  if (n < 0x10000) {
    return "\\u" + n.toString(16).padStart(4, "0");
  }
  return "\\U" + n.toString(16).padStart(8, "0");
}

/** `text` with every character that could act without being seen written out (tab and newline stay). */
export function visible(text: string): string {
  if (PLAIN.test(text) || !ODD.test(text)) {
    return text;
  }
  return text.replace(ODD_ALL, (ch) => (ch === "\t" || ch === "\n" ? ch : escapeChar(ch)));
}

/** The number of characters (code points) of a text. */
export function length(text: string): number {
  let n = 0;
  for (const _ of text) {
    n += 1;
  }
  return n;
}

function head(text: string, limit: number): string {
  let out = "";
  let n = 0;
  for (const ch of text) {
    if (n === limit) {
      break;
    }
    out += ch;
    n += 1;
  }
  return out;
}

/** What a rendering shows of a piece of content: cut first (a 2 MB line is never scanned character by character), then made visible. */
export function show(text: string, limit: number = CONTENT_LIMIT): string {
  if (text.length <= limit) {
    return visible(text); // at most `limit` characters: UTF-16 units are never fewer than code points
  }
  const total = length(text);
  if (total <= limit) {
    return visible(text);
  }
  const more = total - limit;
  return `${visible(head(text, limit))} [cut: ${more} more character${more === 1 ? "" : "s"} in the report JSON]`;
}

/** A short form for a label of a graph or a menu: made visible, and cut at `n` characters with three dots (the whole text is on the page elsewhere). */
export function clip(text: string, n: number): string {
  const shown = show(text);
  return length(shown) <= n ? shown : `${head(shown, n)}...`;
}
