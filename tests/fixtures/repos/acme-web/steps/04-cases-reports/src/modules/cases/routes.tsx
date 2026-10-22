import { Route } from "react-router-dom";

import InvoiceDetailPage from "@/modules/billing/pages/InvoiceDetailPage";
import CaseDetailPage from "./pages/CaseDetailPage";
import CaseListPage from "./pages/CaseListPage";

export const casesRoutes = (
  <>
    <Route path="/cases" element={<CaseListPage />} />
    <Route path="/cases/:caseId" element={<CaseDetailPage />} />
    <Route path="/cases/:caseId/invoices/:invoiceId" element={<InvoiceDetailPage />} />
  </>
);
