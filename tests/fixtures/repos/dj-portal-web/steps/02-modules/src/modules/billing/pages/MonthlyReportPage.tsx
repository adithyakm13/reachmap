import { useEffect, useState } from "react";

import { fetchMonthlyReport } from "../api";
import type { Report } from "../api";

export default function MonthlyReportPage() {
  const [report, setReport] = useState<Report | null>(null);

  useEffect(() => {
    fetchMonthlyReport().then(setReport);
  }, []);

  return <section>{report?.reference}</section>;
}
