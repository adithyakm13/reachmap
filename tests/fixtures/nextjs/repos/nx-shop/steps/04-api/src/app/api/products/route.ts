import { formatPrice } from "@/lib/format";

export async function GET() {
  return Response.json([{ slug: "mug", price: formatPrice(1200) }]);
}
