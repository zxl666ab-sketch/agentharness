import { expect, test } from "@playwright/test";
import { resolve } from "node:path";

/**
 * 全链路 E2E（@slow）：建任务 → AI 解析 → 需求确认 → 字段复核 → 比价 →
 * 提交审批 → 订单（发货/收货）→ 上传发票 → 三单匹配处理 → 核销 → 对账 → 付款。
 *
 * 约束：
 * - 依赖外部模型可用（解析与发票识别是真实 LLM 调用），因此默认跳过，
 *   用 `npm run e2e:full` 显式运行；冒烟见 smoke.spec.ts。
 * - 会在共享的后端数据里写入一条真实任务/订单/发票（演示环境可接受）。
 */

const ROOT = resolve(process.cwd(), "..", "output");
const QUOTES = [
  resolve(ROOT, "procurement-demo-e2e", "华东优包报价单.xlsx"),
  resolve(ROOT, "procurement-demo-e2e", "云帆供应报价单.xlsx"),
];
// 发票号全局唯一（重复登记会被后端拒绝）：预生成一批，运行时挑未使用的
const INVOICE_CANDIDATES = Array.from({ length: 10 }, (_, index) => {
  const no = `20269999${String(index + 1).padStart(4, "0")}`;
  return { no, file: resolve(process.cwd(), "e2e", "fixtures", `invoice-e2e-${no}.xlsx`) };
});
const REQUIREMENT = "采购 PE 快递袋 10,000 个，尺寸 250mm×350mm，厚度 60 微米，白色，单色印刷，10 天内送达，需要开票";

test.skip(process.env.E2E_FULL !== "1", "全链路用例依赖外部模型，设置 E2E_FULL=1 后运行（npm run e2e:full）");

