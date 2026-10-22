import { apiGet } from "@api/http";
import { CASES_URL } from "@api/endpoints";

export interface CaseSummary {
  id: string;
  title: string;
}

export function fetchCases(): Promise<CaseSummary[]> {
  return apiGet<CaseSummary[]>(CASES_URL);
}
