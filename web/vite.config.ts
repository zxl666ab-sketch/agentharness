import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const apiSchemaVersion = 4;
const webBuildId = new Date().toISOString();

export default defineConfig(({ command }) => ({
  plugins: [
    react(),
    {
      name: "agentharness-build-meta",
      generateBundle() {
        this.emitFile({
          type: "asset",
          fileName: "build-meta.json",
          source: JSON.stringify({
            web_build_id: webBuildId,
            api_schema_version: apiSchemaVersion,
          }),
        });
      },
    },
  ],
  define: {
    __AGENTHARNESS_WEB_BUILD_ID__: JSON.stringify(webBuildId),
    __AGENTHARNESS_API_SCHEMA_VERSION__: JSON.stringify(apiSchemaVersion),
    __AGENTHARNESS_ENFORCE_WEB_BUILD_ID__: JSON.stringify(command === "build"),
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8741",
    },
  },
  build: {
    outDir: "../src/agentharness/web_dist",
    emptyOutDir: true,
    sourcemap: false,
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
  },
}));
