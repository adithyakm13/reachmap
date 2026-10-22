import { list } from "@/api/generated/client";
import { cache } from "@/lib/cache";

export default function ReportsPage() {
  const cached = cache.get("reports");
  const names = cached ?? list();
  return <ul>{String(names)}</ul>;
}
