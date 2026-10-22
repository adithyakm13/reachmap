import { Anchor, Badge, Button, Group, NativeSelect, Paper, Stack, Table, Text, TextInput } from "@mantine/core";
import { IconSearch } from "@tabler/icons-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { findingPath, reportPath } from "../../paths";
import { useReportCtx } from "../../pages/reportContext";
import { visible } from "../../report/text";
import type { FindingView } from "../../report/view";
import { KIND_NOTES, W } from "../../report/words";
import { own } from "../../report/read";
import { EmptyState } from "../Boxes";
import { Clip, Code } from "../Content";
import { FindingDrawer } from "../FindingDrawer";
import { ConfidenceBadge, DecisionBadge, KindBadge, NO_DECISION, RiskBadge } from "../badges";

/** `withKind`: the table holds findings of several kinds (the exclusions); inside a group of one kind the column would say the same on every row. */
function FindingsTable({ list, withKind = false }: { list: FindingView[]; withKind?: boolean }) {
  const { name, env, view } = useReportCtx();
  const known = view.decisions === null || view.decisions.records.length === 0;
  return (
    <Paper className="rm-table-paper">
      <Table.ScrollContainer minWidth={760}>
        <Table className="rm-findings rm-fixed rm-dense" layout="fixed" horizontalSpacing="xs">
          <Table.Thead>
            <Table.Tr>
              <Table.Th w={withKind ? "30%" : "38%"}>{W.colTitle}</Table.Th>
              {withKind ? <Table.Th w="12%">{W.colKind}</Table.Th> : null}
              <Table.Th w="11%">{W.colRisk}</Table.Th>
              <Table.Th w="12%">{W.colConfidence}</Table.Th>
              <Table.Th w={withKind ? "15%" : "20%"}>{W.colBasis}</Table.Th>
              <Table.Th w="8%">{W.colEvidence}</Table.Th>
              <Table.Th>{W.colDecision}</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {list.map((f) => {
              const path = findingPath(name, f.id);
              return (
                <Table.Tr key={f.number} data-finding={f.index}>
                  <Table.Td>
                    <Group gap={6} wrap="nowrap" align="center">
                      {path !== null ? (
                        <Anchor component={Link} to={path} size="sm" fw={600}>{`F${f.number}`}</Anchor>
                      ) : (
                        <Text size="sm" fw={600} component="span">{`F${f.number}`}</Text>
                      )}
                      <Text size="sm" component="span" className="rm-trunc">
                        <Clip text={f.title} n={300} />
                      </Text>
                    </Group>
                  </Table.Td>
                  {withKind ? (
                    <Table.Td>
                      <KindBadge kind={f.kind} />
                    </Table.Td>
                  ) : null}
                  <Table.Td>
                    <RiskBadge level={f.risk} />
                  </Table.Td>
                  <Table.Td>
                    <ConfidenceBadge level={f.confidence} />
                  </Table.Td>
                  <Table.Td>
                    <Text size="xs" c="dimmed" component="span" className="rm-trunc">
                      <Clip text={f.basis} n={300} />
                    </Text>
                  </Table.Td>
                  <Table.Td>{f.evidence.length}</Table.Td>
                  <Table.Td>
                    <DecisionBadge decided={f.decision ?? (env !== null && known ? NO_DECISION : null)} />
                  </Table.Td>
                </Table.Tr>
              );
            })}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </Paper>
  );
}

