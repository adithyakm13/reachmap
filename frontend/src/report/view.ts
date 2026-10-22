// The view model: a report (the JSON of `reachmap analyse`) reordered into the sections a QA reader needs, in one place. A port of
// `reachmap/report/model.py` and `author.py`: `fixtures/order.json` (made by the Python view model) holds this one to the same order.
//
// It takes the report JSON and nothing else. Every string copied out of the report stays a plain string here and is shown by
// `components/Content` (made visible, cut when very long, never markup); what the page says about it is in `words.ts`.
// Reading is lenient about the deep payload of a propagation: a missing or odd part is shown as absent, never guessed.

import type { Report, Status } from "../api/types";
import { cmpStr, dct, integer, isDict, lst, text, type Dict } from "./read";
import { readModelNotes, type ModelNotesView } from "./notes";
import { show } from "./text";

export const KIND_ORDER: readonly string[] = [
  "direct",
  "cross-repo",
  "contract-tightened",
  "contract-relaxed",
  "removed_route_still_called",
  "added_route_uncalled",
  "release",
  "branch-missing",
  "duplicate",
];

export const COUNT_KEYS = ["areas", "must_test", "sample", "candidates", "findings", "exclusions", "records", "blocking_records"] as const;
/** The last count is not in a report written before test sources were read: it is made from the records then. */
export const COUNT_KEYS_ALL = [...COUNT_KEYS, "blocking_suggestion_records"] as const;
export const KNOWN_EDGES: readonly string[] = ["http", "package", "contract", "event"];

export interface Ev {
  repo: string;
  path: string;
  line: number;
  ref: string;
  short: string;
  detail: string;
  locator: string;
  /** The claim the report attached to the line (`import:...`, `route:...`), or an empty text: it says what kind of step the line is. */
  claim: string;
}

export interface RecordView {
  kind: string;
  subject: string;
  notEstablished: string;
  detail: string;
  /** Blocks selection: which areas and findings the change has. */
  blocking: boolean;
  /** Says the list of suggested cases may be incomplete (a test source that did not answer, a list cut short). */
  blocksSuggestions: boolean;
  /** What the record is about: the selection (areas and findings) or the suggestions (a test source). */
  scope: "selection" | "suggestions";
}

export interface PullRequestView {
  number: string;
  title: string;
  state: string;
  baseRef: string;
  headRef: string;
}

export interface FileChange {
  path: string;
  oldPath: string | null;
  status: string;
  role: string | null;
  added: number;
  removed: number;
}

export interface HeaderView {
  repo: string;
  label: string;
  status: Status;
  tool: string;
  head: string | null;
  headShort: string | null;
  preChange: string | null;
  preChangeShort: string | null;
  base: string | null;
  how: [string, string][];
  pr: PullRequestView | null;
  asOf: string | null;
  startedAt: string | null;
  elapsedS: number | null;
  complete: boolean | null;
  reasons: number;
  counts: [string, number][] | null;
  files: FileChange[];
  fileCount: number;
  revision: number | null;
}

export interface DecisionView {
  state: "current" | "stale" | "orphaned";
  targetId: string;
  kind: string;
  label: string | null;
  verdict: string;
  reason: string;
  author: string;
  channel: string;
  revision: number;
  createdAt: string;
  fingerprint: string;
  nowFingerprint: string | null;
  changes: [string, string, string][] | null;
  changesKnown: boolean;
  viewed: number | null;
}

/** What an area or a finding shows of its decision: `current`, `stale` (needs review, not applied) or `none` (only when the report has decisions). */
export interface Decided {
  state: "current" | "stale" | "none";
  entry: DecisionView | null;
}

export interface DecisionsView {
  current: DecisionView[];
  stale: DecisionView[];
  orphaned: DecisionView[];
  undecidedAreas: string[];
  undecidedFindings: [number, string][];
  records: RecordView[];
}

export interface AreaView {
  index: number;
  id: string | null;
  fingerprint: string | null;
  label: string;
  kind: string;
  repo: string;
  module: string;
  priority: string;
  byName: boolean;
  routeLine: Ev;
  chains: Ev[][];
  alsoMounted: string[];
  complete: boolean | null;
  reasons: string[];
  findings: number[];
  decision: Decided | null;
}

export interface FindingView {
  number: number;
  index: number;
  id: string | null;
  fingerprint: string | null;
  kind: string;
  title: string;
  risk: string;
  confidence: string;
  basis: string;
  evidence: Ev[];
  isExclusion: boolean;
  decision: Decided | null;
}

