# 阶段二 · Agent 可靠性包 — 真实模型验证（2026-08-06）

> 对应 `docs/agent-upgrade-2026-08-05.md` 第 2 节。真实模型：deepseek-v4-flash（OpenAI 兼容网关，`.env` key）。
> 确定性故障/边界验证（length 重试、预算安全边界）见 `docs/evidence/stage-2-reliability-offline-2026-08-06.md` 与 `tests/test_agent_reliability.py`。

## 验证

### 正常路径不红（真实模型）

- 9 场景跑批（run ids 见 `stage-1-real-model-2026-08-06.md`）：9/9 分析成功，0 failed，0 `provider_retry` 事件（真实网关本轮未出现 length/重试）。
- 单场景阶段证据（run `bdba637b2090466a86535e50cada1c63`）：`provider_retries=[]`、`budget_warnings=[]`，token 10,036，成本 $0.007561。
- 实际预算行为：全部运行在 `max_cost_usd=0.15`、`max_tokens=30000` 内完成；单场景实际成本 $0.0037–0.0076。

### 边界路径（确定性注入，另见离线证据）

- `test_length_zero_output_retries_once_with_widened_budget`：length + 0 输出 → 自动重试一次并放宽输出预算 → 完成。
- `test_persistent_length_failure_gives_chinese_actionable_message`：仍失败给中文可操作提示。
- `test_token_budget_exhaustion_stops_at_safe_boundary` / `test_step_budget_exhaustion_stops_at_safe_boundary`：预算耗尽 → `budget_stopped`「已停在安全边界」而非 failed。

## 结论

真实模型正常路径不红、成本上限生效；边界故障用确定性注入稳定复现并验证；未为触发故障而无限重试或伪造真实模型结果。
