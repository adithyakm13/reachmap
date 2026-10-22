import { Card, Group, List, Paper, Stack, Table, Text, UnstyledButton } from "@mantine/core";
import { useMemo, useState } from "react";
import { branchGrid, FILE_STATES } from "../../report/grid";
import { own } from "../../report/read";
import { visible } from "../../report/text";
import { STATE_WORDS, W } from "../../report/words";
import { useReportCtx } from "../../pages/reportContext";
import { EmptyState } from "../Boxes";
import { C, Code } from "../Content";
import { FindingRef } from "../DrawerParts";
import { EvidenceList } from "../Evidence";
import { StateChip } from "../badges";

interface Pick {
  row: number;
  col: number | null;
}

/** For each branch whether it carries the change, file by file: a grid of state chips; a cell opens the lines the report cites for it. */
export function BranchesTab() {
  const { view, name, findingIds } = useReportCtx();
  const grid = useMemo(() => branchGrid(view), [view]);
  const [pick, setPick] = useState<Pick | null>(null);
  if (view.matrix.length === 0) {
    return <EmptyState title={W.sectionMatrix}>{W.noBranches}</EmptyState>;
  }
  const picked = pick === null ? null : grid.rows[pick.row];
  const evidence = picked === null || pick === null ? [] : pick.col === null ? picked.evidence : picked.cells[pick.col].evidence;
  return (
    <Stack gap="md">
      <div>
        <h2 id="grid-h">{W.gridTitle}</h2>
        <Text size="sm" c="dimmed">
          {W.gridNote}
        </Text>
      </div>
      <Paper className="rm-table-paper">
        <Table.ScrollContainer minWidth={Math.min(1200, 360 + grid.files.length * 170)}>
          <Table className="rm-grid" aria-labelledby="grid-h">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>{W.gridBranch}</Table.Th>
                <Table.Th>{W.colCommit}</Table.Th>
                <Table.Th>{W.gridRow}</Table.Th>
                {grid.files.map((f, i) => (
                  <Table.Th key={i} className="rm-grid-file">
                    <Code text={f} limit={80} />
                  </Table.Th>
                ))}
                <Table.Th>{W.colFindings}</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {grid.rows.map((r, ri) => (
                <Table.Tr key={ri} data-branch={ri}>
                  <Table.Th scope="row">
                    <UnstyledButton onClick={() => setPick({ row: ri, col: null })} className="rm-grid-branch" aria-pressed={pick !== null && pick.row === ri && pick.col === null}>
                      <Code text={r.branch} />
                    </UnstyledButton>
                  </Table.Th>
                  <Table.Td>
                    <Code text={r.short} />
                  </Table.Td>
                  <Table.Td>
                    <Group gap={4} wrap="wrap">
                      {r.states.map((s, i) => (
                        <StateChip key={i} state={s} />
                      ))}
                    </Group>
                  </Table.Td>
                  {r.cells.map((c, ci) => (
                    <Table.Td key={ci}>
                      <UnstyledButton
                        onClick={() => setPick({ row: ri, col: ci })}
                        className="rm-grid-cell"
                        aria-pressed={pick !== null && pick.row === ri && pick.col === ci}
                        aria-label={`${W.gridCellFor} ${visible(r.branch)} ${visible(grid.files[ci])}`}
                      >
                        <StateChip state={c.state} />
                        {c.evidence.length > 0 ? (
                          <Text size="xs" c="dimmed" component="span">
                            {` ${c.evidence.length}`}
                          </Text>
                        ) : null}
                      </UnstyledButton>
                    </Table.Td>
                  ))}
                  <Table.Td>
                    <Group gap={4} wrap="wrap">
                      {r.findings.map((n) => (
                        <FindingRef key={n} n={n} name={name} id={findingIds.get(n) ?? null} />
                      ))}
                    </Group>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </Paper>
      {grid.hiddenFiles > 0 ? (
        <Text size="xs" c="dimmed">
          {`${grid.hiddenFiles} ${W.gridMore}`}
        </Text>
      ) : null}

      <Card padding="md" component="section" aria-labelledby="pick-h" className="rm-pick">
        <h3 id="pick-h">{W.gridCellFor}</h3>
        {picked === null || pick === null ? (
          <Text size="sm" c="dimmed">
            {W.gridPickCell}
          </Text>
        ) : (
          <Stack gap="xs">
            <Text size="sm">
              <Code text={picked.branch} />
              {pick.col !== null ? (
                <>
                  {" / "}
                  <Code text={grid.files[pick.col]} limit={120} />
                </>
              ) : null}
            </Text>
            {evidence.length === 0 ? (
              <Text size="sm" c="dimmed">
                {W.gridNoLines}
              </Text>
            ) : (
              <EvidenceList items={evidence} />
            )}
          </Stack>
        )}
      </Card>

      <section aria-labelledby="legend-h">
        <h3 id="legend-h">{W.gridLegend}</h3>
        <List size="sm" listStyleType="none" spacing={4}>
          {FILE_STATES.map((s) => (
            <List.Item key={s}>
              <Group gap="xs" wrap="nowrap">
                <StateChip state={s} />
                <Text size="sm">{own(STATE_WORDS, s)}</Text>
              </Group>
            </List.Item>
          ))}
        </List>
      </section>

      <section aria-labelledby="details-h">
        <h3 id="details-h">{W.gridRowDetails}</h3>
        <Stack gap={4}>
          {view.matrix.map((b, i) => (
            <Text key={i} size="sm">
              <Code text={b.branch} />: <C text={b.detail} />
            </Text>
          ))}
        </Stack>
      </section>
    </Stack>
  );
}
