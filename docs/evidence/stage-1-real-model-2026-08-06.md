# 阶段一 · Agent 收敛包 — 真实模型验证（2026-08-06）

> 对应 `docs/agent-upgrade-2026-08-05.md` 第 1 节。真实模型：deepseek-v4-flash（OpenAI 兼容网关，`.env` key）。
> 确定性离线验证见 `docs/evidence/stage-1-convergence-offline-2026-08-06.md`。

## 基线（阶段前）

历史单次全链路记录 `docs/evidence/live-real-run-report.md`：**6 次模型回合**，capture 两次因空汇率表被确定性校验拒绝后自纠，另有 read_request 轮询与人工修正后再分析，总 Token 18,690。

## 验证（阶段后，当前 HEAD）

命令（受预算约束，`max_cost_usd` 已通过价格配置生效）：

```powershell
$env:AGENTHARNESS_PROCUREMENT_INPUT/OUTPUT/CACHED_INPUT_PER_MILLION_USD=0.5/1.5/0.5
$env:AGENTHARNESS_PROCUREMENT_MAX_COST_USD=0.15; MAX_TOKENS=30000; MAX_STEPS=8; MAX_WALL_TIME_S=180
python scripts/run_procurement_live_batch.py --output-dir output/procurement-evaluation/live-batch-final
```

结果（报告 `output/procurement-evaluation/live-batch-final/live-batch-20260806-092938.json`）：

| 场景 | run_id | 回合数 | 工具调用 | 重复调用 | 越权调用 | 成本 USD |
|---|---|---|---|---|---|---|
| 01-仓储热敏标签 | 7cad56cf… | 2 | 1 | 0 | 0 | 0.0050 |
| 02-出口瓦楞纸箱 | cafb4116… | 2 | 1 | 0 | 0 | 0.0038 |
| 03-透明封箱胶带 | f89065d8… | 2 | 1 | 0 | 0 | 0.0038 |
| 04-快递袋比价 | 50fdeb9c… | 2 | 1 | 0 | 0 | 0.0038 |
| frozen-01 | 0fb5049a… | 2 | 1 | 0 | 0 | 0.0040 |
| frozen-02 | b85e7230… | 2 | 1 | 0 | 0 | 0.0038 |
| frozen-03 | b04cb6b6… | 2 | 1 | 0 | 0 | 0.0041 |
| frozen-04 | 648c2e55… | 2 | 1 | 0 | 0 | 0.0046 |
| frozen-05 | 4067a4b3… | 3 | 2 | 0 | 0 | 0.0070 |
| **合计/均值** | | **2.11** | **1.11** | **0** | **0** | **0.0399** |

## 对比

- 回合数：6 → 平均 2.11（2–3 期望区间内，9/9 场景达标）。
- 重复调用：历史 read_request 轮询 → 0。
- 越权调用：0；阶段状态机全程无 `tool_stage_denied`。
- 分析成功率：9/9（100%）。

## 结论

阶段一功能验收（状态机拒绝、事件记录、run 稳定）与真实模型指标（2–3 回合、无重复/越权）均达成；数据如实记录，未用 fake 结果替代。
