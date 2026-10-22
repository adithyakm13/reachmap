import { apiGet } from "@/api/http";
import { ADMIN_STATS_URL, INVOICES_URL } from "@/api/endpoints";

export function fetchStats() {
  return apiGet(ADMIN_STATS_URL);
}

export function fetchInvoices() {
  return apiGet(INVOICES_URL);
}
