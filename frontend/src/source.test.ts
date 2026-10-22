import { describe, expect, it } from "vitest";
import { check } from "./report/wording";
import packageJson from "../package.json?raw";
import lockJson from "../package-lock.json?raw";

// A scan of the sources of the page (not of the tests): what must never appear in them, and what must appear in one place only.

const ALL = import.meta.glob(["./**/*.ts", "./**/*.tsx", "./**/*.css"], { query: "?raw", import: "default", eager: true }) as Record<string, string>;
const SOURCES = Object.entries(ALL).filter(([path]) => !path.includes(".test.") && !path.startsWith("./test/"));
const BY_PATH = Object.fromEntries(SOURCES);
const CODE = SOURCES.filter(([path]) => !path.endsWith(".css"));

function hits(pattern: RegExp, only: [string, string][] = CODE): string[] {
  return only.filter(([, text]) => pattern.test(text)).map(([path]) => path);
}

describe("the sources of the page", () => {
  it("has sources to scan", () => {
    expect(CODE.length).toBeGreaterThan(30);
    expect(Object.keys(BY_PATH)).toContain("./api/client.ts");
  });

  it("never turns text into markup or code", () => {
    const forbidden: [string, RegExp][] = [
      ["dangerouslySetInnerHTML", /dangerouslySetInnerHTML/],
      ["innerHTML", /innerHTML/],
      ["outerHTML", /outerHTML/],
      ["insertAdjacentHTML", /insertAdjacent/],
      ["document.write", /document\s*\.\s*write/],
      ["eval", /\beval\s*\(/],
      ["new Function", /new\s+Function\b/],
      ["Function constructor call", /\bFunction\s*\(/],
      ["a string for a timer", /set(Timeout|Interval)\s*\(\s*["'`]/],
      ["setHTMLUnsafe", /setHTMLUnsafe/],
      ["createContextualFragment", /createContextualFragment/],
      ["DOMParser", /DOMParser/],
      ["srcdoc", /srcDoc|srcdoc/],
      ["bracket access to innerHTML", /\[\s*["'`](inner|outer)HTML/],
    ];
    for (const [name, pattern] of forbidden) {
      expect(hits(pattern), name).toEqual([]);
    }
  });

  it("makes no link, source or address from data", () => {
    // an attribute that holds an address is never set from an expression: `href={`, `src={` and the rest
    expect(hits(/\b(href|src|srcSet|action|formAction|poster|xlinkHref|cite|ping)\s*=\s*\{/)).toEqual([]);
    // and nothing that loads a document or a plug-in is drawn at all
    expect(hits(/<(object|embed|iframe|frame|frameset|applet|portal|script|link|meta|base)\b/)).toEqual([]);
    // the one literal address of an attribute is the skip link
    const literal = CODE.flatMap(([path, text]) => [...text.matchAll(/\b(href|src|action|formAction)\s*=\s*"([^"]*)"/g)].map((m) => `${path} ${m[1]}=${m[2]}`));
    expect(literal).toEqual(['./App.tsx href=#main']);
    // `to` of a link is made by `paths.ts` from a checked name and id; no other module builds one from a template
    expect(hits(/\bto\s*=\s*\{\s*`/)).toEqual([]);
    expect(hits(/\b(location|window\.location)\s*(\.href|\.assign|\.replace)?\s*=[^=]/)).toEqual([]);
    expect(hits(/window\.open|location\.assign|location\.replace|navigator\.sendBeacon/)).toEqual([]);
    expect(hits(/<a\s/, CODE)).toEqual(["./App.tsx"]);
  });

  it("calls fetch in one module only, and opens no other channel", () => {
    expect(hits(/\bfetch\s*\(/)).toEqual(["./api/client.ts"]);
    expect(hits(/XMLHttpRequest|WebSocket|EventSource|sendBeacon|importScripts|navigator\.serviceWorker|new\s+Worker\b/)).toEqual([]);
    // a page and the graph libraries are chunks loaded when they are first needed: the only dynamic imports, each with a literal path of this tree
    expect(hits(/\bimport\s*\(/)).toEqual(["./components/LazyGraphs.tsx", "./palette.tsx", "./providers.tsx", "./routes.tsx"]);
    for (const [path, text] of CODE) {
      for (const m of text.matchAll(/\bimport\s*\(([^)]*)\)/g)) {
        // a path of this tree, or one of the three libraries that load after the first paint (the palette, the toasts)
        expect(m[1].trim(), path).toMatch(/^"(\.{1,2}\/[A-Za-z0-9_./-]+|@mantine\/(spotlight|notifications))"$/);
      }
    }
  });

  it("holds no address of another host, in the code or the styles", () => {
    expect(hits(/https?:\/\/|\/\/[a-z0-9.-]+\.[a-z]{2,}\b|\bwss?:/i, SOURCES)).toEqual([]);
    expect(hits(/@import|url\s*\(/i, SOURCES.filter(([p]) => p.endsWith(".css")))).toEqual([]);
  });

  it("sets no style attribute and makes no style element (the policy of the runner allows its own style sheets and the nonce it writes into the page)", () => {
    expect(hits(/\bstyle\s*=\s*\{|\.style\.|setAttribute\(\s*["']style/)).toEqual([]);
    expect(hits(/<style\b|createElement\(\s*["']style/)).toEqual([]);
    expect(hits(/@font-face|fonts\.(googleapis|gstatic)/i, SOURCES)).toEqual([]);
  });

  it("knows the style nonce in one module, and the libraries get it from there", () => {
    expect(hits(/__webpack_nonce__/)).toEqual(["./nonce.ts"]);
    expect(hits(/getStyleNonce/)).toEqual(["./providers.tsx"]);
    expect(BY_PATH["./main.tsx"]).toContain("readNonce(document)");
    expect(BY_PATH["./main.tsx"]).toContain("publishNonce(nonce)");
    // the nonce is read before anything renders
    expect(BY_PATH["./main.tsx"].indexOf("readNonce(document)")).toBeLessThan(BY_PATH["./main.tsx"].indexOf("createRoot(root,"));
  });

  it("keeps the browser's storage to the theme and the name, in one module, and never the token", () => {
    expect(hits(/localStorage|sessionStorage|indexedDB|document\.cookie/)).toEqual(["./storage.ts"]);
    expect(BY_PATH["./storage.ts"]).toMatch(/try\s*{[^}]*localStorage\.getItem/);
    expect(BY_PATH["./storage.ts"]).toMatch(/try\s*{[^}]*localStorage\.setItem/);
    // what is written is written by the module that owns it: the theme button and the decision form (through `rememberAuthor`), nowhere else
    expect(hits(/\bwriteStored\(/)).toEqual(["./storage.ts"]);
    // the theme is chosen with Mantine's hook and written by the colour scheme manager of the storage module
    expect(hits(/colorSchemeManager/)).toEqual(["./providers.tsx", "./storage.ts"]);
    expect(hits(/\brememberAuthor\(/)).toEqual(["./components/Decisions.tsx", "./storage.ts"]);
    // the keys it uses are the theme and the name, and no key or value is the token
    expect([...BY_PATH["./storage.ts"].matchAll(/_KEY\s*=\s*"([^"]*)"/g)].map((m) => m[1])).toEqual(["reachmap-theme", "reachmap-author"]);
  });

  it("takes the token before anything renders, and again when the fragment changes, and keeps it in memory", () => {
    const main = BY_PATH["./main.tsx"];
    expect(main).toContain("startSession(window)");
    expect(main.indexOf("startSession(window)")).toBeLessThan(main.indexOf("createRoot(root,"));
    const session = BY_PATH["./session.ts"];
    expect(session).toContain("takeToken(win.location, win.history)");
    expect(session).toContain('addEventListener("hashchange"');
    expect(BY_PATH["./api/client.ts"]).toContain("hist.replaceState(null");
  });

  it("says nothing that claims safety or coverage, in any string or text of a source", () => {
    const bad = SOURCES.filter(([path]) => path !== "./report/wording.ts").map(([path, text]) => [path, check(text)] as const).filter(([, found]) => found.length > 0);
    expect(bad).toEqual([]);
  });

  it("puts every sentence of the page in words.ts or in a short fixed phrase next to the element it belongs to", () => {
    // a long run of plain words in JSX text is a sentence the page says: it belongs in words.ts, where it is checked when the module loads
    const long: string[] = [];
    for (const [path, text] of CODE.filter(([p]) => p.endsWith(".tsx"))) {
      for (const m of text.matchAll(/>\s*([A-Za-z][^<>{}\n]{70,})\s*</g)) {
        long.push(`${path}: ${m[1].slice(0, 40)}`);
      }
    }
    expect(long).toEqual([]);
  });

  it("holds no dash, arrow, curly quote or invisible character, and no ASCII arrow, in any source of the frontend", () => {
    const odd = /[\u2013\u2014\u2015\u2190-\u21ff\u2018\u2019\u201c\u201d\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff\u2028\u2029]/;
    const everything = Object.entries(ALL);
    expect(everything.filter(([, text]) => odd.test(text)).map(([path]) => path)).toEqual([]);
    expect(everything.filter(([, text]) => /->/.test(text)).map(([path]) => path)).toEqual([]);
  });

  it("has the runtime dependencies the owner named and no other, each at an exact version", () => {
    const pkg = JSON.parse(packageJson) as { dependencies: Record<string, string>; devDependencies: Record<string, string> };
    expect(Object.keys(pkg.dependencies).sort()).toEqual([
      "@dagrejs/dagre",
      "@mantine/core",
      "@mantine/hooks",
      "@mantine/notifications",
      "@mantine/spotlight",
      "@tabler/icons-react",
      "@xyflow/react",
      "prism-react-renderer",
      "react",
      "react-dom",
      "react-router-dom",
    ]);
    for (const [name, version] of [...Object.entries(pkg.dependencies), ...Object.entries(pkg.devDependencies)]) {
      expect(version, name).toMatch(/^\d+\.\d+\.\d+$/); // no ^, no ~, no range
    }
    // the lock holds what it says: a lock of the same direct dependencies at the same versions
    const lock = JSON.parse(lockJson) as { packages: Record<string, { version?: string; dependencies?: Record<string, string> }> };
    expect(lock.packages[""].dependencies).toEqual(pkg.dependencies);
    for (const [name, version] of Object.entries(pkg.dependencies)) {
      expect(lock.packages[`node_modules/${name}`]?.version, name).toBe(version);
    }
  });

  it("hands dagre only ids made by the layout itself, never a string of a report (a repository called __proto__ must not write into Object.prototype)", () => {
    const layout = BY_PATH["./graphs/layout.ts"];
    const calls = [...layout.matchAll(/\.set(?:Node|Edge)\(([^)]*)\)/g)].map((m) => m[1]);
    expect(calls.length).toBeGreaterThan(3);
    for (const args of calls) {
      // every id is a template of a letter and a number of this module: `r${i}`, `n${from}`, ...
      expect(args, args).toMatch(/^`[a-z]\$\{[a-z]+\}`(, `[a-z]\$\{[a-z]+\}`)?(, \{.*\})?$/);
    }
  });

  it("keeps toasts to one module and writes to the console in one place, with a fixed line", () => {
    expect(hits(/notifications\.show\(/)).toEqual(["./toast.tsx"]);
    expect(hits(/\bconsole\s*\./)).toEqual(["./main.tsx"]);
    expect(BY_PATH["./main.tsx"]).toContain('console.error("reachmap: a part of the page could not be drawn")');
    expect(BY_PATH["./main.tsx"]).toMatch(/onCaughtError: logFixed, onUncaughtError: logFixed, onRecoverableError: logFixed/);
    expect(BY_PATH["./main.tsx"]).toContain("onError={logFixed}");
  });
});
