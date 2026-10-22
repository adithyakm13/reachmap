// Decisions (M11): per area and per finding, the line that says what was decided (or that the decision needs review), the form that
// records one through the runner (one decision, or the same decision for a selection, each its own request), and the Decisions section.
// A decision is risk acceptance, never evidence: nothing here changes a priority, a risk, a confidence, a count or a gap, and a stale or
// orphaned decision is never applied. What is shown after a decision is what the runner answered: the page reads the report again.

import { Alert, Button, Group, List, NativeSelect, Stack, Text, TextInput, Textarea, ThemeIcon } from "@mantine/core";
import { IconCheck, IconX } from "@tabler/icons-react";
import { createContext, useId, useState, type FormEvent, type ReactNode } from "react";
import { ApiError, type Client } from "../api/client";
import type { Verdict } from "../api/types";
import { own } from "../report/read";
import type { Decided, DecisionView, DecisionsView } from "../report/view";
import { VERDICT_CHOICES, VERDICT_WORDS, W, errorWords } from "../report/words";
import { rememberAuthor, useRememberedAuthor } from "../storage";
import { toast } from "../toast";
import { C, Code, Pre, Quoted } from "./Content";
import { RecordItem } from "./Records";

export const MAX_REASON = 2000;
export const MAX_AUTHOR = 100;

/** What the page needs to record a decision: the report's name, the runner and what to do when one was recorded. */
export interface DecisionEnv {
  name: string;
  client: Client;
  recorded: () => void;
}

export const DecisionContext = createContext<DecisionEnv | null>(null);

function Verdict({ d }: { d: DecisionView }): ReactNode {
  const words = own(VERDICT_WORDS, d.verdict);
  return words !== undefined ? words : <Code text={d.verdict} />;
}

/** `risk accepted, not tested (reason from <author>: "<reason>")`: the verdict in the tool's words, the person's own words quoted and attributed. */
export function WhatWasDecided({ d }: { d: DecisionView }) {
  if (d.reason.trim() !== "") {
    return (
      <>
        <Verdict d={d} /> (reason from <C text={d.author} />: <Quoted text={d.reason} />)
      </>
    );
  }
  return (
    <>
      <Verdict d={d} /> (by <C text={d.author} />)
    </>
  );
}

/** How a stale decision is worded: against the revision this report is, which is not always the latest. */
function claimDiffers(d: DecisionView): string {
  if (d.viewed !== null && d.viewed !== d.revision) {
    return `the claim on revision ${d.viewed} differs from the claim `;
  }
  return "the claim differs from the claim ";
}

/** The line of an area or a finding about its decision. Nothing at all in a report that has no decisions. */
export function DecisionLine({ decided }: { decided: Decided | null }) {
  if (decided === null) {
    return null;
  }
  const d = decided.entry;
  if (decided.state === "current" && d !== null) {
    return (
      <Text size="sm">
        <b>{W.decisionLabel}</b> <WhatWasDecided d={d} />
      </Text>
    );
  }
  if (decided.state === "stale" && d !== null) {
    return (
      <Text size="sm">
        <b>{W.decisionLabel}</b> <strong>{W.needsReview}</strong>: {claimDiffers(d)}
        <C text={d.author} /> {`decided on at revision ${d.revision} (`}
        <Verdict d={d} />
        ). {W.notApplied}
      </Text>
    );
  }
  return (
    <Text size="sm">
      <b>{W.decisionLabel}</b> {W.noDecision}
    </Text>
  );
}

// ------------------------------------------------------------------------------ the form

export interface Target {
  id: string;
  /** What the person sees for it: content (an area's label, a finding's title). */
  label: string;
}

type Outcome = { id: string; label: string; ok: boolean; text: string; why: string | null };

function whyOf(e: ApiError): string | null {
  return e.code !== null ? (e.field !== null ? `${e.code}: ${e.field}` : e.code) : null;
}

/**
 * The form of a decision. For one target it is the form of M13; for several it records the same verdict, reason and name for each, one request
 * per target, one after the other, and says what the runner answered for each. A refusal is shown as fixed words, the status and the runner's
 * own code and field, never the body of the answer.
 */
