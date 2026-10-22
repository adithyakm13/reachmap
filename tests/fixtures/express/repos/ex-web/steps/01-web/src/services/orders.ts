import { apiGet } from "@/api/http";
import { ORDERS_URL, orderUrl } from "@/api/endpoints";

export function fetchOrders() {
  return apiGet(ORDERS_URL);
}

export function fetchOrder(orderId: string) {
  return apiGet(orderUrl(orderId));
}