test("全链路：建任务 → 解析 → 确认 → 比价 → 审批 → 订单 → 发票 → 付款", async ({ page }) => {
  test.setTimeout(1200_000);

  await page.addInitScript(() => {
    localStorage.setItem("procurement.demo-role", "admin");
    localStorage.setItem("caijiatai.theme", "light");
    localStorage.removeItem("procurement.conv-width");
  });

  // ── 1. 新建采购对话：描述 + 两份报价 ─────────────────────────────
  await page.goto("/?view=tasks");
  await page.getByRole("button", { name: "新建采购对话" }).click();
  await page.getByRole("textbox", { name: "采购目标" }).fill(REQUIREMENT);
  await page.getByTestId("conversation-upload").setInputFiles(QUOTES);
  await expect(page.locator(".proc-compose-count.ok")).toHaveText(/^2 \/ 50 份$/);
  await page.getByRole("button", { name: "开始解析报价" }).click();

  // 提交后进入新任务详情（URL 出现新的 task id）
  await page.waitForURL(/view=tasks&task=/, { timeout: 30_000 });
  const taskId = new URL(page.url()).searchParams.get("task")!;

  // ── 2. AI 解析：需求字段与报价落地（真实模型，等待上限 3 分钟）────
  const itemName = page.getByRole("textbox", { name: "物料名称" });
  await expect(itemName).not.toHaveValue(/待识别|^\s*$/, { timeout: 240_000 });
  await expect(page.getByRole("heading", { name: "供应商报价" })).toBeVisible();
  // 两份报价都解析出字段（报价列表 2 项）
  await expect(page.locator(".proc-quote-item")).toHaveCount(2);

  // ── 3. 保存人工确认 ──────────────────────────────────────────────
  await page.getByRole("button", { name: "保存人工确认" }).click();
  await expect(page.getByText("采购需求已人工确认").first()).toBeVisible();

  // ── 4. 字段复核：逐项确认低置信度字段，直到可以开始比价 ───────────
  const startCompare = page.locator(".proc-analysis-bar").getByRole("button", { name: "开始比价" });
  for (let round = 0; round < 30; round += 1) {
    if (await startCompare.isEnabled()) break;
    const confirm = page.locator(".proc-field-row.needs-review .proc-field-editor button").first();
    if ((await confirm.count()) === 0) break;
    await confirm.click();
    await page.waitForTimeout(900);
  }
  await expect(startCompare).toBeEnabled({ timeout: 30_000 });
  await startCompare.click();

  // ── 5. 比价快照：需求确认会触发 Agent 自动分析，与手动比价存在竞态；
  // 自动那次失败时 UI 允许再次点击「开始比价」——按用户路径重试直到出快照
  const snapshotMark = page.getByText("规则推荐", { exact: true });
  await expect(async () => {
    // count() 而非 isVisible()：后者在多匹配时抛 strict 异常，会被 toPass 吞成无限重试
    if ((await snapshotMark.count()) > 0) return;
    const again = page.locator(".proc-analysis-bar").getByRole("button", { name: "开始比价" });
    if ((await again.count()) === 1 && (await again.isEnabled())) await again.click();
    await expect(snapshotMark.first()).toBeVisible({ timeout: 8_000 });
  }).toPass({ timeout: 300_000, intervals: [6_000] });
  await page.getByRole("button", { name: "提交供应商审批" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("正式选定供应商");
  await dialog.getByRole("checkbox").check();
  await dialog.getByRole("button", { name: "确认选定" }).click();

  // ── 6. 批准后进入履约阶段：发货 → 收货（收满自动派生对账单）──────
  await expect(page.getByText("供应商已人工批准").first()).toBeVisible({ timeout: 120_000 });
  await page.getByRole("button", { name: /前往订单履约|查看订单履约/ }).click();
  await expect(page).toHaveURL(/view=orders&.*order_task=/);
  const orderCard = page.locator(".proc-order-card").first();
  await expect(orderCard).toContainText("待发货", { timeout: 60_000 });

  await orderCard.getByRole("button", { name: "标记发货" }).click();
  await expect(orderCard.getByText(/已发货|部分收货/)).toBeVisible({ timeout: 30_000 });

  await orderCard.getByRole("button", { name: /确认收货|继续收货/ }).click();
  const receiveDialog = page.getByRole("dialog");
  await expect(receiveDialog).toContainText("登记本批收货");
  await receiveDialog.getByRole("button", { name: "登记本批收货" }).click();
  await expect(orderCard.getByText("已收货")).toBeVisible({ timeout: 30_000 });
  // 收满后对账单自动派生（未对账）
  await expect(page.locator(".proc-settlement-section").getByText("未对账").first()).toBeVisible({ timeout: 30_000 });

  // ── 7. 上传发票，等 Agent 解析与三单匹配（真实模型）──────────────
  await orderCard.getByRole("button", { name: /上传发票/ }).click();
  await expect(page).toHaveURL(/view=invoices&.*invoice_order=/, { timeout: 15_000 });
  // 跨中心深链会把订单下拉预选为本订单；等预选完成再传（否则 orderId 为空被拒）
  const orderSelect = page.getByRole("combobox", { name: "选择采购订单" });
  await expect(orderSelect).toHaveValue(/.+/, { timeout: 20_000 });
  const orderId = await orderSelect.inputValue();
  const usedNos = new Set(((await (await page.request.get("/api/procurement/invoices?limit=100")).json()).items || []).map((row) => row.invoice_no));
  const invoice = INVOICE_CANDIDATES.find((item) => !usedNos.has(item.no));
  if (!invoice) throw new Error("E2E 发票号池已耗尽，请补充 fixtures");
  await page.getByTestId("invoice-upload").setInputFiles(invoice.file);

  // 用后端事实定位新发票（列表排序不含新发票时也不误点旧发票）
  let invoiceNo = "";
  await expect.poll(async () => {
    const res = await page.request.get("/api/procurement/invoices?limit=100");
    const body = await res.json();
    const hit = (body.items || []).find((row) => row.order_id === orderId);
    if (hit) invoiceNo = hit.invoice_no;
    return hit ? hit.status : "";
  }, { timeout: 180_000, intervals: [2_500] }).toMatch(/REGISTERED|MATCHED|DIFF_HOLD|RECONCILED/);

  await page.locator(".proc-list-row").filter({ hasText: invoiceNo }).first().click();
  const detailStatus = page.locator(".proc-detail-head .proc-status");
  await expect(detailStatus).toHaveText(/已匹配待核销|差异挂起|已核销/, { timeout: 60_000 });

  // ── 8. 差异处理到核销：DIFF_HOLD 强制通过（allow-once，推到已匹配）→ 核销 ──
  const statusText = await detailStatus.innerText();
  if (statusText.includes("差异挂起")) {
    await page.getByRole("button", { name: "强制通过", exact: true }).click();
    const forceDialog = page.getByRole("dialog");
    await forceDialog.getByRole("textbox").fill("E2E 全链路验证：差异已知（样例发票），人工确认放行");
    await forceDialog.getByRole("checkbox").check();
    await forceDialog.getByRole("button", { name: "确认强制通过" }).click();
  }
  // 强制通过/初始匹配后都是「已匹配」：显式核销才能进入付款
  await expect(detailStatus).toHaveText(/已匹配/, { timeout: 60_000 });
  await page.getByRole("button", { name: "核销", exact: true }).click();
  await expect(detailStatus).toHaveText(/已核销/, { timeout: 30_000 });

  // ── 9. 对账与付款（深链聚焦本任务订单，全新加载取最新结算状态）────
  await page.goto(`/?view=orders&order_task=${taskId}`);
  const settlementSection = page.locator(".proc-settlement-section");
  const settle = settlementSection.getByRole("button", { name: "确认对账" }).first();
  await settle.click({ timeout: 30_000 });
  await expect(settlementSection.getByText("已对账").first()).toBeVisible({ timeout: 30_000 });

  await settlementSection.getByRole("button", { name: "登记付款" }).first().click();
  const payDialog = page.getByRole("dialog");
  await expect(payDialog).toContainText("登记付款");
  await payDialog.getByRole("button", { name: "确认付款" }).click();

  // 终态：订单卡完结（付款完成 → 已关闭）
  await expect(orderCard.getByText(/已付款|已关闭/).first()).toBeVisible({ timeout: 30_000 });
  await expect(settlementSection.getByText("已付款").first()).toBeVisible({ timeout: 30_000 });
});
