// The Areas page: the areas to test grouped by priority, with filters, row selection, a decision for the selection and a keyboard. The keys move
// the focus itself (`j` and `k` put the focus on the primary link of the next or the previous row), Space ticks the box of the row in focus, `t`,
// `s` and `x` open the decision dialog prefilled, Enter opens the drawer (the focused link does it), Escape clears the selection. They are off while
// a field, a dialog or a drawer has the focus or is open. Every decision is its own request to the runner, and what a row shows afterwards is the
// runner's answer. A decision is risk acceptance, never evidence: no priority, count or gap changes.

import { Accordion, Anchor, Badge, Button, Checkbox, Group, Modal, NativeSelect, Paper, SegmentedControl, Stack, Table, Text, TextInput } from "@mantine/core";
import { IconSearch } from "@tabler/icons-react";
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { isTyping, takesEnter, takesSpace } from "../../keys";
import { usePaletteItems, type PaletteItem } from "../../palette";
import { areaPath, findingPath, reportPath } from "../../paths";
import { useReportCtx } from "../../pages/reportContext";
import { visible } from "../../report/text";
import { allAreas, type AreaView } from "../../report/view";
import { W } from "../../report/words";
import { EmptyState } from "../Boxes";
import { Clip, Code } from "../Content";
import { DecisionForm, DecisionsSection, WhatWasDecided, type Target } from "../Decisions";
import { AreaDrawer } from "../AreaDrawer";
import { DecisionBadge, NO_DECISION, OrphanedBadge, PriorityBadge } from "../badges";

type DecisionFilter = "all" | "undecided" | "decided" | "review";

function stateOf(a: AreaView): "undecided" | "decided" | "review" {
  const s = a.decision === null ? "none" : a.decision.state;
  return s === "current" ? "decided" : s === "stale" ? "review" : "undecided";
}

interface Dialog {
  verdict: string;
  targets: Target[];
  key: number;
}

function ChainBadge({ complete }: { complete: boolean | null }) {
  if (complete === false) {
    return (
      <Badge variant="light" color="orange">
        {W.chainPartialBadge}
      </Badge>
    );
  }
  return (
    <Badge variant="light" color="gray" className={complete === null ? "rm-dashed" : undefined}>
      {complete === true ? W.chainReadBadge : W.chainUnknownBadge}
    </Badge>
  );
}

interface RowProps {
  a: AreaView;
  pos: number;
  name: string;
  findingIds: ReadonlyMap<number, string | null>;
  active: boolean;
  selected: boolean;
  canDecide: boolean;
  known: boolean;
  onToggle: (a: AreaView) => void;
  onFocusRow: (index: number) => void;
  onOpen: (a: AreaView) => void;
  setRef: (index: number, el: HTMLElement | null) => void;
}

/** One row. It is memoised on its own values and on callbacks that never change, so a key that moves the focus draws two rows, not five hundred. */
const AreaRow = memo(function AreaRow({ a, pos, name, findingIds, active, selected, canDecide, known, onToggle, onFocusRow, onOpen, setRef }: RowProps) {
  const path = areaPath(name, a.id);
  const hover = a.module === "" ? visible(a.label) : `${visible(a.label)} (${visible(a.module)})`;
  return (
    <Table.Tr
      ref={(el: HTMLTableRowElement | null) => setRef(a.index, el)}
      data-row={pos}
      data-area={a.index}
      data-active={active ? "true" : undefined}
      data-selected={selected ? "true" : undefined}
      aria-current={active ? "true" : undefined}
      tabIndex={path === null ? -1 : undefined}
      className={path !== null ? "rm-row-link" : undefined}
      onFocus={() => onFocusRow(a.index)}
      onClick={(e) => {
        if (path !== null && (e.target as HTMLElement).closest("a,button,input,label") === null) {
          onOpen(a);
        }
      }}
    >
      <Table.Td>{canDecide && a.id !== null ? <Checkbox aria-label={`${W.selectRow}: ${visible(a.label)}`} checked={selected} onChange={() => onToggle(a)} /> : null}</Table.Td>
      <Table.Td>
        <Group gap={6} wrap="nowrap" className="rm-cell-line" title={hover}>
          {path !== null ? (
            <Anchor component={Link} to={path} size="sm" fw={500} className="rm-row-title">
              <Code text={a.label} n={140} />
            </Anchor>
          ) : (
            <Code text={a.label} n={140} />
          )}
          {a.byName || a.priority === "candidate" ? <PriorityBadge priority={a.priority} byName={a.byName} /> : null}
        </Group>
      </Table.Td>
      <Table.Td>
        <Clip text={a.repo} n={36} />
      </Table.Td>
      <Table.Td>
        <Clip text={a.kind} n={30} />
      </Table.Td>
      <Table.Td>
        <Group gap={4} wrap="nowrap">
          {a.findings.map((n) => {
            const p = findingPath(name, findingIds.get(n) ?? null);
            return p !== null ? (
              <Anchor key={n} component={Link} to={p} size="sm">
                {`F${n}`}
              </Anchor>
            ) : (
              <Text key={n} size="sm" component="span">{`F${n}`}</Text>
            );
          })}
        </Group>
      </Table.Td>
      <Table.Td>
        <ChainBadge complete={a.complete} />
      </Table.Td>
      <Table.Td>
        <DecisionBadge decided={a.decision ?? (canDecide && known ? NO_DECISION : null)} />
      </Table.Td>
    </Table.Tr>
  );
});

