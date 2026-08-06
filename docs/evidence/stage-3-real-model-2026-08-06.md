# 阶段三 · Agent 治理与可观测包 — 真实模型验证（2026-08-06）

> 对应 `docs/agent-upgrade-2026-08-05.md` 第 3 节。真实模型：deepseek-v4-flash（OpenAI 兼容网关，`.env` key）。
> 离线验证见 `docs/evidence/stage-3-governance-offline-2026-08-06.md`。

## 验证（run `bdba637b2090466a86535e50cada1c63`，`scripts/verify_live_stage_evidence.py`）

报告 `output/procurement-evaluation/live-stage-final/live-stage-evidence-<ts>.json`：

### 3.1 工具调用理由

`convergence.tool_reasons` 记录了真实模型调用前的说明文本：

- `procurement_capture_requirement`（step 0）：「我将先执行需求结构化，把规格、公差、汇率、开票与交期等约束一次性录入，由后端完成报价解析与确定性比价。」
- `procurement_approve_supplier`（step 2）：「收到采购员确认的供应商选择 JSON，我将执行正式审批以确认 Alpha Packaging 为本单供应商。」

### 3.2 收敛指标

`convergence`：`model_turns=3`、`tool_call_counts={capture:1, approve:1}`、`total_tool_calls=2`、`duplicate_calls=0`、`unauthorized_calls=0`、`tool_reasons` 如上。

### 3.3 独立评审（配置化启用，不阻塞审批）

- `agent.ai_review_enabled=True`、评审 Provider=openai、模型=deepseek-v4-flash。
- 审批记录旁出现 `ai_review` 审计事件：`verdict=pass`，`reason=确定性推荐报价与已批准供应商报价一致，均为80afd0ba…`，`approval_id=886748a2…`，`run_id=bdba637b…`。
- 开关可关：`test_ai_review_toggle_off_produces_no_review_event`（离线）验证关闭后不产生评审事件。

## 结论

运行报告包含工具调用理由、收敛指标与独立评审记录，满足阶段三验收。
