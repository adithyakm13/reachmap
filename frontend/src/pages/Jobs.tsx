import { Alert, Anchor, Button, Card, Group, NativeSelect, Paper, SegmentedControl, Stack, Table, Text, TextInput, Title } from "@mantine/core";
import { IconPlayerPlay, IconX } from "@tabler/icons-react";
import { useId, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../api/client";
import type { ChangeKind, JobInfo, JobSpec } from "../api/types";
import { useClient } from "../context";
import { useAsync, useTitle } from "../hooks";
import { reportPath } from "../paths";
import { visible } from "../report/text";
import { toast } from "../toast";
import { CHANGE_KINDS, W, errorWords } from "../report/words";
import { EmptyState, FailureBox, Loading } from "../components/Boxes";
import { C, Code } from "../components/Content";
import { StatusBadge } from "../components/badges";
import { relative } from "../time";

const POLL_MS = 3000;
const ACTIVE = ["queued", "running"];

function changeOf(job: JobInfo): string {
  for (const kind of ["pr", "compare", "range"]) {
    if (Object.hasOwn(job.change, kind)) {
      return `${kind} ${job.change[kind]}`;
    }
  }
  return "";
}

function StartForm({ onQueued }: { onQueued: () => void }) {
  const client = useClient();
  const stem = useId();
  const repos = useAsync(() => client.repos(), "repos");
  const [repo, setRepo] = useState("");
  const [kind, setKind] = useState<ChangeKind>("pr");
  const [value, setValue] = useState("");
  const [asOf, setAsOf] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [note, setNote] = useState<{ kind: "saving" | "done" | "error"; text: string; why: string | null } | null>(null);
  if (repos.state.status === "loading") {
    return <Loading rows={4} />;
  }
  if (repos.state.status === "error") {
    return <FailureBox error={repos.state.error} retry={repos.reload} />;
  }
  const list = repos.state.data.repos;
  if (list.length === 0) {
    return <Text size="sm">{W.jobNoRepos}</Text>;
  }
  const chosen = repo !== "" && list.some((r) => r.name === repo) ? repo : list[0].name;
  const hint = CHANGE_KINDS.find((k) => k.value === kind)?.hint ?? "";

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (value.trim() === "") {
      setProblem(W.jobValueRequired);
      return;
    }
    setProblem(null);
    const spec: JobSpec = { repo: chosen, [kind]: value.trim() };
    if (asOf.trim() !== "") {
      spec.as_of = asOf.trim();
    }
    setNote({ kind: "saving", text: W.jobStarting, why: null });
    client.submitJob(spec).then(
      () => {
        setNote({ kind: "done", text: W.jobStarted, why: null });
        setValue("");
        toast(W.toastJobQueued);
        onQueued();
      },
      (e: unknown) => {
        const status = e instanceof ApiError ? e.status : 0;
        const why = e instanceof ApiError && e.code !== null ? (e.field !== null ? `${e.code}: ${e.field}` : e.code) : null;
        setNote({ kind: "error", text: `${W.jobNotStarted} ${errorWords(status)}`, why });
      },
    );
  };

  return (
    <form onSubmit={submit} noValidate aria-labelledby={`${stem}-h`}>
      <Stack gap="sm">
        <div>
          <Title order={2} id={`${stem}-h`} size="h3">
            {W.jobsFormTitle}
          </Title>
          <Text size="sm" c="dimmed">
            {W.jobsFormNote}
          </Text>
        </div>
        <NativeSelect label={W.jobRepo} id={`${stem}-repo`} value={chosen} onChange={(e) => setRepo(e.currentTarget.value)}>
          {list.map((r) => (
            <option key={r.name} value={r.name} data-c="">
              {visible(r.name)}
            </option>
          ))}
        </NativeSelect>
        <div>
          <Text size="sm" fw={500} mb={4} id={`${stem}-kind`}>
            {W.jobChange}
          </Text>
          <SegmentedControl
            fullWidth
            aria-labelledby={`${stem}-kind`}
            value={kind}
            onChange={(v) => setKind(v as ChangeKind)}
            data={CHANGE_KINDS.map((k) => ({ value: k.value, label: k.label }))}
          />
        </div>
        <TextInput label={W.jobValue} id={`${stem}-value`} value={value} onChange={(e) => setValue(e.currentTarget.value)} aria-required="true" description={hint} />
        <TextInput label={W.jobAsOf} id={`${stem}-asof`} value={asOf} onChange={(e) => setAsOf(e.currentTarget.value)} />
        {problem !== null ? (
          <Alert color="red" p="xs" role="alert">
            {problem}
          </Alert>
        ) : null}
        <Group gap="sm">
          <Button type="submit" leftSection={<IconPlayerPlay size={14} />} loading={note?.kind === "saving"}>
            {W.jobStart}
          </Button>
          <Text size="sm" c="dimmed" role="status">
            {note !== null && note.kind !== "error" ? note.text : null}
          </Text>
        </Group>
        {note?.kind === "error" ? (
          <Alert color="red" p="xs" role="alert">
            {note.text}
            {note.why !== null ? (
              <>
                {" "}
                (<C text={note.why} />)
              </>
            ) : null}
          </Alert>
        ) : null}
      </Stack>
    </form>
  );
}

