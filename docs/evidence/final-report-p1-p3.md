# 最终报告 — P1→P3 面试升级执行计划（2026-08-16）

> 依据 `docs/interview-upgrade-execution-plan.md`，无人值守执行完毕。8 个 commit：`c8be795`(P1)、`59ed979`(P2-1)、`4d75e07`(P2-2)、`272e402`(P2-3)、`bd78e21`(P3-1)、`9bbc9e6`(P3-2)、`a4c7476`(P3-3 设计笔记)；基线 `df0a173` 之前。

## 完成项

| 阶段 | commit | 内容 | 验收证据 |
|---|---|---|---|
| P1 工作台可用性 | `c8be795` | 共享 viewModel（状态标签/语调/闭环步骤/下一步引导）、9 步闭环进度条、已审批任务一键转订单（order_task 聚焦视图）、信息密度默认折叠、god-component 拆分 1071→382 行（useWorkbenchState/useRequestQueries/useWorkbenchActions + DeleteDialog/ConfigDrawer） | `docs/evidence/p1-frontend-usability.md`，Playwright 走查 22/22 |
| P2-1 LLM 网关熔断/降级 | `59ed979` | Python 侧按 provider 并发信号量 + QPS 令牌桶 + 30s 窗口熔断（半开探测）、可降级解释任务模板降级（标记 model-unavailable）、`provider_gateway.*` 事件 + 心跳快照到 Java 平台接口、SystemInfo 网关卡片、.env 旋钮；故障注入演示 全链路 open→blocked→degraded→recovered | `docs/evidence/p2-1-llm-gateway.md` |
| P2-2 报价纠错闭环 | `4d75e07` | 冲突候选 chip 点击纠正（chosen_from_conflicts 持久化，V13）+ 服务端候选校验（CorrectionConflictPolicy）、只读 `GET /api/procurement/corrections`、导出脚本 → `frozen-evaluation-corrections.json`（修复后已生成并提交，当前 0 条修正记录，随演示数据重跑更新；冻结资源零改动） | `docs/evidence/p2-2-conflict-adjudication.md` |
| P2-3 语义缓存 | `272e402` | Python 侧 Redis 精确匹配缓存（key `semantic:v1:{scope}:{sha256}:{version}`、TTL 24h、版本失效、校验通过才写、Redis 不可用自动 no-op），接入报价解析与需求抽取，agent 容器 AGENT_REDIS_URL | `docs/evidence/p2-3-semantic-cache.md` |
| P3-1 发票三单匹配（旗舰） | `bd78e21` | V14 invoice 域 + 状态机（REGISTERED→MATCHED→RECONCILED / DIFF_HOLD / VOIDED，注册进 StateMachineRegistry）、确定性 ThreeWayMatcher（数量/单价 ±0.01 含税口径/总价 ±0.01/税率 ±0.1%）、差异挂起三种处理（作废/手工改单/强制通过 allow-once）、付款被 409 `unmatched_invoice_blocks_payment` 阻断、Python 只做解析+差异解释（模式 C 数值硬校验）、发票中心 UI、进度 9→10 步、`frozen-evaluation-invoice.json` 合成评测（字段抽取 100%） | `docs/evidence/p3-1-invoice-three-way.md`（五服务演示路径实测），UI 走查 11/11 |
| P3-2 合同管理（旗舰） | `9bbc9e6` | V15 contract 域 + 状态机（DRAFT→PENDING_APPROVAL→EFFECTIVE→EXECUTING→CLOSED / 驳回按来源分流 / CHANGE_REQUEST 变更闭环，审核修复后变更必填修订金额/交期、regen-draft 重新草拟、批准落定、驳回恢复变更前状态）、Agent 起草边界（金额/交期只来自定标快照注入，`evaluate_contract.py` 硬校验）、Java 一致性硬校验（文本金额≠定标金额拒绝生效）+ 条款约束（金额/交期条款必含，risk_level 自动标注）、合同中心 UI + 任务详情入口、`frozen-evaluation-contract.json` 合成评测（8 例，条款精度/召回 1.0） | `docs/evidence/p3-2-contract-management.md`（五服务演示路径实测：createDraft→submit→approve→execute→request_change→regen-draft→approve→驳回恢复→execute→close），UI 走查 8/8 |
| P3-3 供应商准入 | `a4c7476` | **降级为设计笔记**（计划 §5 标注可选 + §6.5 时间盒纪律）：完整实现蓝图（V16 草案/状态机/Agent 边界/规则校验+评分卡/前端/契约/评测/测试计划/面试话术） | `docs/evidence/p3-3-supplier-admission-design.md` |

## 未完成项与原因

- **P3-3 供应商准入未实现**：计划 §5 明确标注「可选（3–4 天），若时间不足降级为设计笔记」；本次已交付全部必做阶段（两个旗舰 P3-1/P3-2 均含五服务实测 + UI 走查），按 §6.5 纪律不烂尾，交付设计笔记作为独立 goal 的开工蓝图。

