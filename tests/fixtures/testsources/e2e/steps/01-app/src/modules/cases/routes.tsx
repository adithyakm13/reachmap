import { Route } from "react-router-dom";

import CaseDetailPage from "./pages/CaseDetailPage";
import CaseListPage from "./pages/CaseListPage";
import CaseNotePage from "./pages/CaseNotePage";
import NewCasePage from "./pages/NewCasePage";

export const casesRoutes = (
  <>
    <Route path="/cases" element={<CaseListPage />} />
    <Route path="/cases/new" element={<NewCasePage />} />
    <Route path="/cases/:caseId" element={<CaseDetailPage />} />
    <Route path="/cases/:caseId/notes/:noteId" element={<CaseNotePage />} />
  </>
);
