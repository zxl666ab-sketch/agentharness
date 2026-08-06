# 阶段五 · 业务闭环与呈现 — 离线证据（2026-08-06）

> 对应 `docs/agent-upgrade-2026-08-05.md` 第 5 节（5.1 PO 导出；5.2 README；5.3 使用层面顺手项）。
> 真实模型阶段验证另见 `docs/evidence/stage-5-real-model-<日期>.md`。

## 实现 commit

- `d9555ea` feat(procurement): phase-5 business closure — PO export, README health, UX polish

## 5.1 审批后生成采购订单（PO）导出

- 实现：
  - `service.purchase_order(request_id)`：审批后生成并持久化订单（schema v14 `procurement_purchase_orders`），含供应商/物料/数量/单价/总金额/币种/快照引用（snapshot_id、版本、input_sha256）/审批引用（approval_id、decision_id）+ `evidence_sha256`；幂等（同单号）。
  - API：`GET /api/procurement/requests/{id}/purchase-order`（JSON）与 `.../purchase-order.csv`（UTF-8 BOM CSV 下载）。
  - Web：审批通过后比价页出现「下载采购订单 CSV」按钮。
  - 审计事件 `purchase_order_created`。
- 测试：`tests/test_purchase_order.py`
  - 完整对话 → 审批 → JSON 字段断言（供应商、数量 10000、快照/审批引用、证据哈希）。
  - CSV 端点：`text/csv`、`attachment`、表头与数据存在；再次获取同号（幂等）。
  - 未审批请求 → 409「采购任务尚未形成正式审批结论」。

## 5.2 README：架构边界图 + 健康度表 + 3 个可靠性案例

- README 新增「架构边界与健康度」章节：
  - 架构边界图（Web/API/Agent 阶段状态机/确定性流水线/领域状态/PO/审计）。
  - 健康度表（241 tests / 81% 覆盖 / ruff / web 14 tests / lint / build / 冻结 617-620 / 真实模型跑批入口 / 行为回归）。
  - 3 个「现象→根因→修复→回归」案例：read_request×4 轮询、审批摘要截断、网关 length 截断红屏。

## 5.3 使用层面顺手项

- 报错中文化 + 一键恢复：
  - Web `friendlyProcurementError` 映射 `UNIQUE constraint failed`/`IntegrityError`/`sqlite3.` → 中文可操作提示。
  - 恢复按钮文案改为「一键恢复（从持久化状态重新分析）」，覆盖 failed/cancelled/interrupted/budget_stopped。
- 审批状态文案：`procurement_approve_supplier` 工具标签与执行中状态改为「等待采购员确认」。
- 一键清理/新建演示任务：
  - `POST /api/procurement/demo`（frozen 案例一键建演示任务并启动 Agent）与 `POST /api/procurement/demo/clean`（只删除带 `demo_request` 审计标记的请求树，含 FK 顺序清理）。
  - Web 侧边栏「演示 / 清理」按钮。
- 测试：`tests/test_demo_ux.py`（创建→分析→清理）、web `procurement.test.tsx`（UNIQUE 文案映射）。

## 硬门槛复算（含 UI 验证）

- Python：`pytest --cov=agentharness --cov-fail-under=80 -q` → **244 passed, 1 skipped**；覆盖率 **81.07%**。
- `ruff check .` → **All checks passed!**
- Web：`npm test` **14 passed**；`npm run lint` **通过**；`npm run build` **通过**（web_dist 已重建）。
- UI 验证（dev server 8741 + Playwright 自动化冒烟，`procurement_fake`）：
  1. 主页面加载，侧边栏出现「演示 / 清理」按钮；
  2. 一键新建演示任务 → Agent 分析 → 比价视图（Alpha Packaging 推荐）；
  3. 提交供应商审批 → 确认 → 状态「已批准」，出现「下载采购订单 CSV」；
  4. CSV 下载返回 200 `text/csv` + `attachment`，内容含 PO 号、供应商、金额、快照/审批引用、证据 SHA-256；
  5. 一键清理后服务器列表仅剩非演示任务；
  6. 配置抽屉显示「审批前启用独立评审」开关与评审 Provider/模型输入。

## 待办

- 真实模型阶段验证：审批 → PO 导出端到端可用（见阶段收尾证据）。
