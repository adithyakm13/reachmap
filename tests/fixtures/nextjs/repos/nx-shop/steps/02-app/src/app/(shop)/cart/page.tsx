import Frame from "@/components/layout";
import { listOrders } from "@/lib/orders";

export default function CartPage() {
  const orders = listOrders();
  return <Frame>{orders.length} open orders</Frame>;
}
