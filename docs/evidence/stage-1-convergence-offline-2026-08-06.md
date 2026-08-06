# 阶段一 · Agent 收敛包 — 离线证据（2026-08-06）

> 对应 `docs/agent-upgrade-2026-08-05.md` 第 1 节（1.1 显式阶段状态机 + 越权拒绝；1.2 同一步去重 / 防绕圈；1.3 人工操作结果直接注入）。
> 本文件为确定性离线验证证据；真实模型阶段验证另见 `docs/evidence/stage-1-real-model-<日期>.md`（阶段收尾验证后提交）。

## 实现 commit

- `e62f3ca` feat(engine): phase-1 agent convergence package
- 改动文件：
  - `src/agentharness/contracts.py`：新增事件类型 `tool_stage_denied` / `tool_call_duplicate` / `human_action_injected`。
  - `src/agentharness/engine/run_state.py`：RunContext 新增 `stage_denied_count` / `duplicate_call_count` 治理计数。
  - `src/agentharness/engine/tool_execution.py`：显式阶段矩阵求值 `current_stage_index`（含结果标记推进 `advance_on_result`）、`_reject_invocation` 治理拒绝落库、按状态纪元去重（`_STATE_CHANGING_EFFECTS`）。
  - `src/agentharness/procurement/agent.py`：`_run_request` 注入 capture→analysis→approve 阶段矩阵；fake provider 处理 `[verification_feedback]` 不再重复调用分析工具。
  - `tests/test_agent_convergence.py`：3 个行为回归测试。

## 1.1 显式阶段状态机 + 越权工具拒绝

- 实现：阶段矩阵 `capture → analysis → approve`；`tool_stage_initial=0`（会话流）/ `1`（结构化流）；分析阶段支持 `advance_on_result`（capture 内部完成分析时推进）；越权调用返回结构化中文提示并落库 failed invocation + `tool_stage_denied` 事件。
- 测试：`test_stage_gate_rejects_repeated_capture_and_records_event`
  - 注入第二次 `procurement_capture_requirement`（越权）→ 被拒。
  - 断言：invocation `failed` / `error_code=tool_stage_denied` / `error_category=governance`；`tool_stage_denied` 事件 1 条；run 状态 `require_human`（不崩溃）。

## 1.2 同一步内工具调用去重 / 防绕圈

- 实现：以「状态纪元」（state-changing 工具成功次数）为键去重；同一纪元内同一工具已成功 → 返回「结果未变化」并计入 `duplicate_tool_call` 事件 + `duplicate_call_count`；直接治理历史上 `read_request×4` 轮询。
- 测试：`test_duplicate_read_within_same_state_epoch_is_blocked`
  - 注入 read → read（无状态变化）→ 第二次 read 被拒。
  - 断言：第二次 read `failed` / `error_code=duplicate_tool_call` / 内容含「结果未变化」；`tool_call_duplicate` 事件 1 条；run 稳定停在 `require_human`。

## 1.3 人工操作结果直接注入 Agent

- 实现：人工复核/确认/审批结果通过 resume 消息直接注入上下文；`[human_review_complete]` 后 1 回合进入分析；`[verification_feedback]` 后不再重复调用分析工具。
- 测试：`test_human_review_injection_reaches_analysis_in_one_turn`
  - 会话首轮 capture → require_human → 注入人工复核结果 resume。
  - 断言：resume 后最后一个 invocation 为 `procurement_execute_analysis` 且成功；请求形成 comparison。

## 硬门槛复算

- `.venv\Scripts\python.exe -m pytest --cov=agentharness --cov-fail-under=80 -q`：**228 passed, 1 skipped**；覆盖率 **80.80%**（≥80）。
- `.venv\Scripts\python.exe -m ruff check .`：**All checks passed!**
- Web 无改动（本轮不改前端）：`npm test` 14 passed / `npm run lint` 通过 / `npm run build` 通过（执行起始基线已复算）。

## 待办

- 真实模型阶段验证（deepseek-v4-flash，受预算约束）：记录固定场景回合数/重复调用/越权调用相对基线的变化，见阶段收尾证据。
