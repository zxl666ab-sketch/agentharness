import { expect, test, type Page } from "@playwright/test";

/**
 * 采价台 E2E 冒烟：关键路径 UI 层（只读 + 防呆，不产生业务写入）。
 * 数据前提：后端 demo seed 存在（≥1 个任务、订单/发票/合同各 ≥1 条）。
 */

/** 管理员视角进入（演示角色存 localStorage；主题固定浅色避免快照歧义）。 */
async function asAdmin(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem("procurement.demo-role", "admin");
    localStorage.setItem("caijiatai.theme", "light");
  });
}

async function asBuyer(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem("procurement.demo-role", "buyer");
    localStorage.setItem("caijiatai.theme", "light");
  });
}

test.describe("采价台关键路径冒烟", () => {
  test("工作台健康门与驾驶舱", async ({ page }) => {
    await asBuyer(page);
    await page.goto("/");
    await expect(page.getByRole("navigation", { name: "采购工作台主导航" })).toBeVisible();
    await expect(page.getByText("采购智能协同看板")).toBeVisible();
    // 采购主线导航项齐全（采购员可见 9 项中的主线 7 项）
    for (const item of ["工作台", "采购任务", "人工审核", "合同中心", "采购订单", "发票中心", "统计报表"]) {
      await expect(page.getByRole("button", { name: item, exact: true })).toBeVisible();
    }
    // 最近任务表格有数据行（seed 任务）
    await expect(page.getByRole("table", { name: "最近采购任务" }).locator("tbody tr").first()).toBeVisible();
  });

  test("全视图导览（采购员 7 项 + 管理员专属 2 项）", async ({ page }) => {
    await asBuyer(page);
    const views: Array<[string, string | RegExp]> = [
      ["tasks", /采购任务/],
      ["reviews", /等待审核|人工审核/],
      ["contracts", /合同列表|合同中心/],
      ["orders", /共 \d+ 张|采购订单/],
      ["invoices", /发票列表|发票中心/],
      ["reports", /状态漏斗|统计报表/],
      ["suppliers", /供应商管理|新建供应商/],
      ["ai", /AI 任务中心|执行队列/],
    ];
    for (const [view, pattern] of views) {
      await page.goto(`/?view=${view}`);
      await expect(page.getByText(pattern).first()).toBeVisible();
    }
    // 管理员专属：审计日志 / 系统信息
    await asAdmin(page);
    await page.goto("/?view=audit");
    await expect(page.getByRole("heading", { name: "审计日志" })).toBeVisible();
    await page.goto("/?view=system");
    await expect(page.getByRole("heading", { name: "系统信息" })).toBeVisible();
  });

  test("任务详情四个标签切换", async ({ page }) => {
    await asAdmin(page);
    await page.goto("/?view=tasks");
    // 自动选中第一个任务；等报价列表渲染（面板标题为 h2）
    await expect(page.getByRole("heading", { name: "供应商报价" })).toBeVisible();
    const nav = page.getByRole("navigation", { name: "采购任务视图" });
    const tabContent = page.locator(".proc-tab-content");
    await nav.getByRole("button", { name: "供应商比价" }).click();
    await expect(page).toHaveURL(/tab=compare/);
    await expect(tabContent).not.toBeEmpty();
    await nav.getByRole("button", { name: "审批报告" }).click();
    await expect(page).toHaveURL(/tab=report/);
    await expect(tabContent).not.toBeEmpty();
    await nav.getByRole("button", { name: "运行审计" }).click();
    await expect(page).toHaveURL(/tab=audit/);
    await expect(tabContent).not.toBeEmpty();
    await nav.getByRole("button", { name: "报价与复核" }).click();
    await expect(page.getByRole("heading", { name: "供应商报价" })).toBeVisible();
    // quotes 是默认 tab：URL 不携带 tab 参数（writeWorkbenchUrl 省略默认值）
    await expect(page).toHaveURL(/view=tasks&task=/);
    await expect(page).not.toHaveURL(/tab=(compare|report|audit)/);
  });

  test("合同与发票：列表到详情联动", async ({ page }) => {
    await asAdmin(page);
    await page.goto("/?view=contracts");
    const firstContract = page.locator(".proc-list-row").first();
    await firstContract.click();
    await expect(page.locator(".proc-detail-head")).toBeVisible();
    await expect(page.locator(".proc-detail-head").getByText(/CT-RFQ/)).toBeVisible();

    await page.goto("/?view=invoices");
    const firstInvoice = page.locator(".proc-list-row").first();
    await firstInvoice.click();
    await expect(page.locator(".proc-detail-head")).toBeVisible();
    // 三单匹配对比表或解析中提示，二者必居其一
    await expect(page.getByText(/三单匹配对比|正在加载完整三单对比/).first()).toBeVisible();
  });

  test("任务搜索：空态与恢复", async ({ page }) => {
    await asBuyer(page);
    await page.goto("/?view=tasks");
    const search = page.getByRole("textbox", { name: "搜索采购任务" });
    await search.fill("绝对不存在的任务名称xyz");
    await expect(page.getByText("没有匹配任务")).toBeVisible();
    await search.fill("");
    await expect(page.locator(".proc-request-item").first()).toBeVisible();
  });

  test("角色视角裁剪与删除防呆", async ({ page }) => {
    await asBuyer(page);
    await page.goto("/");
    // 审批人只保留 工作台/人工审核/采购订单/AI 任务中心
    await page.getByRole("combobox", { name: "演示角色" }).selectOption({ label: "审批人" });
    await expect(page.getByRole("button", { name: "合同中心", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "人工审核", exact: true })).toBeVisible();
    // 回管理员，删除弹窗只开不关数据
    await page.getByRole("combobox", { name: "演示角色" }).selectOption({ label: "管理员" });
    await page.getByRole("button", { name: "采购任务", exact: true }).click();
    await page.locator(".proc-request-item").first().hover();
    await page.getByRole("button", { name: /删除任务/ }).first().click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(page.getByRole("dialog")).toContainText("删除");
    await page.getByRole("button", { name: "取消" }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    // 任务未被删除：列表仍有行
    await expect(page.locator(".proc-request-item").first()).toBeVisible();
  });

  test("Agent 会话面板：拖拽调宽可持久化，且不挤占结构化工作区", async ({ page }) => {
    await asAdmin(page);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/?view=tasks");
    const body = page.locator(".proc-task-body");
    const resizer = page.getByTestId("conversation-resizer");
    await expect(body).toBeVisible();
    await expect(resizer).toBeVisible();

    const firstTrack = () => body.evaluate((el) => Number(getComputedStyle(el).gridTemplateColumns.split(" ")[0].replace("px", "")));
    const convWidth = () => page.locator(".proc-conversation-shell").evaluate((el) => Math.round(el.getBoundingClientRect().width));
    await expect.poll(firstTrack).toBe(300);

    // 指针拖宽：分隔条跟手，偏好写入 localStorage
    const box = (await resizer.boundingBox())!;
    await page.mouse.move(box.x + box.width / 2, box.y + 200);
    await page.mouse.down();
    await page.mouse.move(box.x + 180, box.y + 200, { steps: 10 });
    await page.mouse.up();
    await expect.poll(convWidth).toBeGreaterThan(380);
    await expect.poll(() => page.evaluate(() => Number(localStorage.getItem("procurement.conv-width")))).toBeGreaterThan(380);
    await page.reload();
    await expect.poll(convWidth).toBeGreaterThan(380);

    // 双击复位到默认宽度
    await resizer.dblclick();
    await expect.poll(convWidth).toBe(300);
  });

  test("会话面板偏好宽度不饿死工作区（1280 两列 / 1080 堆叠）", async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("procurement.conv-width", "520"));
    await asAdmin(page);
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.goto("/?view=tasks");
    await expect(page.locator(".proc-task-body")).toBeVisible();
    // 工作区保住可用宽度，页面不出现横向滚动
    await expect(page.locator(".proc-structured-workspace").first()).toBeVisible();
    await expect.poll(() => page.locator(".proc-structured-workspace").evaluate((el) => Math.round(el.getBoundingClientRect().width))).toBeGreaterThanOrEqual(500);
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(1);

    // 窄到 1080：会话栏堆叠到工作区上方，分隔条隐藏
    await page.setViewportSize({ width: 1080, height: 900 });
    const geometry = await page.evaluate(() => {
      const conv = document.querySelector(".proc-conversation-shell")!.getBoundingClientRect();
      const ws = document.querySelector(".proc-structured-workspace")!.getBoundingClientRect();
      const handle = document.querySelector(".proc-conv-resizer")!;
      return { stacked: ws.top > conv.bottom - 2, handleVisible: getComputedStyle(handle).display !== "none" };
    });
    expect(geometry.stacked).toBe(true);
    expect(geometry.handleVisible).toBe(false);
  });
});
