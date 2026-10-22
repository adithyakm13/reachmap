// Reading a report whose deep payload has not been checked: a part that is missing or of another type is shown as absent and never
// guessed (the port of `reachmap/report/read.py`).

export type Dict = Record<string, unknown>;

const EMPTY: Dict = Object.freeze({}) as Dict;

export function dct(v: unknown): Dict {
  return typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Dict) : EMPTY;
}

export function lst(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}

export function text(v: unknown): string {
  if (typeof v === "string") {
    return v;
  }
  return v === null || v === undefined ? "" : String(v);
}

export function integer(v: unknown): number {
  return typeof v === "number" && Number.isInteger(v) ? v : 0;
}

export function isDict(v: unknown): v is Dict {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** A code point order, as Python sorts strings (UTF-16 order differs only above U+FFFF). */
export function cmpStr(a: string, b: string): number {
  if (a === b) {
    return 0;
  }
  const n = Math.min(a.length, b.length);
  for (let i = 0; i < n; i++) {
    const x = a.charCodeAt(i);
    const y = b.charCodeAt(i);
    if (x !== y) {
      const xs = x >= 0xd800 && x <= 0xdfff;
      const ys = y >= 0xd800 && y <= 0xdfff;
      if (xs !== ys) {
        return xs ? 1 : -1; // a surrogate (a character above U+FFFF) sorts after any other unit above it
      }
      return x < y ? -1 : 1;
    }
  }
  return a.length < b.length ? -1 : 1;
}

/**
 * The entry of a table of words for a key from a report. Only an own entry counts: a key such as `__proto__`, `constructor` or `toString`
 * (a report can hold any text there) finds nothing, instead of a member every object has.
 */
export function own<T>(table: Readonly<Record<string, T>>, key: string): T | undefined {
  return Object.hasOwn(table, key) ? table[key] : undefined;
}
