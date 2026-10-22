// The only module that calls `fetch`: the runner API, same origin, with the session token in a header.
//
// The token comes from the address fragment (`#token=...`), once, at start (`takeToken`): it is kept in memory by the caller (never in
// storage), and taken out of the address bar and the history at once. Every request is to a path under `/api/` of this origin, with
// `credentials: "omit"`. An error is an `ApiError` that holds a status and, at most, a code and a field of the fixed form the runner uses
// (`invalid_decision`, `reason`): never the body of the answer, so nothing the server or a report says can reach the page as markup.

import type { DecisionRequest, JobInfo, JobSpec, ReportListItem, ReposAnswer, Report } from "./types";

export const TOKEN_HEADER = "X-Reachmap-Token";
const TOKEN_IN_FRAGMENT = /(?:^|[#&])token=([A-Za-z0-9_-]+)/;
const REPORT_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]{0,200}$/;
const JOB_ID = /^[0-9a-f]{12}$/;
const FIXED_WORD = /^[a-z][a-z_]{0,39}$/;

export function isReportName(name: unknown): name is string {
  return typeof name === "string" && REPORT_NAME.test(name);
}

export function isJobId(id: unknown): id is string {
  return typeof id === "string" && JOB_ID.test(id);
}

export interface LocationLike {
  hash: string;
  pathname: string;
  search: string;
}

export interface HistoryLike {
  replaceState(data: unknown, unused: string, url?: string | URL | null): void;
}

/** The token of the address fragment, or null. The fragment is removed from the address bar and the history whatever it held. */
export function takeToken(loc: LocationLike, hist: HistoryLike): string | null {
  const found = TOKEN_IN_FRAGMENT.exec(loc.hash);
  if (loc.hash !== "") {
    hist.replaceState(null, "", loc.pathname + loc.search);
  }
  return found === null ? null : found[1];
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string | null;
  readonly field: string | null;

  constructor(status: number, code: string | null = null, field: string | null = null) {
    super(`api ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.field = field;
  }
}

export interface Client {
  reports(): Promise<ReportListItem[]>;
  report(name: string): Promise<Report>;
  repos(): Promise<ReposAnswer>;
  jobs(): Promise<JobInfo[]>;
  submitJob(spec: JobSpec): Promise<{ id: string; state: string }>;
  cancelJob(id: string): Promise<{ id: string; state: string }>;
  decide(name: string, request: DecisionRequest): Promise<void>;
}

type FetchLike = (input: string, init: RequestInit) => Promise<Response>;

function fixedWord(value: unknown): string | null {
  return typeof value === "string" && FIXED_WORD.test(value) ? value : null;
}

export function createClient(token: string, fetchImpl?: FetchLike): Client {
  const doFetch: FetchLike = fetchImpl ?? ((input, init) => fetch(input, init));

  async function call(method: "GET" | "POST" | "DELETE", path: string, body?: unknown): Promise<unknown> {
    const headers: Record<string, string> = { [TOKEN_HEADER]: token, Accept: "application/json" };
    const init: RequestInit = { method, headers, cache: "no-store", credentials: "omit", referrerPolicy: "no-referrer" };
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    let response: Response;
    try {
      response = await doFetch(path, init);
    } catch {
      throw new ApiError(0);
    }
    let data: unknown = null;
    try {
      data = await response.json();
    } catch {
      data = null;
    }
    if (!response.ok) {
      const shape = typeof data === "object" && data !== null ? (data as Record<string, unknown>) : {};
      throw new ApiError(response.status, fixedWord(shape.error), fixedWord(shape.field));
    }
    if (data === null) {
      throw new ApiError(response.status === 200 ? 502 : response.status);
    }
    return data;
  }

  function reportPath(name: string): string {
    if (!isReportName(name)) {
      throw new ApiError(404, "not_found");
    }
    return `/api/reports/${encodeURIComponent(name)}`;
  }

  function jobPath(id: string): string {
    if (!isJobId(id)) {
      throw new ApiError(404, "not_found");
    }
    return `/api/jobs/${id}`;
  }

  function listOf(data: unknown, key: string): unknown[] {
    const value = typeof data === "object" && data !== null ? (data as Record<string, unknown>)[key] : undefined;
    if (!Array.isArray(value)) {
      throw new ApiError(502);
    }
    return value;
  }

  return {
    async reports() {
      return listOf(await call("GET", "/api/reports"), "reports") as ReportListItem[];
    },
    async report(name) {
      const data = await call("GET", reportPath(name));
      if (typeof data !== "object" || Array.isArray(data)) {
        throw new ApiError(502);
      }
      return data as Report;
    },
    async repos() {
      const data = (await call("GET", "/api/repos")) as Partial<ReposAnswer>;
      if (typeof data.workspace !== "string" || !Array.isArray(data.repos)) {
        throw new ApiError(502);
      }
      return data as ReposAnswer;
    },
    async jobs() {
      return listOf(await call("GET", "/api/jobs"), "jobs") as JobInfo[];
    },
    async submitJob(spec) {
      return (await call("POST", "/api/jobs", spec)) as { id: string; state: string };
    },
    async cancelJob(id) {
      return (await call("DELETE", jobPath(id))) as { id: string; state: string };
    },
    async decide(name, request) {
      await call("POST", `${reportPath(name)}/decisions`, request);
    },
  };
}
