import { test, expect } from "@playwright/test";
import path from "node:path";
import fs from "node:fs";
import { execFileSync } from "node:child_process";

const shotDir =
  process.env.SCREENSHOT_DIR || path.join(process.cwd(), "..", "output", "playwright");

test.beforeAll(() => {
  fs.mkdirSync(shotDir, { recursive: true });
});

test("运行检查器在临时数据上保持低请求、清晰层级和响应式布局", async ({
  page,
}, testInfo) => {
  const pageErrors: string[] = [];
  const consoleErrors: string[] = [];
  const apiRequests: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname.startsWith("/api/")) apiRequests.push(`${url.pathname}${url.search}`);
  });

  await page.goto("/");
  await expect(page.locator(".product-mark h1")).toHaveText("Agent Harness");

  const health = await page.request.get("/api/health");
  expect(health.ok()).toBeTruthy();
  const healthBody = await health.json();
  expect(healthBody.status).toBe("ok");
  expect(String(healthBody.data_dir)).toContain("agentharness-web-e2e-");
  for (const method of ["POST", "PUT", "PATCH", "DELETE"] as const) {
    const response = await page.request.fetch("/api/runs", { method });
    expect(response.status(), method).toBe(405);
  }

  const shell = page.locator(".app-shell");
  await expect(shell).toBeVisible();
  const shellBox = await shell.boundingBox();
  expect(shellBox?.width).toBeGreaterThan(300);
  expect(shellBox?.height).toBeGreaterThan(300);

  const isMobile = (page.viewportSize()?.width ?? 1440) <= 860;
  const mobileTabs = page.getByTestId("mobile-tabs");
  if (isMobile) {
    await expect(mobileTabs).toBeVisible();
    await mobileTabs.getByRole("button", { name: "检查器" }).click();
  }
  await expect(page.getByRole("heading", { name: /^身份/ })).toBeVisible({
    timeout: 12_000,
  });
  if (isMobile) await mobileTabs.getByRole("button", { name: "运行" }).click();

  await expect(page.locator(".run-item").first()).toBeVisible({ timeout: 12_000 });
  await expect(page.locator(".live-state")).toContainText("实时", { timeout: 12_000 });
  await page.waitForTimeout(250);
  const initialRequests = [...apiRequests];
  const messageRequests = initialRequests.filter((value) => /\/messages(?:\?|$)/.test(value));
  const checkpointRequests = initialRequests.filter((value) => /\/checkpoint(?:\?|$)/.test(value));
  const streamRequest = initialRequests.find((value) => value.startsWith("/api/stream?"));
  expect(messageRequests.length).toBeLessThanOrEqual(1);
  expect(checkpointRequests.length).toBeLessThanOrEqual(1);
  expect(streamRequest).toBeTruthy();
  const streamAfter = Number(new URL(`http://test${streamRequest}`).searchParams.get("after"));
  expect(streamAfter).toBeGreaterThan(0);
  console.log(
    "PERF_EVIDENCE",
    JSON.stringify({
      project: testInfo.project.name,
      initialRequests,
      duplicateCounts: countRequests(initialRequests),
      sseAfter: streamAfter,
    })
  );

  const apiRuns = (await (await page.request.get("/api/runs?limit=500")).json()) as Array<{
    id: string;
    parent_run_id?: string | null;
    user_summary?: string | null;
  }>;
  const toolRun = apiRuns.find((run) => run.user_summary?.includes("[fake:tools]read_file"));
  const parentRun = apiRuns.find((run) => run.user_summary?.includes("[fake:tools]delegate"));
  const longRun = apiRuns.find((run) => run.user_summary?.includes("long trace fixture"));
  expect(toolRun && parentRun && longRun).toBeTruthy();

  const runList = page.getByTestId("run-list");
  const runsBeforeLiveUpdate = Number(await runList.getAttribute("data-run-count"));
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
    .poll(async () => Number(await runList.getAttribute("data-run-count")), {
      timeout: 6_000,
    })
    .toBeGreaterThan(runsBeforeLiveUpdate);

  const search = page.getByRole("textbox", { name: "搜索运行" });
  await search.fill("read_file");
  await page.getByTestId(`run-item-${toolRun!.id}`).click();
  if (isMobile) await mobileTabs.getByRole("button", { name: "追踪" }).click();
  await expect(page.getByTestId("run-overview")).toBeVisible();

  // Goal 4: re-selecting a terminal run must issue zero new run-scoped requests.
  await page.waitForTimeout(500); // let the first toolRun load settle
  if (isMobile) await mobileTabs.getByRole("button", { name: "运行" }).click();
  await search.fill("long trace fixture");
  await page.getByTestId(`run-item-${longRun!.id}`).click();
  if (isMobile) await mobileTabs.getByRole("button", { name: "追踪" }).click();
  await expect(page.getByTestId("run-overview")).toBeVisible();
  await page.waitForTimeout(500); // let the longRun load settle
  const beforeRevisit = apiRequests.length;
  if (isMobile) await mobileTabs.getByRole("button", { name: "运行" }).click();
  await search.fill("read_file");
  await page.getByTestId(`run-item-${toolRun!.id}`).click();
  if (isMobile) await mobileTabs.getByRole("button", { name: "追踪" }).click();
  await expect(page.getByTestId("run-overview")).toBeVisible();
  await page.waitForTimeout(600);
  const revisitRequests = apiRequests
    .slice(beforeRevisit)
    .filter(
      (value) =>
        value.includes(`/api/runs/${toolRun!.id}`) || value.startsWith("/api/sessions/")
    );
  expect(revisitRequests, `revisit issued: ${revisitRequests.join(", ")}`).toEqual([]);

  await page.getByRole("button", { name: "工具", exact: true }).click();
  const toolRows = page.getByTestId("timeline-row");
  await expect(toolRows.first()).toBeVisible({ timeout: 12_000 });
  await toolRows.last().click();

  if (isMobile) await mobileTabs.getByRole("button", { name: "检查器" }).click();
  await expect(page.getByTestId("tool-detail")).toBeVisible({ timeout: 8_000 });
  const toolText = await page.getByTestId("tool-detail").innerText();
  expect(toolText).toContain("参数");
  expect(toolText).toContain("结果");
  await page
    .getByTestId("inspector")
    .getByRole("tab", { name: "运行", exact: true })
    .click();
  await expect(page.getByRole("heading", { name: /^检查点/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: /^运行树/ })).toBeVisible();
  await page
    .getByTestId("inspector")
    .getByRole("tab", { name: "上下文", exact: true })
    .click();
  await expect(page.getByRole("heading", { name: /^对话上下文/ })).toBeVisible();

  if (isMobile) await mobileTabs.getByRole("button", { name: "运行" }).click();
  await search.fill("delegate");
  await page.getByTestId(`run-item-${parentRun!.id}`).click();
  const childRun = apiRuns.find((run) => run.parent_run_id === parentRun!.id);
  expect(childRun).toBeTruthy();

  // Goal 7: the Inspector run-tree child row navigates to the child run on click.
  if (isMobile) await mobileTabs.getByRole("button", { name: "检查器" }).click();
  await page
    .getByTestId("inspector")
    .getByRole("tab", { name: "运行", exact: true })
    .click();
  const treeChild = page.getByTestId(`tree-run-${childRun!.id}`);
  await expect(treeChild).toBeVisible({ timeout: 12_000 });
  await treeChild.click();
  await expect.poll(() => new URL(page.url()).searchParams.get("run")).toBe(childRun!.id);

  // Re-select the parent and verify the Timeline child row also navigates.
  if (isMobile) await mobileTabs.getByRole("button", { name: "运行" }).click();
  await search.fill("delegate");
  await page.getByTestId(`run-item-${parentRun!.id}`).click();
  if (isMobile) await mobileTabs.getByRole("button", { name: "追踪" }).click();
  await page.getByRole("button", { name: "全部", exact: true }).click();
  const childRow = page.locator('[data-trace-kind="child_run"]');
  await expect(childRow).toBeVisible({ timeout: 12_000 });
  await childRow.click();
  await expect.poll(() => new URL(page.url()).searchParams.get("run")).toBe(childRun!.id);

  if (isMobile) await mobileTabs.getByRole("button", { name: "运行" }).click();
  await search.fill("long trace fixture");
  await page.getByTestId(`run-item-${longRun!.id}`).click();
  if (isMobile) await mobileTabs.getByRole("button", { name: "追踪" }).click();
  await expect(page.locator(".event-count")).toContainText(/1[12]\d\d 行/, {
    timeout: 12_000,
  });
  const longEvents = (await (
    await page.request.get(`/api/runs/${longRun!.id}/events?limit=5000`)
  ).json()) as Array<{ run_seq: number }>;
  const lastRunSeq = Math.max(...longEvents.map((event) => event.run_seq));
  expect(await page.getByTestId("timeline-row").count()).toBeLessThan(100);
  const scroller = page.locator('.timeline-list[data-testid="virtuoso-scroller"]');
  await scroller.evaluate((element) => element.scrollTo({ top: element.scrollHeight }));
  await expect(page.locator(`[data-run-seq="${lastRunSeq}"]`)).toBeVisible({
    timeout: 8_000,
  });

  await page.screenshot({
    path: path.join(shotDir, `${testInfo.project.name}-inspector.png`),
    fullPage: true,
  });
  const overflow = await page.evaluate(() => ({
    body: document.body.scrollWidth - window.innerWidth,
    root: document.documentElement.scrollWidth - window.innerWidth,
    rootScroll: document.documentElement.scrollWidth,
    rootClient: document.documentElement.clientWidth,
  }));
  expect(overflow.body).toBeLessThanOrEqual(1);
  expect(overflow.root).toBeLessThanOrEqual(1);
  // Goal 8: no horizontal overflow on either viewport — scrollWidth must equal clientWidth.
  expect(overflow.rootScroll).toBe(overflow.rootClient);

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

  expect(pageErrors, `page errors: ${pageErrors.join("; ")}`).toEqual([]);
  expect(consoleErrors, `console errors: ${consoleErrors.join("; ")}`).toEqual([]);
});

function countRequests(requests: string[]): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const request of requests) {
    const pathName = request.split("?", 1)[0];
    counts[pathName] = (counts[pathName] || 0) + 1;
  }
  return counts;
}
