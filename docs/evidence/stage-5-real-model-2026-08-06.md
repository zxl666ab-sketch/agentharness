# 阶段五 · 业务闭环 — 真实模型验证（2026-08-06）

> 对应 `docs/agent-upgrade-2026-08-05.md` 第 5 节。真实模型：deepseek-v4-flash（OpenAI 兼容网关，`.env` key）。
> 离线验证与 UI 冒烟见 `docs/evidence/stage-5-closure-offline-2026-08-06.md`。

## 验证（run `bdba637b2090466a86535e50cada1c63`，`scripts/verify_live_stage_evidence.py`）

真实模型端到端链路：对话 → 需求结构化（确定性比价）→ 人工审批 → **采购订单导出**：

- 请求：`RFQ-20260806-9E0ACF`，分析状态 `require_human`，比价快照产出。
- 审批：`procurement_approve_supplier` 成功 → 请求状态 `approved`，审批 id `886748a2cb26407091de65ec85d23cd4`。
- PO JSON：`PO-RFQ-20260806-9E0ACF`，供应商 Alpha Packaging，数量 10000，总金额 37440.00 USD，快照 `433d8a3a…`，approval_id 同上，`evidence_sha256=8c47a8ba…`。
- PO CSV：下载端点返回 200（`text/csv` + `attachment`），内容含表头与上述数据。
- 独立评审（可选开启）：`ai_review` 事件 `verdict=pass`。

## 结论

真实模型下「审批 → PO 导出」端到端可用，满足 5.1 验收；README 与使用层顺手项已在离线/UI 验证中覆盖。
