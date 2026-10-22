import { Money } from "@/components/Money";

export default function ReportsHomePage() {
  return (
    <main>
      <h1>Reports</h1>
      <p>Revenue <Money cents={990000} /></p>
    </main>
  );
}
