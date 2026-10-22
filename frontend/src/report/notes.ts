// Model notes (M14): what a model client recorded beside a report over MCP, as the runner serves it in `model_notes`. A port of the notes part of
// `reachmap/report/model.py` (`_note`, `_verification`, `_model_notes`) and of the Model notes section of `reachmap/report/layout.py`, held to
// them by `fixtures/order.json` (`view.test.ts`): the same notes in the same states, and the section line by line.
//
// A note is never a finding, evidence, coverage or a decision: nothing here is counted, and nothing here touches an area, a finding, a
// priority, a risk or a count. Every text of a note (the text, the client's name and version, the target's label, the proposed file, the detail
// of a check) is content, kept as a plain string and shown by `components/Content`; the words for a kind, a state and a reason are chosen from
// fixed tables by an own-property lookup (a kind called `__proto__` finds nothing and is shown as content). The stamp `recorded via MCP, client
// reported as <name>` is built here from the client's own fields and shown outside the text, as the client's claim: a text that says the same is
// text, and the stamp is never read from a note's text.

import { dct, integer, isDict, lst, own, text, type Dict } from "./read";
import { show } from "./text";
import type { RecordView } from "./view";
import {
  KIND_WORDS,
  NOTES_INTRO,
  NOTES_LABEL,
  NOTE_GROUPS,
  NOTE_LABELS,
  NOTE_PHRASES,
  OTHER_NOTES,
  REASON_WORDS,
  STAMP,
  STATE_WORDS,
  UNREADABLE_HEADING,
  UNREADABLE_NOTE,
} from "./notewords";

export interface VerificationView {
  status: string;
  reason: string;
  read: string;
  sha: string | null;
  symbol: string | null;
  lines: number[];
  detail: string | null;
}

export interface NoteView {
  id: string;
  kind: string;
  state: string;
  text: string;
  revision: number;
  /** The short commit the note was made on. */
  head: string | null;
  targetId: string | null;
  targetLabel: string | null;
  clientName: string;
  clientVersion: string;
  createdAt: string;
  /** The revision of the report the runner attached the note to (`viewed`), or null when it did not say. */
  viewed: number | null;
  /** Repository, path and symbol of a proposed file. */
  proposal: [string, string, string] | null;
  verification: VerificationView | null;
}

export interface ModelNotesView {
  notes: NoteView[];
  records: RecordView[];
  /** The current and stale notes by the id of their target (an area or a finding): what a drawer shows. An orphaned note, one whose state this version does not know and one about the report as a whole are attached to nothing. */
  byTarget: ReadonlyMap<string, readonly NoteView[]>;
}

// ------------------------------------------------------------------------------ reading

/** The first twelve characters (code points), as Python slices a commit. */
function short(s: string): string {
  return Array.from(s).slice(0, 12).join("");
}

function filled(v: unknown): string | null {
  return typeof v === "string" && v !== "" ? v : null;
}

function verification(v: unknown): VerificationView | null {
  if (!isDict(v)) {
    return null;
  }
  const sha = filled(v.sha);
  return {
    status: text(v.status),
    reason: text(v.reason),
    read: text(v.read),
    sha: sha === null ? null : short(sha),
    symbol: filled(v.symbol),
    lines: lst(v.lines).filter((x): x is number => typeof x === "number" && Number.isInteger(x)),
    detail: filled(v.detail),
  };
}

function note(n: Dict): NoteView {
  const proposal = n.proposal;
  const p = dct(proposal);
  const client = dct(n.client);
  const head = filled(n.head_sha);
  return {
    id: text(n.id),
    kind: text(n.kind),
    state: text(n.state),
    text: text(n.text),
    revision: integer(n.revision),
    head: head === null ? null : short(head),
    targetId: filled(n.target_id),
    targetLabel: filled(n.target_label),
    clientName: text(client.name),
    clientVersion: text(client.version),
    createdAt: text(n.created_at),
    viewed: typeof n.viewed === "number" && Number.isInteger(n.viewed) ? n.viewed : null,
    proposal: isDict(proposal) ? [text(p.repo), text(p.path), text(p.symbol)] : null,
    verification: verification(n.verification),
  };
}

