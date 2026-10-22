import { apiGet } from "@/api/http";
import { INVOICES_URL, INVOICE_URL, INVOICE_TOTAL_URL } from "@/api/endpoints";

export function fetchInvoices() {
  return apiGet(INVOICES_URL);
}

export function fetchInvoice(invoiceId: string) {
  return apiGet(INVOICE_URL(invoiceId));
}

export function fetchTotal(invoiceId: string) {
  return apiGet(INVOICE_TOTAL_URL(invoiceId));
}
