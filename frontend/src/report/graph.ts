// The graphs of the workspace as plain data: no library, no position. The three graphs are made from a view and from nothing else, so what
// a graph says is what the lists say. `graphs/layout.ts` gives the data a place (dagre) and `graphs/*.tsx` draws it (React Flow).
// Every label here is content (a path, a repository, a line of source): it is drawn as text by `components/Content` and never as markup.

import type { AreaView, Cross, Ev, HopView, View } from "./view";
import { own } from "./read";

/** The edge labels the chain graph may carry. A line whose claim is of another kind, or has none, leaves its edge unlabelled. */
export const EDGE_KINDS = ["import", "route", "http", "contract", "package"] as const;
export type EdgeKind = (typeof EDGE_KINDS)[number];

const KIND_SET: Record<string, EdgeKind> = Object.fromEntries(EDGE_KINDS.map((k) => [k, k]));

export interface ChainNode {
  id: string;
  index: number;
  repo: string;
  /** The lane (swimlane) of the repository: the order the repositories first appear in the chain. */
  lane: number;
  locator: string;
  path: string;
  line: number;
  short: string;
  detail: string;
}

export interface ChainEdge {
  id: string;
  source: string;
  target: string;
  /** import, route, http, contract or package when the report says so, otherwise null (no label is made up). */
  label: EdgeKind | null;
}

export interface ChainGraph {
  nodes: ChainNode[];
  edges: ChainEdge[];
  lanes: string[];
}

function claimKind(ev: Ev): EdgeKind | null {
  const head = ev.claim.split(":", 1)[0];
  return own(KIND_SET, head) ?? null;
}

function crossKind(hops: HopView[], a: string, b: string): EdgeKind | null {
  for (const h of hops) {
    if ((h.fromRepo === a && h.toRepo === b) || (h.fromRepo === b && h.toRepo === a)) {
      const k = own(KIND_SET, h.kind);
      if (k !== undefined) {
        return k;
      }
    }
  }
  return null;
}

/** One chain as a graph: a node per step, left to right in the order of the report, a lane per repository, an edge between steps. */
export function chainGraph(chain: Ev[], cross: Cross): ChainGraph {
  const lanes: string[] = [];
  const nodes: ChainNode[] = chain.map((ev, index) => {
    let lane = lanes.indexOf(ev.repo);
    if (lane < 0) {
      lanes.push(ev.repo);
      lane = lanes.length - 1;
    }
    return { id: `h${index}`, index, repo: ev.repo, lane, locator: ev.locator, path: ev.path, line: ev.line, short: ev.short, detail: ev.detail };
  });
  const edges: ChainEdge[] = [];
  for (let i = 1; i < chain.length; i++) {
    const into = chain[i];
    const from = chain[i - 1];
    // the claim of a line is the kind of step that arrives at it; a step into another repository is named by the edge the run followed
    let label = claimKind(into);
    if (label === null && into.repo !== from.repo) {
      label = crossKind(cross.hops, from.repo, into.repo);
    }
    edges.push({ id: `e${i}`, source: `h${i - 1}`, target: `h${i}`, label });
  }
  return { nodes, edges, lanes };
}

// ------------------------------------------------------------------------------ what the change reaches

export interface ReachArea {
  /** The index of the area in the report (`AreaView.index`), for a link by id. */
  id: string | null;
  label: string;
  kind: string;
  candidate: boolean;
}

export interface ReachRepo {
  repo: string;
  origin: boolean;
  areas: ReachArea[];
  hidden: number;
}

export interface ReachEdge {
  id: string;
  from: string;
  to: string;
  kind: string;
  candidate: boolean;
}

export interface ReachGraph {
  repos: ReachRepo[];
  edges: ReachEdge[];
}

export const REACH_AREAS_PER_REPO = 8;

function areaOf(a: AreaView): ReachArea {
  return { id: a.id, label: a.label, kind: a.kind, candidate: a.byName || a.priority === "candidate" };
}

