export function allCustomers() {
  return [{ id: "c1", name: "Acme" }];
}

export function oneCustomer(id: string) {
  return { id, name: "Acme" };
}

export function ordersFor(customerId: string) {
  return [{ customerId, ref: "A-1" }];
}
