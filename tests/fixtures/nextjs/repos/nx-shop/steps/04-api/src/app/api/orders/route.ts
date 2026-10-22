import { listOrders, createOrder } from "@/lib/orders";
import { warmCache } from "@/lib/session";

warmCache("orders");

export async function GET() {
  return Response.json(listOrders());
}

export async function POST(request: Request) {
  const body = await request.json();
  return Response.json(createOrder(body.totalCents), { status: 201 });
}
