import { Money } from "@/components/Money";

export default function CaseDetailPage() {
  return (
    <main>
      <h1>Case</h1>
      <p>Open balance <Money cents={4200} /></p>
    </main>
  );
}
