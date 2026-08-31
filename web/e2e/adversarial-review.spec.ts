import { expect, test, type Page } from "@playwright/test";

/**
 * 临时对抗性 E2E（审查用，验证后按 AGENTS.md 清理）：
 * 边界 URL、角色深链门控、空表单防呆、搜索特殊字符、审计业务对象筛选。
 */

async function withRole(page: Page, role: "admin" | "buyer") {
  await page.addInitScript((value) => {
    localStorage.setItem("procurement.demo-role", value);
    localStorage.setItem("caijiatai.theme", "light");
  }, role);
}

test.describe("对抗性边界审查", () => {
  test("采购员深链 admin-only 视图：明示回退，不白屏", async ({ page }) => {
    await withRole(page, "buyer");
    await page.goto("/?view=audit");
    await expect(page.getByRole("alert")).toContainText("不可访问");
    await expect(page.getByText("采购智能协同看板")).toBeVisible();
    await page.goto("/?view=system");
    await expect(page.getByRole("alert")).toContainText("不可访问");
  });

  test("未知任务深链：中文错误态，可重试", async ({ page }) => {
    await withRole(page, "admin");
    await page.goto("/?view=tasks&task=ffffffffffffffffffffffffffffffff");
    await expect(page.getByText("采购任务加载失败")).toBeVisible({ timeout: 15_000 });
  });

  test("垃圾 URL 参数不炸：view/tab/status/page 非法值回落默认", async ({ page }) => {
    await withRole(page, "admin");
    await page.goto("/?view=hack&tab=bogus&status=nope&page=-5");
    await expect(page.getByText("采购智能协同看板")).toBeVisible();
  });

  test("搜索正则特殊字符不炸且能恢复", async ({ page }) => {
    await withRole(page, "buyer");
    await page.goto("/?view=tasks");
    const search = page.getByRole("textbox", { name: "搜索采购任务" });
    await search.fill("(([.*+?^${}(|)])");
    await expect(page.getByText("没有匹配任务")).toBeVisible();
    await search.fill("快递");
    await expect(page.locator(".proc-request-item").first()).toBeVisible();
  });

  test("新建供应商空表单防呆：弹窗保持、错误可见、不写入", async ({ page }) => {
    await withRole(page, "admin");
    await page.goto("/?view=suppliers");
    await page.getByRole("button", { name: "新建供应商" }).first().click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: /保存|创建|确定/ }).first().click();
    await expect(dialog).toBeVisible();
    // 表单校验错误必须可见（内联或 role=alert）
    await expect(dialog.getByRole("alert")).toContainText("名称");
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toHaveCount(0);
  });

  test("审计中心：业务对象筛选（修复验证主战场）", async ({ page }) => {
    await withRole(page, "admin");
    await page.goto("/?view=audit");
    await expect(page.getByRole("heading", { name: "审计日志" })).toBeVisible();
    const businessSelect = page.getByLabel("业务对象类型");
    const totalBadge = page.getByText(/共 \d+ 条/);
    for (const option of ["采购任务", "供应商", "采购订单", "对账单", "发票", "合同"]) {
      await businessSelect.selectOption({ label: option });
      // 切换筛选会更换 queryKey：先经历 data=undefined 的瞬时「共 0 条」，必须轮询读取
      await expect
        .poll(async () => Number((await totalBadge.innerText()).replace(/\D/g, "")), { timeout: 15_000 })
        .toBeGreaterThan(0);
      await expect(page.locator(".proc-audit-row").first()).toBeVisible();
    }
    await businessSelect.selectOption({ label: "全部业务对象" });
    await expect(page.locator(".proc-audit-row").first()).toBeVisible();
  });

  test("统计报表无裸 NaN/undefined 直出", async ({ page }) => {
    await withRole(page, "admin");
    await page.goto("/?view=reports");
    await expect(page.getByText(/状态漏斗/).first()).toBeVisible();
    await expect(page.locator(".proc-main")).not.toContainText(/\bNaN\b/);
    await expect(page.locator(".proc-main")).not.toContainText(/undefined/);
  });
});
