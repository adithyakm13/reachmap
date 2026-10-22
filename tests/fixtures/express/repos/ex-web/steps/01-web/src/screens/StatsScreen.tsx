import { fetchStats, fetchInvoices } from "@/services/admin";

export default function StatsScreen() {
  return (
    <div>
      <pre>{JSON.stringify(fetchStats())}</pre>
      <pre>{JSON.stringify(fetchInvoices())}</pre>
    </div>
  );
}
