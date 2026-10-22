


import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { StatusBadge } from "../../../components/StatusBadge";
import { fetchCase } from "../api";
import type { CaseRow } from "../api";

export default function CaseDetailPage() {
  const { caseId = "" } = useParams();
  const [row, setRow] = useState<CaseRow | null>(null);

  useEffect(() => {
    fetchCase(caseId).then(setRow);
  }, [caseId]);

  if (!row) {
    return <p>Loading</p>;
  }
  return (
    <section>
      <h1>{row.title}</h1>
      <StatusBadge status={row.status} />
      <Link to={`/cases/${row.id}/invoices/latest`}>Latest invoice</Link>
    </section>
  );
}
