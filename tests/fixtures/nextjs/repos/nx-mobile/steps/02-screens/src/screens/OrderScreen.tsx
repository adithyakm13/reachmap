import { fetchOrder, cancelOrder } from "@/services/orders";

export default function OrderScreen({ orderId }: { orderId: string }) {
  return (
    <div>
      <pre>{JSON.stringify(fetchOrder(orderId))}</pre>
      <button onClick={() => cancelOrder(orderId)}>Cancel</button>
    </div>
  );
}
