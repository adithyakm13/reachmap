// Model notes (M14): what a model client recorded beside the report over MCP. Drawn apart from everything the analyser found: a note is never a
// finding, evidence, coverage or a decision, and nothing here is counted. Every text of a note, the client's name and the client's claim of what
// it is are content (`components/Content`: text, never markup). The stamp `recorded via MCP, client reported as <name>` is its own labelled line,
// outside the text of the note, and says in the tool's words that the name is the client's own claim. `report/notes.ts` holds the rules; this file
// only draws what it says.

import { Anchor, Badge, Card, Group, Stack, Text } from "@mantine/core";
import { IconNote } from "@tabler/icons-react";
import { Fragment, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { tabPath } from "../paths";
import { useReportCtx } from "../pages/reportContext";
import { madeElsewhere, noteItem, noteSection, notesAbout, type Fact, type NoteItem, type NoteView, type Run } from "../report/notes";
import { KIND_WORDS, NOTES_LABEL, NOTE_PAGE, NOTE_PHRASES, STATE_WORDS } from "../report/notewords";
import { own } from "../report/read";
import { RecordItem } from "./Records";
import { C, Code, Pre } from "./Content";

function Runs({ runs }: { runs: readonly Run[] }) {
  return (
    <>
      {runs.map((r, i) =>
        typeof r === "string" ? (
          <Fragment key={i}>{r}</Fragment>
        ) : "code" in r ? (
          <Code key={i} text={r.code} />
        ) : "c" in r ? (
          <C key={i} text={r.c} />
        ) : (
          <strong key={i}>{r.strong}</strong>
        ),
      )}
    </>
  );
}

/** One labelled line of a note. The text of the note is a block of its own; the stamp is marked, so that a test and a reader can tell it from a note that says the same. */
function NoteFact({ f }: { f: Fact }) {
  return (
    <div className="rm-fact" data-note-field={f.field}>
      <Text size="xs" c="dimmed" tt="uppercase" fw={600} className="rm-fact-label">
        {f.label}
      </Text>{" "}
      <Text size="sm" component="div" className="rm-fact-value">
        {f.block !== null ? <Pre text={f.block} /> : <Runs runs={f.rest} />}
      </Text>
    </div>
  );
}

/** What a note is shown as, a fixed word for a badge, a style and a test (never a piece of content): its state when this version knows it, `other` when it does not, and in a drawer of an earlier report `elsewhere` for a note made on a later revision. */
type Display = "current" | "stale" | "orphaned" | "other" | "elsewhere";

function displayOf(n: NoteView, drawer: boolean): Display {
  if (drawer && madeElsewhere(n)) {
    return "elsewhere";
  }
  return n.state === "current" || n.state === "stale" || n.state === "orphaned" ? n.state : "other";
}

/** The colour of a state: slate for a current note, yellow (a look is needed) for a stale one, grey and struck through for an orphaned one, a grey outline for a note made on another revision than the report's. */
function StateBadge({ n, display }: { n: NoteView; display: Display }) {
  if (display === "elsewhere") {
    return (
      <Badge variant="outline" color="gray" data-note-state={display}>
        {`${NOTE_PHRASES.elsewhereA}${n.revision}`}
      </Badge>
    );
  }
  if (display === "other") {
    return (
      <Badge variant="light" color="gray" data-note-state={display}>
        <Code text={n.state} n={20} />
      </Badge>
    );
  }
  const short = (own(STATE_WORDS, n.state) ?? "").split(":")[0];
  if (display === "orphaned") {
    return (
      <Badge variant="light" color="gray" className="rm-orphaned" data-note-state={display}>
        {short}
      </Badge>
    );
  }
  return (
    <Badge variant="light" color={display === "stale" ? "yellow" : "slate"} data-note-state={display}>
      {short}
    </Badge>
  );
}

/** A note: its kind and state, then the lines the Python layout gives it. */
export function NoteCard({ item, drawer = false }: { item: NoteItem; drawer?: boolean }) {
  const n = item.note;
  if (n === null) {
    return null;
  }
  const kind = own(KIND_WORDS, n.kind);
  const display = displayOf(n, drawer);
  return (
    <Card padding="sm" className={`rm-note rm-note-${display}`} data-note={display}>
      <Group gap="xs" wrap="wrap" mb={6}>
        <Badge variant="default" className="rm-kind">
          {kind !== undefined ? kind : <Code text={n.kind} n={24} />}
        </Badge>
        <StateBadge n={n} display={display} />
        <Text size="xs" c="dimmed" component="span">
          <Code text={n.id} />
        </Text>
      </Group>
      <Stack gap={6}>
        {item.facts.map((f, i) => (
          <NoteFact key={i} f={f} />
        ))}
      </Stack>
    </Card>
  );
}

function Items({ items, drawer = false }: { items: NoteItem[]; drawer?: boolean }): ReactNode {
  return (
    <Stack gap="xs">
      {items.map((item, i) => (item.record !== null ? <RecordItem key={i} r={item.record} /> : <NoteCard key={i} item={item} drawer={drawer} />))}
    </Stack>
  );
}

/** The section of the tab: the label ("Model notes, recorded via MCP; not evidence."), what a note is, then the notes by state. */
export function ModelNotesSection() {
  const { view } = useReportCtx();
  const m = view.modelNotes;
  if (m === null) {
    return null;
  }
  const blocks = noteSection(m);
  return (
    <Stack gap="md" component="section" aria-labelledby="model-notes-h" data-model-notes="">
      <h2 id="model-notes-h" className="rm-h2">
        {NOTE_PAGE.section}
      </h2>
      {blocks.map((b, i) => {
        switch (b.kind) {
          case "intro":
            return (
              <Text key={i} size="sm">
                <strong>{b.label}</strong> {b.text}
              </Text>
            );
          case "heading":
            return (
              <h3 key={i} id={b.id}>
                {b.text}
              </h3>
            );
          case "para":
            return (
              <Text key={i} size="sm" c="dimmed">
                {b.text}
              </Text>
            );
          case "items":
            return <Items key={i} items={b.items} />;
        }
      })}
    </Stack>
  );
}

/** What a drawer shows of the notes about its area or finding: a count, and the same card for each (a stale one says so). Nothing when there are none. */
export function DrawerNotes({ id, kind }: { id: string | null; kind: "area" | "finding" }) {
  const { name, view } = useReportCtx();
  const mine = notesAbout(view.modelNotes, id);
  if (mine.length === 0) {
    return null;
  }
  const all = tabPath(name, "model-notes");
  return (
    <Stack gap="xs" component="section" aria-labelledby="drawer-notes-h" data-drawer-notes="">
      <h3 id="drawer-notes-h">{`${kind === "area" ? NOTE_PAGE.aboutArea : NOTE_PAGE.aboutFinding} (${mine.length})`}</h3>
      <Text size="xs" c="dimmed">
        <strong>{NOTES_LABEL}</strong>
      </Text>
      <Items items={mine.map((n) => noteItem(n, true))} drawer />
      {all !== null ? (
        <Anchor component={Link} to={all} size="sm">
          {NOTE_PAGE.seeAll}
        </Anchor>
      ) : null}
    </Stack>
  );
}

/** The badges of a drawer's header: how many notes there are about it and how many of those are stale. Outlined and dashed, as the other things that are not backed by evidence. */
export function NoteBadges({ id }: { id: string | null }) {
  const { view } = useReportCtx();
  const mine = notesAbout(view.modelNotes, id);
  if (mine.length === 0) {
    return null;
  }
  const stale = mine.filter((n) => n.state === "stale").length;
  return (
    <>
      <Badge variant="outline" color="gray" className="rm-dashed" leftSection={<IconNote size={11} />} data-note-badge="count">
        {mine.length === 1 ? NOTE_PAGE.badgeOne : `${mine.length} ${NOTE_PAGE.badgeMany}`}
      </Badge>
      {stale > 0 ? (
        <Badge variant="light" color="yellow" data-note-badge="stale">
          {`${stale} ${NOTE_PAGE.staleBadge}`}
        </Badge>
      ) : null}
    </>
  );
}
