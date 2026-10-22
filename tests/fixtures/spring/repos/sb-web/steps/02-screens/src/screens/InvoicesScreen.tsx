import { fetchInvoices } from "@/services/invoices";

export default function InvoicesScreen() {
  return <pre>{JSON.stringify(fetchInvoices())}</pre>;
}
