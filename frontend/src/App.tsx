import { ActionIcon, AppShell, Anchor, Box, Breadcrumbs, Burger, Button, Group, Kbd, Modal, Paper, Skeleton, Stack, Table, Text, Title, Tooltip, useMantineColorScheme } from "@mantine/core";
import { IconBriefcase2, IconKeyboard, IconLayoutSidebarLeftCollapse, IconLayoutSidebarLeftExpand, IconListDetails, IconMoon, IconRoute, IconSearch, IconSun } from "@tabler/icons-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { isReportName } from "./api/client";
import { C } from "./components/Content";
import { ClientContext } from "./context";
import { useAsync } from "./hooks";
import { isTyping } from "./keys";
import { PaletteProvider, openPalette, type PaletteItem } from "./palette";
import { JOBS, ROOT, reportPath } from "./paths";
import { visible } from "./report/text";
import { TAB_LABELS, W } from "./report/words";
import { useSession, type Session } from "./session";

function NoToken() {
  return (
    <Paper p="xl" role="alert" maw={560} mx="auto" mt="xl">
      <Stack gap="xs">
        <Title order={1}>{W.noTokenTitle}</Title>
        <Text size="sm">{W.noToken}</Text>
      </Stack>
    </Paper>
  );
}

/** The two ways to say the theme: the button has one fixed label and says its state with `aria-pressed`. */
function ThemeButton() {
  const { colorScheme, setColorScheme } = useMantineColorScheme();
  const dark = colorScheme === "dark";
  return (
    <Tooltip label={W.themeButton}>
      <ActionIcon aria-label={W.themeButton} aria-pressed={dark} onClick={() => setColorScheme(dark ? "light" : "dark")} size="lg">
        {dark ? <IconMoon size={18} /> : <IconSun size={18} />}
      </ActionIcon>
    </Tooltip>
  );
}

const SHORTCUTS: { keys: string[]; what: string; where: string }[] = [
  { keys: ["Ctrl", "K"], what: W.helpPalette, where: W.helpEverywhere },
  { keys: ["?"], what: W.helpThisList, where: W.helpEverywhere },
  { keys: ["Esc"], what: W.helpClose, where: W.helpEverywhere },
  { keys: ["j"], what: W.helpNext, where: W.helpAreas },
  { keys: ["k"], what: W.helpPrevious, where: W.helpAreas },
  { keys: ["Space"], what: W.helpSelect, where: W.helpAreas },
  { keys: ["Enter"], what: W.helpOpen, where: W.helpAreas },
  { keys: ["t"], what: W.helpTest, where: W.helpAreas },
  { keys: ["s"], what: W.helpSample, where: W.helpAreas },
  { keys: ["x"], what: W.helpSkip, where: W.helpAreas },
];

