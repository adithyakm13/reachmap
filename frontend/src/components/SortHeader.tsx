import { Group, Table, UnstyledButton } from "@mantine/core";
import { IconArrowDown, IconArrowUp, IconSelector } from "@tabler/icons-react";
import { cmpStr } from "../report/read";

export type Dir = "asc" | "desc";
export interface Sort<K extends string> {
  key: K;
  dir: Dir;
}

/** A column header that sorts: one button, the state in `aria-sort`. */
export function SortTh<K extends string>({ label, k, sort, onSort, w }: { label: string; k: K; sort: Sort<K>; onSort: (next: Sort<K>) => void; w?: string }) {
  const active = sort.key === k;
  const next: Sort<K> = { key: k, dir: active && sort.dir === "asc" ? "desc" : "asc" };
  return (
    <Table.Th w={w} aria-sort={active ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}>
      <UnstyledButton onClick={() => onSort(next)} className="rm-sort">
        <Group gap={4} wrap="nowrap">
          <span>{label}</span>
          {active ? sort.dir === "asc" ? <IconArrowUp size={12} /> : <IconArrowDown size={12} /> : <IconSelector size={12} className="rm-sort-idle" />}
        </Group>
      </UnstyledButton>
    </Table.Th>
  );
}

/** Order two cells: numbers by value, text by code point (as the Python sorts), nothing last in either direction. */
export function compareCells(a: string | number | null, b: string | number | null, dir: Dir): number {
  if (a === null || b === null) {
    return a === b ? 0 : a === null ? 1 : -1;
  }
  const c = typeof a === "number" && typeof b === "number" ? a - b : cmpStr(String(a), String(b));
  return dir === "asc" ? c : -c;
}
