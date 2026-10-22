// The language a cited line is highlighted as, by the extension of its path. Highlighting only colours the text: a path that is not
// recognised, or has no extension, is shown as plain text. The names are those that `prism-react-renderer` bundles.

import { own } from "./read";

const BY_EXTENSION: Record<string, string> = {
  py: "python",
  pyi: "python",
  ts: "typescript",
  mts: "typescript",
  cts: "typescript",
  tsx: "tsx",
  js: "javascript",
  mjs: "javascript",
  cjs: "javascript",
  jsx: "jsx",
  json: "json",
  css: "css",
  html: "markup",
  htm: "markup",
  xml: "markup",
  svg: "markup",
  yml: "yaml",
  yaml: "yaml",
  md: "markdown",
  go: "go",
  rs: "rust",
  java: "java",
  kt: "kotlin",
  swift: "swift",
  c: "c",
  h: "c",
  cpp: "cpp",
  cc: "cpp",
  hpp: "cpp",
  sql: "sql",
  graphql: "graphql",
  gql: "graphql",
};

/** The language of a path by its extension, or `text` (shown without colours). */
export function languageOf(path: string): string {
  const base = path.slice(path.lastIndexOf("/") + 1);
  const dot = base.lastIndexOf(".");
  if (dot <= 0) {
    return "text";
  }
  return own(BY_EXTENSION, base.slice(dot + 1).toLowerCase()) ?? "text";
}
