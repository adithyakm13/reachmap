import { Route } from "react-router-dom";

import CasesPage from "./pages/CasesPage";

export const casesRoutes = (
  <>
    <Route path="/cases" element={<CasesPage />} />
  </>
);