export function AreasTab({ drawerId }: { drawerId: string | null }) {
  const { name, view, env, findingIds } = useReportCtx();
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [repo, setRepo] = useState("");
  const [kind, setKind] = useState("");
  const [decision, setDecision] = useState<DecisionFilter>("all");
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  // the area in focus is kept by its index in the report, which does not change when a filter does
  const [activeKey, setActiveKey] = useState<number | null>(null);
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const dialogKey = useRef(0);
  const base = reportPath(name);
  const canDecide = env !== null;

  const everything = useMemo(() => allAreas(view), [view]);
  const repos = useMemo(() => [...new Set(everything.map((a) => a.repo))].sort(), [everything]);
  const kinds = useMemo(() => [...new Set(everything.map((a) => a.kind))].sort(), [everything]);
  const keeps = useCallback(
    (a: AreaView): boolean => {
      const q = query.trim().toLowerCase();
      return (
        (repo === "" || a.repo === repo) &&
        (kind === "" || a.kind === kind) &&
        (decision === "all" || stateOf(a) === decision) &&
        (q === "" || [a.label, a.module, a.repo, a.kind].some((v) => v.toLowerCase().includes(q)))
      );
    },
    [query, repo, kind, decision],
  );
  const groups: { key: string; title: string; note: string; list: AreaView[] }[] = useMemo(
    () => [
      { key: "must-test", title: W.mustTest, note: W.mustTestNote, list: view.mustTest.filter(keeps) },
      { key: "sample", title: W.sample, note: W.sampleNote, list: view.sample.filter(keeps) },
      { key: "candidates", title: W.candidates, note: W.candidatesNote, list: view.candidates.filter(keeps) },
    ],
    [view, keeps],
  );
  const ordered = useMemo(() => groups.flatMap((g) => g.list), [groups]);
  const position = useMemo(() => new Map(ordered.map((a, i) => [a.index, i])), [ordered]);
  // when the store of decisions could not be read nothing is known about any area, so none is said to have no decision
  const known = view.decisions === null || view.decisions.records.length === 0;

  // what is selected is always something that is shown
  useEffect(() => {
    setSelected((old) => {
      const shown = new Set(ordered.flatMap((a) => (a.id === null ? [] : [a.id])));
      const kept = [...old].filter((id) => shown.has(id));
      return kept.length === old.size ? old : new Set(kept);
    });
    setActiveKey((k) => (k !== null && !position.has(k) ? null : k));
  }, [ordered, position]);

  // the latest of these, for the key handler and the palette, without making them change every time
  const latest = useRef({ ordered, selected, activeKey, canDecide });
  latest.current = { ordered, selected, activeKey, canDecide };

  const open = useCallback(
    (a: AreaView) => {
      const path = areaPath(name, a.id);
      if (path !== null) {
        navigate(path);
      }
    },
    [name, navigate],
  );

  const decide = useCallback(
    (verdict: string, list: AreaView[]) => {
      const targets = list.flatMap((a) => (a.id !== null && latest.current.canDecide ? [{ id: a.id, label: a.label }] : []));
      if (targets.length > 0) {
        dialogKey.current += 1;
        setDialog({ verdict, targets, key: dialogKey.current });
      }
    },
    [],
  );

  const toggle = useCallback((a: AreaView) => {
    if (a.id === null) {
      return;
    }
    const id = a.id;
    setSelected((old) => {
      const next = new Set(old);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }, []);

  const rowRefs = useRef(new Map<number, HTMLElement>());
  const setRef = useCallback((index: number, el: HTMLElement | null) => {
    if (el === null) {
      rowRefs.current.delete(index);
    } else {
      rowRefs.current.set(index, el);
    }
  }, []);

  const focusRow = useCallback((index: number) => setActiveKey((k) => (k === index ? k : index)), []);

  /** Put the focus on the primary link of the row (or the row itself, when it has no link): the browser scrolls to it and a screen reader reads it. */
  const moveTo = useCallback((a: AreaView) => {
    setActiveKey(a.index);
    const row = rowRefs.current.get(a.index) ?? null;
    (row?.querySelector<HTMLElement>("a.rm-row-title") ?? row)?.focus();
  }, []);

  const decided = useCallback(
    (ids: ReadonlySet<string>) => {
      // after a decision the focus goes on to the next area that has none yet, below the ones just decided
      const { ordered: rows } = latest.current;
      const last = rows.reduce((at, a, i) => (a.id !== null && ids.has(a.id) ? i : at), -1);
      const next = rows.slice(last + 1).find((a) => a.id !== null && !ids.has(a.id) && stateOf(a) === "undecided") ?? rows.find((a) => a.id !== null && !ids.has(a.id) && stateOf(a) === "undecided");
      if (next !== undefined) {
        window.setTimeout(() => moveTo(next), 0);
      }
    },
    [moveTo],
  );

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.ctrlKey || event.metaKey || event.altKey || isTyping(event.target) || document.querySelector('[role="dialog"]') !== null) {
        return;
      }
      const { ordered: rows, selected: chosen, activeKey: key, canDecide: may } = latest.current;
      const at = key === null ? -1 : rows.findIndex((a) => a.index === key);
      const current = at >= 0 ? rows[at] : null;
      const verdicts: Record<string, string> = { t: "test", s: "sample", x: "skip" };
      if (event.key === "j" || event.key === "k") {
        event.preventDefault();
        if (rows.length > 0) {
          const next = event.key === "j" ? Math.min(rows.length - 1, at + 1) : Math.max(0, at < 0 ? 0 : at - 1);
          moveTo(rows[next]);
        }
      } else if (event.key === "Enter" && current !== null && !takesEnter(event.target)) {
        event.preventDefault();
        open(current);
      } else if (event.key === " " && current !== null && !takesSpace(event.target)) {
        event.preventDefault();
        toggle(current);
      } else if (event.key === "Escape" && chosen.size > 0) {
        setSelected(new Set());
      } else if (Object.hasOwn(verdicts, event.key) && may) {
        const picked = chosen.size > 0 ? rows.filter((a) => a.id !== null && chosen.has(a.id)) : current !== null ? [current] : [];
        if (picked.length > 0) {
          event.preventDefault();
          decide(verdicts[event.key], picked);
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [moveTo, open, toggle, decide]);

  // the palette offers the same three decisions for the area in focus (or the selection)
  const focused = activeKey === null ? null : ordered.find((a) => a.index === activeKey) ?? null;
  const paletteItems = useMemo<PaletteItem[]>(() => {
    if (!canDecide) {
      return [];
    }
    const picked = selected.size > 0 ? ordered.filter((a) => a.id !== null && selected.has(a.id)) : focused !== null ? [focused] : [];
    if (picked.length === 0) {
      return [];
    }
    const what = picked.length > 1 ? `${picked.length} ${W.selectedCount}` : picked[0].label;
    return (
      [["test", W.helpTest], ["sample", W.helpSample], ["skip", W.helpSkip]] as const
    ).map(([verdict, words]) => ({ id: `decide:${verdict}`, group: W.paletteDecide, label: words, description: what, run: () => decide(verdict, picked) }));
  }, [canDecide, selected, ordered, focused, decide]);
  usePaletteItems("areas", paletteItems);

  const shownCount = ordered.length;
  const allShownSelected = shownCount > 0 && ordered.every((a) => a.id === null || selected.has(a.id));
  const selectable = ordered.filter((a) => a.id !== null);
  const orphaned = view.decisions?.orphaned ?? [];
  const filtering = query !== "" || repo !== "" || kind !== "" || decision !== "all";

  return (
    <Stack gap="md">
      <Group gap="sm" wrap="wrap" className="rm-toolbar">
        <TextInput aria-label={W.searchAreas} placeholder={W.searchAreas} leftSection={<IconSearch size={14} />} value={query} onChange={(e) => setQuery(e.currentTarget.value)} w={{ base: "100%", sm: 240 }} />
        <NativeSelect w={{ base: "100%", sm: 190 }} aria-label={W.filterRepo} value={repo} onChange={(e) => setRepo(e.currentTarget.value)}>
          <option value="">{W.allRepos}</option>
          {repos.map((r) => (
            <option key={r} value={r} data-c="">
              {visible(r)}
            </option>
          ))}
        </NativeSelect>
        <NativeSelect w={{ base: "100%", sm: 190 }} aria-label={W.filterKind} value={kind} onChange={(e) => setKind(e.currentTarget.value)}>
          <option value="">{W.allKinds}</option>
          {kinds.map((k) => (
            <option key={k} value={k} data-c="">
              {visible(k)}
            </option>
          ))}
        </NativeSelect>
        {canDecide ? (
          <SegmentedControl
            aria-label={W.filterDecision}
            value={decision}
            onChange={(v) => setDecision(v as DecisionFilter)}
            size="xs"
            className="rm-segmented"
            data={[
              { value: "all", label: W.decisionAll },
              { value: "undecided", label: W.decisionUndecided },
              { value: "decided", label: W.decisionDecided },
              { value: "review", label: W.decisionReview },
            ]}
          />
        ) : null}
        {filtering ? (
          <Button
            variant="subtle"
            onClick={() => {
              setQuery("");
              setRepo("");
              setKind("");
              setDecision("all");
            }}
          >
            {W.clearFilters}
          </Button>
        ) : null}
      </Group>

      {everything.length === 0 ? (
        <EmptyState title={W.noAreasTitle}>{view.recordsFirst ? W.noAreasBlocked : W.noAreas}</EmptyState>
      ) : shownCount === 0 ? (
        <EmptyState title={W.noAreasMatch} />
      ) : (
        <>
          <Group justify="space-between" wrap="wrap">
            {canDecide && selectable.length > 0 ? (
              <Checkbox
                label={W.selectAll}
                checked={allShownSelected}
                indeterminate={selected.size > 0 && !allShownSelected}
                onChange={() => setSelected(allShownSelected ? new Set() : new Set(selectable.flatMap((a) => (a.id === null ? [] : [a.id]))))}
              />
            ) : (
              <span />
            )}
            <Text size="xs" c="dimmed">
              {W.triageHint}
            </Text>
          </Group>
          {groups.map((g) =>
            g.list.length > 0 ? (
              <section key={g.key} aria-labelledby={`areas-${g.key}`} className="rm-group">
                <Group gap="xs" align="baseline" mb={4}>
                  <h2 id={`areas-${g.key}`}>{g.title}</h2>
                  <Badge variant="default" size="sm">
                    {g.list.length}
                  </Badge>
                </Group>
                <Text size="xs" c="dimmed" mb="xs">
                  {g.note}
                </Text>
                <Paper className="rm-table-paper">
                  <Table.ScrollContainer minWidth={720}>
                    <Table className="rm-areas rm-fixed rm-dense" layout="fixed" horizontalSpacing="xs">
                      <caption className="rm-sr">{g.title}</caption>
                      <Table.Thead>
                        <Table.Tr>
                          <Table.Th w={40}>
                            <span className="rm-sr">{W.selectRow}</span>
                          </Table.Th>
                          <Table.Th w="30%">{W.colArea}</Table.Th>
                          <Table.Th w="13%">{W.colRepository}</Table.Th>
                          <Table.Th w="10%">{W.colKind}</Table.Th>
                          <Table.Th w="8%">{W.colFindings}</Table.Th>
                          <Table.Th w="13%">{W.colChain}</Table.Th>
                          <Table.Th>{W.colDecision}</Table.Th>
                        </Table.Tr>
                      </Table.Thead>
                      <Table.Tbody>
                        {g.list.map((a) => (
                          <AreaRow
                            key={a.index}
                            a={a}
                            pos={position.get(a.index) ?? 0}
                            name={name}
                            findingIds={findingIds}
                            active={a.index === activeKey}
                            selected={a.id !== null && selected.has(a.id)}
                            canDecide={canDecide}
                            known={known}
                            onToggle={toggle}
                            onFocusRow={focusRow}
                            onOpen={open}
                            setRef={setRef}
                          />
                        ))}
                      </Table.Tbody>
                    </Table>
                  </Table.ScrollContainer>
                </Paper>
              </section>
            ) : null,
          )}
        </>
      )}

      {selected.size > 0 && canDecide ? (
        <Paper className="rm-bulkbar" p="sm" role="region" aria-label={W.bulkDecide}>
          <Group justify="space-between" wrap="wrap">
            <Text size="sm" fw={500}>{`${selected.size} ${W.selectedCount}`}</Text>
            <Group gap="xs">
              <Button onClick={() => decide("test", ordered.filter((a) => a.id !== null && selected.has(a.id)))}>{W.bulkDecide}</Button>
              <Button variant="default" onClick={() => setSelected(new Set())}>
                {W.clearSelection}
              </Button>
            </Group>
          </Group>
        </Paper>
      ) : null}

      <Accordion variant="separated" multiple defaultValue={orphaned.length > 0 || (view.decisions?.stale.length ?? 0) > 0 || (view.decisions?.records.length ?? 0) > 0 ? ["decisions"] : []}>
        {orphaned.length > 0 ? (
          <Accordion.Item value="orphaned">
            <Accordion.Control>{`${W.orphanedTitle} (${orphaned.length})`}</Accordion.Control>
            <Accordion.Panel>
              <Text size="sm" c="dimmed" mb="xs">
                {W.orphanedNote}
              </Text>
              <Stack gap="xs">
                {orphaned.map((d, i) => (
                  <Group key={i} gap="xs" wrap="wrap">
                    <OrphanedBadge>
                      <Code text={d.label ?? d.targetId} />
                    </OrphanedBadge>
                    <Text size="sm" component="span">
                      <WhatWasDecided d={d} />
                    </Text>
                  </Group>
                ))}
              </Stack>
            </Accordion.Panel>
          </Accordion.Item>
        ) : null}
        {view.decisions !== null ? (
          <Accordion.Item value="decisions">
            <Accordion.Control>{W.sectionDecisions}</Accordion.Control>
            <Accordion.Panel>
              <DecisionsSection view={view.decisions} />
            </Accordion.Panel>
          </Accordion.Item>
        ) : null}
      </Accordion>

      <Modal
        opened={dialog !== null}
        onClose={() => setDialog(null)}
        title={dialog !== null && dialog.targets.length > 1 ? W.decideBulkTitle : W.decideTitle}
        size="md"
        centered
        closeButtonProps={{ "aria-label": W.dialogClose }}
      >
        {dialog !== null ? (
          <Stack gap="sm">
            <Text size="sm">
              <b>{W.decideAbout}</b>{" "}
              {dialog.targets.slice(0, 4).map((t, i) => (
                <span key={t.id}>
                  {i > 0 ? ", " : null}
                  <Code text={t.label} n={80} />
                </span>
              ))}
              {dialog.targets.length > 4 ? ` and ${dialog.targets.length - 4} more` : null}
            </Text>
            {dialog.targets.length > 1 ? (
              <Text size="xs" c="dimmed">
                {W.bulkNote}
              </Text>
            ) : null}
            <DecisionForm
              key={dialog.key}
              kind="area"
              targets={dialog.targets}
              env={env}
              verdict={dialog.verdict}
              autoFocus
              notify
              onFinished={(ok, failed) => {
                if (ok > 0) {
                  setSelected(new Set());
                }
                if (failed === 0) {
                  // everything was recorded: the toast says so once and the dialog goes; a refusal keeps the dialog open with an answer per area
                  setDialog(null);
                  decided(new Set(dialog.targets.map((t) => t.id)));
                }
              }}
            />
          </Stack>
        ) : null}
      </Modal>

      <AreaDrawer id={drawerId} backTo={base === null ? null : `${base}/areas`} />
    </Stack>
  );
}
