import { apiGet, apiDelete } from "@/api/http";
import { ORDERS_URL, ORDER_URL } from "@/api/endpoints";

export function fetchOrders() {
  return apiGet(ORDERS_URL);
}

export function fetchOrder(orderId: string) {
  return apiGet(ORDER_URL(orderId.trim()));
}

export function cancelOrder(orderId: string) {
  return apiDelete(ORDER_URL(orderId));
}
