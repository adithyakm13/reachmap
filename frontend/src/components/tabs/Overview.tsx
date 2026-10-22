import { Accordion, Anchor, Badge, Card, Group, List, Paper, Progress, SimpleGrid, Stack, Text } from "@mantine/core";
import { useMemo } from "react";
import { Link, useNavigate } from "react-router-dom";
import { areaPath, tabPath } from "../../paths";
import { reachGraph } from "../../report/graph";
import { own } from "../../report/read";
import { statsOf, type Slice } from "../../report/stats";
import { allAreas } from "../../report/view";
import { COUNT_LABELS, W } from "../../report/words";
import { useReportCtx } from "../../pages/reportContext";
import { SectionBoundary } from "../Boxes";
import { C, Code } from "../Content";
import { ReachGraphView } from "../LazyGraphs";
import { RecordItem } from "../Records";
import { KindBadge } from "../badges";

function Stat({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return (
    <Card padding="md" className="rm-stat" data-tone={tone}>
      <Text size="xs" c="dimmed" tt="uppercase" fw={600}>
        {label}
      </Text>
      <Text className="rm-stat-n" fw={600}>
        {value}
      </Text>
    </Card>
  );
}

interface Part {
  key: string;
  label: React.ReactNode;
  n: number;
  color: string;
}

/** A segmented bar and, under it, the same numbers as text (the colours are never the only way to read it, and there is no text inside a segment: white or black on a colour that is any of several would not read on all of them). */
function Bar({ parts, total, label }: { parts: Part[]; total: number; label: string }) {
  return (
    <Stack gap="xs">
      <Progress.Root size="lg" radius="sm" aria-label={label}>
        {parts
          .filter((p) => p.n > 0)
          .map((p) => (
            <Progress.Section key={p.key} value={total === 0 ? 0 : (p.n / total) * 100} color={p.color} title={`${p.n}`} />
          ))}
      </Progress.Root>
      <List listStyleType="none" size="sm" spacing={2} className="rm-legend">
        {parts.map((p) => (
          <List.Item key={p.key}>
            <Group gap={6} wrap="nowrap">
              <span className="rm-swatch" data-color={p.color} />
              <Text size="sm" component="span">
                {p.label}
              </Text>
              <Text size="sm" component="span" fw={600}>
                {p.n}
              </Text>
            </Group>
          </List.Item>
        ))}
      </List>
    </Stack>
  );
}

const RISK_COLORS: Record<string, string> = { high: "red.6", medium: "pink.5", low: "gray.5" };
const CONFIDENCE_COLORS: Record<string, string> = { high: "accent.8", medium: "accent.5", low: "accent.2" };

function levels(slices: Slice[], colors: Record<string, string>): Part[] {
  return slices.map((s, i) => ({ key: `${i}:${s.key}`, label: <C text={s.key} />, n: s.n, color: own(colors, s.key) ?? "gray.5" }));
}

function Distribution({ title, note, slices, colors }: { title: string; note: string; slices: Slice[]; colors: Record<string, string> }) {
  const total = slices.reduce((n, s) => n + s.n, 0);
  return (
    <Card padding="md" className="rm-distribution">
      <Text fw={600} size="sm">
        {title}
      </Text>
      <Text size="xs" c="dimmed" mb="sm">
        {note}
      </Text>
      {total === 0 ? (
        <Text size="sm" c="dimmed">
          {W.overviewNone}
        </Text>
      ) : (
        <Bar parts={levels(slices, colors)} total={total} label={title} />
      )}
    </Card>
  );
}

/** The overview of a report that completed: the counts, the findings by kind, risk and confidence apart, the decisions so far, what the change reaches. */
export function Overview() {
  const { name, view } = useReportCtx();
  const navigate = useNavigate();
  const stats = useMemo(() => statsOf(view), [view]);
  const areas = useMemo(() => allAreas(view), [view]);
  const reach = useMemo(() => reachGraph(view, areas), [view, areas]);
  const counts = view.header.counts;
  const count = (k: string): number => counts?.find(([key]) => key === k)?.[1] ?? 0;
  const blocking = view.records.filter((r) => r.blocking);
  const areasPath = tabPath(name, "areas");
  const recordsPath = tabPath(name, "records");
  const kindTotal = stats.byKind.reduce((n, s) => n + s.n, 0);
  const progress = stats.progress;
  const progressParts: Part[] =
    progress === null
      ? []
      : [
          { key: "decided", label: W.progressDecided, n: progress.decided, color: "teal.6" },
          { key: "stale", label: W.progressStale, n: progress.stale, color: "yellow.5" },
          { key: "undecided", label: W.progressUndecided, n: progress.undecided, color: "gray.4" },
        ];
  return (
    <Stack gap="md">
      {counts !== null ? (
        <section aria-labelledby="counts-h">
          <h2 id="counts-h" className="rm-sr">
            {W.countsCaption}
          </h2>
          <SimpleGrid cols={{ base: 2, md: 4 }} spacing="sm">
            <Stat label={own(COUNT_LABELS, "must_test") ?? ""} value={count("must_test")} tone="must" />
            <Stat label={own(COUNT_LABELS, "sample") ?? ""} value={count("sample")} tone="sample" />
            <Stat label={own(COUNT_LABELS, "candidates") ?? ""} value={count("candidates")} tone="candidate" />
            <Stat label={own(COUNT_LABELS, "findings") ?? ""} value={count("findings")} />
          </SimpleGrid>
          <Group gap="lg" mt="xs" className="rm-counts-small">
            {(["areas", "exclusions", "records", "blocking_records", "blocking_suggestion_records"] as const)
              .filter((k) => k !== "blocking_suggestion_records" || count(k) > 0)
              .map((k) => (
              <Text key={k} size="xs" c="dimmed">
                {own(COUNT_LABELS, k)}: <b>{count(k)}</b>
              </Text>
            ))}
          </Group>
        </section>
      ) : null}

      <SimpleGrid cols={{ base: 1, md: 3 }} spacing="sm">
        <Card padding="md">
          <Text fw={600} size="sm" mb="sm">
            {W.overviewKinds}
          </Text>
          {stats.byKind.length === 0 ? (
            <Text size="sm" c="dimmed">
              {W.overviewNone}
            </Text>
          ) : (
            <Stack gap={6}>
              {stats.byKind.map((s) => (
                <div key={s.key} className="rm-kindrow">
                  <Group justify="space-between" wrap="nowrap">
                    <KindBadge kind={s.key} />
                    <Text size="sm" fw={600}>
                      {s.n}
                    </Text>
                  </Group>
                  <Progress value={kindTotal === 0 ? 0 : (s.n / kindTotal) * 100} size={4} color="accent.6" aria-hidden="true" mt={4} />
                </div>
              ))}
            </Stack>
          )}
        </Card>
        <Distribution title={W.overviewRisk} note={W.overviewRiskNote} slices={stats.risk} colors={RISK_COLORS} />
        <Distribution title={W.overviewConfidence} note={W.overviewConfidenceNote} slices={stats.confidence} colors={CONFIDENCE_COLORS} />
      </SimpleGrid>

      <SimpleGrid cols={{ base: 1, md: 2 }} spacing="sm">
        <Card padding="md">
          <Text fw={600} size="sm">
            {W.progressTitle}
          </Text>
          <Text size="xs" c="dimmed" mb="sm">
            {W.progressNote}
          </Text>
          {progress !== null ? (
            <Stack gap="xs">
              {progress.total === 0 ? <Text size="sm">{W.overviewNone}</Text> : <Bar parts={progressParts} total={progress.total} label={W.progressTitle} />}
              <Text size="sm">
                {W.progressOrphaned}: <b>{progress.orphaned}</b>
              </Text>
              {areasPath !== null ? (
                <Anchor component={Link} to={areasPath} size="sm">
                  {W.seeDecisions}
                </Anchor>
              ) : null}
            </Stack>
          ) : (
            <Text size="sm">{view.decisions !== null && view.decisions.records.length > 0 ? W.progressUnknown : W.progressNone}</Text>
          )}
        </Card>
        <Card padding="md">
          <Group justify="space-between" mb="sm">
            <Text fw={600} size="sm">
              {W.topBlocking}
            </Text>
            {recordsPath !== null ? (
              <Anchor component={Link} to={recordsPath} size="sm">
                {W.seeAllRecords}
              </Anchor>
            ) : null}
          </Group>
          {count("blocking_suggestion_records") > 0 ? (
            <Text size="xs" c="dimmed">
              {W.suggestionRecordsHint}
            </Text>
          ) : null}
          {blocking.length === 0 ? (
            <Text size="sm" c="dimmed">
              {W.noBlocking}
            </Text>
          ) : (
            <Stack gap="xs">
              {blocking.slice(0, 3).map((r, i) => (
                <RecordItem key={i} r={r} />
              ))}
            </Stack>
          )}
        </Card>
      </SimpleGrid>

      <Card padding="md" component="section" aria-labelledby="reach-h">
        <h2 id="reach-h" className="rm-h2">
          {W.reachTitle}
        </h2>
        <Text size="xs" c="dimmed" mb="sm">
          {W.reachNote}
        </Text>
        <SectionBoundary>
          <ReachGraphView
            model={reach}
            onArea={(id) => {
              const path = areaPath(name, id);
              if (path !== null) {
                navigate(path);
              }
            }}
          />
        </SectionBoundary>
      </Card>

      {view.header.files.length > 0 || view.header.how.length > 0 ? (
        <Accordion variant="separated" multiple>
          {view.header.how.length > 0 ? (
            <Accordion.Item value="how">
              <Accordion.Control>{W.howFound}</Accordion.Control>
              <Accordion.Panel>
                <List size="sm" listStyleType="none" spacing={2}>
                  {view.header.how.map(([k, v]) => (
                    <List.Item key={k}>
                      <Code text={k} />: <C text={v} />
                    </List.Item>
                  ))}
                </List>
              </Accordion.Panel>
            </Accordion.Item>
          ) : null}
          {view.header.files.length > 0 ? (
            <Accordion.Item value="files">
              <Accordion.Control>{`Files in the change (${view.header.files.length} of ${view.header.fileCount})`}</Accordion.Control>
              <Accordion.Panel>
                <Stack gap={4}>
                  {view.header.files.map((f, i) => (
                    <Group key={i} gap={6} wrap="wrap">
                      <Code text={f.path} />
                      <Badge variant="default" className="rm-kind">
                        <C text={f.status} />
                      </Badge>
                      {f.oldPath !== null ? (
                        <Text size="xs" c="dimmed">
                          from <Code text={f.oldPath} />
                        </Text>
                      ) : null}
                      {f.role !== null ? (
                        <Text size="xs" c="dimmed">
                          role <Code text={f.role} />
                        </Text>
                      ) : null}
                      <Text size="xs" c="dimmed">{`+${f.added} -${f.removed}`}</Text>
                    </Group>
                  ))}
                </Stack>
              </Accordion.Panel>
            </Accordion.Item>
          ) : null}
        </Accordion>
      ) : null}
      {view.header.files.length === 0 && view.header.fileCount > 0 ? (
        <Paper p="sm">
          <Text size="sm">
            {W.filesInChange} {view.header.fileCount}
          </Text>
        </Paper>
      ) : null}
    </Stack>
  );
}
