import { useState } from "react";

export default function ScheduleReportPage() {
  const [cron, setCron] = useState("0 6 * * 1");

  return (
    <section>
      <h1>Schedule a report</h1>
      <input value={cron} onChange={(event) => setCron(event.target.value)} />
    </section>
  );
}
