import { useEffect, useState } from "react";

import { StatusBadge } from "../../../components/StatusBadge";
import { truncate } from "@/lib/format";
import { parseLegacyDate } from "@/lib/dates";
import { fetchCases } from "../api";
import type { CaseRow } from "../api";
import { CaseTable } from "../legacy/CaseTable";

export default function CaseListPage() {
  const [cases, setCases] = useState<CaseRow[]>([]);

  useEffect(() => {
    fetchCases().then(setCases);
  }, []);

  return (
    <section>
      <h1>Cases</h1>
      <CaseTable />
      <ul>
        {cases.map((row) => (
          <li key={row.id}>
            {truncate(row.title, 24)} opened {parseLegacyDate(row.opened).toDateString()}{" "}
            <StatusBadge status={row.status} />
          </li>
        ))}
      </ul>
    </section>
  );
}