function JobRow({ job, onCancelled }: { job: JobInfo; onCancelled: () => void }) {
  const client = useClient();
  const [failed, setFailed] = useState(false);
  const path = job.report !== null ? reportPath(job.report) : null;
  const active = ACTIVE.includes(job.state);
  const rel = job.created_at === null ? null : relative(job.created_at);
  return (
    <Table.Tr>
      <Table.Td>
        <Code text={job.id} />
      </Table.Td>
      <Table.Td>
        <C text={job.repo} />
      </Table.Td>
      <Table.Td>
        <Code text={changeOf(job)} />
      </Table.Td>
      <Table.Td>
        <StatusBadge status={job.state} />
      </Table.Td>
      <Table.Td>{job.created_at !== null ? <span title={visible(job.created_at)}>{rel ?? <C text={job.created_at} />}</span> : null}</Table.Td>
      <Table.Td>
        {job.report !== null ? (
          path !== null ? (
            <Anchor component={Link} to={path} size="sm">
              <Code text={job.report} />
            </Anchor>
          ) : (
            <Code text={job.report} />
          )
        ) : null}
      </Table.Td>
      <Table.Td>
        {active ? (
          <Button
            variant="default"
            size="compact-xs"
            leftSection={<IconX size={12} />}
            onClick={() => {
              setFailed(false);
              client.cancelJob(job.id).then(onCancelled, () => setFailed(true));
            }}
          >
            {W.jobCancel}
          </Button>
        ) : null}
        {failed ? (
          <Text size="xs" c="red" role="alert">
            {errorWords(409)}
          </Text>
        ) : null}
      </Table.Td>
    </Table.Tr>
  );
}

/** The queue of the runner (one job at a time) and a form to start an analysis of a repository of the configuration. */
export function Jobs() {
  const client = useClient();
  const { state, reload } = useAsync(() => client.jobs(), "jobs", POLL_MS);
  useTitle(`${W.jobsTitle} - ${W.appName}`);
  return (
    <Stack gap="md" className="rm-page">
      <div>
        <Title order={1}>{W.jobsTitle}</Title>
        <Text size="sm" c="dimmed">
          {W.jobsIntro}
        </Text>
      </div>
      <div className="rm-jobs-grid">
        <Card padding="md">
          <StartForm onQueued={reload} />
        </Card>
        <Stack gap="sm">
          <Title order={2} size="h3">
            {W.jobsQueue}
          </Title>
          {state.status === "loading" ? <Loading rows={3} /> : null}
          {state.status === "error" ? <FailureBox error={state.error} retry={reload} /> : null}
          {state.status === "ready" && state.data.length === 0 ? <EmptyState title={W.jobsNoneTitle}>{W.jobsNone}</EmptyState> : null}
          {state.status === "ready" && state.data.length > 0 ? (
            <Paper className="rm-table-paper">
              <Table.ScrollContainer minWidth={680}>
                <Table aria-label={W.jobsTitle} className="rm-listing">
                  <caption className="rm-sr">{W.jobsTitle}</caption>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>{W.colJob}</Table.Th>
                      <Table.Th>{W.colRepository}</Table.Th>
                      <Table.Th>{W.colChange}</Table.Th>
                      <Table.Th>{W.colStatus}</Table.Th>
                      <Table.Th>{W.colCreated}</Table.Th>
                      <Table.Th>{W.colReportName}</Table.Th>
                      <Table.Th>{W.colActions}</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {state.data.map((job) => (
                      <JobRow key={job.id} job={job} onCancelled={reload} />
                    ))}
                  </Table.Tbody>
                </Table>
              </Table.ScrollContainer>
            </Paper>
          ) : null}
        </Stack>
      </div>
    </Stack>
  );
}
