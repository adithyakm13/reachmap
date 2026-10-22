import { Anchor, Badge, Button, Group, NativeSelect, Paper, Stack, Table, Text, TextInput, Title, Tooltip } from "@mantine/core";
import { IconSearch } from "@tabler/icons-react";
import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useClient } from "../context";
import { takePrefetchedReports } from "../prefetch";
import { useAsync, useTitle } from "../hooks";
import { JOBS, reportPath } from "../paths";
import { isDict, own, text } from "../report/read";
import { visible } from "../report/text";
import { STATUS_WORDS, W } from "../report/words";
import { EmptyState, FailureBox, Loading } from "../components/Boxes";
import { C, Clip, Code } from "../components/Content";
import { CopyButton } from "../components/CopyButton";
import { StatusBadge } from "../components/badges";
import { SortTh, compareCells, type Sort } from "../components/SortHeader";
import { relative } from "../time";
import type { ReportListItem } from "../api/types";

/** The change of a listed report: the text the review layer made (`compare:main...topic`), or, for a report with no review data, the spec the run was asked for. */
export function changeText(value: unknown): string | null {
  if (typeof value === "string") {
    return value;
  }
  if (isDict(value)) {
    for (const kind of ["pr", "compare", "range"]) {
      if (Object.hasOwn(value, kind)) {
        return `${kind}:${text(value[kind])}`;
      }
    }
  }
  return null;
}

function shown(value: unknown): string | null {
  return typeof value === "string" || (typeof value === "number" && Number.isFinite(value)) ? String(value) : null;
}

function whole(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) ? value : null;
}

export interface Row {
  item: ReportListItem;
  name: string;
  path: string | null;
  repo: string | null;
  change: string | null;
  status: string | null;
  revision: number | null;
  created: string | null;
  head: string | null;
  areas: number | null;
  blocking: number | null;
  stale: boolean;
}

/** The rows of the list: each report with what the runner said of it, and whether a later revision of the same change exists. */
export function rowsOf(items: ReportListItem[]): Row[] {
  const latest = new Map<string, number>();
  const keyOf = (r: ReportListItem): string => JSON.stringify([shown(r.key?.workspace), shown(r.key?.repo), changeText(r.key?.change)]);
  for (const r of items) {
    const rev = whole(r.revision);
    if (rev !== null) {
      latest.set(keyOf(r), Math.max(rev, latest.get(keyOf(r)) ?? 0));
    }
  }
  return items.map((r) => {
    const rev = whole(r.revision);
    const head = shown(r.head_sha);
    return {
      item: r,
      name: String(r.name),
      path: reportPath(String(r.name)),
      repo: shown(r.key?.repo),
      change: changeText(r.key?.change),
      status: shown(r.status),
      revision: rev,
      created: shown(r.created),
      head: head !== null && head !== "" ? head : null,
      // a run that did not complete has no count, whatever the answer says (a partial list is never countable)
      areas: shown(r.status) === "completed" ? whole(r.areas) : null,
      blocking: shown(r.status) === "completed" ? whole(r.blocking_records) : null,
      stale: rev !== null && rev < (latest.get(keyOf(r)) ?? 0),
    };
  });
}

type SortKey = "created" | "change" | "repo" | "status" | "revision" | "areas" | "blocking";

function sortValue(row: Row, key: SortKey): string | number | null {
  switch (key) {
    case "created":
      return row.created;
    case "change":
      return row.change ?? row.name;
    case "repo":
      return row.repo;
    case "status":
      return row.status;
    case "revision":
      return row.revision;
    case "areas":
      return row.areas;
    case "blocking":
      return row.blocking;
  }
}

