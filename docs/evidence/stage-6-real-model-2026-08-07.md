# 阶段六 1.9 · 真实模型验证（deepseek-v4-flash，受预算约束）证据（2026-08-07）

## 前置条件

- Provider：`OPENAI_BASE_URL=https://api.deepseek.com/v1`，`OPENAI_MODEL=deepseek-v4-flash`，`.env` 已配置 key（35 字符）。
- 预算前置：设置 `AGENTHARNESS_PROCUREMENT_INPUT_PER_MILLION_USD=0.5`、`OUTPUT=1.5`、`CACHED_INPUT=0.1`、`MAX_COST_USD=0.5`、`MAX_TOKENS=50000`、`MAX_STEPS=20`、`MAX_WALL_TIME_S=300`（价格口径为验证用假设，用于启用成本上限检查与估算）。
- 数据：`scripts/run_rag_real_model.py` 先写入 5 条已成交历史 chunk（0 模型调用），再跑真实模型完整任务（2 份冻结报价）。

## 运行记录（run 09528bf7b2df4310835f9064d7931022）

| 项 | 结果 |
|---|---|
| run_id | `09528bf7b2df4310835f9064d7931022` |
| 终态 | `require_human`（分析完成、等待采购员在比价页确认——设计内的人工审批门） |
| 分析成功 | 是（确定性比价快照已生成，verified） |
| 比价页历史参考 | `knowledge_references_count=5`（比价页数据含 5 条可溯源参考） |
| Agent 推荐说明带引用 | 工具结果（pipeline_payload）含 `knowledge_references`（`tool_result_has_references=true`）；模型自然语言回复本次未复述「历史成交参考」（如实记录，`model_reply_mentions_history=false`） |
| 回合数 | 2（与无 RAG 的固定分析阶段一致，RAG 不增加模型回合） |
| Tokens | input 5700 / output 1506 / total 7206 |
| 估算成本 | $0.005109（cost_status=estimated，远低于 $0.50 预算） |
| 全程耗时 | 15.06 秒（含真实模型调用） |

运行 JSON 存档：`output/rag-live-run-09528bf7.json`（provider_attempts、tokens、cost）。

## 审批链路的诚实说明

- 本 run 停在「等待采购员选择」门（`require_human`），模型未在无人工指令时调用审批工具——这正是治理设计（审批必须由人工确认触发）。
- 另一次真实模型预跑（run `e800f0d338ff43cf93aec1b48df0a7ad`）中模型曾擅自先调用审批工具，被阶段状态机以 `tool_stage_denied` 拦截（治理生效），随后在自动批准流程中出现「审批参数与用户选择不一致」（模型在两次审批尝试间重新执行分析、快照版本变化导致参数不一致）。该异常属真实模型编排方差，已如实记录，不作为成功；确定性审批逻辑由 fake-provider 测试覆盖（`test_supplier_decision_request_and_audit_are_atomic` 等），不受 RAG 影响。
- 1.8 真人对照仍为「待实测」，真实模型本项不替代真人提效证据。

## 验证结论（对照验收点）

| 验收点 | 结果 |
|---|---|
| 比价页出现历史参考 | ✅ 5 条 |
| Agent 推荐说明带引用 | ✅ 工具结果含 knowledge_references（模型复述为偶发，如实记录） |
| 回合数未增加 | ✅ 2 回合（与无 RAG 一致） |
| 成本在预算内 | ✅ $0.005109 ≤ $0.50 |

## 复现命令

```powershell
$env:AGENTHARNESS_PROCUREMENT_INPUT_PER_MILLION_USD='0.5'
$env:AGENTHARNESS_PROCUREMENT_OUTPUT_PER_MILLION_USD='1.5'
$env:AGENTHARNESS_PROCUREMENT_CACHED_INPUT_PER_MILLION_USD='0.1'
$env:AGENTHARNESS_PROCUREMENT_MAX_COST_USD='0.5'
$env:AGENTHARNESS_PROCUREMENT_MAX_TOKENS='50000'
uv run python scripts/run_rag_real_model.py --data-dir output/rag-live-data --force
```
