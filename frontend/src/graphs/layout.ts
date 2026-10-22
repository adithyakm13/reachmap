// Giving the graphs of `report/graph.ts` a place: dagre lays the boxes out, React Flow draws them (`Graphs.tsx`). The data of every node is the
// plain data of the model (strings, numbers); what is drawn from it is drawn as text.
//
// No string of a report is ever a key of an object here: dagre keeps its nodes and edges in plain objects keyed by node id, and a repository
// called `__proto__` or `constructor` would write into `Object.prototype`. Every node and edge handed to dagre has an id made by this module
// from its position in the model (`r0`, `c1`, `a2b3`), and the names are mapped back from the model, never from dagre.

import { Graph, layout } from "@dagrejs/dagre";
import type { Edge, Node } from "@xyflow/react";
import type { CrossGraph, ReachGraph } from "../report/graph";
import { clip } from "../report/text";

export const REPO_W = 188;
export const REPO_H = 52;
const AREA_W = 176;
const AREA_H = 34;
const GROUP_HEAD = 34;
const GROUP_PAD = 10;
const EMPTY_H = 30;

export interface Laid {
  nodes: Node[];
  edges: Edge[];
  /** The height a container needs to show every box at the size it is laid out at (with a margin). */
  height: number;
  width: number;
}

function place(g: Graph, id: string): { x: number; y: number } {
  const n = g.node(id) as { x: number; y: number };
  return { x: n.x, y: n.y };
}

function extent(nodes: Node[]): { width: number; height: number } {
  let right = 0;
  let bottom = 0;
  for (const n of nodes) {
    if (n.parentId === undefined) {
      right = Math.max(right, n.position.x + (n.width ?? 0));
      bottom = Math.max(bottom, n.position.y + (n.height ?? 0));
    }
  }
  return { width: right + 24, height: bottom + 24 };
}

/** What the change reaches: a group per repository with the areas inside it in a grid, the groups placed by dagre, the edges between the groups. */
export function layoutReach(model: ReachGraph): Laid {
  const sizes = new Map<number, { w: number; h: number; cols: number }>();
  const g = new Graph();
  g.setGraph({ rankdir: "LR", nodesep: 36, ranksep: 90, marginx: 16, marginy: 16 });
  g.setDefaultEdgeLabel(() => ({}));
  const byName = new Map<string, number>();
  model.repos.forEach((r, i) => {
    byName.set(r.repo, i);
    const shown = r.areas.length + (r.hidden > 0 ? 1 : 0);
    const cols = shown > 4 ? 2 : 1;
    const rows = Math.max(1, Math.ceil(shown / cols));
    const w = cols * AREA_W + (cols + 1) * GROUP_PAD;
    const h = GROUP_HEAD + (shown === 0 ? EMPTY_H : rows * (AREA_H + 8)) + GROUP_PAD;
    sizes.set(i, { w, h, cols });
    g.setNode(`r${i}`, { width: w, height: h });
  });
  const linked: { id: string; from: number; to: number; kind: string; candidate: boolean }[] = [];
  for (const e of model.edges) {
    const from = byName.get(e.from);
    const to = byName.get(e.to);
    if (from !== undefined && to !== undefined) {
      g.setEdge(`r${from}`, `r${to}`);
      linked.push({ id: e.id, from, to, kind: e.kind, candidate: e.candidate });
    }
  }
  layout(g);
  const nodes: Node[] = [];
  for (const [i, r] of model.repos.entries()) {
    const s = sizes.get(i) as { w: number; h: number; cols: number };
    const p = place(g, `r${i}`);
    const gid = `g${i}`;
    nodes.push({ id: gid, type: "repo", position: { x: p.x - s.w / 2, y: p.y - s.h / 2 }, width: s.w, height: s.h, data: { repo: r.repo, origin: r.origin, empty: r.areas.length === 0 && r.hidden === 0 }, draggable: false });
    r.areas.forEach((a, j) => {
      nodes.push({
        id: `${gid}a${j}`,
        type: "area",
        parentId: gid,
        extent: "parent",
        position: { x: GROUP_PAD + (j % s.cols) * (AREA_W + GROUP_PAD), y: GROUP_HEAD + Math.floor(j / s.cols) * (AREA_H + 8) },
        width: AREA_W,
        height: AREA_H,
        data: { id: a.id, label: a.label, kind: a.kind, candidate: a.candidate },
        draggable: false,
      });
    });
    if (r.hidden > 0) {
      const j = r.areas.length;
      nodes.push({
        id: `${gid}more`,
        type: "more",
        parentId: gid,
        extent: "parent",
        position: { x: GROUP_PAD + (j % s.cols) * (AREA_W + GROUP_PAD), y: GROUP_HEAD + Math.floor(j / s.cols) * (AREA_H + 8) },
        width: AREA_W,
        height: AREA_H,
        data: { n: r.hidden },
        draggable: false,
        selectable: false,
      });
    }
  }
  const edges: Edge[] = linked.map((e, i) => ({
    id: `e${i}`,
    source: `g${e.from}`,
    target: `g${e.to}`,
    label: clip(e.kind, 40),
    type: "smoothstep",
    className: e.candidate ? "rm-edge rm-edge-candidate" : "rm-edge",
    data: { kind: e.kind },
  }));
  return { nodes, edges, ...extent(nodes) };
}

