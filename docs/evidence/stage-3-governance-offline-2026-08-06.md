# 阶段三 · Agent 治理与可观测包 — 离线证据（2026-08-06）

> 对应 `docs/agent-upgrade-2026-08-05.md` 第 3 节（3.1 工具调用理由记录；3.2 收敛指标进运行报告；3.3 独立评审最小版）。
> 真实模型阶段验证另见 `docs/evidence/stage-3-real-model-<日期>.md`。

## 实现 commit

- `d558cf7` feat(governance): phase-3 observability and independent review
- 改动：
  - `contracts.py`：`ToolCall.reason` / `ToolInvocationRecord.reason`。
  - `storage/migrations.py`：schema v13（`tool_invocations.reason`）。
  - `storage/tool_invocations.py`：reason 持久化与读取（含冲突更新）。
  - `engine/runtime.py`：模型回合文本截断 500 字作为每次工具调用的 reason。
  - `engine/tool_execution.py`：invocation 落库 reason；`tool_call_validated` 事件携带 reason。
  - `api/reporting.py`：报告新增 `convergence` 段（模型回合数、每工具调用次数、重复调用数、越权调用数、每次调用理由）。
  - `procurement/agent.py` + `api/procurement.py` + web 配置抽屉：`ai_review_enabled` / `review_provider` / `review_model` 配置化；`_run_ai_review` 用第二个 provider/模型交叉验证并写入 `ai_review` 审计事件（pass/fail + 理由，不阻塞审批）。
  - `tests/test_agent_governance.py`：4 个测试。

## 3.1 工具调用理由记录

- 测试：`test_tool_call_reason_recorded_in_invocation_and_report`
  - provider 先输出「Calling tools...」再调用 read_status → invocation.reason 与报告 `convergence.tool_reasons[0].reason` 均为该文本。

## 3.2 收敛指标进运行报告

- 测试：`test_convergence_metrics_in_run_report`
  - 断言报告 `convergence` 含 `model_turns`、`tool_call_counts`、`total_tool_calls`、`duplicate_calls`、`unauthorized_calls`、`tool_reasons`。

## 3.3 独立评审最小版（启用 `_ai_check` 模式）

- 实现：配置化开关（默认关，env `AGENTHARNESS_PROCUREMENT_AI_REVIEW_ENABLED` 或 UI 配置抽屉）；评审 Provider/模型可配置（默认 openai / 主模型）；审批完成后用第二个 provider 交叉验证「已批准报价 vs 确定性推荐」，输出 pass/fail + 理由写入 `ai_review` 审计事件（紧邻审批记录）；评审失败记 `verdict=error`，**不阻塞审批**。
- 测试：`test_ai_review_records_verdict_beside_approval`
  - 启用后完整对话 → 审批 → 断言 `ai_review` 事件 1 条、`verdict=pass`、`reason=与确定性比价一致`、带 approval_id/run_id。
- 测试：`test_ai_review_toggle_off_produces_no_review_event`
  - 开关关闭 → 评审 provider 0 次调用、无 `ai_review` 事件。

## 硬门槛复算

- Python：`pytest --cov=agentharness --cov-fail-under=80 -q` → **237 passed, 1 skipped**；覆盖率 **80.42%**。
- `ruff check .` → **All checks passed!**
- Web：`npm test` **14 passed**；`npm run lint` **通过**；`npm run build` **通过**（web_dist 已重建）。

## 待办

- 真实模型阶段验证：运行报告包含工具调用理由、收敛指标、独立评审记录（见阶段收尾证据）。