export function DecisionForm({
  kind,
  targets,
  env,
  verdict: initialVerdict,
  onFinished,
  autoFocus = false,
  notify = false,
}: {
  kind: "area" | "finding";
  targets: Target[];
  env: DecisionEnv | null;
  verdict?: string;
  onFinished?: (recorded: number, failed: number) => void;
  autoFocus?: boolean;
  /** A toast says the answer once, for a form in a dialog that closes on success; a form in a drawer says it in its own status line. */
  notify?: boolean;
}) {
  const stem = useId();
  const choices = VERDICT_CHOICES[kind];
  const first = choices.find((c) => c.value === initialVerdict)?.value ?? choices[0].value;
  const [verdict, setVerdict] = useState(first);
  const [reason, setReason] = useState("");
  const remembered = useRememberedAuthor();
  const [typed, setTyped] = useState<string | null>(null);
  const author = typed ?? remembered;
  const [phase, setPhase] = useState<"idle" | "saving" | "done">("idle");
  const [progress, setProgress] = useState(0);
  const [problems, setProblems] = useState<string[]>([]);
  const [outcomes, setOutcomes] = useState<Outcome[]>([]);
  if (env === null || targets.length === 0) {
    return null;
  }
  const many = targets.length > 1;
  const needsReason = verdict === "skip" || verdict === "reject";

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const found: string[] = [];
    if (author.trim() === "") found.push(W.authorRequired);
    if (Array.from(author).length > MAX_AUTHOR) found.push(W.authorTooLong);
    if (needsReason && reason.trim() === "") found.push(W.reasonRequired);
    if (Array.from(reason).length > MAX_REASON) found.push(W.reasonTooLong);
    setProblems(found);
    if (found.length > 0) {
      return;
    }
    setPhase("saving");
    setOutcomes([]);
    const done: Outcome[] = [];
    for (const [i, t] of targets.entries()) {
      setProgress(i);
      try {
        await env.client.decide(env.name, { target: t.id, verdict: verdict as Verdict, reason: reason.trim() === "" ? "" : reason, author: author.trim() });
        done.push({ id: t.id, label: t.label, ok: true, text: W.bulkRecorded, why: null });
      } catch (e) {
        const status = e instanceof ApiError ? e.status : 0;
        done.push({ id: t.id, label: t.label, ok: false, text: `${W.notRecorded} ${errorWords(status)}`, why: e instanceof ApiError ? whyOf(e) : null });
      }
    }
    const ok = done.filter((o) => o.ok).length;
    const failed = done.length - ok;
    setOutcomes(done);
    setPhase("done");
    if (ok > 0) {
      rememberAuthor(author.trim());
      setTyped(null);
      setReason("");
      env.recorded(); // the page reads the report again: what it then shows is the runner's answer
    }
    if (notify && failed === 0) {
      toast(many ? W.toastBulk : W.toastRecorded);
    }
    onFinished?.(ok, failed);
  };

  return (
    <form
      onSubmit={submit}
      noValidate
      aria-label={W.decideTitle}
      onKeyDown={(e) => {
        if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
          e.preventDefault();
          e.currentTarget.requestSubmit();
        }
      }}
    >
      <Stack gap="sm">
        <NativeSelect label={W.verdictLabel} id={`${stem}-verdict`} value={verdict} onChange={(e) => setVerdict(e.currentTarget.value)} data-autofocus={autoFocus && !needsReason ? "" : undefined}>
          {choices.map((c) => (
            <option key={c.value} value={c.value}>
              {c.label}
            </option>
          ))}
        </NativeSelect>
        <Textarea label={W.reasonLabel} id={`${stem}-reason`} rows={3} value={reason} onChange={(e) => setReason(e.currentTarget.value)} aria-required={needsReason} data-autofocus={autoFocus && needsReason ? "" : undefined} />
        <TextInput label={W.authorLabel} id={`${stem}-author`} autoComplete="name" value={author} onChange={(e) => setTyped(e.currentTarget.value)} aria-required="true" />
        {problems.length > 0 ? (
          <Alert color="red" role="alert" p="xs">
            <List size="sm" listStyleType="none" className="problems">
              {problems.map((p) => (
                <List.Item key={p}>{p}</List.Item>
              ))}
            </List>
          </Alert>
        ) : null}
        <Group gap="sm">
          <Button type="submit" loading={phase === "saving"}>
            {W.record}
          </Button>
          <Text size="sm" c="dimmed" role="status">
            {phase === "saving" ? (many ? `${W.bulkRunning} ${progress + 1} ${W.ofWord} ${targets.length}.` : W.recording) : null}
            {phase === "done" && outcomes.length > 0 && outcomes.every((o) => o.ok) ? (many ? W.toastBulk : W.recorded) : null}
          </Text>
        </Group>
        {phase === "done" && outcomes.some((o) => !o.ok) && !many ? (
          <Alert color="red" role="alert" p="xs">
            <Text size="sm">
              {outcomes[0].text}
              {outcomes[0].why !== null ? (
                <>
                  {" "}
                  (<C text={outcomes[0].why} />)
                </>
              ) : null}
            </Text>
          </Alert>
        ) : null}
        {many && outcomes.length > 0 ? (
          <List spacing={4} size="sm" listStyleType="none" aria-label={W.decideBulkTitle} className="rm-bulk-results">
            {outcomes.map((o) => (
              <List.Item
                key={o.id}
                icon={
                  <ThemeIcon size={18} radius="xl" color={o.ok ? "teal" : "red"} variant="light">
                    {o.ok ? <IconCheck size={12} /> : <IconX size={12} />}
                  </ThemeIcon>
                }
              >
                <Code text={o.label} /> {o.text}
                {o.why !== null ? (
                  <>
                    {" "}
                    (<C text={o.why} />)
                  </>
                ) : null}
              </List.Item>
            ))}
          </List>
        ) : null}
      </Stack>
    </form>
  );
}

