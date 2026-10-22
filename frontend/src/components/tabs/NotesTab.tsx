import { Card, List, Stack, Text } from "@mantine/core";
import { W } from "../../report/words";
import { useReportCtx } from "../../pages/reportContext";
import { C } from "../Content";

/** The notes the run wrote about what it did: text, never markup. They are not findings. */
export function NotesTab() {
  const { view } = useReportCtx();
  return (
    <Card padding="md" component="section" aria-labelledby="notes-h">
      <Stack gap="xs">
        <h2 id="notes-h" className="rm-h2">
          {`${W.tabNotes} (${view.notes.length})`}
        </h2>
        <Text size="sm" c="dimmed">
          {W.notesIntro}
        </Text>
        {view.notes.length === 0 ? (
          <Text size="sm">{W.noNotes}</Text>
        ) : (
          <List listStyleType="none" spacing={4} size="sm">
            {view.notes.map((n, i) => (
              <List.Item key={i}>
                <C text={n} />
              </List.Item>
            ))}
          </List>
        )}
      </Stack>
    </Card>
  );
}
