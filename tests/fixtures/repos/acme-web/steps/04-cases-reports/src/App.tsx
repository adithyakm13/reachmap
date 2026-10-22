import { Routes } from "react-router-dom";

import { billingRoutes } from "@/modules/billing/routes";
import { casesRoutes } from "@/modules/cases/routes";
import { reportsRoutes } from "@/modules/reports/routes";

export function App() {
  return (
    <Routes>
      {billingRoutes}
      {casesRoutes}
      {reportsRoutes}
    </Routes>
  );
}
