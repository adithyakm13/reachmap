import { fetchCustomer, fetchCustomerOrders } from "@/services/customers";

export default function CustomerScreen({ customerId }: { customerId: string }) {
  return (
    <div>
      <pre>{JSON.stringify(fetchCustomer(customerId))}</pre>
      <pre>{JSON.stringify(fetchCustomerOrders(customerId))}</pre>
    </div>
  );
}
