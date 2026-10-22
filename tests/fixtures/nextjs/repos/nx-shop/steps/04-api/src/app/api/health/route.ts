import { buildRoute } from "@/lib/route";
import { withLogging } from "@/utils/middleware";

export async function GET() {
  const path = buildRoute("/health", {});
  return Response.json({ ok: true, note: withLogging(path) });
}