export interface FindingGroup {
  kind: string;
  findings: FindingView[];
}

export interface BranchRow {
  branch: string;
  short: string;
  state: string;
  found: number;
  detail: string;
  evidence: Ev[];
  findings: number[];
}

export interface HopView {
  kind: string;
  fromRepo: string;
  toRepo: string;
  depth: number;
  detail: string;
  candidate: boolean;
  complete: boolean | null;
  reasons: string[];
  pins: [string, string, string][];
}

export interface ConsumerRow {
  repo: string;
  producer: string;
  branch: string;
  pin: string | null;
  state: string;
  adoption: string;
  tag: string | null;
  firstRelease: string | null;
  detail: string;
  evidence: Ev[];
}

export interface HttpView {
  clientRepo: string;
  serverRepo: string;
  method: string | null;
  url: string;
  route: string;
  kind: string;
  candidate: boolean;
  removed: boolean;
  rematched: string | null;
  note: string;
  client: Ev;
  via: Ev[];
  routeEvidence: Ev[];
}

export interface ContractChangeView {
  repo: string;
  symbol: string;
  path: string;
  change: string;
  kinds: string[];
  before: string;
  after: string;
  evidence: Ev[];
}

export interface ContractReadView {
  repo: string;
  branch: string;
  owner: string;
  field: string;
  basis: string;
  form: string;
  pin: string | null;
  adoption: string;
  symbol: string;
  evidence: Ev[];
}

export interface Cross {
  hops: HopView[];
  consumers: ConsumerRow[];
  http: HttpView[];
  changes: ContractChangeView[];
  reads: ContractReadView[];
  candidates: ContractReadView[];
  unfollowed: string[];
  unread: string[];
  empty: boolean;
}

export interface BackendView {
  client: Ev;
  method: string;
  path: string;
  repo: string;
  handler: string;
  modules: { depth: number; path: string; importedBy: string; evidence: Ev }[];
  complete: boolean;
  reasons: string[];
}

export interface SuggestionView {
  source: string;
  caseId: string;
  title: string;
  words: string[];
  /** The index of the report's areas the case was suggested for (null: tied to no area of this report). */
  area: number | null;
  /** The spec line of a suggestion that reads code. */
  evidence: Ev[];
}

export interface MappedView {
  areaId: string;
  caseId: string;
  title: string;
  source: string;
  mapping: string;
}

/** `listed` (suggestions), `unavailable`, `incomplete` or `unconfigured` (the records say), `read_none` (every source was read completely and
 *  suggested nothing) or `not_read` (this run read no test source: nothing is claimed either way). */
export type SuggestedState = "listed" | "unavailable" | "incomplete" | "unconfigured" | "read_none" | "not_read";

export interface Suggested {
  state: SuggestedState;
  items: SuggestionView[];
  /** The source records of every state, blocking ones first: a list of suggestions may still be partial. */
  records: RecordView[];
  /** What a spec source read but could not use: addresses that are not literals, visits that match no screen. */
  partial: RecordView[];
  /** Each configured source and what became of it, when the report says (`inputs.test_sources`). */
  sources: { source: string; state: string }[];
}

/** An edge kind of the author view: a fixed word the tool follows, or content (a kind the view does not know). */
export interface EdgeWord {
  text: string;
  content: boolean;
}

export interface AuthorRepo {
  repo: string;
  edges: EdgeWord[];
}

export interface AuthorView {
  repos: AuthorRepo[];
  files: { repo: string; path: string }[];
  branches: { branch: string; state: string; carries: boolean }[];
  tests: { repo: string; command: string | null; quoted: string[]; text: string }[];
}

export interface View {
  header: HeaderView;
  status: Status;
  records: RecordView[];
  notes: string[];
  recordsFirst: boolean;
  mustTest: AreaView[];
  sample: AreaView[];
  candidates: AreaView[];
  groups: FindingGroup[];
  exclusions: FindingView[];
  matrix: BranchRow[];
  cross: Cross;
  backend: BackendView[];
  suggested: Suggested;
  mapped: MappedView[];
  author: AuthorView | null;
  decisions: DecisionsView | null;
  /** The model notes of M14 (`model_notes`): kept apart from everything above, never counted, never matched to an area as evidence. Null when the report has none and no record. */
  modelNotes: ModelNotesView | null;
  completed: boolean;
}

// ------------------------------------------------------------------------------ reading

function ev(v: unknown): Ev {
  const d = dct(v);
  const ref = text(d.ref);
  const path = text(d.path);
  const line = integer(d.line);
  return { repo: text(d.repo), path, line, ref, short: ref.slice(0, 12), detail: text(d.detail), locator: `${path}:${line}`, claim: text(d.claim) };
}

