# P3-2 合同管理（旗舰）— 验收证据（2026-08-16）

> 依据 `docs/interview-upgrade-execution-plan.md` §5 P3-2。commit 见 `git log -1`。
> 五服务（mysql/redis/kafka/agent/procurement）已用新镜像重建并跑通演示路径；
> UI 走查 `output/ui-walk/p32-walk.py` 8/8 全过（截图在本目录），0 console 错误。

## 实现

| 层 | 内容 |
|---|---|
| 迁移 | `V15__contract_domain.sql`：contract 表（合同编号/名称/类型/供应商/金额/交期天数/签署日期/生效日期/结束日期/关联任务+PO + 状态 + 一致性校验结果 JSON + 乐观锁 + UNIQUE(contract_no) / UNIQUE(task_id)） |
| 状态机 | `contract` 机器注册进 StateMachineRegistry：`DRAFT --SUBMIT--> PENDING_APPROVAL --APPROVE--> EFFECTIVE --EXECUTE--> EXECUTING --CLOSE--> CLOSED`，`DRAFT/PENDING_APPROVAL --REJECT--> REJECTED`，`EFFECTIVE --REQUEST_CHANGE--> CHANGE_REQUESTED --APPROVE_CHANGE--> EFFECTIVE`（变更后 EXECUTE 可重入） |
| Agent 边界 | Python 只做两件事：`build_contract_draft`（`contract_drafting.py`，模板 + 条款 risk_level 标注 + 软性提示，确定性输出）+ `_draft_contract` 操作 handler；金额/交期**只来自注入的定标快照**（`evaluate_contract.py` 硬校验字段注入一致性） |
| 硬校验 | `ContractConsistencyPolicy`（Java 权威）：草稿文本中的金额必须等于定标结果（regex 抽取 + 金额比对）、交期天数同理；不一致 → `consistency.consistent=false` + 具体原因，拒绝生效 |
| 条款约束 | `ContractClausePolicy`：合同必须包含「金额条款」「交期条款」两簇（缺任一 → 409 `missing_required_clause`），条款自动打 risk_level（低/中/高，金额±10% 或交期±30% 为高） |
| 审计 | 全部 `business_type=contract`（contract_draft_created / contract_submitted / contract_approved / contract_rejected / contract_change_requested / contract_change_approved / contract_executing / contract_closed），变更历史带 from/to 状态 |
| 前端 | 合同中心（列表/详情/条款与风险等级/一致性结果/变更历史/操作按钮）+ 导航「合同中心」+ 任务详情「生成合同」入口（`taskContractQuery`，`workbenchUrl` view=contracts） |
| 评测 | `frozen-evaluation-contract.json`（12 例合成合同场景，README 标注 synthetic）+ `scripts/evaluate_contract.py`（条款精度/召回 1.0 + 字段注入一致性 + 风险等级正确性硬校验） |

## 五服务演示路径（实测，任务 b923de09 定标金额 10400 / 交期 12）

1. 已审批任务「生成合同」→ AgentCommand `draft_contract` → Agent `build_contract_draft` → Java 落库 **DRAFT**（6 条款、risk_level 标注、`consistency.consistent=true`、amount=10400 / lead_days=12）；
2. 提交 → **PENDING_APPROVAL**；
3. 审批 → **EFFECTIVE**（自动关联 PO-RFQ-20260814-B923DE，合同编号 CT-RFQ-20260814-B923DE）✅；
4. 执行 → **EXECUTING**；
5. 变更申请（request_change，history=1：EFFECTIVE→CHANGE_REQUESTED）→ 变更审批 → 回到 **EFFECTIVE** → 再次执行 → **EXECUTING**；
6. 关闭 → **CLOSED**（合同全生命周期闭环，变更历史完整保留）✅；
7. 负例：非 EXECUTING 状态关闭 → 409（状态机校验）；缺金额/交期条款 → 409 `missing_required_clause`（证据见 ContractPoliciesTest）。

UI 走查 8/8：合同列表、草稿文本展示、条款+风险等级、一致性结果、变更历史、操作按钮随状态切换、任务详情合同入口、0 console 错误。

## 数字变化

- Java 测试：148 → **153**（+5：ContractPoliciesTest 4 + ContractStateMachineTest 状态机 1）
- Python 测试：260 → **266**（+6：`test_contract_drafting.py`，覆盖率 85.52%，contract_drafting 92%）
- 新增：`contract/` 11 个 Java 文件 + `contract_drafting.py` + `frozen-evaluation-contract.json` + `scripts/evaluate_contract.py` + `ContractCenter.tsx` + 契约（ContractView/Page/ActionInput/Status + 9 个 path + AgentCommandBody Literal `draftContract`）
- Web 测试 56 passed、lint 0 warnings、build 通过；web/dist 与 Java static 静态包 4 文件字节级一致（determinism ✓）
- 冻结资源零改动（新评测独立文件，README 标注 synthetic）

## 面试话术更新点

- 「合同从定标结果自动起草：金额/交期只来自定标快照（Python 不产生数值，评测硬校验），Java 再做一致性硬校验（文本金额≠定标金额直接拒绝）；条款自动标风险等级；全生命周期状态机 DRAFT→审批→生效→执行→变更（变更走审批）→关闭，每一步都有审计事件和变更历史；合同没生效前付款/履约环节无法推进」
