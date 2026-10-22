import { fetchInvoice, fetchTotal } from "@/services/invoices";

export default function InvoiceScreen({ invoiceId }: { invoiceId: string }) {
  return (
    <div>
      <pre>{JSON.stringify(fetchInvoice(invoiceId))}</pre>
      <pre>{JSON.stringify(fetchTotal(invoiceId))}</pre>
    </div>
  );
}
