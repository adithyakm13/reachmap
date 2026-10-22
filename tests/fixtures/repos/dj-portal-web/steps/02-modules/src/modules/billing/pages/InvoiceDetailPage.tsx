import { useEffect, useState } from "react";

import { fetchInvoice, voidInvoice } from "../api";
import type { Invoice } from "../api";

export default function InvoiceDetailPage() {
  const [invoice, setInvoice] = useState<Invoice | null>(null);

  useEffect(() => {
    fetchInvoice("1").then(setInvoice);
  }, []);

  return (
    <section>
      <h1>{invoice?.number}</h1>
      <button onClick={() => voidInvoice("1")}>Void</button>
    </section>
  );
}
