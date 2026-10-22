import { apiGet, apiPost } from "@api/http";
import { INVOICES_URL, MONTHLY_REPORT_URL, OVERDUE_URL, invoiceUrl, voidInvoiceUrl } from "@api/endpoints";

export interface Invoice {
  id: string;
  number: string;
  amount_cents: number;
  settled: boolean;
}

export interface Report {
  reference: string;
}

export function fetchInvoices(): Promise<Invoice[]> {
  return apiGet<Invoice[]>(INVOICES_URL);
}

export function fetchOverdue(): Promise<Invoice[]> {
  return apiGet<Invoice[]>(OVERDUE_URL);
}

export function fetchInvoice(invoiceId: string): Promise<Invoice> {
  return apiGet<Invoice>(invoiceUrl(invoiceId));
}

export function voidInvoice(invoiceId: string): Promise<Invoice> {
  return apiPost<Invoice>(voidInvoiceUrl(invoiceId), {});
}

export function fetchMonthlyReport(): Promise<Report> {
  return apiGet<Report>(MONTHLY_REPORT_URL);
}
