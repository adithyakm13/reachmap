// The command palette (Cmd or Ctrl and K). It jumps to a report, an area, a finding or a page, and runs the actions of the workspace.
// What is on the page registers its own entries while it is open (`usePaletteItems`); the reports are asked for when the palette opens.
// Every label is plain text, made visible and cut, and every jump is to a path built by `paths.ts` from a name and an id that were checked.

import { useHotkeys } from "@mantine/hooks";
import type { SpotlightActionGroupData } from "@mantine/spotlight";
import { IconArrowRight, IconBolt } from "@tabler/icons-react";
import { createContext, lazy, Suspense, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import type { Client } from "./api/client";
import { reportPath } from "./paths";
import { visible } from "./report/text";
import { W } from "./report/words";

export interface PaletteItem {
  id: string;
  group: string;
  label: string;
  description?: string;
  /** A path of the workspace (made by `paths.ts`), or a function to run. */
  to?: string;
  run?: () => void;
  keywords?: string[];
}

interface Registry {
  set(key: string, items: PaletteItem[]): void;
}

// the palette itself (and the library under it) is a chunk of its own that loads the first time it is asked for: Ctrl or Cmd and K, or the Search
// button, mount it already open. Once it is there its own shortcut takes over.
const PaletteView = lazy(() => import("./PaletteView"));
let requestOpen: (() => void) | null = null;

const RegistryContext = createContext<Registry>({ set: () => undefined });

/** Put entries in the palette while the calling page is open. Pass a memoised array. */
export function usePaletteItems(key: string, items: PaletteItem[]): void {
  const registry = useContext(RegistryContext);
  useEffect(() => {
    registry.set(key, items);
    return () => registry.set(key, []);
  }, [registry, key, items]);
}

const SHOWN = 120;

function text(value: string): string {
  const v = visible(value);
  return v.length > SHOWN ? `${v.slice(0, SHOWN)}...` : v;
}

export function PaletteProvider({ client, children, builtIn }: { client: Client | null; children: ReactNode; builtIn: PaletteItem[] }) {
  const navigate = useNavigate();
  const [registered, setRegistered] = useState<Record<string, PaletteItem[]>>({});
  const [reports, setReports] = useState<PaletteItem[]>([]);
  const registry = useMemo<Registry>(
    () => ({
      set: (key, items) =>
        setRegistered((old) => {
          if (items.length === 0 && !(key in old)) {
            return old;
          }
          const next = { ...old };
          if (items.length === 0) {
            delete next[key];
          } else {
            next[key] = items;
          }
          return next;
        }),
    }),
    [],
  );

  const [mounted, setMounted] = useState(false);
  const [opened, setOpened] = useState(false);
  const openNow = useRef(false);
  const open = useCallback(() => {
    openNow.current = true;
    setMounted(true);
  }, []);
  useEffect(() => {
    requestOpen = open;
    return () => {
      requestOpen = null;
    };
  }, [open]);
  useHotkeys(mounted ? [] : [["mod+K", open, { preventDefault: true }]], []);
  const loadReports = useCallback(() => {
    if (client === null) {
      return;
    }
    client.reports().then(
      (list) =>
        setReports(
          list.slice(0, 50).flatMap((r) => {
            const path = typeof r.name === "string" ? reportPath(r.name) : null;
            return path === null ? [] : [{ id: `report:${r.name}`, group: W.paletteReports, label: r.name, description: W.paletteOpenReport, to: path } as PaletteItem];
          }),
        ),
      () => setReports([]), // the palette still works without the list
    );
  }, [client]);
  // the reports are asked for when the palette is opened, however it was opened (the button or the keys)
  useEffect(() => {
    if (opened) {
      loadReports();
    }
  }, [opened, loadReports]);

  const groups = useMemo<SpotlightActionGroupData[]>(() => {
    const all: PaletteItem[] = [...builtIn, ...Object.values(registered).flat(), ...reports];
    const byGroup = new Map<string, PaletteItem[]>();
    for (const item of all) {
      byGroup.set(item.group, [...(byGroup.get(item.group) ?? []), item]);
    }
    return [...byGroup.entries()].map(([group, items]) => ({
      group,
      actions: items.map((item) => ({
        id: item.id,
        label: text(item.label),
        description: item.description === undefined ? undefined : text(item.description),
        keywords: item.keywords,
        leftSection: item.to !== undefined ? <IconArrowRight size={16} /> : <IconBolt size={16} />,
        onClick: () => {
          if (item.to !== undefined) {
            navigate(item.to);
          }
          item.run?.();
        },
      })),
    }));
  }, [builtIn, registered, reports, navigate]);

  return (
    <RegistryContext.Provider value={registry}>
      {children}
      {mounted ? (
        <Suspense fallback={null}>
          <PaletteView groups={groups} openNow={openNow.current} onOpened={setOpened} />
        </Suspense>
      ) : null}
    </RegistryContext.Provider>
  );
}

export function openPalette(): void {
  requestOpen?.();
}
