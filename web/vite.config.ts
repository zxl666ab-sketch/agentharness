import { createHash } from "node:crypto";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const apiSchemaVersion = 19;
const webRoot = dirname(fileURLToPath(import.meta.url));

function sourceFiles(path: string): string[] {
  return readdirSync(path, { withFileTypes: true }).flatMap((entry) => {
    const child = join(path, entry.name);
    return entry.isDirectory() ? sourceFiles(child) : [child];
  });
}

function computeWebBuildId(): string {
  const files = [
    ...sourceFiles(join(webRoot, "src")),
    join(webRoot, "index.html"),
    join(webRoot, "package.json"),
    join(webRoot, "package-lock.json"),
    join(webRoot, "tsconfig.app.json"),
    join(webRoot, "vite.config.ts"),
  ].sort();
  const hash = createHash("sha256");
  for (const path of files) {
    hash.update(relative(webRoot, path).replaceAll("\\", "/"));
    hash.update("\0");
    hash.update(readFileSync(path, "utf8").replaceAll("\r\n", "\n"));
    hash.update("\0");
  }
  return `sha256:${hash.digest("hex").slice(0, 20)}`;
}

const webBuildId = computeWebBuildId();

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
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    setupFiles: ["./test/setup.ts"],
  },
}));