function Help({ opened, onClose }: { opened: boolean; onClose: () => void }) {
  return (
    <Modal opened={opened} onClose={onClose} title={W.helpTitle} size="md" centered closeButtonProps={{ "aria-label": W.dialogClose }}>
      <Table>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>{W.helpKeys}</Table.Th>
            <Table.Th>{W.helpWhat}</Table.Th>
            <Table.Th>{W.helpWhere}</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {SHORTCUTS.map((s) => (
            <Table.Tr key={s.keys.join("+")}>
              <Table.Td w={120}>
                <Group gap={4} wrap="nowrap">
                  {s.keys.map((k) => (
                    <Kbd key={k}>{k}</Kbd>
                  ))}
                </Group>
              </Table.Td>
              <Table.Td>{s.what}</Table.Td>
              <Table.Td>{s.where}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      <Text size="xs" c="dimmed" mt="md">
        {W.helpCredit}
      </Text>
    </Modal>
  );
}

/** The path as crumbs: Reports, the report, the tab, and an area or a finding when its drawer is open. */
function Crumbs() {
  const { pathname } = useLocation();
  const parts = pathname.split("/").filter((p) => p !== "");
  const crumbs: { label: React.ReactNode; to: string | null }[] = [];
  if (parts[0] === "jobs") {
    crumbs.push({ label: W.navJobs, to: null });
  } else {
    crumbs.push({ label: W.navReports, to: parts.length > 0 ? ROOT : null });
  }
  if (parts[0] === "reports" && parts.length > 1) {
    let name = parts[1];
    try {
      name = decodeURIComponent(name);
    } catch {
      // keep the segment as it is
    }
    const base = isReportName(name) ? reportPath(name) : null;
    const tab = parts[2];
    crumbs.push({ label: <C text={name} />, to: parts.length > 2 ? base : null });
    if (tab !== undefined && Object.hasOwn(TAB_LABELS, tab)) {
      crumbs.push({ label: TAB_LABELS[tab], to: parts.length > 3 && base !== null ? `${base}/${tab}` : null });
    }
    if (parts.length > 3) {
      crumbs.push({ label: tab === "findings" ? W.findingTitle : W.areaTitle, to: null });
    }
  }
  return (
    <nav aria-label={W.breadcrumbLabel} className="rm-crumbs">
    <Breadcrumbs separator="/">
      {crumbs.map((c, i) =>
        c.to !== null ? (
          <Anchor key={i} component={Link} to={c.to} size="sm" className="rm-crumb">
            {c.label}
          </Anchor>
        ) : (
          <Text key={i} size="sm" fw={500} component="span" className="rm-crumb" aria-current={i === crumbs.length - 1 ? "page" : undefined}>
            {c.label}
          </Text>
        ),
      )}
      </Breadcrumbs>
    </nav>
  );
}

function Navigation({ rail }: { rail: boolean }) {
  const items = [
    { to: ROOT, label: W.navReports, icon: <IconListDetails size={18} />, end: true },
    { to: JOBS, label: W.navJobs, icon: <IconBriefcase2 size={18} />, end: false },
  ];
  return (
    <nav aria-label={W.navLabel}>
      <Stack gap={2}>
        {items.map((item) => (
          <NavLink key={item.to} to={item.to} end={item.end} className="rm-nav-link" aria-label={rail ? item.label : undefined} title={rail ? item.label : undefined}>
            {item.icon}
            {rail ? null : <span>{item.label}</span>}
          </NavLink>
        ))}
      </Stack>
    </nav>
  );
}

/** The frame of every page: the sidebar, the top bar, the palette and the shortcuts. Without a session token there is nothing to show but how to get one. */
export function App({ session }: { session: Session }) {
  const { client, version } = useSession(session);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [rail, setRail] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const navigateHelp = useCallback(() => setHelpOpen((o) => !o), []);
  const workspace = useAsync(async () => (client === null ? null : (await client.repos()).workspace), `workspace:${version}`);
  const { setColorScheme, colorScheme } = useMantineColorScheme();

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "?" && !event.ctrlKey && !event.metaKey && !event.altKey && !isTyping(event.target)) {
        event.preventDefault();
        navigateHelp();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navigateHelp]);

  const builtIn = useMemo<PaletteItem[]>(
    () => [
      { id: "go:reports", group: W.paletteGo, label: W.navReports, to: ROOT, keywords: ["list", "home"] },
      { id: "go:jobs", group: W.paletteGo, label: W.navJobs, to: JOBS, keywords: ["queue", "analysis"] },
      { id: "do:start", group: W.paletteActions, label: W.jobsFormTitle, description: W.paletteStartHint, to: JOBS, keywords: ["run", "analyse", "analyze"] },
      { id: "do:theme", group: W.paletteActions, label: W.themeButton, run: () => setColorScheme(colorScheme === "dark" ? "light" : "dark"), keywords: ["theme", "light", "dark"] },
      { id: "do:help", group: W.paletteActions, label: W.helpTitle, run: () => setHelpOpen(true), keywords: ["keys", "shortcuts", "help"] },
    ],
    [setColorScheme, colorScheme],
  );

  const name = workspace.state.status === "ready" ? workspace.state.data : null;
  return (
    <ClientContext.Provider value={client}>
      <a className="rm-skip" href="#main">
        {W.skipLink}
      </a>
      <PaletteProvider client={client} builtIn={builtIn}>
        <AppShell
          header={{ height: 52 }}
          navbar={{ width: rail ? 60 : 232, breakpoint: "sm", collapsed: { mobile: !mobileOpen } }}
          padding={0}
        >
          <AppShell.Header className="rm-header">
            <Group h="100%" px="md" gap="sm" wrap="nowrap" justify="space-between">
              <Group gap="sm" wrap="nowrap" miw={0}>
                <Burger opened={mobileOpen} onClick={() => setMobileOpen((o) => !o)} hiddenFrom="sm" size="sm" aria-label={W.menuToggle} />
                <Crumbs />
              </Group>
              <Group gap={6} wrap="nowrap">
                <Button variant="default" leftSection={<IconSearch size={14} />} rightSection={<Kbd size="xs">Ctrl K</Kbd>} onClick={openPalette} className="rm-search-button" aria-label={W.palettePlaceholder}>
                  <span className="rm-search-label">{W.searchButton}</span>
                </Button>
                <Tooltip label={W.helpTitle}>
                  <ActionIcon aria-label={W.helpTitle} onClick={() => setHelpOpen(true)} size="lg">
                    <IconKeyboard size={18} />
                  </ActionIcon>
                </Tooltip>
                <ThemeButton />
              </Group>
            </Group>
          </AppShell.Header>
          <AppShell.Navbar className="rm-navbar" p="sm" component="div">
            <Stack justify="space-between" h="100%" gap="md">
              <Stack gap="md">
                <Group gap="xs" wrap="nowrap" px={rail ? 0 : 6} justify={rail ? "center" : "flex-start"}>
                  <IconRoute size={20} aria-hidden="true" />
                  {rail ? null : (
                    <Box miw={0}>
                      <Text fw={600} size="sm" lh={1.2}>
                        {W.appName}
                      </Text>
                      {name !== null ? (
                        <Text size="xs" c="dimmed" truncate="end" title={visible(name)}>
                          <C text={name} />
                        </Text>
                      ) : client !== null ? (
                        <Skeleton height={10} width={90} mt={4} />
                      ) : null}
                    </Box>
                  )}
                </Group>
                <Navigation rail={rail} />
              </Stack>
              <Group visibleFrom="sm" justify={rail ? "center" : "flex-end"}>
                <Tooltip label={rail ? W.navExpand : W.navCollapse}>
                  <ActionIcon aria-label={rail ? W.navExpand : W.navCollapse} aria-expanded={!rail} onClick={() => setRail((r) => !r)}>
                    {rail ? <IconLayoutSidebarLeftExpand size={18} /> : <IconLayoutSidebarLeftCollapse size={18} />}
                  </ActionIcon>
                </Tooltip>
              </Group>
            </Stack>
          </AppShell.Navbar>
          <AppShell.Main id="main" tabIndex={-1}>
            <div className="rm-main">{client === null ? <NoToken /> : <Outlet key={version} />}</div>
          </AppShell.Main>
        </AppShell>
        <Help opened={helpOpen} onClose={() => setHelpOpen(false)} />
      </PaletteProvider>
    </ClientContext.Provider>
  );
}