function evs(v: unknown): Ev[] {
  return lst(v).map(ev);
}

function opt(v: unknown): string | null {
  return v === null || v === undefined ? null : text(v);
}

function strings(v: unknown): string[] {
  return lst(v).map(text);
}

/** The kinds of record that are about the suggested cases (a test source), not about the areas: the others are about the selection. */
const SUGGESTION_KINDS: readonly string[] = ["source_unconfigured", "source_unavailable", "suggestions_incomplete", "spec_url_partial", "spec_visit_unmatched"];

/** `selection` or `suggestions`: what a record is about. A record written before `scope` existed is told from its kind (`read.py: scope_of`). */
function scopeOf(r: Dict): "selection" | "suggestions" {
  if (r.scope === "selection" || r.scope === "suggestions") {
    return r.scope;
  }
  return SUGGESTION_KINDS.includes(text(r.kind)) ? "suggestions" : "selection";
}

/** (blocks selection, blocks suggestions) of a record of a report (`read.py: blocks`). A record written before `scope` existed has neither
 *  key: a record of a test source then said it blocked selection, and it is read as blocking suggestions, which is what it always meant. */
function blocksOf(r: Dict): [boolean, boolean] {
  const selection = r.blocks_selection === true;
  if ("scope" in r) {
    return [selection, r.blocks_suggestions === true];
  }
  return scopeOf(r) === "suggestions" ? [false, selection] : [selection, false];
}

function record(r: Dict): RecordView {
  const [selection, suggestions] = blocksOf(r);
  return { kind: text(r.kind), subject: text(r.subject), notEstablished: text(r.not_established), detail: text(r.detail), blocking: selection, blocksSuggestions: suggestions, scope: scopeOf(r) };
}

function records(report: Report): RecordView[] {
  const out = lst(report.incompleteness).filter(isDict).map(record);
  const first = (r: RecordView): boolean => r.blocking || r.blocksSuggestions;
  return [...out.filter(first), ...out.filter((r) => !first(r))]; // blocking first (either scope), the report's order otherwise
}

function label(change: Dict): string {
  const spec = dct(change.spec);
  for (const key of ["pr", "compare", "range"]) {
    if (key in spec) {
      return `${key} ${text(spec[key])}`;
    }
  }
  return text(change.kind);
}

function propagation(report: Report): Dict {
  const props = lst(report.propagations);
  return props.length > 0 ? dct(props[0]) : {};
}

function isInt(v: unknown): v is number {
  return typeof v === "number" && Number.isInteger(v);
}

function counts(report: Report): [string, number][] | null {
  if (report.status !== "completed") {
    return null; // a run that did not complete has no count: a partial list must never be countable
  }
  const given = report.counts;
  const flags = lst(report.incompleteness).filter(isDict).map(blocksOf);
  const suggestionBlocking = flags.filter(([, sug]) => sug).length;
  if (isDict(given) && COUNT_KEYS.every((k) => isInt(given[k]))) {
    const last = given.blocking_suggestion_records;
    return [...COUNT_KEYS.map((k): [string, number] => [k, given[k] as number]), ["blocking_suggestion_records", isInt(last) ? last : suggestionBlocking]];
  }
  const areas = lst(report.areas).map(dct);
  const findings = lst(report.findings).map(dct);
  const n = (list: Dict[], f: (x: Dict) => boolean) => list.filter(f).length;
  return [
    ["areas", n(areas, (a) => !a.by_name)],
    ["must_test", n(areas, (a) => a.priority === "must_test")],
    ["sample", n(areas, (a) => a.priority === "sample")],
    ["candidates", n(areas, (a) => Boolean(a.by_name))],
    ["findings", findings.length],
    ["exclusions", n(findings, (f) => Boolean(f.is_exclusion))],
    ["records", flags.length],
    ["blocking_records", flags.filter(([sel]) => sel).length],
    ["blocking_suggestion_records", suggestionBlocking],
  ];
}

function sha(v: unknown): string | null {
  return typeof v === "string" && v !== "" ? v : null;
}

