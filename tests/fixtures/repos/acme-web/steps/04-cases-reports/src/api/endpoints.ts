// URLs of the backend API, one place for the whole client.
export const INVOICES_URL = "/api/billing/invoices";

export const invoiceUrl = (invoiceId: string) => `/api/billing/invoices/${invoiceId}`;

export const SETTLEMENT_SUMMARY_URL =
  "/api/billing/settlements/summary";

export const AUDIT_LOG_URL = "/api/billing/audit-log";

export const CASES_URL = "/api/cases";

export const caseUrl = (caseId: string) => `/api/cases/${caseId}`;
