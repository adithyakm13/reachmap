import { fetchInvoices } from "../api";

export default function InvoiceListPage() {
  const rows = fetchInvoices();
  return <ul>{String(rows)}</ul>;
}
