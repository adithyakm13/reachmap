// The list of reports is asked for as soon as the route is matched, in parallel with the fetch of the page's chunk, and the page takes the answer
// when it mounts (it does not wait for its own code to arrive before it asks). An answer that nobody takes within a few seconds is dropped.

import type { Client } from "./api/client";
import type { ReportListItem } from "./api/types";

interface Held {
  client: Client;
  at: number;
  promise: Promise<ReportListItem[]>;
}

let held: Held | null = null;
const FRESH_MS = 10_000;

export function prefetchReports(client: Client | null): void {
  if (client === null) {
    return;
  }
  const promise = client.reports();
  promise.catch(() => undefined); // an error is seen by whoever takes the promise, if anyone does
  held = { client, at: Date.now(), promise };
}

export function takePrefetchedReports(client: Client): Promise<ReportListItem[]> | null {
  if (held !== null && held.client === client && Date.now() - held.at < FRESH_MS) {
    const { promise } = held;
    held = null;
    return promise;
  }
  held = null;
  return null;
}
