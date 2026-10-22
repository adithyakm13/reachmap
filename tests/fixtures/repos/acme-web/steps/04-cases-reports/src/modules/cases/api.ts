import { apiGet } from "@api/http";
import { CASES_URL, caseUrl } from "@api/endpoints";

export interface CaseRow {
  id: string;
  title: string;
  status: "open" | "paid" | "overdue";
  opened: string;
}

export function fetchCases(): Promise<CaseRow[]> {
  return apiGet<CaseRow[]>(CASES_URL);
}

export function fetchCase(caseId: string): Promise<CaseRow> {
  return apiGet<CaseRow>(caseUrl(caseId));
}
