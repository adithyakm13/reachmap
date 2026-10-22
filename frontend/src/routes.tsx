import type { RouteObject } from "react-router-dom";
import type { Client } from "./api/client";
import { createSession, type Session } from "./session";
import { App } from "./App";
import { Loading } from "./components/Boxes";
import { Crash } from "./pages/Crash";
import { NotFound } from "./pages/NotFound";
import { prefetchReports } from "./prefetch";

function isSession(v: Client | null | Session): v is Session {
  return v !== null && typeof (v as Session).subscribe === "function";
}

// The pages are loaded when their route is first visited, each as a chunk of its own: the list of reports, one report (its tabs and drawers,
// and the graph libraries it draws with) and the jobs. The paths are literal: nothing is imported by a name that came from data.

/**
 * The routes of the workspace: the reports, one report (`reports/:name/*`: its tab, and an area or a finding as a drawer over the list, are
 * read from the rest of the address by `pages/ReportPage.tsx`, so that the page stays mounted while a drawer opens and closes), and the jobs.
 */
export function makeRoutes(source: Client | null | Session): RouteObject[] {
  const session = isSession(source) ? source : createSession(source);
  return [
    {
      element: <App session={session} />,
      // shown while the first page is fetched (every page is a chunk loaded when it is first visited)
      hydrateFallbackElement: <Loading rows={5} />,
      errorElement: <Crash />,
      children: [
        {
          // a page that fails to render shows fixed words inside the frame (navigation, theme), not the router's own error page
          errorElement: <Crash />,
          children: [
            { index: true, loader: () => (prefetchReports(session.get()), null), lazy: () => import("./pages/Reports").then((m) => ({ Component: m.Reports })) },
            { path: "reports/:name/*", lazy: () => import("./pages/ReportPage").then((m) => ({ Component: m.ReportPage })) },
            { path: "jobs", lazy: () => import("./pages/Jobs").then((m) => ({ Component: m.Jobs })) },
            { path: "*", element: <NotFound /> },
          ],
        },
      ],
    },
  ];
}
