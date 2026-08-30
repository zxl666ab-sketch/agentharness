import { defineConfig } from "@playwright/test";

/**
 * E2E 冒烟（关键路径 UI 层）：锁定「导航/视图/联动/筛选/角色/防呆」跨页流程。
 * 约定：
 * - 复用已在跑的 dev server（5173）；没有时自动拉起。
 * - workers=1：所有用例共享同一后端 seed 数据，顺序执行互不污染。
 * - 依赖真实后端（8741）的只读面；不触发解析/审批等写操作（全链路数据流
 *   由后端 Python E2E 覆盖，且解析依赖外部模型可用性，不宜放进 UI 冒烟）。
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: "list",
  timeout: 30_000,
  use: {
    baseURL: "http://localhost:5173",
    locale: "zh-CN",
    viewport: { width: 1440, height: 900 },
  },
  webServer: {
    command: "npm run dev",
    url: "http://localhost:5173",
    reuseExistingServer: true,
    timeout: 60_000,
  },
});
