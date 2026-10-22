import { Badge, Button, Group, Paper, Text, Title } from "@mantine/core";
import { IconAlertTriangle, IconCircleX, IconClockOff } from "@tabler/icons-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import type { HeaderView } from "../report/view";
import { visible } from "../report/text";
import { W } from "../report/words";
import { duration, relative } from "../time";
import { C, Clip, Code } from "./Content";
import { CopyButton } from "./CopyButton";
import { StatusBadge } from "./badges";

function Sha({ value }: { value: string }) {
  return (
    <Group gap={2} wrap="nowrap" component="span">
      <Code text={value.slice(0, 12)} title={value} />
      <CopyButton value={value} label={W.copyCommit} />
    </Group>
  );
}

/** One fact of the row under the title: a small label and its value, on one line. */
function Meta({ label, children }: { label: string; children: ReactNode }) {
  return (
    <span className="rm-meta">
      <Text size="xs" c="dimmed" fw={600} component="span" className="rm-meta-label">
        {label}
      </Text>{" "}
      <Text size="sm" component="span">
        {children}
      </Text>
    </span>
  );
}

/** The header of a report in two lines: what was analysed and how the run ended, then the commits and the run. Counts are never here: only a run that completed has them (Overview). */
export function ReportHeader({ h }: { h: HeaderView }) {
  const started = h.startedAt === null ? null : relative(h.startedAt);
  return (
    <Paper px="md" py="sm" component="section" aria-labelledby="header-h" className="rm-header-card">
      <Group gap="sm" wrap="wrap" align="center">
        <StatusBadge status={h.status} />
        <Title order={1} id="header-h" className="rm-title">
          <Clip text={h.repo} n={60} />,{" "}
          <span className="rm-title-sub">
            <Clip text={h.label} n={90} />
          </span>
        </Title>
        {h.revision !== null ? (
          <Badge variant="default">
            {W.headerRevision} {h.revision}
          </Badge>
        ) : null}
      </Group>
      <Group gap="md" mt={6} wrap="wrap" className="rm-metarow">
        {h.pr !== null ? (
          <Meta label={W.headerPr}>
            #<C text={h.pr.number} limit={20} /> <Clip text={h.pr.title} n={70} /> (<C text={h.pr.state} limit={20} />), <Clip text={h.pr.headRef} n={30} /> into <Clip text={h.pr.baseRef} n={30} />
          </Meta>
        ) : null}
        {h.head !== null ? (
          <Meta label={W.headerHead}>
            <Sha value={h.head} />
          </Meta>
        ) : null}
        {h.preChange !== null ? (
          <Meta label={W.headerBefore}>
            <Sha value={h.preChange} />
          </Meta>
        ) : null}
        {h.base !== null ? (
          <Meta label={W.headerBase}>
            <Sha value={h.base} />
          </Meta>
        ) : null}
        {h.asOf !== null ? (
          <Meta label={W.headerAsOf}>
            <Clip text={h.asOf} n={40} />
          </Meta>
        ) : null}
        {h.startedAt !== null ? (
          <Meta label={W.headerRun}>
            <span title={visible(h.startedAt)}>{started ?? <C text={h.startedAt} />}</span>
            {h.elapsedS !== null ? `, ${W.tookSeconds} ${duration(h.elapsedS)}` : null}
          </Meta>
        ) : null}
        <Meta label={W.headerTool}>
          reachmap <Clip text={h.tool} n={40} />
        </Meta>
      </Group>
    </Paper>
  );
}

function Strip({ color, icon, title, children, banner }: { color: "red" | "orange"; icon: ReactNode; title: string; children?: ReactNode; banner?: string }) {
  return (
    <div className="rm-banner" role="alert" data-color={color} data-banner={banner}>
      <span className="rm-banner-icon" aria-hidden="true">
        {icon}
      </span>
      <Text size="sm" fw={600} component="span">
        {title}
      </Text>
      {children}
    </div>
  );
}

/** The strip above the tabs: a run that did not complete says so; a run that did not establish everything says so and links to the records. */
export function RunBanner({ h, blocking, recordsPath }: { h: HeaderView; blocking: number; recordsPath: string | null }) {
  if (h.status === "failed" || h.status === "timed_out") {
    return h.status === "failed" ? (
      <Strip color="red" icon={<IconCircleX size={16} />} title={W.runFailed} />
    ) : (
      <Strip color="orange" icon={<IconClockOff size={16} />} title={W.runTimedOut} />
    );
  }
  if (blocking === 0 && h.complete !== false) {
    return null;
  }
  return (
    <Strip color="orange" icon={<IconAlertTriangle size={16} />} title={W.incompleteTitle} banner="incomplete">
      <Text size="sm" component="span" className="rm-banner-text">
        {blocking > 0 ? `${blocking} ${blocking === 1 ? "blocking record" : "blocking records"}. ${W.incompleteNote}` : `${h.reasons} ${h.reasons === 1 ? "reason" : "reasons"} recorded. ${W.incompleteNoteSoft}`}
      </Text>
      {recordsPath !== null ? (
        <Button component={Link} to={recordsPath} variant="default" size="compact-xs" className="rm-banner-link">
          {W.viewRecords}
        </Button>
      ) : null}
    </Strip>
  );
}