function ReportsTable({ rows }: { rows: Row[] }) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [repo, setRepo] = useState("");
  const [status, setStatus] = useState("");
  // the order the runner gave (newest first) until a column is chosen
  const [sort, setSort] = useState<Sort<SortKey> | null>(null);
  const repos = useMemo(() => [...new Set(rows.flatMap((r) => (r.repo === null ? [] : [r.repo])))].sort(), [rows]);
  const statuses = useMemo(() => [...new Set(rows.flatMap((r) => (r.status === null ? [] : [r.status])))].sort(), [rows]);
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const kept = rows.filter(
      (r) =>
        (repo === "" || r.repo === repo) &&
        (status === "" || r.status === status) &&
        (q === "" || [r.name, r.repo ?? "", r.change ?? ""].some((v) => v.toLowerCase().includes(q))),
    );
    if (sort === null) {
      return kept;
    }
    return [...kept].sort((a, b) => compareCells(sortValue(a, sort.key), sortValue(b, sort.key), sort.dir));
  }, [rows, query, repo, status, sort]);
  const active = sort ?? { key: "created" as SortKey, dir: "desc" as const };
  const filtering = query !== "" || repo !== "" || status !== "";

  return (
    <Stack gap="sm">
      <Group gap="sm" wrap="wrap">
        <TextInput
          aria-label={W.searchReports}
          placeholder={W.searchReports}
          leftSection={<IconSearch size={14} />}
          value={query}
          onChange={(e) => setQuery(e.currentTarget.value)}
          w={{ base: "100%", sm: 260 }}
        />
        <NativeSelect w={{ base: "100%", sm: 190 }} aria-label={W.filterRepo} value={repo} onChange={(e) => setRepo(e.currentTarget.value)}>
          <option value="">{W.allRepos}</option>
          {repos.map((r) => (
            <option key={r} value={r} data-c="">
              {visible(r)}
            </option>
          ))}
        </NativeSelect>
        <NativeSelect w={{ base: "100%", sm: 190 }} aria-label={W.filterStatus} value={status} onChange={(e) => setStatus(e.currentTarget.value)}>
          <option value="">{W.allStatuses}</option>
          {statuses.map((s) => (
            <option key={s} value={s} data-c="">
              {own(STATUS_WORDS, s) ?? visible(s)}
            </option>
          ))}
        </NativeSelect>
        {filtering ? (
          <Button
            variant="subtle"
            onClick={() => {
              setQuery("");
              setRepo("");
              setStatus("");
            }}
          >
            {W.clearFilters}
          </Button>
        ) : null}
      </Group>
      {filtered.length === 0 ? (
        <EmptyState title={W.noMatch} />
      ) : (
        <Paper className="rm-table-paper">
          <Table.ScrollContainer minWidth={820}>
            <Table className="rm-listing rm-fixed rm-dense" layout="fixed" horizontalSpacing="xs" aria-label={W.reportsTitle}>
              <caption className="rm-sr">{W.reportsTitle}</caption>
              <Table.Thead>
                <Table.Tr>
                  <SortTh label={W.colChange} k="change" sort={active} onSort={setSort} w="24%" />
                  <SortTh label={W.colRepository} k="repo" sort={active} onSort={setSort} w="12%" />
                  <SortTh label={W.colStatus} k="status" sort={active} onSort={setSort} w="13%" />
                  <SortTh label={W.colRevision} k="revision" sort={active} onSort={setSort} w="7%" />
                  <Table.Th w="12%">{W.colHead}</Table.Th>
                  <SortTh label={W.colStarted} k="created" sort={active} onSort={setSort} w="14%" />
                  <SortTh label={W.colAreasToTest} k="areas" sort={active} onSort={setSort} w="8%" />
                  <SortTh label={W.colBlockingRecords} k="blocking" sort={active} onSort={setSort} w="9%" />
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {filtered.map((r) => {
                  const rel = r.created === null ? null : relative(r.created);
                  return (
                    <Table.Tr key={r.name} className={r.path !== null ? "rm-row-link" : undefined} onClick={r.path === null ? undefined : (e) => (e.target as HTMLElement).closest("a,button") === null && navigate(r.path as string)}>
                      <Table.Td>
                        <Text size="sm" lineClamp={1} className="rm-trunc" title={visible(r.name)}>
                          {r.path !== null ? (
                            <Anchor component={Link} to={r.path} size="sm" fw={500} className="rm-row-title">
                              <Code text={r.change ?? r.name} n={140} />
                            </Anchor>
                          ) : (
                            <Code text={r.change ?? r.name} n={140} />
                          )}
                        </Text>
                      </Table.Td>
                      <Table.Td>{r.repo !== null ? <Clip text={r.repo} n={36} /> : null}</Table.Td>
                      <Table.Td>
                        <Group gap={4} wrap="wrap">
                          {r.status !== null ? <StatusBadge status={r.status} /> : null}
                          {r.stale ? (
                            <Tooltip label={W.staleHint}>
                              <Badge variant="light" color="yellow" data-stale="true">
                                {W.staleBadge}
                              </Badge>
                            </Tooltip>
                          ) : null}
                        </Group>
                      </Table.Td>
                      <Table.Td>{r.revision}</Table.Td>
                      <Table.Td>
                        {r.head !== null ? (
                          <Group gap={2} wrap="nowrap">
                            <Code text={r.head.slice(0, 12)} title={r.head} />
                            <CopyButton value={r.head} label={W.copyCommit} />
                          </Group>
                        ) : null}
                      </Table.Td>
                      <Table.Td>
                        {r.created !== null ? (
                          <Tooltip label={<C text={r.created} />}>
                            <Text size="sm" component="span">
                              {rel ?? <C text={r.created} />}
                            </Text>
                          </Tooltip>
                        ) : null}
                      </Table.Td>
                      <Table.Td>{r.areas}</Table.Td>
                      <Table.Td>
                        {r.blocking !== null ? (
                          r.blocking > 0 ? (
                            <Badge variant="filled" color="red.8">
                              {r.blocking}
                            </Badge>
                          ) : (
                            <Text size="sm" component="span" c="dimmed">
                              {r.blocking}
                            </Text>
                          )
                        ) : null}
                      </Table.Td>
                    </Table.Tr>
                  );
                })}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        </Paper>
      )}
    </Stack>
  );
}

/** The reports the runner has, newest first. */
export function Reports() {
  const client = useClient();
  const { state, reload } = useAsync(() => takePrefetchedReports(client) ?? client.reports(), "reports");
  useTitle(`${W.reportsTitle} - ${W.appName}`);
  const rows = useMemo(() => (state.status === "ready" ? rowsOf(state.data) : []), [state]);
  return (
    <Stack gap="md" className="rm-page">
      <div>
        <Title order={1}>{W.reportsTitle}</Title>
        <Text size="sm" c="dimmed">
          {W.reportsIntro}
        </Text>
      </div>
      {state.status === "loading" ? <Loading rows={6} /> : null}
      {state.status === "error" ? <FailureBox error={state.error} retry={reload} /> : null}
      {state.status === "ready" && rows.length === 0 ? (
        <EmptyState
          title={W.reportsNoneTitle}
          footer={
            <Button component={Link} to={JOBS}>
              {W.startAnalysis}
            </Button>
          }
        >
          {W.reportsNone}
        </EmptyState>
      ) : null}
      {state.status === "ready" && rows.length > 0 ? <ReportsTable rows={rows} /> : null}
    </Stack>
  );
}
