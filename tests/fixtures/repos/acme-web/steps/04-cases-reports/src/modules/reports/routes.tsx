import React from "react";
import { Route } from "react-router-dom";

import ReportsHomePage from "./pages/ReportsHomePage";

const ScheduleReportPage = React.lazy(() => import("./pages/ScheduleReportPage"));

export const reportsRoutes = (
  <>
    <Route path="/reports" element={<ReportsHomePage />} />
    <Route
      path="/reports/schedule"
      element={
        <React.Suspense fallback={null}>
          <ScheduleReportPage />
        </React.Suspense>
      }
    />
  </>
);
