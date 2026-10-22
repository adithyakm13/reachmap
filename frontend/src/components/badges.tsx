// The badges, and the one place that says which colour means what. Every colour has one meaning, so that two things that differ are never the same hue:
//   red     blocking, risk high, a failed run, a read that failed
//   orange  the run did not establish everything: the banner, a chain read in part, a run that timed out
//   yellow  something needs a person's look: a decision that needs review, a newer revision exists, a branch that has part of the change
//   pink    risk medium
//   gray    risk low, a candidate (dashed), a state with nothing to carry (pre_change, file_absent, not_compared), an orphaned decision (struck through)
//   indigo  the accent: must test, confidence (an outlined pill), a running job, the verdict "test"
//   slate   sample
//   teal    finished, carries the change, accepted
//   grape   a branch that differs from both versions (diverged)
//   cyan    the verdict "skip": risk accepted, not tested
//   A model note (M14) is never one of these: its count in a drawer is an outlined dashed grey badge (like a candidate: not backed by evidence), its state a slate
//   (current), yellow (stale: a look is needed, as for a decision) or struck-through grey (orphaned) badge; see `ModelNotes.tsx`.
// Risk and confidence are two different badge styles on purpose (risk: a filled light rectangle with a warning mark; confidence: an outlined pill
// with a target mark, never in the colours of risk), so that two values of "high" are never read as one scale.

import { Badge, Popover, Text } from "@mantine/core";
import { IconAlertTriangle, IconBan, IconClockPause, IconEyeCheck, IconTarget } from "@tabler/icons-react";
import type { ReactNode } from "react";
import { own } from "../report/read";
import type { Decided, DecisionView } from "../report/view";
import { CANDIDATE, PRIORITY_WORDS, STATUS_WORDS, VERDICT_WORDS, W } from "../report/words";
import { C, Clip, Code, Pre } from "./Content";

const RISK_COLOR: Record<string, string> = { high: "red", medium: "pink", low: "gray" };
const CONFIDENCE_COLOR: Record<string, string> = { high: "accent", medium: "accent", low: "gray" };
const STATUS_COLOR: Record<string, string> = { completed: "teal", failed: "red", timed_out: "orange", queued: "gray", running: "accent", cancelled: "gray" };
const VERDICT_COLOR: Record<string, string> = { test: "accent", sample: "slate", skip: "cyan", accept: "teal", reject: "gray" };
const STATE_COLOR: Record<string, string> = {
  present: "teal",
  partial: "yellow",
  pre_change: "gray",
  diverged: "grape",
  file_absent: "gray",
  unreadable: "red",
  not_compared: "gray",
};

export function StatusBadge({ status }: { status: string }) {
  return (
    <Badge variant="light" color={own(STATUS_COLOR, status) ?? "gray"}>
      {own(STATUS_WORDS, status) ?? <Clip text={status} n={20} />}
    </Badge>
  );
}

export function PriorityBadge({ priority, byName }: { priority: string; byName: boolean }) {
  if (byName || priority === "candidate") {
    return (
      <Badge variant="outline" color="gray" className="rm-dashed" title={CANDIDATE}>
        {W.candidatesBadge}
      </Badge>
    );
  }
  if (priority === "must_test") {
    return (
      <Badge variant="light" color="accent">
        {own(PRIORITY_WORDS, priority)}
      </Badge>
    );
  }
  if (priority === "sample") {
    return (
      <Badge variant="light" color="slate">
        {own(PRIORITY_WORDS, priority)}
      </Badge>
    );
  }
  return (
    <Badge variant="light" color="gray">
      {own(PRIORITY_WORDS, priority) ?? <C text={priority} />}
    </Badge>
  );
}

/** Risk: a filled light rectangle with a warning mark. Never styled like a confidence. */
export function RiskBadge({ level }: { level: string }) {
  return (
    <Badge variant="light" color={own(RISK_COLOR, level) ?? "gray"} leftSection={<IconAlertTriangle size={11} />} data-kind="risk">
      <Clip text={level} n={16} />
    </Badge>
  );
}

