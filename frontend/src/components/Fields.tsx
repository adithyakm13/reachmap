import type { ReactNode } from "react";

/** A labelled line of a list: `Label: value`. */
export function Field({ label, children }: { label: string; children?: ReactNode }) {
  return (
    <li>
      <strong>{label}</strong>
      {children === undefined ? null : <> {children}</>}
    </li>
  );
}

/** Nodes separated by a separator. */
export function Join({ items, sep = ", " }: { items: ReactNode[]; sep?: string }) {
  return (
    <>
      {items.map((item, i) => (
        <span key={i}>
          {i > 0 ? sep : null}
          {item}
        </span>
      ))}
    </>
  );
}
