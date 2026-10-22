// The paths of the workspace, built from names and ids the pages have checked (`isReportName`, `isTargetId`): a link is never made from
// a string that could be a scheme.

import { isReportName } from "./api/client";

const TARGET_ID = /^[af]-[0-9a-f]{16}$/;

export function isTargetId(id: unknown): id is string {
  return typeof id === "string" && TARGET_ID.test(id);
}

export const ROOT = "/";
export const JOBS = "/jobs";

export function reportPath(name: string): string | null {
  return isReportName(name) ? `/reports/${encodeURIComponent(name)}` : null;
}

export function areaPath(name: string, id: string | null): string | null {
  const base = reportPath(name);
  return base !== null && isTargetId(id) && id.startsWith("a-") ? `${base}/areas/${id}` : null;
}

export function findingPath(name: string, id: string | null): string | null {
  const base = reportPath(name);
  return base !== null && isTargetId(id) && id.startsWith("f-") ? `${base}/findings/${id}` : null;
}

/** The tabs of a report page, in order. The path of each is `/reports/<name>/<tab>` (the first one is the report itself). */
export const TAB_ORDER = ["overview", "areas", "findings", "branches", "cross-repo", "tests", "records", "model-notes", "author", "notes"] as const;
export type Tab = (typeof TAB_ORDER)[number];

export function isTab(value: string): value is Tab {
  return (TAB_ORDER as readonly string[]).includes(value);
}

export function tabPath(name: string, tab: Tab): string | null {
  const base = reportPath(name);
  return base === null ? null : tab === "overview" ? base : `${base}/${tab}`;
}