function header(report: Report): HeaderView {
  const change = dct(report.change);
  const prop = propagation(report);
  const complete = typeof prop.complete === "boolean" ? prop.complete : null;
  const head = sha(change.head_sha);
  const pre = sha(change.pre_change_sha);
  const base = sha(change.base_sha);
  const cr = change.change_request;
  const pr: PullRequestView | null = isDict(cr)
    ? { number: text(cr.number), title: text(cr.title), state: text(cr.state), baseRef: text(cr.base_ref), headRef: text(cr.head_ref) }
    : null;
  const files: FileChange[] = lst(change.files)
    .filter(isDict)
    .map((f) => ({ path: text(f.path), oldPath: opt(f.old_path), status: text(f.status), role: opt(f.role), added: integer(f.added), removed: integer(f.removed) }));
  const how: [string, string][] = Object.entries(dct(change.how)).map(([k, v]) => [k, text(v)]);
  const review = dct(report.review);
  return {
    repo: text(change.repo || dct(report.inputs).repo),
    label: label(change),
    status: report.status,
    tool: text(dct(report.tool).version),
    head,
    headShort: head === null ? null : head.slice(0, 12),
    preChange: pre,
    preChangeShort: pre === null ? null : pre.slice(0, 12),
    base,
    how,
    pr,
    asOf: opt(change.as_of),
    startedAt: opt(dct(report.run).started_at),
    elapsedS: typeof dct(report.run).elapsed_s === "number" ? (dct(report.run).elapsed_s as number) : null,
    complete,
    reasons: lst(prop.reasons).length,
    counts: counts(report),
    files,
    fileCount: integer(change.file_count) || files.length,
    revision: isInt(review.revision) ? review.revision : null,
  };
}

// ------------------------------------------------------------------------------ decisions

function decision(state: DecisionView["state"], d: unknown, viewed: number | null): DecisionView | null {
  if (!isDict(d)) {
    return null;
  }
  const ch = d.changes;
  const known = isDict(ch);
  const lines: [string, string, string][] = [];
  if (isDict(ch)) {
    for (const side of ["removed", "added"]) {
      for (const x of lst(ch[side])) {
        const y = dct(x);
        lines.push([side, `${text(y.repo)} ${text(y.path)}`, text(y.detail)]);
      }
    }
  }
  const now = d.now_fingerprint;
  return {
    state,
    targetId: text(d.target_id),
    kind: text(d.kind),
    label: opt(d.label),
    verdict: text(d.verdict),
    reason: text(d.reason),
    author: text(d.author),
    channel: text(d.channel),
    revision: integer(d.revision),
    createdAt: text(d.created_at),
    fingerprint: text(d.fingerprint).slice(0, 12),
    nowFingerprint: typeof now === "string" ? now.slice(0, 12) : null,
    changes: known ? lines : null,
    changesKnown: known,
    viewed,
  };
}

function ids(items: unknown[]): (string | null)[] {
  return items.map((x) => (isDict(x) && typeof x.id === "string" ? x.id : null));
}

function viewedRevision(report: Report): number | null {
  const n = dct(report.review).revision;
  return isInt(n) ? n : null;
}

interface DecisionParts {
  view: DecisionsView | null;
  byId: Map<string, Decided>;
  areaIds: (string | null)[];
  findingIds: (string | null)[];
}

function decisions(report: Report, findings: [number, Dict][]): DecisionParts {
  const areas = lst(report.areas);
  const areaIds = ids(areas);
  const findingIds = ids(findings.map(([, f]) => f));
  const none = (): DecisionParts => ({ view: null, byId: new Map(), areaIds, findingIds });
  const given = dct(report.review).decisions;
  if (!isDict(given)) {
    return none();
  }
  const viewed = viewedRevision(report);
  const lists = {
    current: lst(given.current).map((x) => decision("current", x, viewed)).filter((x): x is DecisionView => x !== null),
    stale: lst(given.stale).map((x) => decision("stale", x, viewed)).filter((x): x is DecisionView => x !== null),
    orphaned: lst(given.orphaned).map((x) => decision("orphaned", x, viewed)).filter((x): x is DecisionView => x !== null),
  };
  const recs = lst(given.records).filter(isDict).map(record);
  if (recs.length > 0) {
    // the store could not be read: nothing is known about any target, so no target says it has no decision
    return { view: { current: [], stale: [], orphaned: [], undecidedAreas: [], undecidedFindings: [], records: recs }, byId: new Map(), areaIds, findingIds };
  }
  if (lists.current.length + lists.stale.length + lists.orphaned.length === 0) {
    return none();
  }
  const byId = new Map<string, Decided>();
  for (const state of ["current", "stale"] as const) {
    for (const v of lists[state]) {
      byId.set(v.targetId, { state, entry: v });
    }
  }
  const undecidedAreas = areas.filter((_, i) => areaIds[i] !== null && !byId.has(areaIds[i] as string)).map((a) => text(dct(a).label));
  const undecidedFindings: [number, string][] = findings
    .filter((_, i) => findingIds[i] !== null && !byId.has(findingIds[i] as string))
    .map(([n, f]) => [n, text(f.title)]);
  return { view: { ...lists, undecidedAreas, undecidedFindings, records: recs }, byId, areaIds, findingIds };
}

