import { test, expect } from "@playwright/test";
import path from "node:path";
import fs from "node:fs";
import { execFileSync } from "node:child_process";

const shotDir =
  process.env.SCREENSHOT_DIR || path.join(process.cwd(), "..", "output", "playwright");

test.beforeAll(() => {
  fs.mkdirSync(shotDir, { recursive: true });
});

test("run inspector exposes real execution detail without layout overflow", async ({
  page,
}, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(String(error)));

  await page.goto("/");
  await expect(page.locator(".product-mark h1")).toHaveText("Agent Harness");

  const health = await page.request.get("/api/health");
  expect(health.ok()).toBeTruthy();
  const healthBody = await health.json();
  expect(healthBody.status).toBe("ok");
  for (const method of ["POST", "PUT", "PATCH", "DELETE"] as const) {
    const response = await page.request.fetch("/api/runs", { method });
    expect(response.status(), method).toBe(405);
  }

  const shell = page.locator(".app-shell");
  await expect(shell).toBeVisible();
  const shellBox = await shell.boundingBox();
  expect(shellBox?.width).toBeGreaterThan(300);
  expect(shellBox?.height).toBeGreaterThan(300);

  const isMobile = testInfo.project.name === "mobile";
  const mobileTabs = page.getByTestId("mobile-tabs");
  if (isMobile) {
    await expect(mobileTabs).toBeVisible();
    await mobileTabs.getByRole("button", { name: "Runs" }).click();
  }

  const runItems = page.locator(".run-item");
  await expect(runItems.first()).toBeVisible({ timeout: 12_000 });
  const apiRuns = (await (await page.request.get("/api/runs?limit=100")).json()) as Array<{
    id: string;
  }>;
  let toolRunId: string | null = null;
  for (const run of apiRuns) {
    const messages = (await (
      await page.request.get(`/api/runs/${run.id}/messages`)
    ).json()) as Array<{ tool_calls?: Array<{ name?: string }> | null }>;
    if (messages.some((message) => message.tool_calls?.some((call) => call.name === "read_file"))) {
      toolRunId = run.id;
      break;
    }
  }
  expect(toolRunId).toBeTruthy();
  const toolRunTestId = `run-item-${toolRunId}`;
  await expect(page.getByTestId(toolRunTestId)).toBeVisible({ timeout: 12_000 });
  const runsBeforeLiveUpdate = await runItems.count();
  const repoRoot = path.resolve(process.cwd(), "..");
  const python = path.join(
    repoRoot,
    ".venv",
    process.platform === "win32" ? "Scripts/python.exe" : "bin/python"
  );
  execFileSync(
    python,
    [
      "-m",
      "agentharness.cli.main",
      "run",
      "[fake:text]playwright live update",
      "--provider",
      "fake",
      "--approval",
      "auto",
      "--data-dir",
      String(healthBody.data_dir),
    ],
    { cwd: repoRoot, stdio: "ignore" }
  );
  await expect
    .poll(() => runItems.count(), { timeout: 4_000 })
    .toBeGreaterThan(runsBeforeLiveUpdate);
  await page.getByTestId(toolRunTestId).click();

  if (isMobile) {
    await mobileTabs.getByRole("button", { name: "Timeline" }).click();
  }
  await expect(page.getByTestId("run-overview")).toBeVisible();
  await expect(page.getByTestId("timeline-row").first()).toBeVisible({
    timeout: 12_000,
  });

  await page.getByRole("button", { name: "Tools", exact: true }).click();
  const toolRows = page.getByTestId("timeline-row");
  await expect(toolRows.first()).toBeVisible({ timeout: 12_000 });
  await toolRows.last().click();

  if (isMobile) {
    await mobileTabs.getByRole("button", { name: "Inspector" }).click();
  }
  await expect(page.getByTestId("inspector")).toBeVisible();
  await expect(page.getByTestId("tool-detail")).toBeVisible({ timeout: 8_000 });
  const toolText = await page.getByTestId("tool-detail").innerText();
  expect(/arguments/i.test(toolText)).toBeTruthy();
  expect(/result/i.test(toolText)).toBeTruthy();

  await page.getByRole("button", { name: "Run", exact: true }).click();
  await expect(page.getByRole("heading", { name: /^Checkpoint/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: /^Run tree/ })).toBeVisible();
  await page.getByRole("button", { name: "Context", exact: true }).click();
  await expect(page.getByRole("heading", { name: /^Conversation context/ })).toBeVisible();

  await page.screenshot({
    path: path.join(shotDir, `${testInfo.project.name}-inspector.png`),
    fullPage: true,
  });

  const overflow = await page.evaluate(() => ({
    body: document.body.scrollWidth - window.innerWidth,
    root: document.documentElement.scrollWidth - window.innerWidth,
  }));
  expect(overflow.body).toBeLessThanOrEqual(1);
  expect(overflow.root).toBeLessThanOrEqual(1);

  if (!isMobile) {
    const runsBox = await page.getByTestId("runs-panel").boundingBox();
    const timelineBox = await page.getByTestId("timeline-panel").boundingBox();
    const inspectorBox = await page.getByTestId("inspector-panel").boundingBox();
    expect(runsBox && timelineBox && inspectorBox).toBeTruthy();
    if (runsBox && timelineBox && inspectorBox) {
      expect(runsBox.x + runsBox.width).toBeLessThanOrEqual(timelineBox.x + 2);
      expect(timelineBox.x + timelineBox.width).toBeLessThanOrEqual(inspectorBox.x + 2);
    }
  }

  expect(errors, `page errors: ${errors.join("; ")}`).toEqual([]);
});
