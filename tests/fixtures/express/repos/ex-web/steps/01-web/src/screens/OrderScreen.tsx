import { fetchOrder } from "@/services/orders";

export default function OrderScreen({ orderId }: { orderId: string }) {
  return <pre>{JSON.stringify(fetchOrder(orderId))}</pre>;
}
