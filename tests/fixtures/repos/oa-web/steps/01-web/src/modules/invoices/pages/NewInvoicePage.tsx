import { createInvoice } from "../../../api/generated/client";

export default function NewInvoicePage() {
  const submit = () => createInvoice({ customerId: "cus-1", total: { amount: 100, currency: "EUR" } });
  return <button onClick={submit}>Create</button>;
}