function shown(byId: Map<string, Decided>, ident: string | null, active: boolean): Decided | null {
  if (!active) {
    return null;
  }
  return ident !== null && byId.has(ident) ? (byId.get(ident) as Decided) : { state: "none", entry: null };
}

// ------------------------------------------------------------------------------ areas and findings

function sameKey(identity: unknown, key: [string, string, string]): boolean {
  const parts = lst(identity).slice(1, 4).map(text);
  return parts.length === 3 && parts[0] === key[0] && parts[1] === key[1] && parts[2] === key[2];
}

function areas(report: Report, findings: [number, Dict][], decided: (i: number) => Decided | null): AreaView[] {
  const reached = new Map<string, Dict>();
  for (const r of lst(propagation(report).reached)) {
    const area = dct(dct(r).area);
    const key = JSON.stringify([text(area.repo), text(area.kind), text(area.label)]);
    const old = reached.get(key);
    if (old === undefined || (dct(r).complete === false && old.complete !== false)) {
      reached.set(key, dct(r));
    }
  }
  return lst(report.areas)
    .filter(isDict)
    .map((a, i) => {
      const key: [string, string, string] = [text(a.repo), text(a.kind), text(a.label)];
      const got = reached.get(JSON.stringify(key));
      const mine = findings.filter(([, f]) => (f.kind === "direct" || f.kind === "cross-repo") && sameKey(f.identity, key)).map(([n]) => n);
      return {
        index: i,
        id: typeof a.id === "string" ? a.id : null,
        fingerprint: typeof a.fingerprint === "string" ? a.fingerprint : null,
        label: key[2],
        kind: key[1],
        repo: key[0],
        module: text(a.module),
        priority: text(a.priority),
        byName: Boolean(a.by_name),
        routeLine: ev(a.route_line),
        chains: lst(a.chains).map(evs),
        alsoMounted: strings(a.also_mounted),
        complete: got !== undefined && typeof got.complete === "boolean" ? got.complete : null,
        reasons: got !== undefined ? strings(got.reasons) : [],
        findings: mine,
        decision: decided(i),
      };
    });
}

function sortAreas(list: AreaView[]): AreaView[] {
  // ordered by what is shown of each (the Python view model sorts the `Content`, which is already made visible)
  const key = (a: AreaView): [string, string, string] => [show(a.repo), show(a.kind), show(a.label)];
  return [...list].sort((x, y) => {
    const [a, b] = [key(x), key(y)];
    return cmpStr(a[0], b[0]) || cmpStr(a[1], b[1]) || cmpStr(a[2], b[2]) || x.index - y.index;
  });
}

function findingViews(report: Report, decided: (i: number) => Decided | null): { groups: FindingGroup[]; exclusions: FindingView[]; raw: [number, Dict][] } {
  const raw: [number, Dict][] = lst(report.findings).map((f, i): [number, Dict] => [i + 1, dct(f)]);
  const views: FindingView[] = raw.map(([n, f]) => ({
    number: n,
    index: n - 1,
    id: typeof f.id === "string" ? f.id : null,
    fingerprint: typeof f.fingerprint === "string" ? f.fingerprint : null,
    kind: text(f.kind),
    title: text(f.title),
    risk: text(f.risk),
    confidence: text(f.confidence),
    basis: text(f.confidence_basis),
    evidence: evs(f.evidence),
    isExclusion: Boolean(f.is_exclusion),
    decision: decided(n - 1),
  }));
  const plain = views.filter((v) => !v.isExclusion);
  const rank = (k: string) => (KIND_ORDER.includes(k) ? KIND_ORDER.indexOf(k) : KIND_ORDER.length);
  const kinds = [...new Set(plain.map((v) => v.kind))].sort((a, b) => rank(show(a)) - rank(show(b)) || cmpStr(show(a), show(b)));
  const groups = kinds.map((k) => ({ kind: k, findings: plain.filter((v) => v.kind === k) }));
  const exclusions = views.filter((v) => v.isExclusion).sort((a, b) => cmpStr(show(a.kind), show(b.kind)) || a.number - b.number);
  return { groups, exclusions, raw };
}

