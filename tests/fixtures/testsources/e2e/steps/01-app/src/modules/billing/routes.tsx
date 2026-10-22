import { Route } from "react-router-dom";

import InvoiceDetailPage from "./pages/InvoiceDetailPage";
import InvoiceListPage from "./pages/InvoiceListPage";
import SettlementsPage from "./pages/SettlementsPage";

export const billingRoutes = (
  <>
    <Route path="/billing/invoices" element={<InvoiceListPage />} />
    <Route path="/billing/invoices/:invoiceId" element={<InvoiceDetailPage />} />
    <Route path="/billing/settlements" element={<SettlementsPage />} />
  </>
);
