// Everything copied out of a report (a source line, a path, a title, a record's detail, a person's reason) is shown through one of these,
// and only these: as React text (never as markup), with control, direction and invisible characters written out as escapes and a very
// long piece cut with a note (`report/text.ts`). The `data-c` mark says "content, not the page's own words": the wording test removes
// everything marked before it checks what the page says.

import { clip, show, visible } from "../report/text";

interface Props {
  text: string;
  title?: string;
  /** Show at most this many characters (the rest is cut with a note); the default is the limit of `report/text.ts`. */
  limit?: number;
  /** A short form for a table or a heading: cut at this many characters with three dots, the whole text in the title. */
  n?: number;
}

/** Text from a report or from the runner is a string by contract, and anything else (a number, an object, nothing) is shown as the text of it. */
function plain(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  if (value === null || value === undefined) {
    return "";
  }
  try {
    return typeof value === "object" ? JSON.stringify(value) : String(value);
  } catch {
    return "";
  }
}

export function C({ text, limit }: Props) {
  return <span data-c="">{show(plain(text), limit)}</span>;
}

export function Code({ text, title, limit, n }: Props) {
  const whole = plain(text);
  const shown = n === undefined ? show(whole, limit) : clip(whole, n);
  const hover = title !== undefined ? visible(plain(title)) : n !== undefined && shown !== visible(whole) ? visible(whole.length > 400 ? whole.slice(0, 400) : whole) : undefined;
  return (
    <code data-c="" title={hover}>
      {shown}
    </code>
  );
}

/** A piece of content that keeps its line breaks: a source line or a block of lines. */
export function Pre({ text }: Props) {
  return (
    <pre data-c="">
      <code>{show(plain(text))}</code>
    </pre>
  );
}

export function Quoted({ text }: Props) {
  return <span data-c="">{show(JSON.stringify(plain(text)))}</span>;
}

/** A short form of a piece of content, for a box of a graph: cut at `n` characters, the whole text in the title. */
export function Clip({ text, n }: { text: string; n: number }) {
  const whole = plain(text);
  return (
    <span data-c="" title={visible(whole.length > 400 ? whole.slice(0, 400) : whole)}>
      {clip(whole, n)}
    </span>
  );
}
