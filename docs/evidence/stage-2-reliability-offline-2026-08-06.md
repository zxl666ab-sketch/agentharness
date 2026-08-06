# 阶段二 · Agent 可靠性包 — 离线证据（2026-08-06）

> 对应 `docs/agent-upgrade-2026-08-05.md` 第 2 节（2.1 length/0 输出自动重试 + 输出预算放宽；2.2 预算/回合感知降级；2.3 Few-shot 示例注入）。
> 真实模型阶段验证另见 `docs/evidence/stage-2-real-model-<日期>.md`。

## 实现 commit

- `fb995f6` feat(runtime): phase-2 reliability package
- 改动：
  - `src/agentharness/providers/openai_adapter.py`：`finish_reason=length`/`content_filter` 与 `response.incomplete` 归类为 `error_kind="length"`。
  - `src/agentharness/engine/runtime.py`：length 且 0 输出自动重试一次并放宽输出预算（`output_budget_relaxed_to`）；仍失败输出中文可操作提示；预算耗尽先 `_degrade_budget_once`（上下文预算减半 + `budget_warning` 事件）再停到 `RunStatus.budget_stopped` 安全边界。
  - `src/agentharness/contracts.py` / `engine/lifecycle.py`：新增 `budget_stopped` 状态与 `run_budget_stopped` 事件；`budget_stopped` 可恢复。
  - `src/agentharness/procurement/agent.py`：系统提示词注入 3 条理想工具序列 few-shot。
  - `src/agentharness/api/reporting.py` + `web/src/*`：报告与 UI 显示「已停在安全边界」。
  - `tests/test_agent_reliability.py`：5 个测试。

## 2.1 length / 0 输出自动重试 + 输出预算放宽

- 测试：`test_length_zero_output_retries_once_with_widened_budget`
  - 注入 `error_kind=length`（0 输出）→ 自动重试一次 → 完成。
  - 断言：status completed；provider 调用 2 次；`provider_retry` 事件含 `error_kind=length`、`next_attempt=2`、`output_budget_relaxed_to>0`。
- 测试：`test_persistent_length_failure_gives_chinese_actionable_message`
  - 连续两次 length → 中文提示「模型输出被截断（finish_reason=length），自动放宽输出预算后仍失败。请调高输出预算或精简上下文后重试。」。

## 2.2 预算/回合感知降级

- 实现：token/成本预算耗尽时先把 `max_context_tokens` 减半（下限 8000）并重规划（`budget_warning` 事件），仍不够则 `budget_stopped`（「已停在安全边界」，保留 checkpoint 可恢复）；回合数（max_steps）耗尽直接安全停止；`error_kind=budget` 一律映射为 `budget_stopped`。
- 测试：`test_token_budget_exhaustion_stops_at_safe_boundary`
  - max_tokens=80 注入循环工具调用 → 断言 `budget_stopped`、错误含「安全边界」、`budget_warning` 事件存在且 `context_shrunk_to=8000`。
- 测试：`test_step_budget_exhaustion_stops_at_safe_boundary`
  - max_steps=2 → 断言 `budget_stopped`、错误含「回合数预算已用尽」。

## 2.3 Few-shot 示例注入

- 测试：`test_few_shot_examples_in_procurement_system_prompt`
  - 断言系统提示词包含「理想工具序列（few-shot）」及三个工具名。
- 既有测试无回归（full suite 233 passed / 1 skipped）。

## 硬门槛复算

- Python：`pytest --cov=agentharness --cov-fail-under=80 -q` → **233 passed, 1 skipped**；覆盖率 **80.74%**。
- `ruff check .` → **All checks passed!**
- Web：`npm test` **14 passed**；`npm run lint` **通过**；`npm run build` **通过**（web_dist 已重建）。

## 待办

- 真实模型阶段验证：正常路径不红 + 记录实际 finish_reason / 预算行为（见阶段收尾证据）。
