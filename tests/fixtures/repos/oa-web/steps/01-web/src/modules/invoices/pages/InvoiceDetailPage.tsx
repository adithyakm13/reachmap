import { voidInvoice } from "@/api/generated/client";
import { fetchInvoice } from "../api";

export default function InvoiceDetailPage() {
  const invoice = fetchInvoice("inv-1");
  const cancel = () => voidInvoice("inv-1");
  return <button onClick={cancel}>{String(invoice)}</button>;
}
