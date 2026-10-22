// One report: the header and a banner, then tabs whose state is in the address (`/reports/<name>`, `/reports/<name>/areas`, and an area or a
// finding as a drawer over its list, `/reports/<name>/areas/<id>`), so that a link or a reload comes back to the same place. A run that did not
// complete shows its header and its records and nothing else: no counts, no tabs, no list, no form.

import { Badge, Stack, Tabs, VisuallyHidden } from "@mantine/core";
import { IconAlertTriangle } from "@tabler/icons-react";
import { useMemo } from "react";
import { Navigate, useNavigate, useParams } from "react-router-dom";
import { isReportName } from "../api/client";
import { SectionBoundary } from "../components/Boxes";
import { RecordsList } from "../components/Records";
import { ReportHeader, RunBanner } from "../components/Header";
import { AreasTab } from "../components/tabs/AreasTab";
import { AuthorTab } from "../components/tabs/AuthorTab";
import { BranchesTab } from "../components/tabs/BranchesTab";
import { CrossTab } from "../components/tabs/CrossTab";
import { FindingsTab } from "../components/tabs/FindingsTab";
import { ModelNotesTab } from "../components/tabs/ModelNotesTab";
import { NotesTab } from "../components/tabs/NotesTab";
import { Overview } from "../components/tabs/Overview";
import { RecordsTab } from "../components/tabs/RecordsTab";
import { TestsTab } from "../components/tabs/TestsTab";
import { FailureBox, Loading } from "../components/Boxes";
import { useTitle } from "../hooks";
import { usePaletteItems, type PaletteItem } from "../palette";
import { TAB_ORDER, areaPath, findingPath, isTab, isTargetId, reportPath, tabPath, type Tab } from "../paths";
import { allAreas, allFindings } from "../report/view";
import { NOTE_PAGE } from "../report/notewords";
import { TAB_LABELS, W } from "../report/words";
import { NotFound } from "./NotFound";
import { ReportContext } from "./reportContext";
import { findingIdsOf, useReport } from "./useReport";

/** The tab and the drawer an address names: the part after `/reports/<name>/`. Null when it names nothing the page has. */
export function parseTail(rest: string | undefined): { tab: Tab; id: string | null } | null {
  const parts = (rest ?? "").split("/").filter((p) => p !== "");
  if (parts.length === 0) {
    return { tab: "overview", id: null };
  }
  const [tab, id, ...more] = parts;
  if (!isTab(tab) || more.length > 0) {
    return null;
  }
  if (id === undefined) {
    return { tab, id: null };
  }
  if ((tab === "areas" && isTargetId(id) && id.startsWith("a-")) || (tab === "findings" && isTargetId(id) && id.startsWith("f-"))) {
    return { tab, id };
  }
  return null;
}

