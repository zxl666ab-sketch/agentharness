# P3-1 发票三单匹配（旗舰）— 验收证据（2026-08-16）

> 依据 `docs/interview-upgrade-execution-plan.md` §5 P3-1。commit 见 `git log -1`。
> 五服务（mysql/redis/kafka/agent/procurement）已用新镜像重建并跑通演示路径；
> UI 走查 `output/ui-walk/p3-walk.py` 11/11 全过（截图在本目录）。

## 实现

| 层 | 内容 |
|---|---|
| 迁移 | `V14__invoice_domain.sql`：invoice 表（发票代码/号码/开票日期/数量/单价/不含税金额/税额/价税合计/税率 + match_result/match_explanation JSON + 乐观锁 + UNIQUE(invoice_no)） |
| 状态机 | `invoice` 机器注册进 StateMachineRegistry：`REGISTERED --MATCH--> MATCHED --RECONCILE--> RECONCILED`，`REGISTERED/DIFF_HOLD --VOID--> VOIDED`，`DIFF_HOLD --FORCE_MATCH--> MATCHED`（allow-once） |
| 三单匹配 | `ThreeWayMatcher`（纯确定性）：PO 数量/单价/总价/税率 vs 收货 GRN vs 发票；容差 数量 0 / 单价 ±0.01（统一含税口径：到货总价÷数量 vs 价税合计÷数量）/ 总价 ±0.01 / 税率 ±0.1%（期望税率来自批准报价快照，缺失跳过） |
| Agent 边界 | Python 只做两件事：`parse_invoice` 确定性字段抽取（xlsx/pdf，`invoice_parsing.py`）+ `explain_invoice_diff` 差异解释（模式 C：数值只来自注入的结构化 diffs，评测硬校验） |
| 差异挂起处理 | 作废（退回重开，原因必填）/ 手工改单（记录审计 + 重新匹配）/ 强制通过（allow-once + 勾选确认 + 人工备注）；全部经状态机校验 + 乐观锁 409 |
| 付款联动 | `SettlementService.pay` 前校验：订单存在 REGISTERED/DIFF_HOLD 发票 → 409 `unmatched_invoice_blocks_payment` |
| 审计 | 全部 `business_type=invoice`（invoice_upload_accepted / invoice_registered / invoice_matched / invoice_diff_hold / invoice_explained / invoice_voided / invoice_manual_corrected / invoice_force_matched / invoice_reconciled） |
| 前端 | 发票中心（列表/详情/三单对比/差异挂起队列/处理弹窗）+ 导航「发票中心」；任务进度条 9→10 步（…审批→订单→收货→**发票匹配**→对账→付款），已付款任务 10/10 |
| 评测 | `frozen-evaluation-invoice.json`（16 例合成发票，README 标注 synthetic）+ `scripts/evaluate_invoice.py`（字段抽取 ≥99% + 差异解释数值引用一致性硬校验） |

## 五服务演示路径（实测）

1. 上传差异发票（总价 7800 vs 订单 7500）→ Agent 解析 → Java 匹配 → **DIFF_HOLD**（差异：单价 0.5 vs 0.52、价税合计 7500 vs 7800）；
2. Agent 差异解释到达（`deterministic_agent`）："三单匹配存在 2 项差异：单价不一致：订单/收货为 0.5，发票为 0.52（差异 0.020000）；价税合计不一致：订单/收货为 7500，发票为 7800（差异 300）…"+ 两条处理建议；
3. 付款尝试 → **409 `unmatched_invoice_blocks_payment`**（差异挂起阻断付款）✅；
4. 手工改单（价税合计→7500）→ 重新匹配 → **MATCHED**；
5. 核销 → **RECONCILED**；
6. 付款 → **PAID**（证据：settlement ST-20260813-51EBF1 status=PAID）；
7. 第二张发票（价税合计 11000 = 订单 11000）上传 → 自动 **MATCHED**（0 差异）✅。

UI 走查 11/11：发票列表 3 张（DIFF_HOLD/MATCHED/RECONCILED）、三单对比表、结构化差异、Agent 解释、处理操作按钮、10 步进度条、10/10 已付款任务、0 console 错误。

## 数字变化

- Java 测试：140 → **148**（+8：ThreeWayMatcherTest 6 + SettlementServiceTest 付款阻断 1 + 构造函数适配 1）
- Python 测试：255 → **260**（+5：`test_invoice_parsing.py`）
- 新增：`invoice/` 8 个 Java 文件 + `invoice_parsing.py` + `frozen-evaluation-invoice.json` + `scripts/evaluate_invoice.py` + `InvoiceCenter.tsx` + 契约（InvoiceView/Page/ActionInput/Status + 4 个 path）
- 冻结资源零改动（新评测独立文件，README 标注 synthetic）

## 面试话术更新点

- 「三单匹配全链路：上传发票 → Python 只解析字段 → Java 确定性比对数量/单价/总价/税率（容差 ±0.01）→ 差异挂起时 Python 生成自然语言解释（解释里的每个数字都来自结构化差异，有硬校验）→ 手工改单/强制通过（allow-once）/作废 → 核销 → 付款；发票没匹配好，付款直接被 409 挡住」
