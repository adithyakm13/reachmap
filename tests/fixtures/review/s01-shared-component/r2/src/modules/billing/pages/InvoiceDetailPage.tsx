import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { StatusBadge } from "@components/StatusBadge";
import { formatAmount, formatDelta } from "@/lib/money";
import { fetchAuditLog, fetchInvoice } from "../api";
import type { AuditEntry, Invoice } from "../api";

export default function InvoiceDetailPage() {
  const { invoiceId = "" } = useParams();
  const [invoice, setInvoice] = useState<Invoice | null>(null);
  const [audit, setAudit] = useState<AuditEntry[]>([]);

  useEffect(() => {
    fetchInvoice(invoiceId).then(setInvoice);
    fetchAuditLog().then(setAudit);
  }, [invoiceId]);

  if (!invoice) {
    return <p>Loading</p>;
  }
  return (
    <section>
      <h1>Invoice {invoice.number}</h1>
      <StatusBadge status={invoice.status} />
      <p>{formatAmount(invoice.amount_cents / 100, "modern")}</p>
      <ol>
        {audit.map((entry) => (
          <li key={entry.at}>{entry.action}</li>
        ))}
      </ol>
    </section>
  );
}
