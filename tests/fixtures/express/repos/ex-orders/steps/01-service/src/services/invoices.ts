export function invoicesFor(customer: unknown) {
  return [{ customer, number: "INV-1" }];
}

export function oneInvoice(id: string) {
  return { id, number: "INV-1" };
}