/** Confidence: an outlined pill with a target mark. Never styled like a risk. */
export function ConfidenceBadge({ level }: { level: string }) {
  return (
    <Badge variant="outline" radius="xl" color={own(CONFIDENCE_COLOR, level) ?? "gray"} leftSection={<IconTarget size={11} />} data-kind="confidence">
      <Clip text={level} n={16} />
    </Badge>
  );
}

export function KindBadge({ kind }: { kind: string }) {
  return (
    <Badge variant="default" className="rm-kind">
      <Clip text={kind} n={36} />
    </Badge>
  );
}

export function StateChip({ state }: { state: string | null }) {
  if (state === null) {
    return (
      <Badge variant="outline" color="gray" className="rm-dashed">
        {W.stateUnknownChip}
      </Badge>
    );
  }
  return (
    <Badge variant="light" color={own(STATE_COLOR, state) ?? "gray"}>
      <Code text={state} />
    </Badge>
  );
}

export function BlockingBadge({ n }: { n: number }) {
  return (
    <Badge variant="filled" color="red.8" leftSection={<IconBan size={11} />}>
      {`${n} ${W.blockingWord}`}
    </Badge>
  );
}

export function IncompleteBadge() {
  return (
    <Badge variant="light" color="orange" leftSection={<IconClockPause size={11} />}>
      {W.incompleteBadge}
    </Badge>
  );
}

function Verdict({ d }: { d: DecisionView }): ReactNode {
  const words = own(VERDICT_WORDS, d.verdict);
  return words !== undefined ? words : <Code text={d.verdict} />;
}

/** The state of a target that can be decided and has no decision yet. */
export const NO_DECISION: Decided = { state: "none", entry: null };

/** What a decision shows in a row: the verdict in the tool's words (current), a yellow "needs review" badge whose popover says what changed (stale). */
export function DecisionBadge({ decided }: { decided: Decided | null }) {
  if (decided === null) {
    return null;
  }
  const d = decided.entry;
  if (decided.state === "current" && d !== null) {
    return (
      <Badge variant="light" color={own(VERDICT_COLOR, d.verdict) ?? "gray"} leftSection={<IconEyeCheck size={11} />} data-decision="current">
        <Verdict d={d} />
      </Badge>
    );
  }
  if (decided.state === "stale" && d !== null) {
    return (
      <Popover width={360} position="bottom" withArrow shadow="md">
        <Popover.Target>
          <Badge component="button" type="button" variant="light" color="yellow" data-decision="stale" className="rm-clickable">
            {W.needsReview}
          </Badge>
        </Popover.Target>
        <Popover.Dropdown>
          <Text size="sm" fw={600}>
            {W.whatChanged}
          </Text>
          <Text size="sm">{W.claimDiffers}</Text>
          {d.changesKnown && d.changes !== null && d.changes.length > 0 ? (
            <div>
              {d.changes.map(([side, where, detail], i) => (
                <div key={i}>
                  <Text size="xs" c="dimmed">
                    {side === "removed" ? W.sideRemoved : side === "added" ? W.sideAdded : <C text={side} />} <Code text={where} />
                  </Text>
                  <Pre text={detail} />
                </div>
              ))}
            </div>
          ) : (
            <Text size="sm">{d.changesKnown ? W.changedNone : W.changedUnknown}</Text>
          )}
          <Text size="sm" mt="xs">
            {W.notApplied}
          </Text>
        </Popover.Dropdown>
      </Popover>
    );
  }
  return (
    <Text component="span" size="xs" c="dimmed" data-decision="none">
      {W.notDecided}
    </Text>
  );
}

/** A decision about something no longer in the report: grey, with a line through it. */
export function OrphanedBadge({ children }: { children: ReactNode }) {
  return (
    <Badge variant="light" color="gray" className="rm-orphaned">
      {children}
    </Badge>
  );
}
