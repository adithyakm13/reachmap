import { apiGet } from "@api/http";
import {
  AUDIT_LOG_URL,
  INVOICES_URL,
  SETTLEMENT_SUMMARY_URL,
  invoiceUrl,
} from "@api/endpoints";

export interface Invoice {
  id: string;
  number: string;
  status: "open" | "paid" | "overdue";
  amount_cents: number;
  created_at: string;
}

export interface SettlementSummary {
  count: number;
  total_cents: number;
  references: string[];
}

export interface AuditEntry {
  at: string;
  action: string;
}

export function fetchInvoices(): Promise<Invoice[]> {
  return apiGet<Invoice[]>(INVOICES_URL);
}

export function fetchInvoice(invoiceId: string): Promise<Invoice> {
  return apiGet<Invoice>(invoiceUrl(invoiceId));
}

export async function fetchSettlementSummary(): Promise<SettlementSummary> {
  const summary = await apiGet<SettlementSummary>(SETTLEMENT_SUMMARY_URL);
  return { ...summary, references: summary.references ?? [] };
}

export function fetchAuditLog(): Promise<AuditEntry[]> {
  return apiGet<AuditEntry[]>(AUDIT_LOG_URL);
}
