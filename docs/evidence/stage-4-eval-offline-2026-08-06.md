# 阶段四 · Agent 评测与行为回归包 — 离线证据（2026-08-06）

> 对应 `docs/agent-upgrade-2026-08-05.md` 第 4 节。本文件覆盖 4.2 行为回归测试集与 4.1 跑批脚本的离线冒烟；
> 4.1 的真实模型跑批报告由 `scripts/run_procurement_live_batch.py` 实际运行生成，见 `docs/evidence/stage-4-live-batch-<日期>.md`。

## 实现 commit

- `f836584` feat(eval): phase-4 acceptance batch + agent behavior regression set

## 4.2 Agent 行为回归测试集

`tests/test_agent_behavior_regression.py`（4 个用例，全部离线、确定性）：

| 坏行为 | 注入方式 | 系统拦截/纠正 | 测试名 |
|---|---|---|---|
| 跳阶段 | 首轮直接调用 `procurement_approve_supplier` | 被阶段门/前置条件拒绝（`tool_stage_denied` 或 `tool_disabled`），无审批结论，run 停在 `require_human` | `test_stage_skip_is_intercepted` |
| 重复调用 | 同一状态纪元内 read_request ×2 | 第二次被 `duplicate_tool_call` 拦截，事件 `tool_call_duplicate`，run 稳定 | `test_duplicate_call_is_blocked` |
| 编造参数 | `capture_requirement` 传入 `quantity=-5` | JSON Schema 校验拒绝（`invalid_arguments`），run 停在 `require_human` | `test_fabricated_arguments_are_rejected` |
| 提前声称成功 | 首轮输出「【采购决策已验证】」但未调用审批 | 验证环不通过（缺 approval 工具），run 停在 `require_human`，**不产生 decision** | `test_premature_success_claim_is_not_verified` |

## 4.1 真实模型验收跑批脚本（离线冒烟）

- `scripts/run_procurement_live_batch.py`：
  - 场景：`output/procurement-scenarios` 4 个目录 + 冻结数据集 5 组报价对（共 9 个场景，可用 `--limit` 截断）。
  - 每场景输出：run_id、状态、分析成功、回合数、工具调用数（分工具）、重复调用、越权调用、Token、成本、审批成功（`--with-approval`，默认开）。
  - 聚合摘要 + 诚实分层说明（确定性冻结评测 617/620 为 0 模型调用 vs 真实模型编排基线）。
  - 预算：使用 `AGENTHARNESS_PROCUREMENT_MAX_COST_USD` / `MAX_TOKENS`（需先配置价格，否则 runtime 提示）。
- 冒烟：`--fake --scenarios-dir output/procurement-evaluation/smoke-scenarios` → 5/5 分析成功，回合数均值 2.0，工具调用均值 1.0（离线确定性路径）。

## 硬门槛复算

- Python：`pytest --cov=agentharness --cov-fail-under=80 -q` → **241 passed, 1 skipped**；覆盖率 **80.93%**。
- `ruff check .` → **All checks passed!**
- Web 无改动（本轮不改前端）。

## 待办

- 4.1：在真实模型（deepseek-v4-flash，`.env` key，受预算约束）下运行跑批并提交报告（阶段四验证本身）。
