import { Alert, Anchor, Stack, Text, Title } from "@mantine/core";
import { Link } from "react-router-dom";
import { ROOT } from "../paths";
import { W } from "../report/words";

/** What a route shows when rendering it fails: fixed words, never the error (it can hold text from a report). */
export function Crash() {
  return (
    <Alert color="red" role="alert" m="lg" maw={640}>
      <Stack gap="xs">
        <Title order={1} size="h2">
          {W.crashTitle}
        </Title>
        <Text size="sm">{W.crash}</Text>
        <Anchor component={Link} to={ROOT} size="sm">
          {W.backToReports}
        </Anchor>
      </Stack>
    </Alert>
  );
}
