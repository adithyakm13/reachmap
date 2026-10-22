// The report fixtures: the four hand-made ones of M10 and the two real scenario reports (`frontend/fixtures/make_reports.py` made them).
// They are read as text and parsed here, not imported as JSON modules: the hostile report holds a lone surrogate (`\ud800`), which the
// JSON loader of the bundler refuses and `JSON.parse` accepts.

import type { Report } from "../api/types";
import complete from "../../fixtures/complete.json?raw";
import failed from "../../fixtures/failed.json?raw";
import hostile from "../../fixtures/hostile.json?raw";
import noteWords from "../../fixtures/note_words.json?raw";
import notes from "../../fixtures/notes.json?raw";
import notesHostile from "../../fixtures/notes_hostile.json?raw";
import notesR1 from "../../fixtures/notes_r1.json?raw";
import notesUnreadable from "../../fixtures/notes_unreadable.json?raw";
import order from "../../fixtures/order.json?raw";
import s11 from "../../fixtures/s11.json?raw";
import s13 from "../../fixtures/s13.json?raw";
import sources from "../../fixtures/sources.json?raw";
import sourcesIncomplete from "../../fixtures/sources_incomplete.json?raw";
import sourcesNone from "../../fixtures/sources_none.json?raw";
import sourcesUnavailable from "../../fixtures/sources_unavailable.json?raw";
import sourcesUnconfigured from "../../fixtures/sources_unconfigured.json?raw";
import timedOut from "../../fixtures/timed_out.json?raw";
import unordered from "../../fixtures/unordered.json?raw";

// the `sources*` ones are s13 with test sources read in each state of the suggested cases, the `notes*` ones the `unordered` report with model notes in each
// state (current, stale, orphaned), the first report seen while its rerun exists, with hostile ones, and with a store that cannot be read: all made by the Python model (see make_reports.py)
export const RAW = {
  complete, failed, hostile, s11, s13, timed_out: timedOut, unordered, sources,
  sources_incomplete: sourcesIncomplete, sources_none: sourcesNone, sources_unavailable: sourcesUnavailable, sources_unconfigured: sourcesUnconfigured,
  notes, notes_r1: notesR1, notes_hostile: notesHostile, notes_unreadable: notesUnreadable,
} as const;
export type FixtureName = keyof typeof RAW;
export const NAMES = Object.keys(RAW) as FixtureName[];

/** A fresh copy of a fixture report (a test may change it). */
export function fixture(name: FixtureName): Report {
  return JSON.parse(RAW[name]) as Report;
}

export const ORDER = JSON.parse(order) as Record<FixtureName, Record<string, unknown>>;

/** The words of `reachmap.report.notewords` and the sentences of the layout around them, as Python says them. */
export const NOTE_WORDS = JSON.parse(noteWords) as {
  stamp: string;
  label: string;
  kinds: Record<string, string>;
  states: Record<string, string>;
  reasons: Record<string, string>;
  intro: string;
  groups: [string, string, string, string][];
  other: [string, string, string];
};
