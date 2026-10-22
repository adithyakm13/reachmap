import { useMemo } from "react";
import { ApiError, isReportName, type Client } from "../api/client";
import type { Report } from "../api/types";
import { useClient } from "../context";
import { useAsync, type Async } from "../hooks";
import { checkReport } from "../report/check";
import { allFindings, build, type View } from "../report/view";
import type { DecisionEnv } from "../components/Decisions";

export interface Loaded {
  report: Report;
  view: View;
}

async function loadReport(client: Client, name: string): Promise<Loaded> {
  const report = checkReport(await client.report(name));
  return { report, view: build(report) };
}

/** The ids of the findings by their number: a link to the page of a finding needs the id. */
export function findingIdsOf(view: View): Map<number, string | null> {
  return new Map(allFindings(view).map((f) => [f.number, f.id]));
}

/**
 * The report called `name`, loaded from the runner, checked and turned into its view; and what the decision forms need to record a
 * decision about it (only for a report that completed and has a place among the revisions of its change).
 */
export function useReport(name: string | undefined): Async<Loaded> & { env: DecisionEnv | null; client: Client } {
  const client = useClient();
  const load = useAsync<Loaded>(async () => {
    if (!isReportName(name)) {
      throw new ApiError(404, "not_found");
    }
    return loadReport(client, name);
  }, `report:${name ?? ""}`);
  const { state, reload } = load;
  const env = useMemo<DecisionEnv | null>(() => {
    if (!isReportName(name) || state.status !== "ready") {
      return null;
    }
    const { view } = state.data;
    return view.completed && view.header.revision !== null ? { name, client, recorded: reload } : null;
  }, [name, client, reload, state]);
  return { ...load, env, client };
}
