import { useEffect, useState } from "react";

import { StatusBadge } from "@/components";
import { formatDate } from "@/lib/dates";
import { truncate } from "@/lib/format";
import { formatAmount } from "@/lib/money";
import { fetchInvoices } from "../api";
import type { Invoice } from "../api";
import { InvoiceTable } from "../legacy/InvoiceTable";

export default function InvoiceListPage() {
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [mode, setMode] = useState<"legacy" | "modern">("modern");

  useEffect(() => {
    fetchInvoices().then(setInvoices);
  }, []);

  return (
    <section>
      <h1>Invoices</h1>
      <button onClick={() => setMode(mode === "modern" ? "legacy" : "modern")}>Switch format</button>
      <InvoiceTable />
      <ul>
        {invoices.map((invoice) => (
          <li key={invoice.id}>
            {truncate(invoice.number, 12)} {formatDate(invoice.created_at)}{" "}
            {formatAmount(invoice.amount_cents / 100, mode)} <StatusBadge status={invoice.status} />
          </li>
        ))}
      </ul>
    </section>
  );
}
