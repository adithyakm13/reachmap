// The drawer of an area: it opens over the list with its own address (`/reports/<name>/areas/<id>`), so a link or a reload comes back to it.
// The chain that reaches the area is drawn left to right (a lane per repository), and under the graph every step is a block of its own
// with the line verbatim and coloured. The graph is a view of the same list of steps: the list is there when the graph cannot be drawn.

import { Accordion, Alert, Anchor, Card, Drawer, Group, Stack, Text } from "@mantine/core";
import { IconRoute } from "@tabler/icons-react";
import { useMemo } from "react";
import { Link, useNavigate } from "react-router-dom";
import { findingPath } from "../paths";
import { useReportCtx } from "../pages/reportContext";
import { chainGraph } from "../report/graph";
import { allAreas, allFindings, type AreaView } from "../report/view";
import { CANDIDATE, PRIORITY_WORDS, W } from "../report/words";
import { own } from "../report/read";
import { SectionBoundary } from "./Boxes";
import { C, Code } from "./Content";
import { DecisionForm, DecisionLine } from "./Decisions";
import { Fact, Facts, FindingRef, Joined } from "./DrawerParts";
import { EvidenceBlock } from "./Evidence";
import { ChainSteps } from "./ChainSteps";
import { SuggestionCard } from "./tabs/TestsTab";
import { ConfidenceBadge, KindBadge, PriorityBadge, RiskBadge } from "./badges";
import { DrawerNotes, NoteBadges } from "./ModelNotes";

function Chain({ area, index }: { area: AreaView; index: number }) {
  const { view } = useReportCtx();
  const chain = area.chains[index];
  const model = useMemo(() => chainGraph(chain, view.cross), [chain, view.cross]);
  return <ChainSteps chain={chain} model={model} index={index} />;
}

/** The chains that reach the area: one list of steps; with several chains, each is folded and the first is open. */
function Chains({ area }: { area: AreaView }) {
  if (area.chains.length === 1) {
    return (
      <Stack gap="xs">
        <Text size="sm" fw={600}>{`${W.chainNumber} 1 ${W.ofWord} 1, ${area.chains[0].length} ${area.chains[0].length === 1 ? "step" : "steps"}`}</Text>
        <Chain area={area} index={0} />
      </Stack>
    );
  }
  return (
    <Accordion variant="separated" defaultValue="chain-0">
      {area.chains.map((c, i) => (
        <Accordion.Item key={i} value={`chain-${i}`}>
          <Accordion.Control>{`${W.chainNumber} ${i + 1} ${W.ofWord} ${area.chains.length}, ${c.length} ${c.length === 1 ? "step" : "steps"}`}</Accordion.Control>
          <Accordion.Panel>
            <Chain area={area} index={i} />
          </Accordion.Panel>
        </Accordion.Item>
      ))}
    </Accordion>
  );
}

/** The cases suggested for this area (they are places to look, not coverage). A source that was not read completely is said: nothing is claimed about the area then. */
function SuggestedForArea({ area }: { area: AreaView }) {
  const { view } = useReportCtx();
  const s = view.suggested;
  if (s.state === "not_read") {
    return null;
  }
  const mine = s.items.filter((x) => x.area === area.index);
  const whole = s.records.length === 0 && s.partial.length === 0 && (s.state === "listed" || s.state === "read_none");
  return (
    <Stack gap="xs" component="section" aria-labelledby="suggested-for-h" data-suggested-for={area.index}>
      <h3 id="suggested-for-h">{W.drawerSuggested}</h3>
      <Text size="xs" c="dimmed">
        {W.suggestedNote}
      </Text>
      {mine.map((x, i) => (
        <SuggestionCard key={i} x={x} />
      ))}
      {mine.length === 0 ? <Text size="sm">{whole ? W.drawerSuggestedNone : W.drawerSuggestedUnknown}</Text> : null}
    </Stack>
  );
}

