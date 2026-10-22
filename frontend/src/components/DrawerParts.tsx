import { Anchor, Group, Stack, Text } from "@mantine/core";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { findingPath } from "../paths";

/** A labelled line of the facts of an area or a finding. */
export function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="rm-fact">
      <Text size="xs" c="dimmed" tt="uppercase" fw={600} className="rm-fact-label">
        {label}
      </Text>
      <Text size="sm" component="div" className="rm-fact-value">
        {children}
      </Text>
    </div>
  );
}

export function Facts({ children }: { children: ReactNode }) {
  return (
    <Stack gap="xs" className="rm-facts">
      {children}
    </Stack>
  );
}

export function Joined({ items }: { items: ReactNode[] }) {
  return (
    <Group gap={6} wrap="wrap" component="span">
      {items.map((item, i) => (
        <span key={i}>{item}</span>
      ))}
    </Group>
  );
}

/** `F3`: the number of a finding, as the report numbers them (a link to its drawer when the path can be named). */
export function FindingRef({ n, name, id }: { n: number; name: string; id: string | null }) {
  const path = findingPath(name, id);
  return path !== null ? (
    <Anchor component={Link} to={path} size="sm">{`F${n}`}</Anchor>
  ) : (
    <Text size="sm" component="span">{`F${n}`}</Text>
  );
}
