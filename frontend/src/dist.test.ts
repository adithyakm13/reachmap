import { describe, expect, it } from "vitest";

// The built page (`npm run build` makes `dist/`): when it is there, it holds no address of another host and loads nothing but its own
// hashed files. Skipped when there is no build yet (CI runs the tests before the build; `npm run build && npm test` runs this).

const DIST = import.meta.glob("../dist/**/*", { query: "?raw", import: "default", eager: true }) as Record<string, string>;
const FILES = Object.entries(DIST).map(([path, text]) => [path.replace("../dist/", ""), text] as const);
const HTML = FILES.filter(([p]) => p.endsWith(".html"));
const JS = FILES.filter(([p]) => p.endsWith(".js"));
const CSS = FILES.filter(([p]) => p.endsWith(".css"));

// The libraries carry some address strings that are never requested: the XML namespaces of SVG, MathML and XLink (`www.w3.org`), the address
// printed in the text of a minified production error (`react.dev`), the address in a text of a message of the router (`reactrouter.com`),
// the base the router parses a path against (`http://localhost`, never fetched), the address in a warning of Mantine about colours
// (`mantine.dev`) and the addresses of React Flow's attribution link (the page hides it) and its error messages (`reactflow.dev`). Nothing
// else may be there: a new host in the build is a change of posture and fails this test.
const LIBRARY_HOSTS = new Set(["www.w3.org", "react.dev", "reactrouter.com", "localhost", "mantine.dev", "reactflow.dev"]);
const PLACEHOLDER = "__REACHMAP_STYLE_NONCE__";

describe.skipIf(FILES.length === 0)("the built page", () => {
  it("is one page, with scripts and style sheets of hashed names, split by route, and the graph libraries in a chunk of their own", () => {
    expect(HTML.map(([p]) => p)).toEqual(["index.html"]);
    for (const [name] of [...JS, ...CSS]) {
      expect(name).toMatch(/^assets\/[A-Za-z0-9_-]+-[A-Za-z0-9_-]{8}\.(js|css)$/);
    }
    expect(FILES.map(([p]) => p).filter((p) => p.endsWith(".map"))).toEqual([]);
    const html = HTML[0][1];
    const entries = [...html.matchAll(/<script type="module"[^>]*\bsrc="(\/assets\/[^"]+)"/g)].map((m) => m[1]);
    expect(entries.length).toBe(1);
    const entry = JS.find(([p]) => `/${p}` === entries[0]);
    expect(entry).toBeTruthy();
    expect(JS.some(([p]) => p.includes("Graphs"))).toBe(true);
    // a page with no graph never loads the libraries of the graphs
    expect(entry?.[1]).not.toContain("react-flow__");
    for (const [name, js] of JS) {
      if (name !== entry?.[0] && !name.includes("Graphs")) {
        expect(js, name).not.toContain("react-flow__");
      }
    }
  });

  it("loads only its own files from the page, and has no inline script or style", () => {
    const html = HTML[0][1];
    const attrs = [...html.matchAll(/\b(?:src|href|action)="([^"]*)"/g)].map((m) => m[1]);
    for (const value of attrs) {
      expect(value === "data:," || /^\/assets\/[A-Za-z0-9_.-]+$/.test(value), value).toBe(true);
    }
    for (const m of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)) {
      expect(m[1]).toContain('src="/assets/');
      expect(m[2].trim()).toBe("");
    }
    expect(html).not.toMatch(/<style\b|\sstyle=|\son[a-z]+=/);
    expect(html).not.toMatch(/https?:|\/\/[a-z]/i);
    // the one place of the nonce the runner writes into the page
    expect(html.split(PLACEHOLDER).length - 1).toBe(1);
  });

  it("has a style sheet that loads nothing", () => {
    for (const [, css] of CSS) {
      expect(css).not.toMatch(/@import|url\s*\(|https?:|\/\/[a-z]/i);
    }
  });

  it("has no address of another host in the script but the library strings that are never requested", () => {
    for (const [, js] of JS) {
      const hosts = new Set([...js.matchAll(/\bhttps?:\/\/([A-Za-z0-9.-]+)/g)].map((m) => m[1]));
      for (const host of hosts) {
        expect(LIBRARY_HOSTS.has(host), host).toBe(true);
      }
      expect(js).not.toMatch(/\bwss?:\/\//);
      expect(js).not.toMatch(/\/\/[A-Za-z0-9-]+\.[A-Za-z]{2,}\/[^\s"'`]*\.(js|css|woff2?|png|svg)/);
    }
  });
});
