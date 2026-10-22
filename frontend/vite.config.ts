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
    rolldownOptions: {
      output: {
        // A chunk takes its name from a module (`Mantine.context`): only letters, digits, `_` and `-` stay in the file name, which the
        // static tests and the runner's asset names expect (`assets/<name>-<8 characters>.js`).
        chunkFileNames: (chunk) => `assets/${chunk.name.replace(/[^A-Za-z0-9_-]/g, "_")}-[hash].js`,
        // The entry held React DOM and the router with Mantine and the frame (510.7 kB, over Vite's 500 kB warning). The two libraries that
        // are largest and change least are chunks of their own, so the entry is the frame and Mantine; the limit of the warning stays at its
        // default. Only `node_modules/` paths are matched (the project's own folders are never captured by a vendor group), and the chunks of
        // the pages and of the graph libraries are made by their `import()` as before: nothing here pulls a graph library into a first load.
        codeSplitting: {
          groups: [
            { name: "react", test: /node_modules[\\/](?:react|react-dom|scheduler)[\\/]/, priority: 20 },
            { name: "router", test: /node_modules[\\/](?:react-router|react-router-dom|cookie|set-cookie-parser)[\\/]/, priority: 10 },
          ],
        },
      },
    },
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
