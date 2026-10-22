// The branch matrix as a grid, branches by files. The report holds one state per row of a branch and, in the row's detail, each file as
// "path: state" (`reachmap/branches.py`); a file's state is read from that text only when the exact words are there, never guessed. A
// cell with no state of its own says so. The lines cited as evidence are the row's, grouped by the file they were read from.

import type { BranchRow, Ev, View } from "./view";

export const FILE_STATES: readonly string[] = ["present", "partial", "pre_change", "diverged", "file_absent", "unreadable", "not_compared"];
export const MAX_GRID_FILES = 30;

export interface GridCell {
  /** The state of this file on this branch, when the detail of a row says it in the words `branches.py` writes; otherwise null. */
  state: string | null;
  evidence: Ev[];
}

export interface GridRow {
  branch: string;
  short: string;
  /** The states of the rows of the branch (one row over the readable files, one more for each file that could not be read). */
  states: string[];
  found: number;
  cells: GridCell[];
  details: string[];
  evidence: Ev[];
  findings: number[];
}

export interface Grid {
  files: string[];
  hiddenFiles: number;
  rows: GridRow[];
}

function stateInDetail(detail: string, path: string): string | null {
  const found = new Set<string>();
  for (const state of FILE_STATES) {
    const needle = `${path}: ${state}`;
    for (let i = detail.indexOf(needle); i >= 0; i = detail.indexOf(needle, i + 1)) {
      const next = detail.charAt(i + needle.length);
      if ((i === 0 || detail.slice(i - 2, i) === "; ") && (next === "" || next === " " || next === ";")) {
        found.add(state); // the exact words `branches.py` writes: a file, a colon, one state, then the end, a space or the next file
      }
    }
  }
  return found.size === 1 ? [...found][0] : null; // a text that says two things is not read
}

export function branchGrid(view: View): Grid {
  const changed = view.header.files.map((f) => ({ path: f.path, old: f.oldPath }));
  let columns = changed;
  if (columns.length === 0) {
    const seen = new Set<string>();
    for (const row of view.matrix) {
      for (const e of row.evidence) {
        seen.add(e.path);
      }
    }
    columns = [...seen].map((path) => ({ path, old: null }));
  }
  const shown = columns.slice(0, MAX_GRID_FILES);
  const groups = new Map<string, BranchRow[]>();
  for (const row of view.matrix) {
    groups.set(row.branch, [...(groups.get(row.branch) ?? []), row]);
  }
  const rows: GridRow[] = [...groups.entries()].map(([branch, mine]) => {
    const cells = shown.map((file): GridCell => {
      const evidence = mine.flatMap((r) => r.evidence).filter((e) => e.path === file.path || (file.old !== null && e.path === file.old));
      let state: string | null = null;
      for (const r of mine) {
        state = state ?? stateInDetail(r.detail, file.path);
        if (state === null && r.state === "unreadable" && r.detail.startsWith(`${file.path}:`)) {
          state = "unreadable";
        }
      }
      const readable = mine.filter((r) => r.state !== "unreadable");
      if (state === null && columns.length === 1 && readable.length === 1) {
        state = readable[0].state; // one file, one row: the row's state is the file's
      }
      return { state, evidence };
    });
    return {
      branch,
      short: mine[0].short,
      states: [...new Set(mine.map((r) => r.state))],
      found: mine.reduce((n, r) => n + r.found, 0),
      cells,
      details: mine.map((r) => r.detail),
      evidence: mine.flatMap((r) => r.evidence),
      findings: [...new Set(mine.flatMap((r) => r.findings))],
    };
  });
  return { files: shown.map((f) => f.path), hiddenFiles: columns.length - shown.length, rows };
}
