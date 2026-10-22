import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The workspace is served by `reachmap serve` from the same origin, under `/`: assets are named by a hash of their content, there is
// no inline script and no inline style of its own (the runner's policy allows `'self'` and the style nonce it writes into the page) and
// nothing is loaded from another host. Each route and the graph libraries are chunks of their own, each with its own style sheet.
export default defineConfig({
  plugins: [react()],
  base: "/",
  build: {
    outDir: "dist",
    emptyOutDir: true,
    assetsDir: "assets",
    sourcemap: false,
    cssCodeSplit: true,
    assetsInlineLimit: 0,
    modulePreload: { polyfill: false },
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    setupFiles: ["src/test/setup.ts"],
    restoreMocks: true,
    // the pages are chunks loaded on first use and the graph libraries are heavy to start: a loaded machine needs more than the defaults
    testTimeout: 30000,
    css: false,
  },
});
