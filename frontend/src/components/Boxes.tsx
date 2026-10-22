import { Alert, Button, Group, Paper, Skeleton, Stack, Text, Title } from "@mantine/core";
import { IconAlertCircle, IconInbox, IconRefresh } from "@tabler/icons-react";
import { Component, type ErrorInfo, type ReactNode } from "react";
import { ApiError } from "../api/client";
import { ReportShapeError } from "../report/check";
import { W, errorWords } from "../report/words";
import { C } from "./Content";

/** What a page shows while it waits: grey blocks where the content will be, and one status for a screen reader. */
export function Loading({ rows = 4 }: { rows?: number }) {
  return (
    <Stack gap="xs" role="status" aria-label={W.loading} aria-busy="true">
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} height={i === 0 ? 28 : 18} radius="sm" width={i === 0 ? "40%" : "100%"} />
      ))}
    </Stack>
  );
}

/** Nothing to list: one sentence, and an action when there is one. */
export function EmptyState({ title, children, footer }: { title: string; children?: ReactNode; footer?: ReactNode }) {
  return (
    <Paper p="xl" className="rm-empty">
      <Stack align="center" gap="xs">
        <IconInbox size={28} stroke={1.4} aria-hidden="true" />
        <Title order={2} size="h3">
          {title}
        </Title>
        {children !== undefined ? (
          <Text size="sm" c="dimmed" ta="center" maw={460}>
            {children}
          </Text>
        ) : null}
        {footer}
      </Stack>
    </Paper>
  );
}

/** An API error as text: a fixed message for the status, the status itself, and a code of the runner's fixed words at most. Never the body of the answer. */
export function ErrorBox({ error, retry }: { error: ApiError; retry?: () => void }) {
  return (
    <Alert color="red" icon={<IconAlertCircle size={18} />} title={W.errorTitle} role="alert">
      <Stack gap={6} align="flex-start">
        <Text size="sm">{errorWords(error.status)}</Text>
        <Text size="xs" c="dimmed">
          {error.status > 0 ? `Status ${error.status}` : null}
          {error.code !== null ? (
            <>
              {error.status > 0 ? ", " : null}
              <C text={error.code} />
            </>
          ) : null}
        </Text>
        {retry !== undefined ? (
          <Button variant="default" leftSection={<IconRefresh size={14} />} onClick={retry}>
            {W.retry}
          </Button>
        ) : null}
      </Stack>
    </Alert>
  );
}

export function BadReport() {
  return (
    <Alert color="red" icon={<IconAlertCircle size={18} />} title={W.badReportTitle} role="alert">
      <Text size="sm">{W.badReport}</Text>
    </Alert>
  );
}

/** Whatever went wrong while loading: an API error as text, a body that is not a report as a fixed message, anything else as an unreachable runner. */
export function FailureBox({ error, retry }: { error: Error; retry?: () => void }) {
  if (error instanceof ReportShapeError) {
    return <BadReport />;
  }
  return <ErrorBox error={error instanceof ApiError ? error : new ApiError(0)} retry={retry} />;
}

interface BoundaryState {
  failed: boolean;
}

/** One section of a page that fails to draw shows fixed words and leaves the rest of the page alone. It never shows the error: it can hold text from a report. */
export class SectionBoundary extends Component<{ children: ReactNode; name?: string }, BoundaryState> {
  state: BoundaryState = { failed: false };

  static getDerivedStateFromError(): BoundaryState {
    return { failed: true };
  }

  componentDidCatch(_error: Error, _info: ErrorInfo): void {
    // nothing is logged: the error can hold text from a report
  }

  render(): ReactNode {
    if (this.state.failed) {
      return (
        <Alert color="orange" icon={<IconAlertCircle size={18} />} title={W.sectionFailedTitle} role="alert">
          <Group>
            <Text size="sm">{W.sectionFailed}</Text>
          </Group>
        </Alert>
      );
    }
    return this.props.children;
  }
}
