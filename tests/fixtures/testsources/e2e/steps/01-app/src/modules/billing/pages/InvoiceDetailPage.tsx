import { Money } from "@/components/Money";

export default function InvoiceDetailPage() {
  return (
    <main>
      <h1>Invoice</h1>
      <Money cents={1250} />
    </main>
  );
}