/** Repositories as groups, the screens and routes the change reaches inside them (at most `REACH_AREAS_PER_REPO` each, the rest counted), and the edges the run followed between them. */
export function reachGraph(view: View, areas: AreaView[]): ReachGraph {
  const byRepo = new Map<string, AreaView[]>();
  const order: string[] = [];
  const see = (repo: string): void => {
    if (repo !== "" && !byRepo.has(repo)) {
      byRepo.set(repo, []);
      order.push(repo);
    }
  };
  see(view.header.repo);
  for (const a of areas) {
    see(a.repo);
    byRepo.get(a.repo)?.push(a);
  }
  for (const h of view.cross.hops) {
    see(h.fromRepo);
    see(h.toRepo);
  }
  const repos: ReachRepo[] = order.map((repo) => {
    const mine = byRepo.get(repo) ?? [];
    return { repo, origin: repo === view.header.repo, areas: mine.slice(0, REACH_AREAS_PER_REPO).map(areaOf), hidden: Math.max(0, mine.length - REACH_AREAS_PER_REPO) };
  });
  const seen = new Set<string>();
  const edges: ReachEdge[] = [];
  for (const h of view.cross.hops) {
    const key = JSON.stringify([h.fromRepo, h.toRepo, h.kind, h.candidate]);
    if (h.fromRepo !== "" && h.toRepo !== "" && !seen.has(key)) {
      seen.add(key);
      edges.push({ id: `r${edges.length}`, from: h.fromRepo, to: h.toRepo, kind: h.kind, candidate: h.candidate });
    }
  }
  return { repos, edges };
}

// ------------------------------------------------------------------------------ cross-repository reach

export interface CrossNote {
  /** A branch with the pin read there and the adoption, or a line of the edge's own words. */
  branch: string;
  pin: string | null;
  adoption: string;
}

export interface CrossEdge {
  id: string;
  from: string;
  to: string;
  kind: "package" | "http" | "contract" | "edge";
  /** For an edge the run followed that is none of the three: the kind as the report gives it (content). */
  rawKind: string;
  candidate: boolean;
  notes: CrossNote[];
}

export interface CrossGraph {
  repos: string[];
  edges: CrossEdge[];
}

/**
 * Repositories and the edges between them: the edges followed (hops), package consumers with the pin and adoption of each branch, http calls
 * from client to server, reads of a changed contract member in the consumers. What was matched by name only is a candidate and is drawn apart.
 */
export function crossGraph(view: View): CrossGraph {
  const c = view.cross;
  const origin = view.header.repo;
  const repos: string[] = [];
  const see = (repo: string): void => {
    if (repo !== "" && !repos.includes(repo)) {
      repos.push(repo);
    }
  };
  const edges = new Map<string, CrossEdge>();
  const add = (from: string, to: string, kind: CrossEdge["kind"], rawKind: string, candidate: boolean, note: CrossNote | null): void => {
    if (from === "" || to === "") {
      return;
    }
    see(from);
    see(to);
    const key = JSON.stringify([from, to, kind, kind === "edge" ? rawKind : "", candidate]);
    let e = edges.get(key);
    if (e === undefined) {
      e = { id: `x${edges.size}`, from, to, kind, rawKind, candidate, notes: [] };
      edges.set(key, e);
    }
    if (note !== null) {
      e.notes.push(note);
    }
  };
  see(origin);
  for (const h of c.hops) {
    const k = h.kind === "package" || h.kind === "http" || h.kind === "contract" ? h.kind : "edge";
    add(h.fromRepo, h.toRepo, k, h.kind, h.candidate, null);
  }
  for (const r of c.consumers) {
    add(r.producer, r.repo, "package", "package", false, { branch: r.branch, pin: r.pin, adoption: r.adoption });
  }
  for (const e of c.http) {
    add(e.clientRepo, e.serverRepo, "http", "http", e.candidate, null);
  }
  const producers = [...new Set(c.changes.map((ch) => ch.repo))];
  const producer = producers.length > 0 ? producers[0] : origin;
  for (const r of c.reads) {
    add(producer, r.repo, "contract", "contract", false, { branch: r.branch, pin: r.pin, adoption: r.adoption });
  }
  for (const r of c.candidates) {
    add(producer, r.repo, "contract", "contract", true, { branch: r.branch, pin: r.pin, adoption: r.adoption });
  }
  return { repos, edges: [...edges.values()] };
}