export function FindingsTab({ drawerId }: { drawerId: string | null }) {
  const { name, view } = useReportCtx();
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("");
  const [risk, setRisk] = useState("");
  const [confidence, setConfidence] = useState("");
  const base = reportPath(name);
  const all = useMemo(() => [...view.groups.flatMap((g) => g.findings), ...view.exclusions], [view]);
  const kinds = useMemo(() => [...new Set(all.map((f) => f.kind))].sort(), [all]);
  const risks = useMemo(() => [...new Set(all.map((f) => f.risk))].sort(), [all]);
  const confidences = useMemo(() => [...new Set(all.map((f) => f.confidence))].sort(), [all]);
  const keeps = (f: FindingView): boolean => {
    const q = query.trim().toLowerCase();
    return (kind === "" || f.kind === kind) && (risk === "" || f.risk === risk) && (confidence === "" || f.confidence === confidence) && (q === "" || f.title.toLowerCase().includes(q) || f.basis.toLowerCase().includes(q));
  };
  const groups = view.groups.map((g) => ({ ...g, findings: g.findings.filter(keeps) })).filter((g) => g.findings.length > 0);
  const exclusions = view.exclusions.filter(keeps);
  const filtering = query !== "" || kind !== "" || risk !== "" || confidence !== "";
  const shown = groups.length + exclusions.length;
  return (
    <Stack gap="md">
      <Group gap="sm" wrap="wrap" className="rm-toolbar">
        <TextInput aria-label={W.searchFindings} placeholder={W.searchFindings} leftSection={<IconSearch size={14} />} value={query} onChange={(e) => setQuery(e.currentTarget.value)} w={{ base: "100%", sm: 240 }} />
        <NativeSelect w={{ base: "100%", sm: 190 }} aria-label={W.filterKind} value={kind} onChange={(e) => setKind(e.currentTarget.value)}>
          <option value="">{W.allKinds}</option>
          {kinds.map((k) => (
            <option key={k} value={k} data-c="">
              {visible(k)}
            </option>
          ))}
        </NativeSelect>
        <NativeSelect w={{ base: "100%", sm: 190 }} aria-label={W.filterRisk} value={risk} onChange={(e) => setRisk(e.currentTarget.value)}>
          <option value="">{`${W.filterRisk}: ${W.allLevels}`}</option>
          {risks.map((k) => (
            <option key={k} value={k} data-c="">
              {visible(k)}
            </option>
          ))}
        </NativeSelect>
        <NativeSelect w={{ base: "100%", sm: 190 }} aria-label={W.filterConfidence} value={confidence} onChange={(e) => setConfidence(e.currentTarget.value)}>
          <option value="">{`${W.filterConfidence}: ${W.allLevels}`}</option>
          {confidences.map((k) => (
            <option key={k} value={k} data-c="">
              {visible(k)}
            </option>
          ))}
        </NativeSelect>
        {filtering ? (
          <Button
            variant="subtle"
            onClick={() => {
              setQuery("");
              setKind("");
              setRisk("");
              setConfidence("");
            }}
          >
            {W.clearFilters}
          </Button>
        ) : null}
      </Group>
      {all.length === 0 ? <EmptyState title={W.noFindingsTitle}>{W.noFindings}</EmptyState> : null}
      {all.length > 0 && shown === 0 ? <EmptyState title={W.noFindingsMatch} /> : null}
      {groups.map((g) => (
        <section key={g.kind} aria-labelledby={`kind-${groups.indexOf(g)}`}>
          <Group gap="xs" align="baseline" mb={4}>
            <h2 id={`kind-${groups.indexOf(g)}`}>
              <Code text={g.kind} />
            </h2>
            <Badge variant="default" size="sm">
              {g.findings.length}
            </Badge>
          </Group>
          {own(KIND_NOTES, g.kind) !== undefined ? (
            <Text size="xs" c="dimmed" mb="xs">
              {own(KIND_NOTES, g.kind)}
            </Text>
          ) : null}
          <FindingsTable list={g.findings} />
        </section>
      ))}
      {exclusions.length > 0 ? (
        <section aria-labelledby="not-reached-h">
          <Group gap="xs" align="baseline" mb={4}>
            <h2 id="not-reached-h">{W.notReached}</h2>
            <Badge variant="default" size="sm">
              {exclusions.length}
            </Badge>
          </Group>
          <Text size="xs" c="dimmed" mb="xs">
            {W.notReachedNote}
          </Text>
          <FindingsTable list={exclusions} withKind />
        </section>
      ) : null}
      <FindingDrawer id={drawerId} backTo={base === null ? null : `${base}/findings`} />
    </Stack>
  );
}
