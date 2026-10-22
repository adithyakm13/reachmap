// The words this page says about a model note (M14): a port of `reachmap/report/notewords.py` and of the sentences of the Model notes section of
// `reachmap/report/layout.py`. They are fixed sentences, none of which quotes a note, a claim or a file, and they pass the wording check when this
// module is loaded. `frontend/fixtures/note_words.json` (made by the Python tables) holds this file to them, word for word.

import { clean } from "./wording";

export const STAMP = "recorded via MCP, client reported as";
export const NOTES_LABEL = "Model notes, recorded via MCP; not evidence.";

export const NOTES_INTRO =
  "A note is what a model client wrote beside this report. It is not a finding, not evidence and not coverage, and it changes no area, " +
  "priority, risk, confidence, count or gap. A verified proposed file is still a note: the check says the file exists at a revision the report " +
  "read and names the symbol in code, not that the change reaches it.";

export const KIND_WORDS: Record<string, string> = clean(
  { observation: "observation", question: "question", proposed_test: "proposed test", proposed_file: "proposed file" },
  "KIND_WORDS",
);

export const STATE_WORDS: Record<string, string> = clean(
  {
    current: "current: made on the latest revision, about the claim this report makes",
    stale: "stale: made on an earlier revision, or about a claim that has changed since",
    orphaned: "orphaned: what it was about is not in the latest report",
  },
  "STATE_WORDS",
);

/** What the check of a proposed file found (the codes are what is stored). */
export const REASON_WORDS: Record<string, string> = clean(
  {
    verified: "the file exists at a revision the report read and names a changed symbol in code, outside a comment or a string",
    no_config: "no workspace configuration was given to reachmap mcp, so no file was read",
    unknown_repo: "the repository is not in the workspace configuration, so no file was read",
    repo_not_read: "the report did not read this repository at any revision, so there is no revision to read the file at",
    symbol_not_changed: "the symbol is not the name of a symbol or a contract field that this change touched",
    symbol_qualifier: "the symbol is qualified with a class or module other than the one the change touched",
    symbol_not_distinctive: "the symbol is defined too often, or its definitions could not be counted, so it does not link code by name",
    file_absent: "the file is not in the tree at the revision the report read",
    file_unreadable: "the file could not be read, so nothing is established about it",
    no_reader: "this kind of file is not read as code by this version",
    unparsed: "the file could not be read as code",
    not_in_code: "the symbol does not appear in the code of the file (a comment or a string does not count)",
    unchecked_text: "a proposed file given as plain note text was not checked; propose_file checks one",
  },
  "REASON_WORDS",
);

/** The groups of the section, in order: the state, the heading, the sentence under it and the id of the heading. */
export const NOTE_GROUPS: readonly (readonly [string, string, string, string])[] = [
  ["current", "Current notes", "Made on the latest revision, about the claim this report makes.", "notes-current"],
  [
    "stale",
    "Notes on an earlier revision or an older claim",
    "Each shows the revision it was made on. A rerun makes a note stale; it is kept, not applied.",
    "notes-stale",
  ],
  [
    "orphaned",
    "Notes about something no longer in the latest report",
    "The area or finding these were about is gone. They are listed here and attached to nothing.",
    "notes-orphaned",
  ],
];
export const OTHER_NOTES: readonly [string, string, string] = ["Notes with a state this version does not know", "The state is shown as it was recorded.", "notes-other"];

export const UNREADABLE_HEADING = "Notes that could not be read";
export const UNREADABLE_NOTE = "A blocking record means the notes recorded for this change are not shown. It blocks this section only.";

/** The labels of the lines of a note and of a record (each with its colon). */
export const NOTE_LABELS = clean(
  {
    text: "Text:",
    about: "About:",
    state: "State:",
    madeOn: "Made on:",
    stamp: "Stamp:",
    recorded: "Recorded:",
    proposedFile: "Proposed file:",
    verification: "Verification:",
    readAt: "Read at:",
    where: "Where:",
    detail: "Detail:",
    notEstablished: "Not established:",
  },
  "NOTE_LABELS",
);

/** The pieces of a line, the words of this tool (they are checked above) and the claim of the client that the name is its own. */
export const NOTE_PHRASES = clean(
  {
    wholeReport: "the report as a whole",
    atCommit: " at commit ",
    nameIsClaimNoVersion: " (the name is the client's own claim; it gave no version)",
    nameIsClaimVersionBefore: " (the name is the client's own claim; its version is ",
    verified: "verified",
    notVerified: "not verified",
    symbolAtLines: "the symbol is in the code of the file at lines ",
    symbol: ", symbol ",
    elsewhereA: "made on revision ",
    elsewhereB: ", not on revision ",
    elsewhereC: ", the revision of this report",
  },
  "NOTE_PHRASES",
);

// What the page says outside the section of the report: the tab, the section of a drawer and its badges.
export const NOTE_PAGE = clean(
  {
    section: "Model notes",
    tabUnreadable: "The notes recorded for this change could not be read",
    aboutArea: "Model notes about this area",
    aboutFinding: "Model notes about this finding",
    seeAll: "All model notes of this report",
    badgeOne: "1 model note",
    badgeMany: "model notes",
    staleBadge: "stale",
  },
  "NOTE_PAGE",
);
