import { Money } from "@/components/Money";

export default function InvoiceListPage() {
  return (
    <main>
      <h1>Invoices</h1>
      <Money cents={1250} />
    </main>
  );
}
