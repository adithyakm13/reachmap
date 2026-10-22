export const INVOICES_URL = "/ledger/v1/invoices";

export const INVOICE_URL = (invoiceId: string) => `/ledger/v1/invoices/${invoiceId}`;

export const INVOICE_TOTAL_URL = (invoiceId: string) => `/ledger/v1/invoices/${invoiceId}/total`;

export const STATUS_URL = "/ledger/v1/status";
