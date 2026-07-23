import { defineConfig, devices } from "@playwright/test";
import path from "node:path";

const externalBaseUrl = process.env.PLAYWRIGHT_BASE_URL;
const port = 8765;
const python = path.resolve(
  process.cwd(),
  "..",
  ".venv",
  process.platform === "win32" ? "Scripts/python.exe" : "bin/python"
);
const serverScript = path.resolve(process.cwd(), "..", "scripts", "e2e_web_server.py");

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  use: {
    baseURL: externalBaseUrl || `http://127.0.0.1:${port}`,
    trace: "off",
  },
  webServer: externalBaseUrl
    ? undefined
    : {
        command: `"${python}" "${serverScript}" --port ${port}`,
        url: `http://127.0.0.1:${port}/api/health`,
        reuseExistingServer: false,
        timeout: 30_000,
      },
  projects: [
    {
      name: "desktop",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } },
    },
    {
      name: "tablet",
      use: { ...devices["Desktop Chrome"], viewport: { width: 768, height: 1024 } },
    },
    {
      name: "mobile",
      use: { viewport: { width: 390, height: 844 } },
    },
  ],
});