/** One report, with its sections in the order a QA reader needs them. */
export function ReportPage() {
  const { name, "*": rest } = useParams();
  const { state, reload, env } = useReport(name);
  const navigate = useNavigate();
  useTitle(`${W.reportTitle} - ${W.appName}`);
  const loaded = state.status === "ready" ? state.data : null;
  const view = loaded === null ? null : loaded.view;
  const valid = name !== undefined && isReportName(name);

  const items = useMemo<PaletteItem[]>(() => {
    if (view === null || !view.completed || !valid) {
      return [];
    }
    const tabs: PaletteItem[] = TAB_ORDER.flatMap((t) => {
      const to = tabPath(name, t);
      return to === null || (t === "notes" && view.notes.length === 0) || (t === "model-notes" && view.modelNotes === null) ? [] : [{ id: `tab:${t}`, group: W.paletteTabs, label: TAB_LABELS[t], to }];
    });
    const areas: PaletteItem[] = allAreas(view).flatMap((a) => {
      const to = areaPath(name, a.id);
      return to === null ? [] : [{ id: `area:${a.index}`, group: W.paletteAreas, label: a.label, description: `${a.repo} ${a.kind}`, to }];
    });
    const findings: PaletteItem[] = allFindings(view).flatMap((f) => {
      const to = findingPath(name, f.id);
      return to === null ? [] : [{ id: `finding:${f.number}`, group: W.paletteFindings, label: `F${f.number} ${f.title}`, description: f.kind, to }];
    });
    return [...tabs, ...areas, ...findings];
  }, [view, name, valid]);
  usePaletteItems("report", items);

  const ctx = useMemo(
    () =>
      loaded === null || !valid
        ? null
        : { name, report: loaded.report, view: loaded.view, env, reload, findingIds: findingIdsOf(loaded.view) },
    [loaded, valid, name, env, reload],
  );

  if (!valid) {
    return <NotFound />;
  }
  const parsed = parseTail(rest);
  if (parsed === null) {
    return <NotFound />;
  }
  if (state.status === "loading") {
    return (
      <Stack gap="md" className="rm-page">
        <Loading rows={6} />
      </Stack>
    );
  }
  if (state.status === "error" || ctx === null) {
    return (
      <Stack gap="md" className="rm-page">
        <h1>{W.reportTitle}</h1>
        {state.status === "error" ? <FailureBox error={state.error} retry={reload} /> : null}
      </Stack>
    );
  }
  const v = ctx.view;
  const blocking = v.records.filter((r) => r.blocking).length;
  const recordsPath = tabPath(name, "records");
  if (!v.completed) {
    // a run that did not complete shows its header and its records and nothing else: no count, no list
    if (parsed.tab !== "overview") {
      return <Navigate to={reportPath(name) ?? "/"} replace />;
    }
    return (
      <ReportContext.Provider value={ctx}>
        <Stack gap="md" className="rm-page">
          <ReportHeader h={v.header} />
          <RunBanner h={v.header} blocking={blocking} recordsPath={null} />
          <SectionBoundary>
            <RecordsList records={v.records} completed={false} />
          </SectionBoundary>
        </Stack>
      </ReportContext.Provider>
    );
  }
  if ((parsed.tab === "notes" && v.notes.length === 0) || (parsed.tab === "model-notes" && v.modelNotes === null)) {
    return <Navigate to={reportPath(name) ?? "/"} replace />;
  }
  const tabs = TAB_ORDER.filter((t) => (t !== "notes" || v.notes.length > 0) && (t !== "model-notes" || v.modelNotes !== null));
  const countOf = (t: Tab): number | null => {
    const counts = v.header.counts;
    const n = (k: string) => counts?.find(([key]) => key === k)?.[1] ?? null;
    return t === "areas" ? n("areas") : t === "findings" ? n("findings") : t === "records" ? n("records") : t === "branches" ? v.matrix.length : t === "model-notes" ? (v.modelNotes === null ? null : v.modelNotes.notes.length) : null;
  };
  const panel = (() => {
    switch (parsed.tab) {
      case "overview":
        return <Overview />;
      case "areas":
        return <AreasTab drawerId={parsed.id} />;
      case "findings":
        return <FindingsTab drawerId={parsed.id} />;
      case "branches":
        return <BranchesTab />;
      case "cross-repo":
        return <CrossTab />;
      case "tests":
        return <TestsTab />;
      case "records":
        return <RecordsTab />;
      case "author":
        return <AuthorTab />;
      case "notes":
        return <NotesTab />;
      case "model-notes":
        return <ModelNotesTab />;
    }
  })();
  return (
    <ReportContext.Provider value={ctx}>
      <Stack gap="md" className="rm-page">
        <ReportHeader h={v.header} />
        <RunBanner h={v.header} blocking={blocking} recordsPath={recordsPath} />
        <Tabs
          value={parsed.tab}
          onChange={(t) => {
            const to = t !== null && isTab(t) ? tabPath(name, t) : null;
            if (to !== null) {
              navigate(to);
            }
          }}
          keepMounted={false}
          className="rm-tabs"
          aria-label={W.reportTitle}
        >
          <Tabs.List className="rm-tabs-list">
            {tabs.map((t) => {
              const n = countOf(t);
              // a store of model notes that could not be read has no count to give: the tab says so in a warning colour instead
              const unreadable = t === "model-notes" && v.modelNotes !== null && v.modelNotes.records.some((r) => r.blocking);
              return (
                <Tabs.Tab
                  key={t}
                  value={t}
                  rightSection={
                    unreadable ? (
                      <Badge variant="light" color="orange" size="sm" title={NOTE_PAGE.tabUnreadable} data-tab-warning="model-notes">
                        <IconAlertTriangle size={12} aria-hidden="true" />
                        <VisuallyHidden>{NOTE_PAGE.tabUnreadable}</VisuallyHidden>
                      </Badge>
                    ) : n !== null ? (
                      <Badge variant={t === "records" && blocking > 0 ? "filled" : "default"} color={t === "records" && blocking > 0 ? "red.8" : undefined} size="sm">
                        {n}
                      </Badge>
                    ) : null
                  }
                >
                  {TAB_LABELS[t]}
                </Tabs.Tab>
              );
            })}
          </Tabs.List>
          <Tabs.Panel value={parsed.tab} pt="md">
            <SectionBoundary key={parsed.tab}>{panel}</SectionBoundary>
          </Tabs.Panel>
        </Tabs>
      </Stack>
    </ReportContext.Provider>
  );
}