function matrix(report: Report, raw: [number, Dict][]): BranchRow[] {
  return lst(report.branches)
    .filter(isDict)
    .map((b) => {
      const name = text(b.branch);
      const linked = raw
        .filter(([, f]) => f.kind === "branch-missing" && lst(f.identity).length > 2 && text(lst(f.identity)[2]) === name)
        .map(([n]) => n);
      return { branch: name, short: text(b.commit).slice(0, 12), state: text(b.state), found: integer(b.added_lines_found), detail: text(b.detail), evidence: evs(b.evidence), findings: linked };
    });
}

// ------------------------------------------------------------------------------ cross-repository reach, backend calls

function member(m: unknown): string {
  const d = dct(m);
  if (Object.keys(d).length === 0) {
    return "";
  }
  let out = text(d.annotation) || text(d.kind);
  if (d.has_default) {
    out += ` = ${text(d.default)}`;
  }
  return out;
}

function reads(v: unknown): ContractReadView[] {
  return lst(v)
    .filter(isDict)
    .map((r) => ({
      repo: text(r.repo),
      branch: text(r.branch),
      owner: text(r.owner),
      field: text(r.field),
      basis: text(r.basis),
      form: text(r.form),
      pin: opt(r.pin),
      adoption: text(r.adoption),
      symbol: text(r.symbol),
      evidence: evs(r.evidence),
    }));
}

function methodPath(v: unknown): string {
  const d = dct(v);
  return `${text(d.method)} ${text(d.path)}`.trim();
}

function cross(report: Report): Cross {
  const p = propagation(report);
  const hops: HopView[] = lst(p.hops)
    .filter(isDict)
    .map((h) => ({
      kind: text(h.kind),
      fromRepo: text(h.from_repo),
      toRepo: text(h.to_repo),
      depth: integer(h.depth),
      detail: text(h.detail),
      candidate: Boolean(h.candidate),
      complete: typeof h.complete === "boolean" ? h.complete : null,
      reasons: strings(h.reasons),
      pins: lst(h.pins).filter(isDict).map((x): [string, string, string] => [text(x.ref), text(x.role), text(x.sha).slice(0, 12)]),
    }));
  const consumers: ConsumerRow[] = lst(p.consumers)
    .filter(isDict)
    .map((c) => ({
      repo: text(c.repo),
      producer: text(c.producer),
      branch: text(c.branch),
      pin: opt(c.pin),
      state: text(c.state),
      adoption: text(c.adoption),
      tag: opt(c.tag),
      firstRelease: opt(c.first_release),
      detail: text(c.detail),
      evidence: evs(c.evidence),
    }));
  const http: HttpView[] = lst(p.http_edges).map((e) => {
    const d = dct(e);
    const route = dct(d.route);
    return {
      clientRepo: text(d.client_repo),
      serverRepo: text(d.server_repo),
      method: opt(d.method),
      url: text(d.url),
      route: methodPath(route),
      kind: text(d.kind),
      candidate: Boolean(d.candidate),
      removed: Boolean(d.removed),
      rematched: isDict(d.rematched) ? methodPath(d.rematched) : null,
      note: text(d.note),
      client: ev(d.client),
      via: evs(d.via),
      routeEvidence: evs(route.evidence),
    };
  });
  const changes: ContractChangeView[] = lst(p.contract_changes).map((c) => {
    const d = dct(c);
    return {
      repo: text(d.repo),
      symbol: text(d.symbol),
      path: text(d.path),
      change: text(d.change),
      kinds: strings(d.kinds),
      before: member(d.before),
      after: member(d.after),
      evidence: evs(d.evidence),
    };
  });
  const out = {
    hops,
    consumers,
    http,
    changes,
    reads: reads(p.contract_reads),
    candidates: reads(p.contract_candidates),
    unfollowed: strings(p.unfollowed),
    unread: strings(p.unread_entry_points),
  };
  return { ...out, empty: hops.length + consumers.length + http.length + changes.length + out.reads.length + out.candidates.length === 0 };
}

function backend(report: Report): BackendView[] {
  return lst(report.backend_calls).map((b) => {
    const d = dct(b);
    const route = dct(d.route);
    return {
      client: ev(d.client),
      method: text(route.method),
      path: text(route.path),
      repo: text(route.repo),
      handler: text(d.handler),
      modules: lst(d.modules).filter(isDict).map((m) => ({ depth: integer(m.depth), path: text(m.path), importedBy: text(m.imported_by), evidence: ev(m.evidence) })),
      complete: d.complete !== false,
      reasons: strings(d.reasons),
    };
  });
}

