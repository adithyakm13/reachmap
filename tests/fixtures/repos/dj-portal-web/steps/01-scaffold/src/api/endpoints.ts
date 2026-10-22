// URLs of the portal backend, one place for the whole client.
export const INVOICES_URL = "/billing/invoices/";

export const invoiceUrl = (invoiceId: string) => `/billing/invoices/${invoiceId}/`;

export const voidInvoiceUrl = (invoiceId: string) => `/billing/invoices/${invoiceId}/void/`;

export const MONTHLY_REPORT_URL = "/billing/reports/monthly/";

export const OVERDUE_URL =
  "/api/v1/invoices/overdue/";

export const CASES_URL = "/cases/";
