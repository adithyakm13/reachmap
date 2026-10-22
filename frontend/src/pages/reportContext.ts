import { createContext, useContext } from "react";
import type { Report } from "../api/types";
import type { DecisionEnv } from "../components/Decisions";
import type { View } from "../report/view";

/** What the tabs and drawers of one report page share: the report, its view, and what a decision form needs. */
export interface ReportCtx {
  name: string;
  report: Report;
  view: View;
  /** Null when a decision cannot be recorded for this report (it did not complete, or it has no place among the revisions of its change). */
  env: DecisionEnv | null;
  reload: () => void;
  /** The id of each finding by its number: a link to a finding's drawer needs the id. */
  findingIds: ReadonlyMap<number, string | null>;
}

export const ReportContext = createContext<ReportCtx | null>(null);

export function useReportCtx(): ReportCtx {
  const ctx = useContext(ReportContext);
  if (ctx === null) {
    throw new Error("no report: the component was rendered outside a report page");
  }
  return ctx;
}
