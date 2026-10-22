import { useEffect, useState } from "react";

import { formatDate } from "@/lib/dates";
import { fetchInvoicesForReport } from "../api";
import type { ReportRow } from "../api";
import { ReportTable } from "../legacy/ReportTable";

export default function ReportsHomePage() {
  const [rows, setRows] = useState<ReportRow[]>([]);

  useEffect(() => {
    fetchInvoicesForReport().then(setRows);
  }, []);

  return (
    <section>
      <h1>Reports</h1>
      <p>Generated {formatDate(new Date().toISOString())}</p>
      <ReportTable />
      <p>{rows.length} invoices in scope</p>
    </section>
  );
}
