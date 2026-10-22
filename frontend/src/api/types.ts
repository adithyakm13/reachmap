// Types for the report JSON (schema 1, any minor version) and for the answers of the runner API (`reachmap/runner/api.py`).
//
// Only what the pages rely on is typed. The deep payload of a propagation is `unknown` and is read with the tolerant readers of
// `report/read.ts`: a part that is missing or of another type is shown as absent, never guessed.

export const SCHEMA = 1;

export type Status = "completed" | "failed" | "timed_out";
export type Priority = "must_test" | "sample" | "candidate";
export type AreaVerdict = "test" | "sample" | "skip";
export type FindingVerdict = "accept" | "reject";
export type Verdict = AreaVerdict | FindingVerdict;

export interface Evidence {
  repo: string;
  path: string;
  line: number;
  ref: string;
  detail: string;
  claim?: string;
}

export interface IncompletenessRecord {
  kind: string;
  subject: string;
  not_established: string;
  detail: string;
  blocks_selection: boolean;
  /** `selection` or `suggestions`, and whether the record says the suggested cases may be incomplete: a report written before test sources has neither. */
  scope?: string;
  blocks_suggestions?: boolean;
}

export interface AreaJson {
  label: string;
  kind: string;
  repo: string;
  module: string;
  route_line: Evidence;
  chains: Evidence[][];
  also_mounted: string[];
  priority: Priority;
  by_name: boolean;
  id?: string;
  fingerprint?: string;
}

export interface FindingJson {
  kind: string;
  title: string;
  workflow: string;
  risk: string;
  confidence: string;
  confidence_basis: string;
  evidence: Evidence[];
  identity: string[];
  is_exclusion: boolean;
  id?: string;
  fingerprint?: string;
}

export interface BranchJson {
  branch: string;
  commit: string;
  state: string;
  added_lines_found: number;
  evidence: Evidence[];
  detail: string;
}

/** A decision as `review.decisions` carries it. */
export interface DecisionJson {
  target_id: string;
  kind?: string;
  label?: string | null;
  verdict: string;
  reason: string;
  author: string;
  channel: string;
  revision: number;
  created_at: string;
  fingerprint: string;
  now_fingerprint?: string | null;
  changes?: { removed?: unknown[]; added?: unknown[] } | null;
}

export interface DecisionsJson {
  current: DecisionJson[];
  stale: DecisionJson[];
  orphaned: DecisionJson[];
  undecided?: { areas?: string[]; findings?: string[] };
  records?: IncompletenessRecord[];
  as_of?: string | null;
}

export interface ReviewJson {
  key: { workspace: string; repo: string; change: string; slug?: string } | null;
  revision: number | null;
  previous?: { number: number; file: string; head_sha: string } | null;
  decisions: DecisionsJson | null;
  as_of?: string | null;
}

export interface Report {
  schema: number;
  schema_minor?: number;
  tool?: { name?: string; version?: string };
  run?: { started_at?: string; elapsed_s?: number };
  inputs?: Record<string, unknown>;
  counts?: Record<string, number> | null;
  change: Record<string, unknown>;
  ref: string | null;
  branches: BranchJson[] | null;
  findings: FindingJson[] | null;
  areas: AreaJson[] | null;
  propagations: unknown[] | null;
  backend_calls: unknown[] | null;
  suggestions: unknown[] | null;
  mapped_tests?: unknown[] | null;
  incompleteness: IncompletenessRecord[];
  notes: string[];
  revision?: number;
  status: Status;
  review?: ReviewJson | null;
  /** The notes a model client recorded beside the report (M14), as the runner attaches them: read with `report/notes.ts`, never trusted. */
  model_notes?: unknown;
}

// ------------------------------------------------------------------------------ the runner API

/**
 * One report of the list. The runner fills `key` from the report's own review data, or, for a report that has none, from what the run was
 * asked for: so `change` is a text (`compare:main...topic`) or an object (`{"compare": "main...topic"}`), and any of them may be null.
 */
export interface ReportListItem {
  name: string;
  key: { workspace?: unknown; repo?: unknown; change?: unknown };
  revision: unknown;
  status: unknown;
  created: unknown;
  /** The head commit, and the counts of a run that completed (null for a run that did not: it has no count). A runner of an earlier version leaves all three out. */
  head_sha?: unknown;
  areas?: unknown;
  blocking_records?: unknown;
}

export interface RepoInfo {
  name: string;
  roles: string[];
  branches: string[];
}

export interface ReposAnswer {
  workspace: string;
  repos: RepoInfo[];
}

export type JobState = "queued" | "running" | "completed" | "failed" | "timed_out" | "cancelled";

export interface JobInfo {
  id: string;
  state: JobState | string;
  repo: string;
  change: Record<string, string>;
  as_of: string | null;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  report: string | null;
  exit_code: number | null;
  reason: string | null;
}

export type ChangeKind = "pr" | "compare" | "range";

export interface JobSpec {
  repo: string;
  pr?: string;
  compare?: string;
  range?: string;
  as_of?: string;
}

export interface DecisionRequest {
  target: string;
  verdict: Verdict;
  reason: string;
  author: string;
}