/** The model notes of a report (`report.model_notes`), or null when it has none and no record: a report with none shows no tab. */
export function readModelNotes(given: unknown, toRecord: (r: Dict) => RecordView): ModelNotesView | null {
  if (!isDict(given)) {
    return null;
  }
  const notes = lst(given.notes).filter(isDict).map(note);
  const records = lst(given.records).filter(isDict).map(toRecord);
  if (notes.length === 0 && records.length === 0) {
    return null;
  }
  const byTarget = new Map<string, NoteView[]>();
  for (const n of notes) {
    if (n.targetId !== null && (n.state === "current" || n.state === "stale")) {
      const mine = byTarget.get(n.targetId);
      if (mine === undefined) {
        byTarget.set(n.targetId, [n]);
      } else {
        mine.push(n);
      }
    }
  }
  return { notes, records, byTarget };
}

/**
 * A note the runner calls current is about the latest revision. When the report being viewed is an earlier one (`viewed` is not the revision the
 * note was made on), a drawer of that report must not call it current: it is shown as "made on revision N" instead. A stale or an orphaned note
 * already says it was made on another revision, and the tab keeps the words of the Python layout (it shows the section as `layout.py` does).
 */
export function madeElsewhere(n: NoteView): boolean {
  return n.state === "current" && n.viewed !== null && n.viewed !== n.revision;
}

/** The notes a drawer shows for an area or a finding (none for a target without an id). */
export function notesAbout(m: ModelNotesView | null, id: string | null): readonly NoteView[] {
  return m === null || id === null ? [] : (m.byTarget.get(id) ?? []);
}

// ------------------------------------------------------------------------------ the section

/** A piece of a line: the words of this tool, content shown as a code span, content shown as plain text, or strong words. */
export type Run = string | { code: string } | { c: string } | { strong: string };

/** A labelled line of a note: `<label> <rest>`, and for the text of a note a block of its own under it. */
export interface Fact {
  /** What the line is, for a test and a style: never a piece of content. */
  field: string;
  label: string;
  rest: Run[];
  block: string | null;
}

export interface NoteItem {
  head: Run[];
  facts: Fact[];
  /** The note this item is made from, or the record, for the component that draws it. */
  note: NoteView | null;
  record: RecordView | null;
}

export type SectionBlock =
  | { kind: "intro"; label: string; text: string }
  | { kind: "heading"; text: string; id: string }
  | { kind: "para"; text: string }
  | { kind: "items"; items: NoteItem[] };

function fact(field: string, label: string, ...rest: Run[]): Fact {
  return { field, label, rest, block: null };
}

function verificationFacts(v: VerificationView): Fact[] {
  const status = own({ verified: NOTE_PHRASES.verified, not_verified: NOTE_PHRASES.notVerified }, v.status);
  const reason = own(REASON_WORDS, v.reason);
  const out = [
    fact("verification", NOTE_LABELS.verification, status !== undefined ? status : { code: v.status }, ": ", reason !== undefined ? reason : { code: v.reason }),
  ];
  if (v.sha !== null) {
    out.push(fact("read-at", NOTE_LABELS.readAt, { code: v.sha }));
  }
  if (v.lines.length > 0) {
    out.push(fact("where", NOTE_LABELS.where, NOTE_PHRASES.symbolAtLines + v.lines.map((n) => String(n)).join(", ")));
  }
  if (v.detail !== null) {
    out.push(fact("detail", NOTE_LABELS.detail, { c: v.detail }));
  }
  return out;
}

/** The rest of the stamp line: the name is the client's own claim, and its version (or that it gave none). */
function clientVersion(n: NoteView): Run[] {
  if (show(n.clientVersion).trim() !== "") {
    return [NOTE_PHRASES.nameIsClaimVersionBefore, { code: n.clientVersion }, ")"];
  }
  return [NOTE_PHRASES.nameIsClaimNoVersion];
}

