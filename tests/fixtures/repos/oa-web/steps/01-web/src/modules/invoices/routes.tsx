import { Route } from "react-router-dom";
import InvoiceDetailPage from "./pages/InvoiceDetailPage";
import InvoiceListPage from "./pages/InvoiceListPage";
import NewInvoicePage from "./pages/NewInvoicePage";

export const invoiceRoutes = (
  <>
    <Route path="/invoices" element={<InvoiceListPage />} />
    <Route path="/invoices/new" element={<NewInvoicePage />} />
    <Route path="/invoices/:invoiceId" element={<InvoiceDetailPage />} />
  </>
);