/** Repositories and the edges between them, left to right. */
export function layoutCross(model: CrossGraph): Laid {
  const g = new Graph();
  g.setGraph({ rankdir: "LR", nodesep: 40, ranksep: 120, marginx: 24, marginy: 24 });
  g.setDefaultEdgeLabel(() => ({}));
  const byName = new Map<string, number>();
  model.repos.forEach((repo, i) => {
    byName.set(repo, i);
    g.setNode(`n${i}`, { width: REPO_W, height: REPO_H });
  });
  // several edges between the same two repositories would be drawn one on top of another: they are one line, named by all their kinds
  // (the table next to the graph lists each of them)
  const grouped = new Map<string, { from: number; to: number; candidate: boolean; kinds: Map<string, number> }>();
  for (const e of model.edges) {
    const from = byName.get(e.from);
    const to = byName.get(e.to);
    if (from === undefined || to === undefined) {
      continue;
    }
    g.setEdge(`n${from}`, `n${to}`);
    const key = `${from}>${to}>${e.candidate ? "c" : "f"}`;
    const entry = grouped.get(key) ?? { from, to, candidate: e.candidate, kinds: new Map<string, number>() };
    const kind = e.kind === "edge" ? clip(e.rawKind, 24) : e.kind;
    entry.kinds.set(kind, (entry.kinds.get(kind) ?? 0) + Math.max(1, e.notes.length));
    grouped.set(key, entry);
  }
  layout(g);
  const nodes: Node[] = model.repos.map((repo, i) => {
    const p = place(g, `n${i}`);
    return { id: `c${i}`, type: "plain", position: { x: p.x - REPO_W / 2, y: p.y - REPO_H / 2 }, width: REPO_W, height: REPO_H, data: { repo }, draggable: false };
  });
  // two repositories that call each other would be two lines on one another (one of them looping round the boxes): one line with an arrow at
  // each end, named by the kinds of both (the table next to the graph says which way each goes)
  const merged: { from: number; to: number; candidate: boolean; kinds: Map<string, number>; both: boolean }[] = [];
  const taken = new Set<string>();
  for (const [key, e] of grouped) {
    if (taken.has(key)) {
      continue;
    }
    const back = `${e.to}>${e.from}>${e.candidate ? "c" : "f"}`;
    const other = grouped.get(back);
    if (other !== undefined && back !== key) {
      taken.add(back);
      const kinds = new Map(e.kinds);
      for (const [k, n] of other.kinds) {
        kinds.set(k, (kinds.get(k) ?? 0) + n);
      }
      merged.push({ ...e, kinds, both: true });
    } else {
      merged.push({ ...e, both: false });
    }
  }
  const edges: Edge[] = merged.map((e, i) => ({
    id: `x${i}`,
    source: `c${e.from}`,
    target: `c${e.to}`,
    label: [...e.kinds.entries()].map(([k, n]) => (n > 1 ? `${k} x${n}` : k)).join(", "),
    type: "default",
    data: { both: e.both },
    className: e.candidate ? "rm-edge rm-edge-candidate" : "rm-edge",
  }));
  return { nodes, edges, ...extent(nodes) };
}
