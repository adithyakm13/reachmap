import { ActionIcon, Group, Text, Tooltip } from "@mantine/core";
import { IconCheck, IconCopy } from "@tabler/icons-react";
import { useEffect, useState } from "react";
import { W } from "../report/words";

/** Copy a text (a whole commit id, a command): the short form is shown, the whole one is copied. The answer is a fixed word, never an error. */
export function CopyButton({ value, label }: { value: string; label: string }) {
  const [note, setNote] = useState("");
  // the answer is not left on the page: it is there for a few seconds
  useEffect(() => {
    if (note === "") {
      return undefined;
    }
    const timer = window.setTimeout(() => setNote(""), 3500);
    return () => window.clearTimeout(timer);
  }, [note]);
  const copy = () => {
    const clipboard = typeof navigator === "undefined" ? undefined : navigator.clipboard;
    if (clipboard === undefined || typeof clipboard.writeText !== "function") {
      setNote(W.copyFailed);
      return;
    }
    clipboard.writeText(value).then(
      () => setNote(W.copiedCommit),
      () => setNote(W.copyFailed),
    );
  };
  return (
    <Group gap={4} wrap="nowrap" component="span" className="rm-copy">
      <Tooltip label={label}>
        <ActionIcon variant="subtle" size="sm" aria-label={label} onClick={copy}>
          {note === W.copiedCommit ? <IconCheck size={14} /> : <IconCopy size={14} />}
        </ActionIcon>
      </Tooltip>
      <Text component="span" size="xs" c="dimmed" role="status">
        {note}
      </Text>
    </Group>
  );
}
