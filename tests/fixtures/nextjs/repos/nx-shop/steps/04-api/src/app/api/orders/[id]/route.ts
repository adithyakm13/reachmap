import { getOrder, cancelOrder } from "@/lib/orders";

export async function GET(_request: Request, { params }: { params: { id: string } }) {
  const order = getOrder(params.id);
  return order ? Response.json(order) : new Response("not found", { status: 404 });
}

export async function DELETE(_request: Request, { params }: { params: { id: string } }) {
  return Response.json({ cancelled: cancelOrder(params.id) });
}