/** The lines of one note, as `layout._note_item` lists them. */
export function noteItem(n: NoteView, drawer = false): NoteItem {
  const kind = own(KIND_WORDS, n.kind);
  const elsewhere = drawer && madeElsewhere(n);
  const state = elsewhere
    ? `${NOTE_PHRASES.elsewhereA}${n.revision}${NOTE_PHRASES.elsewhereB}${n.viewed}${NOTE_PHRASES.elsewhereC}`
    : own(STATE_WORDS, n.state);
  const about: Run[] = n.targetId !== null ? [{ code: n.targetLabel !== null ? n.targetLabel : n.targetId }] : [NOTE_PHRASES.wholeReport];
  const facts: Fact[] = [
    { field: "text", label: NOTE_LABELS.text, rest: [], block: n.text },
    fact("about", NOTE_LABELS.about, ...about),
    fact("state", NOTE_LABELS.state, state !== undefined ? state : { code: n.state }),
    fact("made-on", NOTE_LABELS.madeOn, `revision ${n.revision}`, ...(n.head !== null ? [NOTE_PHRASES.atCommit, { code: n.head }] : [])),
    fact("stamp", NOTE_LABELS.stamp, STAMP, " ", { code: n.clientName }, ...clientVersion(n)),
    fact("recorded", NOTE_LABELS.recorded, { code: n.createdAt }),
  ];
  if (n.proposal !== null) {
    const [repo, path, symbol] = n.proposal;
    facts.push(fact("proposed-file", NOTE_LABELS.proposedFile, { code: repo }, " ", { code: path }, NOTE_PHRASES.symbol, { code: symbol }));
  }
  if (n.verification !== null) {
    facts.push(...verificationFacts(n.verification));
  }
  return { head: [kind !== undefined ? kind : { code: n.kind }, " ", { code: n.id }], facts, note: n, record: null };
}

/** The lines of a record of an unreadable store, as `layout._record_item` lists them. */
function recordItem(r: RecordView): NoteItem {
  const facts = [fact("not-established", NOTE_LABELS.notEstablished, { c: r.notEstablished })];
  if (show(r.detail) !== "") {
    facts.push(fact("detail", NOTE_LABELS.detail, { c: r.detail }));
  }
  return { head: [{ code: r.kind }, " ", { code: r.subject }], facts, note: null, record: r };
}

/** The Model notes section as `layout._model_notes` builds it: the label and the sentence that says what a note is, the records of an unreadable store, then the notes by state. */
export function noteSection(m: ModelNotesView): SectionBlock[] {
  const blocks: SectionBlock[] = [{ kind: "intro", label: NOTES_LABEL, text: NOTES_INTRO }];
  if (m.records.length > 0) {
    blocks.push(
      { kind: "heading", text: UNREADABLE_HEADING, id: "notes-unreadable" },
      { kind: "para", text: UNREADABLE_NOTE },
      { kind: "items", items: m.records.map(recordItem) },
    );
  }
  const known = new Set(NOTE_GROUPS.map(([state]) => state));
  for (const [state, title, sentence, id] of NOTE_GROUPS) {
    const mine = m.notes.filter((n) => n.state === state);
    if (mine.length > 0) {
      blocks.push({ kind: "heading", text: title, id }, { kind: "para", text: sentence }, { kind: "items", items: mine.map((n) => noteItem(n)) });
    }
  }
  const other = m.notes.filter((n) => !known.has(n.state));
  if (other.length > 0) {
    const [title, sentence, id] = OTHER_NOTES;
    blocks.push({ kind: "heading", text: title, id }, { kind: "para", text: sentence }, { kind: "items", items: other.map((n) => noteItem(n)) });
  }
  return blocks;
}

// ------------------------------------------------------------------------------ as plain text, for the tests

export function flatRuns(runs: readonly Run[]): string {
  return runs
    .map((r) => (typeof r === "string" ? r : "code" in r ? show(r.code) : "c" in r ? show(r.c) : r.strong))
    .join("");
}

/** What a line says: its label, a space and the rest (as Python's `**Label:** rest`), and the block of the text of a note under it. */
export function flatFact(f: Fact): [string, string | null] {
  return [f.rest.length > 0 ? `${f.label} ${flatRuns(f.rest)}` : f.label, f.block === null ? null : show(f.block)];
}

/** A section as the lists `make_reports.py: section_of` makes of the Python one. */
export function flatSection(blocks: readonly SectionBlock[]): unknown[] {
  return blocks.map((b) => {
    switch (b.kind) {
      case "intro":
        return ["para", `${b.label} ${b.text}`];
      case "heading":
        return ["heading", b.text, b.id];
      case "para":
        return ["para", b.text];
      case "items":
        return ["items", b.items.map((i) => [flatRuns(i.head), i.facts.map(flatFact)])];
    }
  });
}
