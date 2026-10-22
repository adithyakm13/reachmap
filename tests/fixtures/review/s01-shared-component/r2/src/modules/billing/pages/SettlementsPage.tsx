import { useEffect, useState } from "react";

import { StatusBadge } from "@/components/StatusBadge";
import { formatAmount } from "@/lib/money";
import { fetchSettlementSummary } from "../api";
import type { SettlementSummary } from "../api";

export default function SettlementsPage() {
  const [summary, setSummary] = useState<SettlementSummary | null>(null);

  useEffect(() => {
    fetchSettlementSummary().then(setSummary);
  }, []);

  if (!summary) {
    return <p>Loading</p>;
  }
  return (
    <section>
      <h1>Settlements</h1>
      <StatusBadge status="open" />
      <p>
        {summary.count} invoices, total {formatAmount(summary.total_cents / 100, "legacy")}
      </p>
      <ul>
        {summary.references.map((reference) => (
          <li key={reference}>{reference}</li>
        ))}
      </ul>
    </section>
  );
}
