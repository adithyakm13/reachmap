import { useEffect, useState } from "react";

import { fetchInvoices, fetchOverdue } from "../api";
import type { Invoice } from "../api";

export default function InvoiceListPage() {
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [overdue, setOverdue] = useState<Invoice[]>([]);

  useEffect(() => {
    fetchInvoices().then(setInvoices);
    fetchOverdue().then(setOverdue);
  }, []);

  return (
    <section>
      <h1>Invoices</h1>
      <p>{overdue.length} overdue</p>
      <ul>
        {invoices.map((invoice) => (
          <li key={invoice.id}>{invoice.number}</li>
        ))}
      </ul>
    </section>
  );
}
