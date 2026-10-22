import { Card, Group, List, Stack, Text } from "@mantine/core";
import { own } from "../../report/read";
import { STATE_WORDS, W } from "../../report/words";
import { useReportCtx } from "../../pages/reportContext";
import { Code, Pre } from "../Content";
import { CopyButton } from "../CopyButton";
import { StateChip } from "../badges";

/** For the author: other repositories reached, files beyond the diff, other branches, unit tests to run (each command copyable as text). */
export function AuthorTab() {
  const { view } = useReportCtx();
  const a = view.author;
  if (a === null) {
    return null;
  }
  return (
    <Stack gap="lg">
      <Card padding="md" component="section" aria-labelledby="author-tests-h">
        <h2 id="author-tests-h" className="rm-h2">
          {W.authorTests}
        </h2>
        {a.tests.length > 0 ? (
          <Stack gap="sm">
            <Text size="sm" c="dimmed">
              {W.authorTestsNote}
            </Text>
            {a.tests.map((t, i) => (
              <div key={i}>
                <Group gap="xs" justify="space-between">
                  <Text size="sm">
                    <Code text={t.repo} />
                    {`: ${t.quoted.length} ${t.quoted.length === 1 ? "test file" : "test files"}`}
                  </Text>
                  <CopyButton value={t.text} label={W.copyText} />
                </Group>
                {t.command === null ? (
                  <Text size="xs" c="dimmed">
                    {W.authorNoCommand}
                  </Text>
                ) : null}
                <Pre text={t.text} />
              </div>
            ))}
          </Stack>
        ) : (
          <Text size="sm">{W.authorNoTests}</Text>
        )}
      </Card>
      <Card padding="md" component="section" aria-labelledby="author-repos-h">
        <h2 id="author-repos-h" className="rm-h2">
          {W.authorRepos}
        </h2>
        {a.repos.length > 0 ? (
          <List listStyleType="none" spacing={4} size="sm">
            {a.repos.map((r, i) => (
              <List.Item key={i}>
                <Code text={r.repo} /> (
                {r.edges.map((e, j) => (
                  <span key={j}>
                    {j > 0 ? ", " : null}
                    {e.content ? <Code text={e.text} /> : e.text}
                  </span>
                ))}
                )
              </List.Item>
            ))}
          </List>
        ) : (
          <Text size="sm">{W.noCross}</Text>
        )}
      </Card>
      <Card padding="md" component="section" aria-labelledby="author-branches-h">
        <h2 id="author-branches-h" className="rm-h2">
          {W.authorBranches}
        </h2>
        {a.branches.length > 0 ? (
          <List listStyleType="none" spacing={4} size="sm">
            {a.branches.map((b, i) => (
              <List.Item key={i}>
                <Group gap="xs" wrap="wrap">
                  <Code text={b.branch} />
                  <StateChip state={b.state} />
                  <Text size="sm" c="dimmed" component="span">
                    {own(STATE_WORDS, b.state) ?? W.seeBranches}
                  </Text>
                </Group>
              </List.Item>
            ))}
          </List>
        ) : (
          <Text size="sm">{W.authorNoBranches}</Text>
        )}
      </Card>
      <Card padding="md" component="section" aria-labelledby="author-files-h">
        <h2 id="author-files-h" className="rm-h2">
          {W.authorFiles}
        </h2>
        {a.files.length > 0 ? (
          <Stack gap="xs">
            <Text size="sm" c="dimmed">
              {W.authorFilesNote}
            </Text>
            <List listStyleType="none" spacing={2} size="sm">
              {a.files.map((f, i) => (
                <List.Item key={i}>
                  <Code text={f.repo} /> <Code text={f.path} />
                </List.Item>
              ))}
            </List>
          </Stack>
        ) : (
          <Text size="sm">{W.authorNoFiles}</Text>
        )}
      </Card>
    </Stack>
  );
}
