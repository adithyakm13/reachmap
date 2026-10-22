import { Anchor, Stack, Text, Title } from "@mantine/core";
import { Link } from "react-router-dom";
import { useTitle } from "../hooks";
import { ROOT } from "../paths";
import { W } from "../report/words";

export function NotFound() {
  useTitle(`${W.notFoundTitle} - ${W.appName}`);
  return (
    <Stack gap="xs" className="rm-page">
      <Title order={1}>{W.notFoundTitle}</Title>
      <Text size="sm">{W.notFound}</Text>
      <Anchor component={Link} to={ROOT} size="sm">
        {W.backToReports}
      </Anchor>
    </Stack>
  );
}
