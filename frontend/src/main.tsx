// Mantine's sheets of the components this page uses, one by one (its whole sheet is a third larger); a component that is added needs its sheet here
import "@mantine/core/styles/default-css-variables.css";
import "@mantine/core/styles/global.css";
import "@mantine/core/styles/baseline.css";
import "@mantine/core/styles/UnstyledButton.css";
import "@mantine/core/styles/VisuallyHidden.css";
import "@mantine/core/styles/Paper.css";
import "@mantine/core/styles/Text.css";
import "@mantine/core/styles/Title.css";
import "@mantine/core/styles/Stack.css";
import "@mantine/core/styles/Group.css";
import "@mantine/core/styles/SimpleGrid.css";
import "@mantine/core/styles/Badge.css";
import "@mantine/core/styles/Button.css";
import "@mantine/core/styles/Loader.css";
import "@mantine/core/styles/ActionIcon.css";
import "@mantine/core/styles/CloseButton.css";
import "@mantine/core/styles/Card.css";
import "@mantine/core/styles/Alert.css";
import "@mantine/core/styles/Anchor.css";
import "@mantine/core/styles/AppShell.css";
import "@mantine/core/styles/Breadcrumbs.css";
import "@mantine/core/styles/Burger.css";
import "@mantine/core/styles/Checkbox.css";
import "@mantine/core/styles/CheckboxIndicator.css";
import "@mantine/core/styles/InlineInput.css";
import "@mantine/core/styles/Input.css";
import "@mantine/core/styles/Drawer.css";
import "@mantine/core/styles/Modal.css";
import "@mantine/core/styles/ModalBase.css";
import "@mantine/core/styles/Overlay.css";
import "@mantine/core/styles/Kbd.css";
import "@mantine/core/styles/List.css";
import "@mantine/core/styles/Popover.css";
import "@mantine/core/styles/Progress.css";
import "@mantine/core/styles/ScrollArea.css";
import "@mantine/core/styles/SegmentedControl.css";
import "@mantine/core/styles/FloatingIndicator.css";
import "@mantine/core/styles/Skeleton.css";
import "@mantine/core/styles/Table.css";
import "@mantine/core/styles/Tabs.css";
import "@mantine/core/styles/ThemeIcon.css";
import "@mantine/core/styles/Tooltip.css";
import "@mantine/core/styles/Accordion.css";
import "@mantine/core/styles/Notification.css";
import "@mantine/notifications/styles.css";
import "@mantine/spotlight/styles.css";
import "./styles/app.css";
import "./styles/graph.css";
import "./styles/code.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { publishNonce, readNonce } from "./nonce";
import { AppProviders } from "./providers";
import { makeRoutes } from "./routes";
import { startSession } from "./session";
import { applyTheme, storedTheme } from "./storage";

// The token is in the address fragment: it is read here, before anything renders (and again if the fragment changes), kept in memory in
// the client and taken out of the address bar and the history. It is never stored.
const session = startSession(window);
applyTheme(storedTheme());
// The runner's policy allows a style element only with the nonce it wrote into this page.
const nonce = readNonce(document);
publishNonce(nonce);

const router = createBrowserRouter(makeRoutes(session));
const root = document.getElementById("root");
if (root === null) {
  throw new Error("no root element");
}
// React and the router log an error they catch together with its message and the component stack, and a message can hold text of a report: what
// is logged instead is one fixed line (what failed is shown by the page, in fixed words)
const logFixed = (): void => console.error("reachmap: a part of the page could not be drawn");
createRoot(root, { onCaughtError: logFixed, onUncaughtError: logFixed, onRecoverableError: logFixed }).render(
  <StrictMode>
    <AppProviders nonce={nonce}>
      <RouterProvider router={router} onError={logFixed} />
    </AppProviders>
  </StrictMode>,
);