## 证据清单

- 各阶段报告：`docs/evidence/p1-frontend-usability.md`、`p2-1-llm-gateway.md`、`p2-2-conflict-adjudication.md`、`p2-3-semantic-cache.md`、`p3-1-invoice-three-way.md`、`p3-2-contract-management.md`、`p3-3-supplier-admission-design.md`、本报告
- 截图/JSON：`docs/evidence/p1/`（P1 走查截图）、`docs/evidence/p32/`（合同中心 4 张截图 + p32-result.json + contract-draft/closed-detail.json + contract-evaluation.json）
- 五服务演示产物：`output/p3-demo/`（发票 4 张 + 合同全生命周期 detail JSON）、`output/ui-walk/`（p3-walk/p32-walk 结果与截图）
- 评测产物：`output/procurement-evaluation/`、`scripts/evaluate_invoice.py`、`scripts/evaluate_contract.py`、`scripts/export_corrections_to_eval.py`
- 冻结评测基线未动：31 例黄金契约字段抽取 617/620（99.52%）、物料 31/31、金额 31/31、硬约束漏检 0、不合格错误入选 0（P1 证据中复测确认）

## 数字变化

- **测试数**：Java 133 → **153**（P2-1 +4 / P2-2 +3 / P3-1 +8 / P3-2 +5）；Python 228 → **266**（P2-1 +17 / P2-3 +10 / P3-1 +5 / P3-2 +6，覆盖率 85.52%）；Web **56**（P1 54 + P2-1 +1 + P2-2 +1）
- **评测**：冻结基线 617/620、31/31、0 漏检、0 错误入选——字节级不变；新增评测产物：invoice 合成评测集（16 例，字段 100%）+ contract 合成评测集（8 例，条款 precision·recall 1.0）+ corrections 人工修正导出候选（当前 0 条，随演示数据重跑生成），README 均如实标注 synthetic
- **新增 API**：corrections（1 path）+ invoices（7 path）+ contracts（10 path，含 regen-draft）+ platform gateway 状态接口，全部同步 `contracts/procurement-workbench.schema.json` + OpenAPI + web types/api（contracts.test.ts 对齐）
- **web bundle**：每次变更后同步 Java static 包，`check_web_build_determinism.py` 4 文件字节级一致 ✓
- **契约数字**：状态机注册 +3（invoice/contract/correction 相关）、审计事件类型 +11、进度条 9→10 步、导航 +2（发票中心/合同中心）

## 面试话术更新点

1. 「三单匹配全链路：上传发票 → Python 只解析字段 → Java 确定性比对数量/单价/总价/税率（±0.01 容差）→ 差异挂起时 Python 生成自然语言解释（解释里的每个数字都来自结构化差异，有硬校验）→ 手工改单/强制通过（allow-once）/作废 → 核销 → 付款；**发票没匹配好，付款直接被 409 挡住**」
2. 「合同从定标结果自动起草：金额/交期只来自定标快照（Python 不产生数值，评测硬校验），Java 再做一致性硬校验（文本金额≠定标金额直接拒绝）；条款自动标风险等级；全生命周期状态机 + 变更审批闭环 + 审计事件」
3. 「LLM 网关：按 provider 限流 + 30s 窗口熔断 + 半开探测；可降级解释任务在熔断时降级成确定性摘要并标记 model-unavailable，比价核心永不依赖模型」
4. 「P2-2 纠错闭环：冲突候选 chip 一键纠正 + 服务端候选校验 + 只读审计接口 + 导出合成评测集（幂等）」
5. 「P2-3 语义缓存：版本化 key + 24h TTL + 校验通过才写 + Redis 挂了自动 no-op，评测口径不因缓存改变」
6. 「架构复用一句话：发票、合同、准入共用同一套『状态机注册 + Outbox AgentCommand + 审计事件 + 乐观锁 409』骨架，Python 只做解析/起草/解释，Java 做权威校验」

## 建议的下一步

1. **P3-3 独立 goal 落地**：按 `docs/evidence/p3-3-supplier-admission-design.md` 蓝图开工（3–4 天，V16 + 准入状态机 + 评分卡 + 准入中心 UI + 合成评测）；
2. **Track B**（`docs/recruitment-value-upgrade.md`）：压测/可观测（P2-1 熔断事件与 Track B P0-2 监控指标天然衔接，本计划只落事件与接口，未装 Prometheus/Grafana）、虚拟线程、LLM-as-Judge、MCP、向量 RAG——建议另开 goal 会话执行（§7：与本文档不并行）；
3. 面试材料：`docs/resume.md` 量化指标可更新为 153/266/56 测试数 + 10 步闭环 + 双旗舰演示路径（已有截图证据）。
