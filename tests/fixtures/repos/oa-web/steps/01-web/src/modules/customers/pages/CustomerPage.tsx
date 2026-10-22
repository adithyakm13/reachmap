import { getCustomer } from "@/api/generated/client";

export default function CustomerPage() {
  const customer = getCustomer("cus-1");
  return <section>{String(customer)}</section>;
}
