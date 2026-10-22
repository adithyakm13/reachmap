import { MantineProvider } from "@mantine/core";
import { lazy, Suspense, type ReactNode } from "react";
import { colorSchemeManager } from "./storage";
import { theme } from "./theme";

// The toasts are drawn by a component that loads after the first paint (the store they are shown from is `toast.tsx`'s and is small).
const Toasts = lazy(() => import("@mantine/notifications").then((m) => ({ default: m.Notifications })));

/**
 * The Mantine provider of the workspace and its toasts. The theme is light unless dark was chosen (`storage.ts`). `nonce` is the style nonce
 * the runner wrote into the page (`nonce.ts`): Mantine writes its theme variables into style elements and gives them this nonce, so the
 * policy of the runner (`style-src 'self' 'nonce-...'`) allows them and nothing else that is inline. The toasts are at the bottom left, away
 * from the form of a drawer, which opens on the right.
 */
export function AppProviders({ children, nonce }: { children: ReactNode; nonce?: string }) {
  return (
    <MantineProvider theme={theme} colorSchemeManager={colorSchemeManager} defaultColorScheme="light" getStyleNonce={nonce === undefined ? undefined : () => nonce}>
      <Suspense fallback={null}>
        <Toasts position="bottom-left" limit={4} autoClose={5000} containerWidth={380} />
      </Suspense>
      {children}
    </MantineProvider>
  );
}
