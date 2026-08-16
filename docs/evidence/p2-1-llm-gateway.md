# P2-1 LLM 网关：限流 / 熔断 / 降级 — 验收证据（2026-08-16）

> 依据 `docs/interview-upgrade-execution-plan.md` §4 P2-1。commit 见 `git log -1`。
> 故障注入演示：`output/p2-demo/p2-1-demo.py`，证据 `docs/evidence/p2/p2-1.json`（熔断→结构化拒绝→模板降级→恢复全链路）。

## 实现（Python 侧，Java 调用协议零改动）

| 能力 | 位置 | 说明 |
|---|---|---|
| 并发配额 | `providers/gateway.py::ProviderGateway` | `asyncio.Semaphore` 按 provider 全局限并发（默认 4），超限排队 |
| QPS 限流 | `TokenBucket` | provider 维度令牌桶；等待超 `bucket_wait_s` 拒绝并产生 `rate_limited` 事件 |
| 熔断 | `CircuitBreaker` | 30s 滑动窗口失败率 > 50%（最少 5 样本）→ 熔断 60s；窗口过后半开探测，成功恢复、失败重熔 |
| 降级 | `GatewayAdapter::stream` + `degraded_summary` | 熔断期间 `gateway_degradable` 请求返回确定性模板文本（注明「模型服务暂不可用，展示确定性摘要」）；其余请求抛 `GatewayBlockedError` → 结构化错误 → Java AiTask FAILED（既有恢复路径） |
| 事件 | `agent_service._publish_gateway_event` | `provider_gateway.*` 事件写 Kafka runtime 事件；心跳 `heartbeat.ping` 随带脱敏快照 |
| 平台接口 | Java `PlatformController.gatewayStatus()` + `GatewayStatusView` | `/api/procurement/platform` 暴露 `gateway.providers[]`（状态/剩余秒/统计/限额），回退推导自 `provider_gateway.*` 事件；不泄漏密钥 |
| 前端标识 | `SystemInfo.tsx` | 新增「LLM 网关（限流/熔断/降级）」卡片：熔断中（danger，剩余秒）/ 降级中（warning）/ 正常；`PlatformInfo` 契约同步 |

配置全部走 `.env`（`.env.example` 已加 8 个 `AGENTHARNESS_PROVIDER_*` 变量）。

## 故障注入演示（p2-1-demo.py，全链路）

1. **注入故障**：fake provider 连续 3 次失败 → 熔断 `open`（失败 3 / 成功 0）；
2. **解析类请求**（非降级）→ `GatewayBlockedError(circuit_open, retry_after_s=2.0)`，统计 `circuit_blocked=1`；
3. **解释类请求**（`gateway_degradable`）→ 确定性模板文本：
   `模型服务暂不可用，展示确定性摘要：已请求 Java 执行确定性比价，请等待人工审批结果。`，统计 `degraded=1`，事件 `provider_gateway.degraded`；
4. **恢复**：切换健康 provider，熔断期（2s）过后半开探测成功 → `closed`，事件 `provider_gateway.circuit_closed`。

事件流（证据 JSON 原样）：`circuit_closed → circuit_closed → circuit_opened → circuit_open → degraded → circuit_closed`。

## 测试

- Python 新增 `tests/test_provider_gateway.py`（**17 个单测**）：令牌桶、熔断开关/半开恢复/窗口剪枝、限流拒绝、并发排队、降级模板、事件与脱敏快照、Harness 包装接线。`providers/gateway.py` 覆盖率 **97%**。
- Java 新增 `GatewayStatusViewTest`（4 个）：心跳快照优先 + 字段白名单、事件回退推导、无事件占位、状态映射。
- Web 新增 `systemInfo.test.tsx`：网关状态渲染（熔断中/剩余 42s/失败 7 限流 2 降级 1/正常）与无密钥泄漏断言。
- 运行时错误分类扩展：`runtime._provider_exception_kind` 识别 `rate_limited/circuit_open`，`_exception_retry_after_s` 读取 `retry_after_s`。

## 数字变化

- Python 测试：228 → **245**（+17）；总覆盖率 84.04% → 84.78%
- Java 测试：133 → **137**（+4）
- Web 测试：54 → **55**（+1）
- contracts：`PlatformInfo` 增加可选 `gateway`（schema + OpenAPI）；冻结资源零改动

## 面试话术更新点

- 「LLM 网关有三道闸：并发配额、QPS 令牌桶、失败率熔断——熔断 60 秒后半开探测；解析类任务熔断时给结构化错误走 AiTask 恢复，解释类任务直接降级成确定性摘要并明确标注『模型不可用』，前端系统信息页能看到熔断/降级标识」
