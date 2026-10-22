import { vi } from "vitest";
import type { Client } from "../api/client";
import type { DecisionRequest, JobInfo, ReportListItem, ReposAnswer, Report } from "../api/types";

export interface FakeClient extends Client {
  reports: ReturnType<typeof vi.fn<Client["reports"]>>;
  report: ReturnType<typeof vi.fn<Client["report"]>>;
  repos: ReturnType<typeof vi.fn<Client["repos"]>>;
  jobs: ReturnType<typeof vi.fn<Client["jobs"]>>;
  submitJob: ReturnType<typeof vi.fn<Client["submitJob"]>>;
  cancelJob: ReturnType<typeof vi.fn<Client["cancelJob"]>>;
  decide: ReturnType<typeof vi.fn<Client["decide"]>>;
}

export interface FakeOptions {
  reports?: ReportListItem[];
  /** The report served for a name (a function, so that a decision recorded can change what the next read answers). */
  report?: (name: string) => Report;
  repos?: ReposAnswer;
  jobs?: JobInfo[];
}

export function fakeClient(options: FakeOptions = {}): FakeClient {
  return {
    reports: vi.fn(async () => options.reports ?? []),
    report: vi.fn(async (name: string) => {
      if (options.report === undefined) {
        throw new Error("no report");
      }
      return options.report(name);
    }),
    repos: vi.fn(async () => options.repos ?? { workspace: "acme", repos: [{ name: "acme/api", roles: ["backend"], branches: ["main"] }] }),
    jobs: vi.fn(async () => options.jobs ?? []),
    submitJob: vi.fn(async () => ({ id: "0123456789ab", state: "queued" })),
    cancelJob: vi.fn(async (id: string) => ({ id, state: "cancelled" })),
    decide: vi.fn(async (_name: string, _request: DecisionRequest) => undefined),
  } as FakeClient;
}
