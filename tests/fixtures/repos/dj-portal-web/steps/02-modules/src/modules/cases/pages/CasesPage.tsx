import { useEffect, useState } from "react";

import { fetchCases } from "../api";
import type { CaseSummary } from "../api";

export default function CasesPage() {
  const [cases, setCases] = useState<CaseSummary[]>([]);

  useEffect(() => {
    fetchCases().then(setCases);
  }, []);

  return (
    <section>
      <h1>Cases</h1>
      <ul>
        {cases.map((c) => (
          <li key={c.id}>{c.title}</li>
        ))}
      </ul>
    </section>
  );
}
