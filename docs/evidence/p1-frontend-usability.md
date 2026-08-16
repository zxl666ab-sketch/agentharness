# P1 前端可用性与结构修复 — 验收证据（2026-08-16）

> 依据 `docs/interview-upgrade-execution-plan.md` §3。本阶段 commit：
> `git log -1 --oneline` 见下；走查脚本 `output/ui-walk/p1-walk.py`（无头 Playwright，隔离上下文），结果 `p1-result.json` + 截图在本目录。

## 基线数字（§1，开工时记录）

| 套件 | 命令 | 结果 |
|---|---|---|
| Python | `uv run ruff check .` + `pytest --cov-fail-under=80` | ruff 通过；**228 passed, 1 skipped**，覆盖率 84.04% |
| Java | `.\mvnw.cmd test`（Testcontainers MySQL+Kafka） | **133 passed, 0 failures**（26 个测试类） |
| Web | `npm test` + `npm run lint` + `npm run build` | **48 passed** → 本阶段后 **54 passed**；lint 0 警告；build 通过 |
| 确定性 | `scripts/check_web_build_determinism.py` | 4 个文件 byte-identical，Java static bundle 一致 |
| 冻结评测 | `scripts/evaluate_procurement.py run` + `verify` | 31 case；字段抽取 99.52%、物料匹配 100%、金额 100%、硬约束漏检 0、不合格报价入选 0；acceptance 全 true（617/620 口径不变） |

## 走查结果（22/22 全过，console 错误 0）

| P1 子项 | 验收检查 | 结果 |
|---|---|---|
| P1-1 | 首页/任务列表无英文状态枚举直出（`approved`/`no_award`/`collecting`… 逐 token 扫描） | ✅ |
| P1-1 | 「待审批（比价完成）」与「审批处理中」不再同文案；列表实测标签集合 | ✅ |
| P1-2 | 任务详情头部「下一步」引导条可见，卡点原因可见（实测：`确认需求并复核报价字段 / 需求待人工确认，请先保存需求确认 / 去复核`） | ✅ |
| P1-2 | 创建面板显示「至少上传 2 家供应商报价才能开始分析」+ 已选文件计数（实测 `0 / 50 份`） | ✅ |
| P1-3 | 进度条扩展为 9 步闭环：创建需求→报价→复核→比价→审批→订单→收货→对账→付款 | ✅ |
| P1-3 | 已批准任务详情出现「订单已生成 →」按钮；点击直达 `?view=orders&order_task=<task>` 聚焦视图；「← 返回采购任务」可回到原任务（实测 RFQ-20260814-B923DE） | ✅ |
| P1-4 | 对话面板默认折叠（aria-expanded=false，面板不渲染），点击展开 | ✅ |
| P1-4 | 报价证据条收进 `details` 证据面板（默认关闭，徽标「证据已验证」） | ✅ |
| P1-4 | 字段复核默认「仅待复核」（checkbox 默认勾选） | ✅ |
| P1-4 | 比价表默认 8 列（收起税额/运费/成本指数），「展开详情」后 11 列 | ✅ |
| 全流程 | 无 JS console error | ✅ |

截图：`01-home.png`（无枚举首页）、`02-tasks.png`（列表文案）、`03-task-detail.png`（9 步进度+下一步条）、`04-approved-detail.png`（订单入口）、`05-orders-focused.png`（聚焦订单视图）、`06-back-to-task.png`、`07-compare.png`（折叠比价表）、`08-create-panel.png`（创建提示）。

## P1-5 拆分结果（职责一句话）

- `useWorkbenchState.ts`：URL 状态（view/task/ai/review/tab/filter/q/page/order_task）+ navigate/openView/openTask/openCreate + popstate 恢复；
- `useRequestQueries.ts`：全部 react-query（meta/config/requests/aiTasks/reviews/detail/report/taskOrder）+ 轮询策略 + Agent 流事件失效 + commit；
- `useWorkbenchActions.ts`：13 个动作 handler（对话/上传/修正/分析/AI 重试取消/恢复/审批/流标/重开/删除/配置/下一步）+ busy/error 状态；
- `DeleteDialog.tsx`、`ConfigDrawer.tsx`：两个内联弹窗独立组件；
- `ProcurementWorkbench.tsx`：1071 → 382 行（含导入与三个工具函数），仅布局壳 + 视图分发。

`viewModel.ts`：状态文案/色调/闭环步数/下一步引导单一来源，单测覆盖全部 10 个状态（`viewModel.test.ts`）。

## 数字变化

- Web 测试：48 → **54**（viewModel 4、workbenchUrl +1、QuoteWorkspace 密度行为 +1）
- `ProcurementWorkbench.tsx`：1071 → 382 行；新增文件 8 个（5 组件/hook + viewModel + 2 测试）
- 冻结资源、contracts、黄金契约：零改动；新 UI 未新增后端接口（订单聚焦复用现有 `GET /orders` 客户端过滤）

## 面试话术更新点

- 「已批准任务在详情页点一下直达订单，订单页确认对账、登记付款——整条 审批→订单→对账→付款 链路 3 次点击走完」
- 「同一个列表里再也不会出现『待审批』和『等待审批』两个无法区分的文案——所有状态标签只有一个来源（viewModel），10 个状态都有单测」