const SOURCE_KINDS: readonly string[] = ["source_unavailable", "suggestions_incomplete", "source_unconfigured"];
const PARTIAL_KINDS: readonly string[] = ["spec_url_partial", "spec_visit_unmatched"];

/** Port of `_suggested` of `reachmap/report/model.py`: what the Tests tab says about the suggested cases. */
function suggested(report: Report, recs: RecordView[]): Suggested {
  const index = new Map<string, number>();
  lst(report.areas).forEach((a, i) => {
    if (isDict(a) && typeof a.id === "string") {
      index.set(a.id, i);
    }
  });
  const items: SuggestionView[] = lst(report.suggestions)
    .filter(isDict)
    .map((s) => ({
      source: text(s.source),
      caseId: text(s.case_id),
      title: text(s.title),
      words: strings(s.matched_words),
      area: typeof s.area_id === "string" && index.has(s.area_id) ? (index.get(s.area_id) as number) : null,
      evidence: evs(s.evidence),
    }));
  const mine = recs.filter((r) => SOURCE_KINDS.includes(r.kind));
  const partial = recs.filter((r) => PARTIAL_KINDS.includes(r.kind));
  const sources = lst(dct(report.inputs).test_sources)
    .filter(isDict)
    .map((x) => ({ source: text(x.source), state: text(x.state) }));
  const kinds = new Set(mine.map((r) => r.kind));
  if (items.length > 0) {
    return { state: "listed", items, records: mine, partial, sources };
  }
  if (kinds.has("source_unavailable")) {
    return { state: "unavailable", items: [], records: mine, partial, sources };
  }
  if (kinds.has("suggestions_incomplete")) {
    return { state: "incomplete", items: [], records: mine, partial, sources };
  }
  if (kinds.has("source_unconfigured")) {
    return { state: "unconfigured", items: [], records: mine, partial, sources };
  }
  if (dct(report.inputs).test_source_read === true) {
    return { state: "read_none", items: [], records: [], partial, sources };
  }
  return { state: "not_read", items: [], records: [], partial: [], sources };
}

// ------------------------------------------------------------------------------ the author view (port of author.py)

const SAFE_SHELL = /^[A-Za-z0-9_@%+=:,./-]+$/;

/** `shlex.quote`. */
export function shellQuote(s: string): string {
  if (s === "") {
    return "''";
  }
  return SAFE_SHELL.test(s) ? s : `'${s.replace(/'/g, `'"'"'`)}'`;
}

function author(report: Report): AuthorView {
  const change = dct(report.change);
  const origin = text(change.repo);
  const p = propagation(report);

  // other repositories reached, with the kinds of edge that led there
  const found = new Map<string, Map<string, EdgeWord>>();
  const add = (repo: unknown, word: string, isContent = false): void => {
    const r = text(repo);
    if (r !== "" && r !== origin) {
      const kinds = found.get(r) ?? new Map<string, EdgeWord>();
      kinds.set(`${isContent ? 1 : 0}:${word}`, { text: word, content: isContent });
      found.set(r, kinds);
    }
  };
  const edge = (kind: unknown): [string, boolean] => {
    const k = text(kind);
    return [k, !KNOWN_EDGES.includes(k)];
  };
  for (const h of lst(p.hops)) {
    add(dct(h).to_repo, ...edge(dct(h).kind));
  }
  for (const ps of lst(p.passes)) {
    add(dct(ps).repo, "walk");
  }
  for (const c of lst(p.consumers)) {
    add(dct(c).repo, "package");
  }
  for (const e of lst(p.http_edges)) {
    add(dct(e).client_repo, "http");
    add(dct(e).server_repo, "http");
  }
  for (const r of [...lst(p.contract_reads), ...lst(p.contract_candidates)]) {
    add(dct(r).repo, "contract");
  }
  for (const a of lst(report.areas)) {
    add(dct(a).repo, "walk");
  }
  const repos: AuthorRepo[] = [...found.entries()]
    .sort(([a], [b]) => cmpStr(a, b))
    .map(([repo, kinds]) => {
      const all = [...kinds.values()];
      // a pass or an area only says the walk was in the repository: keep it only when no edge kind says more
      const real = all.filter((k) => !(k.text === "walk" && !k.content));
      const chosen = (real.length > 0 ? real : all).sort((x, y) => cmpStr(x.text, y.text) || Number(x.content) - Number(y.content));
      return { repo, edges: chosen };
    });

  // files beyond the diff
  const touched = new Set<string>();
  for (const f of lst(change.files)) {
    touched.add(JSON.stringify([origin, text(dct(f).path)]));
    if (dct(f).old_path) {
      touched.add(JSON.stringify([origin, text(dct(f).old_path)]));
    }
  }
  const seen = new Map<string, { repo: string; path: string }>();
  const cite = (e: unknown): void => {
    const d = dct(e);
    const repo = text(d.repo);
    const path = text(d.path);
    const key = JSON.stringify([repo, path]);
    if (repo !== "" && path !== "" && !touched.has(key)) {
      seen.set(key, { repo, path });
    }
  };
  for (const a of lst(report.areas)) {
    cite(dct(a).route_line);
    for (const chain of lst(dct(a).chains)) {
      lst(chain).forEach(cite);
    }
  }
  for (const f of lst(report.findings)) {
    if (dct(f).is_exclusion === true) {
      continue;
    }
    lst(dct(f).evidence).forEach(cite);
  }
  const files = [...seen.values()].sort((x, y) => cmpStr(x.repo, y.repo) || cmpStr(x.path, y.path));

  const branches = lst(report.branches)
    .map((b) => ({ branch: text(dct(b).branch), state: text(dct(b).state), carries: text(dct(b).state) === "present" }))
    .sort((x, y) => Number(x.carries) - Number(y.carries) || cmpStr(show(x.branch), show(y.branch))); // without the change first

  const tests = lst(p.unit_test_commands)
    .map((c) => {
      const d = dct(c);
      const quoted = lst(d.paths).map((x) => shellQuote(text(x)));
      const command = typeof d.command === "string" && d.command.trim() !== "" ? d.command : null;
      return { repo: text(d.repo), command, quoted, text: command !== null ? command : quoted.join(" ") };
    })
    .sort((x, y) => cmpStr(show(x.repo), show(y.repo)));
  return { repos, files, branches, tests };
}

