export interface Order {
  id: string;
  status: "open" | "paid" | "cancelled";
  totalCents: number;
}

const ORDERS: Order[] = [{ id: "o-1", status: "open", totalCents: 1200 }];

export function listOrders(): Order[] {
  return ORDERS;
}

export function getOrder(id: string): Order | undefined {
  return ORDERS.find((order) => order.id === id);
}

export function createOrder(totalCents: number): Order {
  const order: Order = { id: `o-${ORDERS.length + 1}`, status: "open", totalCents };
  ORDERS.push(order);
  return order;
}

export function cancelOrder(id: string): boolean {
  const order = getOrder(id);
  if (!order) {
    return false;
  }
  order.status = "cancelled";
  return true;
}