// ------------------------------------------------------------------------------ the section

const TARGET_WORDS: Record<string, string> = { "a-": W.kindArea, "f-": W.kindFinding };

function DecisionItem({ d }: { d: DecisionView }) {
  const kind = own(TARGET_WORDS, d.targetId.slice(0, 2));
  return (
    <List.Item>
      <Code text={d.label ?? d.targetId} />
      <Stack gap={2} mt={4} className="rm-decision-item">
        <Text size="sm">
          <b>{W.decisionLabel}</b> <WhatWasDecided d={d} />
        </Text>
        <Text size="sm">
          <b>{W.madeBy}</b> <C text={d.author} /> through <Code text={d.channel} /> {`on revision ${d.revision}, recorded `}
          <Code text={d.createdAt} />
        </Text>
        <Text size="sm">
          <b>{W.about}</b> {kind !== undefined ? kind : <Code text={d.kind} />} <Code text={d.targetId} />
        </Text>
        {d.state === "stale" ? (
          <>
            <Text size="sm">
              <b>{W.claimWhenDecided}</b> <Code text={d.fingerprint} />
            </Text>
            {d.nowFingerprint !== null ? (
              <Text size="sm">
                <b>{W.claimNow}</b> <Code text={d.nowFingerprint} />
              </Text>
            ) : null}
            <div>
              <Text size="sm" fw={700} component="span">
                {W.whatChanged}
              </Text>{" "}
              {d.changesKnown && d.changes !== null && d.changes.length > 0 ? (
                <Stack gap={2}>
                  {d.changes.map(([side, where, detail], i) => (
                    <div key={i}>
                      {side === "removed" ? W.sideRemoved : side === "added" ? W.sideAdded : <Code text={side} />} <Code text={where} />
                      <Pre text={detail} />
                    </div>
                  ))}
                </Stack>
              ) : d.changesKnown ? (
                <Text size="sm" component="span">
                  {W.changedNone}
                </Text>
              ) : (
                <Text size="sm" component="span">
                  {W.changedUnknown}
                </Text>
              )}
            </div>
          </>
        ) : null}
      </Stack>
    </List.Item>
  );
}

function Group3({ title, note, children }: { title: string; note: string; children: ReactNode }) {
  return (
    <div>
      <h3>{title}</h3>
      <Text size="sm" c="dimmed" mb="xs">
        {note}
      </Text>
      <List spacing="sm" listStyleType="none">
        {children}
      </List>
    </div>
  );
}

export function DecisionsSection({ view }: { view: DecisionsView }) {
  return (
    <Stack gap="md" component="section" aria-labelledby="decisions-h">
      <h2 id="decisions-h">{W.sectionDecisions}</h2>
      <Text size="sm">{W.decisionIntro}</Text>
      {view.records.length > 0 ? (
        <div>
          <h3>{W.decisionsUnreadable}</h3>
          <Text size="sm" mb="xs">
            {W.decisionsUnreadableNote}
          </Text>
          <Stack gap="xs">
            {view.records.map((r, i) => (
              <RecordItem key={i} r={r} />
            ))}
          </Stack>
        </div>
      ) : null}
      {view.current.length > 0 ? (
        <Group3 title={W.decisionsCurrent} note={W.decisionsCurrentNote}>
          {view.current.map((d, i) => (
            <DecisionItem key={i} d={d} />
          ))}
        </Group3>
      ) : null}
      {view.stale.length > 0 ? (
        <Group3 title={W.decisionsStale} note={W.decisionsStaleNote}>
          {view.stale.map((d, i) => (
            <DecisionItem key={i} d={d} />
          ))}
        </Group3>
      ) : null}
      {view.orphaned.length > 0 ? (
        <Group3 title={W.decisionsOrphaned} note={W.decisionsOrphanedNote}>
          {view.orphaned.map((d, i) => (
            <DecisionItem key={i} d={d} />
          ))}
        </Group3>
      ) : null}
      {view.undecidedAreas.length > 0 || view.undecidedFindings.length > 0 ? (
        <Group3 title={W.decisionsNone} note={W.decisionsNoneNote}>
          {view.undecidedAreas.map((label, i) => (
            <List.Item key={`a${i}`}>
              {W.kindArea} <Code text={label} />
            </List.Item>
          ))}
          {view.undecidedFindings.map(([n, title]) => (
            <List.Item key={`f${n}`}>
              <strong>{`F${n}`}</strong> <C text={title} />
            </List.Item>
          ))}
        </Group3>
      ) : null}
    </Stack>
  );
}
