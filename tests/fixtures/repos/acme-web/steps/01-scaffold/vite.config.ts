import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import tsconfigPaths from "vite-tsconfig-paths";

// Aliases come from tsconfig.json through vite-tsconfig-paths.
export default defineConfig({
  plugins: [react(), tsconfigPaths()],
});
