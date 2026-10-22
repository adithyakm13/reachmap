import InvoicesScreen from "./screens/InvoicesScreen";
import InvoiceScreen from "./screens/InvoiceScreen";
import StatusScreen from "./screens/StatusScreen";

export const routes = (
  <>
    <Route path="/invoices" element={<InvoicesScreen />} />
    <Route path="/invoices/:invoiceId" element={<InvoiceScreen />} />
    <Route path="/status" element={<StatusScreen />} />
  </>
);
