import { fetchOrders } from "@/services/orders";

export default function OrdersScreen() {
  return <pre>{JSON.stringify(fetchOrders())}</pre>;
}
