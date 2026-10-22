export interface Order {
  id: string;
  ref: string;
  total: number;
}

const orders: Order[] = [{ id: "1", ref: "A-1", total: 1000 }];

export function findOrders(): Order[] {
  return orders;
}

export function findOrder(id: string): Order {
  return orders.find((o) => o.id === id) as Order;
}

export function saveOrder(order: Order): Order {
  orders.push(order);
  return order;
}

export function findLines(orderId: string) {
  return [{ orderId, sku: "S-1", qty: 1 }];
}

export function saveLine(orderId: string, line: { sku: string; qty: number }) {
  return { orderId, ...line };
}

export function countOrders(): number {
  return orders.length;
}
