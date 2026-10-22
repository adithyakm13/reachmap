// Banned wording (context section 4, rule 2): no output claims safety or coverage. A mirror of `reachmap/wording.py`.
//
// Matching is case-insensitive on word boundaries, so "safe" matches but "unsafe", "safety" and "safely" do not. Between the words of a
// phrase any run of whitespace or hyphens matches ("fully-covered" is "fully covered"). Gaps are worded "no mapped test", never "not
// covered". Applied to everything this page says itself; never to content copied from a report (a source line, a path, a title).

export const BANNED: readonly string[] = [
  "covered by",
  "already covers",
  "no need to test",
  "nothing to test",
  "safe",
  // close variants
  "is safe",
  "safe to release",
  "fully covered",
  "no impact",
];

function escapeRegExp(word: string): string {
  return word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// Python's `\s` for a text: the characters `str.isspace()` is true for. (The `\s` of JavaScript differs: it has U+FEFF and lacks U+001C to
// U+001F and U+0085.)
const SPACE = "\\t\\n\\v\\f\\r\\x1c-\\x1f \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
// Python's `\b` is a change between a word character (`str.isalnum()` or an underscore) and anything else.
const WORD_CHAR = /^[\p{L}\p{N}_]$/u;

const PATTERNS: readonly (readonly [string, RegExp])[] = BANNED.map((phrase) => {
  const words = phrase.split(/\s+/).map(escapeRegExp).join(`[${SPACE}-]+`);
  return [phrase, new RegExp(words, "giu")] as const;
});

function before(text: string, index: number): string {
  const unit = text.charCodeAt(index - 1);
  const pair = unit >= 0xdc00 && unit <= 0xdfff && index >= 2 && text.charCodeAt(index - 2) >= 0xd800 && text.charCodeAt(index - 2) <= 0xdbff;
  return text.slice(pair ? index - 2 : index - 1, index);
}

function after(text: string, index: number): string {
  const code = text.codePointAt(index);
  return code === undefined ? "" : String.fromCodePoint(code);
}

function found(pattern: RegExp, text: string): boolean {
  pattern.lastIndex = 0;
  for (let m = pattern.exec(text); m !== null; m = pattern.exec(text)) {
    const b = m.index > 0 ? before(text, m.index) : "";
    const a = after(text, m.index + m[0].length);
    // the match is a whole word or run of words: no word character touches it on either side (the case folding of the pattern must not
    // reach into the boundary test, so the test is made here and not in the pattern)
    if (!WORD_CHAR.test(b) && !WORD_CHAR.test(a)) {
      return true;
    }
    pattern.lastIndex = m.index + 1; // as the regular expressions of Python do: try again from the next position
  }
  return false;
}

/**
 * The banned phrases found in `text`, in the order of BANNED. Matches `reachmap/wording.py` for every text, with these known differences:
 * a character that Python's Unicode tables (the version of its `unicodedata`) do not know as a letter or a digit and the browser's do (a
 * letter added to Unicode after the Python in use) counts as a word character here; no other difference was found by comparing every code
 * point next to each phrase (`frontend/fixtures/wording_cases.json` holds the cases both sides are tested with).
 */
export function check(text: string): string[] {
  // Python's case-insensitive match takes U+0130 (lower-cased to "i") and U+0131 (an equivalence of its own) for "i"; the case folding of
  // JavaScript leaves both alone
  const folded = text.replace(/[\u0130\u0131]/g, "i");
  return PATTERNS.filter(([, pattern]) => found(pattern, folded)).map(([phrase]) => phrase);
}

export class WordingError extends Error {
  constructor(field: string, found: readonly string[]) {
    super(`${field}: banned wording ${found.map((p) => `'${p}'`).join(", ")}`);
    this.name = "WordingError";
  }
}

/** Throws `WordingError` naming the field and every banned phrase found in `text`; returns the text otherwise. */
export function requireClean(text: string, field: string): string {
  const found = check(text);
  if (found.length > 0) {
    throw new WordingError(field, found);
  }
  return text;
}

/** Every value of a table of words, checked once when the table is made. */
export function clean<T extends Record<string, string>>(table: T, field: string): T {
  for (const [key, value] of Object.entries(table)) {
    requireClean(value, `${field}.${key}`);
  }
  return table;
}
