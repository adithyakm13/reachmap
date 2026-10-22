import { Route } from "react-router-dom";

import InvoiceDetailPage from "./pages/InvoiceDetailPage";
import InvoiceListPage from "./pages/InvoiceListPage";
import MonthlyReportPage from "./pages/MonthlyReportPage";

export const billingRoutes = (
  <>
    <Route path="/billing/invoices" element={<InvoiceListPage />} />
    <Route path="/billing/invoices/:invoiceId" element={<InvoiceDetailPage />} />
    <Route path="/billing/reports/monthly" element={<MonthlyReportPage />} />
  </>
);
