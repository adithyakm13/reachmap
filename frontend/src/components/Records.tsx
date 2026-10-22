import { Badge, Card, Group, Stack, Text } from "@mantine/core";
import { IconAlertOctagon, IconInfoCircle } from "@tabler/icons-react";
import type { RecordView } from "../report/view";
import { W } from "../report/words";
import { C, Code } from "./Content";
import { EmptyState } from "./Boxes";

/** One record of what the run could not establish: what kind, about what, what was not established, the detail. Blocking ones are red. */
export function RecordItem({ r }: { r: RecordView }) {
  const blocks = r.blocking || r.blocksSuggestions;
  return (
    <Card padding="sm" className={r.blocking ? "rm-record rm-record-blocking" : "rm-record"} data-blocking={blocks ? "true" : "false"} data-scope={r.scope}>
      <Group gap="xs" wrap="wrap" mb={4}>
        {r.blocking ? (
          <Badge color="red.8" variant="filled" leftSection={<IconAlertOctagon size={11} />}>
            {W.blockingWord}
          </Badge>
        ) : r.blocksSuggestions ? (
          <Badge color="orange" variant="light" leftSection={<IconAlertOctagon size={11} />}>
            {W.blockingWord}
          </Badge>
        ) : (
          <Badge color="gray" variant="light" leftSection={<IconInfoCircle size={11} />}>
            {W.notBlockingWord}
          </Badge>
        )}
        {r.scope === "suggestions" ? (
          <Badge variant="outline" color="gray" className="rm-chip" data-chip="suggestions-only">
            {W.suggestionsOnly}
          </Badge>
        ) : null}
        <Badge variant="default" className="rm-kind">
          <Code text={r.kind} />
        </Badge>
        <Text size="sm" fw={500} component="span">
          <Code text={r.subject} />
        </Text>
      </Group>
      <Stack gap={2}>
        <Text size="sm">
          <b>{W.notEstablished}</b> <C text={r.notEstablished} />
        </Text>
        {r.detail !== "" ? (
          <Text size="sm">
            <b>{W.detail}</b> <C text={r.detail} />
          </Text>
        ) : null}
      </Stack>
    </Card>
  );
}

/** What the run could not establish: blocking records first, then the others; a run that did not complete says why nothing else is shown. */
export function RecordsList({ records, completed }: { records: RecordView[]; completed: boolean }) {
  const blocking = records.filter((r) => r.blocking || r.blocksSuggestions);
  const other = records.filter((r) => !(r.blocking || r.blocksSuggestions));
  const forAreas = blocking.filter((r) => r.blocking);
  return (
    <Stack gap="md" component="section" aria-labelledby="records-h">
      <h2 id="records-h">{W.sectionRecords}</h2>
      {!completed ? <Text size="sm">{W.didNotComplete}</Text> : null}
      {blocking.length > 0 ? (
        <Stack gap="xs">
          <h3>{W.blockingRecords}</h3>
          {completed && forAreas.length > 0 ? (
            <Text size="sm" c="dimmed">
              {W.blockingNote}
            </Text>
          ) : null}
          {completed && forAreas.length < blocking.length ? (
            <Text size="sm" c="dimmed">
              {W.blockingSuggestionsNote}
            </Text>
          ) : null}
          {blocking.map((r, i) => (
            <RecordItem key={i} r={r} />
          ))}
        </Stack>
      ) : null}
      {other.length > 0 ? (
        <Stack gap="xs">
          <h3>{W.otherRecords}</h3>
          {other.map((r, i) => (
            <RecordItem key={i} r={r} />
          ))}
        </Stack>
      ) : null}
      {records.length === 0 ? <EmptyState title={W.noRecordsTitle}>{W.noRecords}</EmptyState> : null}
    </Stack>
  );
}
