import { Alert, Anchor, Drawer, Group, Stack, Text } from "@mantine/core";
import { Link, useNavigate } from "react-router-dom";
import { areaPath } from "../paths";
import { useReportCtx } from "../pages/reportContext";
import { allAreas, allFindings } from "../report/view";
import { KIND_NOTES, W } from "../report/words";
import { own } from "../report/read";
import { C, Code } from "./Content";
import { DecisionForm, DecisionLine } from "./Decisions";
import { Fact, Facts } from "./DrawerParts";
import { EvidenceList } from "./Evidence";
import { ConfidenceBadge, KindBadge, RiskBadge } from "./badges";
import { DrawerNotes, NoteBadges } from "./ModelNotes";

/** The drawer of a finding, with its own address: risk and confidence apart, the basis, every line cited, the areas it is about, the decision. */
export function FindingDrawer({ id, backTo }: { id: string | null; backTo: string | null }) {
  const { name, view, env } = useReportCtx();
  const navigate = useNavigate();
  const finding = id === null ? undefined : allFindings(view).find((f) => f.id === id);
  const areas = finding === undefined ? [] : allAreas(view).filter((a) => a.findings.includes(finding.number));
  const note = finding === undefined ? undefined : own(KIND_NOTES, finding.kind);
  return (
    <Drawer
      opened={id !== null}
      onClose={() => backTo !== null && navigate(backTo)}
      position="right"
      size="min(760px, 100vw)"
      title={finding === undefined ? W.notFoundTitle : `${W.findingTitle} F${finding.number}`}
      closeButtonProps={{ "aria-label": W.drawerClose }}
      className="rm-drawer"
      data-drawer="finding"
    >
      {finding === undefined ? (
        <Text size="sm">{W.notInReport}</Text>
      ) : (
        <Stack gap="lg">
          <Text fw={600}>
            <C text={finding.title} />
          </Text>
          <Group gap="xs" wrap="wrap">
            <KindBadge kind={finding.kind} />
            <RiskBadge level={finding.risk} />
            <ConfidenceBadge level={finding.confidence} />
            <NoteBadges id={finding.id} />
            {finding.isExclusion ? (
              <Text size="xs" c="dimmed">
                {W.notReached}
              </Text>
            ) : null}
          </Group>
          {note !== undefined ? (
            <Text size="sm" c="dimmed">
              {note}
            </Text>
          ) : null}
          <Facts>
            <Fact label={W.labelRisk}>
              <C text={finding.risk} />
            </Fact>
            <Fact label={W.labelConfidence}>
              <C text={finding.confidence} />
            </Fact>
            <Fact label={W.labelBasis}>
              <C text={finding.basis} />
            </Fact>
            <Fact label={W.labelKind}>
              <Code text={finding.kind} />
            </Fact>
          </Facts>
          <Stack gap="xs" component="section" aria-labelledby="evidence-h">
            <h3 id="evidence-h">{`${W.evidenceHeading} (${finding.evidence.length})`}</h3>
            <EvidenceList items={finding.evidence} />
          </Stack>
          {areas.length > 0 ? (
            <Stack gap="xs" component="section" aria-labelledby="areas-h">
              <h3 id="areas-h">{W.relatedAreas}</h3>
              {areas.map((a) => {
                const path = areaPath(name, a.id);
                return path !== null ? (
                  <Anchor key={a.index} component={Link} to={path} size="sm">
                    <Code text={a.label} />
                  </Anchor>
                ) : (
                  <Code key={a.index} text={a.label} />
                );
              })}
            </Stack>
          ) : null}
          <DrawerNotes id={finding.id} kind="finding" />
          <Stack gap="xs" component="section" aria-labelledby="decide-h">
            <h3 id="decide-h">{W.decisionHeading}</h3>
            <DecisionLine decided={finding.decision} />
            {env !== null && finding.id !== null ? (
              <DecisionForm kind="finding" targets={[{ id: finding.id, label: finding.title }]} env={env} />
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
