import { apiGet } from "@api/http";
import { INVOICES_URL } from "@api/endpoints";

export interface ReportRow {
  id: string;
  number: string;
  amount_cents: number;
}

export function fetchInvoicesForReport(): Promise<ReportRow[]> {
  return apiGet<ReportRow[]>(INVOICES_URL);
}