// ------------------------------------------------------------------------------ the whole view

export function build(report: Report): View {
  const status = report.status;
  const recs = records(report);
  const notes = strings(report.notes);
  const head = header(report);
  const empty: Cross = { hops: [], consumers: [], http: [], changes: [], reads: [], candidates: [], unfollowed: [], unread: [], empty: true };
  if (status !== "completed") {
    return {
      header: head, status, records: recs, notes, recordsFirst: true, mustTest: [], sample: [], candidates: [], groups: [], exclusions: [], matrix: [], cross: empty,
      backend: [], suggested: { state: "not_read", items: [], records: [], partial: [], sources: [] }, mapped: [], author: null, decisions: null, modelNotes: null, completed: false,
    };
  }
  const rawFindings: [number, Dict][] = lst(report.findings).map((f, i): [number, Dict] => [i + 1, dct(f)]);
  const parts = decisions(report, rawFindings);
  const active = parts.view !== null && parts.view.records.length === 0;
  const forIds = (list: (string | null)[]) => (i: number): Decided | null => shown(parts.byId, i < list.length ? list[i] : null, active);
  const { groups, exclusions, raw } = findingViews(report, forIds(parts.findingIds));
  const all = areas(report, raw, forIds(parts.areaIds));
  const mapped: MappedView[] = lst(report.mapped_tests)
    .filter(isDict)
    .map((m) => ({ areaId: text(m.area_id), caseId: text(m.case_id), title: text(m.title), source: text(m.source), mapping: text(m.mapping) }));
  return {
    header: head,
    status,
    records: recs,
    notes,
    recordsFirst: recs.some((r) => r.blocking),
    mustTest: sortAreas(all.filter((a) => a.priority === "must_test" && !a.byName)),
    sample: sortAreas(all.filter((a) => a.priority === "sample" && !a.byName)),
    candidates: sortAreas(all.filter((a) => a.priority === "candidate" || a.byName)),
    groups,
    exclusions,
    matrix: matrix(report, raw),
    cross: cross(report),
    backend: backend(report),
    suggested: suggested(report, recs),
    mapped,
    author: author(report),
    decisions: parts.view,
    modelNotes: readModelNotes(report.model_notes, record),
    completed: true,
  };
}

/** Every area of a view, in the order of the report (the order of `AreaView.index`). */
export function allAreas(view: View): AreaView[] {
  return [...view.mustTest, ...view.sample, ...view.candidates].sort((a, b) => a.index - b.index);
}

/** Every finding of a view, plain and excluded, by number. */
export function allFindings(view: View): FindingView[] {
  return [...view.groups.flatMap((g) => g.findings), ...view.exclusions].sort((a, b) => a.number - b.number);
}
