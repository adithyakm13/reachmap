import { Badge, Card, Group, Paper, Stack, Table, Text } from "@mantine/core";
import { useMemo } from "react";
import { crossGraph } from "../../report/graph";
import { ADOPTION_WORDS, W } from "../../report/words";
import { own } from "../../report/read";
import { useReportCtx } from "../../pages/reportContext";
import { Backend } from "../Backend";
import { SectionBoundary } from "../Boxes";
import { C, Code } from "../Content";
import { CrossDetails } from "../CrossDetails";
import { CrossGraphView } from "../LazyGraphs";

/** Cross-repository reach: a graph of the repositories and the edges between them, the same edges as a table (pins and adoption per branch, candidates apart), then the details. */
export function CrossTab() {
  const { view } = useReportCtx();
  const model = useMemo(() => crossGraph(view), [view]);
  return (
    <Stack gap="md">
      <Card padding="md" component="section" aria-labelledby="cross-h">
        <h2 id="cross-h" className="rm-h2">
          {W.crossGraphTitle}
        </h2>
        <Text size="xs" c="dimmed" mb="sm">
          {W.crossGraphNote}
        </Text>
        {view.cross.empty ? (
          <Text size="sm">{W.noCross}</Text>
        ) : (
          <SectionBoundary>
            <CrossGraphView model={model} />
          </SectionBoundary>
        )}
      </Card>
      {model.edges.length > 0 ? (
        <section aria-labelledby="edges-h">
          <h3 id="edges-h">{W.crossEdges}</h3>
          <Paper className="rm-table-paper">
            <Table.ScrollContainer minWidth={720}>
              <Table className="rm-edges">
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>{W.crossFrom}</Table.Th>
                    <Table.Th>{W.crossTo}</Table.Th>
                    <Table.Th>{W.crossKind}</Table.Th>
                    <Table.Th>{W.crossCandidate}</Table.Th>
                    <Table.Th>{W.crossPins}</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {model.edges.map((e) => (
                    <Table.Tr key={e.id} data-candidate={e.candidate ? "true" : "false"}>
                      <Table.Td>
                        <Code text={e.from} />
                      </Table.Td>
                      <Table.Td>
                        <Code text={e.to} />
                      </Table.Td>
                      <Table.Td>
                        <Badge variant="default" className="rm-kind">
                          {e.kind === "edge" ? <C text={e.rawKind} /> : e.kind}
                        </Badge>
                      </Table.Td>
                      <Table.Td>{e.candidate ? <Badge variant="outline" color="gray" className="rm-dashed">{W.crossYes}</Badge> : W.crossNo}</Table.Td>
                      <Table.Td>
                        {e.notes.length === 0 ? (
                          <Text size="xs" c="dimmed">
                            {W.crossNoPins}
                          </Text>
                        ) : (
                          <Stack gap={2}>
                            {e.notes.map((n, i) => (
                              <Group key={i} gap={6} wrap="wrap">
                                <Code text={n.branch} />
                                {n.pin !== null ? <Code text={n.pin} limit={60} /> : <Text size="xs" c="dimmed" component="span">no pin read</Text>}
                                <Text size="xs" c="dimmed" component="span">
                                  {own(ADOPTION_WORDS, n.adoption) ?? <C text={n.adoption} />}
                                </Text>
                              </Group>
                            ))}
                          </Stack>
                        )}
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          </Paper>
        </section>
      ) : null}
      <CrossDetails view={view} />
      <Backend view={view} />
    </Stack>
  );
}