export function AreaDrawer({ id, backTo }: { id: string | null; backTo: string | null }) {
  const { name, view, env, findingIds } = useReportCtx();
  const navigate = useNavigate();
  const area = id === null ? undefined : allAreas(view).find((a) => a.id === id);
  const related = area === undefined ? [] : allFindings(view).filter((f) => area.findings.includes(f.number));
  const candidate = area !== undefined && (area.byName || area.priority === "candidate");
  const close = () => {
    if (backTo !== null) {
      navigate(backTo);
    }
  };
  return (
    <Drawer
      opened={id !== null}
      onClose={close}
      position="right"
      size="min(760px, 100vw)"
      title={area === undefined ? W.notFoundTitle : <C text={area.label} />}
      closeButtonProps={{ "aria-label": W.drawerClose }}
      className="rm-drawer"
      data-drawer="area"
    >
      {area === undefined ? (
        <Text size="sm">{W.notInReport}</Text>
      ) : (
        <Stack gap="lg">
          <Group gap="xs" wrap="wrap">
            <PriorityBadge priority={area.priority} byName={area.byName} />
            <KindBadge kind={area.kind} />
            <NoteBadges id={area.id} />
            {area.complete === false ? (
              <Text size="xs" c="orange" fw={600}>
                {W.chainPartialBadge}
              </Text>
            ) : null}
          </Group>
          <Facts>
            <Fact label={W.labelPriority}>{candidate ? CANDIDATE : (own(PRIORITY_WORDS, area.priority) ?? <C text={area.priority} />)}</Fact>
            <Fact label={W.labelRepository}>
              <Code text={area.repo} />
            </Fact>
            <Fact label={W.labelKind}>
              <Code text={area.kind} />
            </Fact>
            {area.module !== "" ? (
              <Fact label={W.labelModule}>
                <Code text={area.module} />
              </Fact>
            ) : null}
            {area.alsoMounted.length > 0 ? (
              <Fact label={W.labelAlsoMounted}>
                <Joined items={area.alsoMounted.map((m, i) => <Code key={i} text={m} />)} />
              </Fact>
            ) : null}
            {area.findings.length > 0 ? (
              <Fact label={W.labelFindings}>
                <Joined items={area.findings.map((n) => <FindingRef key={n} n={n} name={name} id={findingIds.get(n) ?? null} />)} />
              </Fact>
            ) : null}
            {area.complete === false ? (
              <Fact label={W.chainRead}>
                {W.chainReadWhy}
                {area.reasons.length > 0 ? <Joined items={area.reasons.map((x, i) => <C key={i} text={x} />)} /> : W.noReason}
              </Fact>
            ) : null}
          </Facts>
          <Stack gap="xs" component="section" aria-labelledby="route-h">
            <Text id="route-h" size="sm" fw={600}>
              {W.routeLine}
            </Text>
            <EvidenceBlock ev={area.routeLine} />
          </Stack>
          <Stack gap="sm" component="section" aria-labelledby="chains-h">
            <Group gap={6}>
              <IconRoute size={16} aria-hidden="true" />
              <h3 id="chains-h">{W.chainsTitle}</h3>
            </Group>
            {area.chains.length === 0 ? <Text size="sm">{W.noChains}</Text> : null}
            {area.chains.length > 0 ? (
              <SectionBoundary>
                <Chains area={area} />
              </SectionBoundary>
            ) : null}
            {area.chains.length > 0 ? (
              <Text size="xs" c="dimmed">
                {W.chainStepsNote}
              </Text>
            ) : null}
          </Stack>
          <SuggestedForArea area={area} />
          <Stack gap="xs" component="section" aria-labelledby="related-h">
            <h3 id="related-h">{W.relatedFindings}</h3>
            {related.length === 0 ? <Text size="sm">{W.noRelatedFindings}</Text> : null}
            {related.map((f) => {
              const path = findingPath(name, f.id);
              return (
                <Card key={f.number} padding="sm">
                  <Group gap="xs" wrap="wrap" mb={4}>
                    {path !== null ? (
                      <Anchor component={Link} to={path} size="sm" fw={600}>{`F${f.number}`}</Anchor>
                    ) : (
                      <Text size="sm" fw={600} component="span">{`F${f.number}`}</Text>
                    )}
                    <KindBadge kind={f.kind} />
                    <RiskBadge level={f.risk} />
                    <ConfidenceBadge level={f.confidence} />
                  </Group>
                  <Text size="sm">
                    <C text={f.title} />
                  </Text>
                </Card>
              );
            })}
          </Stack>
          <DrawerNotes id={area.id} kind="area" />
          <Stack gap="xs" component="section" aria-labelledby="decide-h">
            <h3 id="decide-h">{W.decisionHeading}</h3>
            <DecisionLine decided={area.decision} />
            {env !== null && area.id !== null ? (
              <DecisionForm kind="area" targets={[{ id: area.id, label: area.label }]} env={env} />
            ) : (
              <Alert color="gray" p="xs">
                <Text size="sm">{W.decisionNotAvailable}</Text>
              </Alert>
            )}
          </Stack>
        </Stack>
      )}
    </Drawer>
  );
}
