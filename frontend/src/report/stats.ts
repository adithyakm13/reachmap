// What the Overview counts, from a view: findings by kind, risk and confidence as two separate distributions (never one scale), and how
// far the decisions have got. A decision is risk acceptance, never evidence, so nothing here feeds back into a priority, a risk, a
// confidence or a count of the report: the progress is a count of decisions, shown next to those counts and never in them.

import { cmpStr } from "./read";
import { allAreas, allFindings, KIND_ORDER, type View } from "./view";
import { show } from "./text";

export interface Slice {
  /** The value as the report gives it (content): shown by `components/Content`, looked up in tables of words only through `own`. */
  key: string;
  n: number;
}

export interface Progress {
  decided: number;
  stale: number;
  undecided: number;
  orphaned: number;
  total: number;
}

export interface Stats {
  byKind: Slice[];
  risk: Slice[];
  confidence: Slice[];
  /** null when decisions cannot be recorded for this report, or when the store of decisions could not be read (nothing is known then). */
  progress: Progress | null;
}

const LEVELS = ["high", "medium", "low"];

function tally(values: string[], order: readonly string[]): Slice[] {
  const counts = new Map<string, number>();
  for (const v of values) {
    counts.set(v, (counts.get(v) ?? 0) + 1);
  }
  const rank = (k: string): number => (order.includes(k) ? order.indexOf(k) : order.length);
  return [...counts.entries()].map(([key, n]) => ({ key, n })).sort((a, b) => rank(a.key) - rank(b.key) || cmpStr(show(a.key), show(b.key)));
}

export function statsOf(view: View): Stats {
  const findings = allFindings(view).filter((f) => !f.isExclusion);
  const withId = [...allAreas(view).filter((a) => a.id !== null), ...allFindings(view).filter((f) => f.id !== null)];
  const known = view.decisions !== null && view.decisions.records.length === 0;
  const canDecide = view.header.revision !== null;
  let progress: Progress | null = null;
  if (canDecide && (known || view.decisions === null)) {
    const state = (t: (typeof withId)[number]) => (t.decision === null ? "none" : t.decision.state);
    const decided = withId.filter((t) => state(t) === "current").length;
    const stale = withId.filter((t) => state(t) === "stale").length;
    progress = {
      decided,
      stale,
      undecided: withId.length - decided - stale,
      orphaned: view.decisions === null ? 0 : view.decisions.orphaned.length,
      total: withId.length,
    };
  }
  return {
    byKind: tally(findings.map((f) => f.kind), KIND_ORDER),
    risk: tally(findings.map((f) => f.risk), LEVELS),
    confidence: tally(findings.map((f) => f.confidence), LEVELS),
    progress,
  };
}
