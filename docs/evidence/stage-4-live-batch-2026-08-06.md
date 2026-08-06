# 阶段四 · 真实模型验收跑批报告（2026-08-06）

> 对应 `docs/agent-upgrade-2026-08-05.md` 4.1。本文件即 4.1 的验收证据：脚本可复现、报告含场景级指标 + 诚实分层说明。
> 4.2 行为回归见 `docs/evidence/stage-4-eval-offline-2026-08-06.md`。

## 运行方式（可复现）

```powershell
# 1) 配置价格（否则 max_cost_usd 不生效）
$env:AGENTHARNESS_PROCUREMENT_INPUT_PER_MILLION_USD=0.5
$env:AGENTHARNESS_PROCUREMENT_OUTPUT_PER_MILLION_USD=1.5
$env:AGENTHARNESS_PROCUREMENT_CACHED_INPUT_PER_MILLION_USD=0.5
$env:AGENTHARNESS_PROCUREMENT_MAX_COST_USD=0.15
$env:AGENTHARNESS_PROCUREMENT_MAX_TOKENS=30000
$env:AGENTHARNESS_PROCUREMENT_MAX_STEPS=8
$env:AGENTHARNESS_PROCUREMENT_MAX_WALL_TIME_S=180
# 2) 运行
python scripts/run_procurement_live_batch.py --output-dir output/procurement-evaluation/live-batch-final
```

- 场景：`output/procurement-scenarios` 4 个目录 + 冻结数据集 5 组报价对 = 9 个。
- 输出：`output/procurement-evaluation/live-batch-final/live-batch-20260806-092938.json`（schema_version 1，含 summary/scenarios/layering）。
- 脚本：`scripts/run_procurement_live_batch.py`（提交于 f836584、5b91a5d）。

## 场景级指标

| 场景 | run_id | 状态 | 回合 | 工具调用 | 重复 | 越权 | Token | 成本 USD |
|---|---|---|---|---|---|---|---|---|
| 01-仓储热敏标签 | 7cad56cfd97d44dc83dbe12b86fda2e4 | require_human | 2 | 1 | 0 | 0 | 6,509 | 0.0050 |
| 02-出口瓦楞纸箱 | cafb4116ae2641428cb514f7302e256f | require_human | 2 | 1 | 0 | 0 | 5,328 | 0.0038 |
| 03-透明封箱胶带 | f89065d83c624bb6b6a2971d377403cc | require_human | 2 | 1 | 0 | 0 | 5,387 | 0.0038 |
| 04-快递袋比价 | 50fdeb9c27bd447989325947c0e48681 | require_human | 2 | 1 | 0 | 0 | 5,336 | 0.0038 |
| frozen-01 | 0fb5049a58c5453b85835252b1394f77 | require_human | 2 | 1 | 0 | 0 | 5,480 | 0.0040 |
| frozen-02 | b85e723013b949dba0f5b5f87eb327a9 | require_human | 2 | 1 | 0 | 0 | 5,212 | 0.0038 |
| frozen-03 | b04cb6b6ed4b4433ab2c1570167cd4f6 | require_human | 2 | 1 | 0 | 0 | 5,485 | 0.0041 |
| frozen-04 | 648c2e5523a247ebb579f1e0df3aee9b | require_human | 2 | 1 | 0 | 0 | 6,034 | 0.0046 |
| frozen-05 | 4067a4b39cad457ca58aa6c2277cac15 | require_human | 3 | 2 | 0 | 0 | 8,396 | 0.0070 |
| **合计/均值** | | | **2.11** | **1.11** | **0** | **0** | **55,167** | **0.0399** |

- 分析成功率：9/9（100%）。5/9 直接产出比价快照；4 个场景进入 `needs_review`（字段待人工复核，符合产品行为，跑批不做复核修正）。
- 审批腿（`--with-approval`）：3/9 审批成功；2/9 因所选推荐报价不在合格集而被确定性审批校验拒绝（frozen-04/05 的推荐本身不合格）；其余 4 个因快照未直接产出未发起审批。成功率如实分层，不伪装为 100%。

## 诚实分层说明

- 确定性冻结评测（`output/procurement-evaluation/raw-results.json`）：**617/620 字段抽取、31/31 成本计算，0 模型调用**，独立可复算。
- 本批为**真实模型编排基线**：9 场景、55,167 token、$0.0399，回合/工具调用/重复/越权如上。
- 两者是不同分层：确定性管线不调用模型；真实模型结果受模型/网关/场景差异影响，不混用、不外推。

## 结论

4.1 验收达成：脚本可复现；报告含场景级指标与诚实分层说明。
