import { apiGet } from "@/api/http";
import { customerOrdersUrl, customerUrl } from "@/api/endpoints";

export function fetchCustomerOrders(customerId: string) {
  return apiGet(customerOrdersUrl(customerId));
}

export function fetchCustomer(customerId: string) {
  return apiGet(customerUrl(customerId));
}
