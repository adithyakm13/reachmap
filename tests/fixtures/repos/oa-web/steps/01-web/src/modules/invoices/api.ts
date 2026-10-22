import { getInvoice, listInvoices } from "@/api/generated/client";

export function fetchInvoices() {
  return listInvoices();
}

export function fetchInvoice(invoiceId: string) {
  return getInvoice(invoiceId);
}
