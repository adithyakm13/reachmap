import { Group, Text } from "@mantine/core";
import type { Ev } from "../report/view";
import { W } from "../report/words";
import { CodeLine } from "./CodeLine";
import { CopyButton } from "./CopyButton";
import { Code } from "./Content";

/** One pinned line: where it is, the line itself verbatim and coloured, and the commit it was read at (short, with the whole id to copy). */
export function EvidenceBlock({ ev, id }: { ev: Ev; id?: string }) {
  return (
    <div className="rm-evidence" id={id}>
      <div className="rm-evidence-head">
        <Group gap={6} wrap="wrap" className="rm-evidence-where">
          <Code text={ev.repo} />
          <Code text={ev.locator} />
        </Group>
        <Group gap={4} wrap="nowrap" className="rm-evidence-at">
          <Text component="span" size="xs" c="dimmed">
            {W.commitAt}
          </Text>
          <Code text={ev.short} title={ev.ref} />
          {ev.ref !== "" ? <CopyButton value={ev.ref} label={W.copyCommit} /> : null}
        </Group>
      </div>
      <div className="rm-evidence-body">
        <span className="rm-evidence-no" aria-hidden="true">
          {ev.line}
        </span>
        <pre className="rm-evidence-code">
          <CodeLine text={ev.detail} path={ev.path} />
        </pre>
      </div>
    </div>
  );
}

export function EvidenceList({ items }: { items: Ev[] }) {
  return (
    <div className="rm-evidence-list">
      {items.map((ev, i) => (
        <EvidenceBlock key={i} ev={ev} />
      ))}
    </div>
  );
}
