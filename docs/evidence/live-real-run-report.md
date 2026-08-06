# 真实模型单次全链路运行记录（非公开可复现评测）

> 诚实口径：这是**一条**真实模型（deepseek-v4-flash，经 OpenAI 兼容网关）全链路运行的记录，
> 数据来自本地 `output/proc-review-live-real/`（该目录被 `.gitignore` 忽略，不作为干净 checkout 的
> 可复现评测）。它证明“受治理、人在环路的采购比价”编排链路在本次运行中走通，**不**外推为
> 真实模型准确率或成本结论。617/620 等冻结指标是确定性管线（0 次模型调用）的独立证据，见
> `evaluation-summary.json`。

## 运行事实（取自本地 SQLite 运行库，2026-08-06）

| 项目 | 值 |
|---|---|
| Run ID | `b064511d369547a78bf5826eab6ce6a5` |
| 采购需求 | `4df2b31f1eaf4a69bb63e61dc223c322`（RFQ-20260806-4DF2B3，状态 `approved`） |
| Provider / Model | `openai`（OpenAI 兼容网关）/ `deepseek-v4-flash` |
| 终态 | `completed`，输出含逐字标记【采购决策已验证】 |
| 验证 | `verification_result` step 6 `action=pass / passed=true` |
| 审批 | `procurement_approve_supplier` → `allow_once`（arguments_sha256 `00ea78ae…`） |
| 模型回合 | 6 次 `model_turn_start/end`（provider attempts 全部 `completed`） |
| Token | input 16,097 / output 2,593 / total 18,690 / cached input 7,936 |
| 估算费用 | **null（未配置价格，未虚报 $0.0000）** |
| 事件 | 137（text_delta 40、checkpoint 19、span 20、context_manifest 6、tool_* 20、verification_* 8、approval_* 2、run_* 6 等） |

## 阶段时间线（来自 `tool_invocations` / 事件流）

1. **create → capture（step 0）**：`procurement_capture_requirement` 两次因空汇率表被
   确定性校验拒绝（`failed`），模型自纠后补入本位币 `CNY:1`。
2. **capture（step 1）**：`succeeded`；后端检测到「星河包装报价单.pdf」供应商名称待人工复核。
3. **read_request（step 2）**：模型读取需求详情，运行进入 `require_human`（前置 Run
   `0d05011d…` 为同一轮人工停等点）。
4. **人工修正 + analyze**：供应商名称经结构化人工 API 修正后，`procurement_execute_analysis`
   （step 3）成功产出比价快照。
5. **approve（step 5）**：`procurement_approve_supplier` 成功，审批 `allow_once` 一次性放行。
6. **验证（step 6）**：确定性 output validator 通过，运行 `completed`，采购需求写入 `approved`。

## 限制（不要外推）

- 单次运行，未做多轮/多供应商/多模型稳定性测量；
- 运行目录被 gitignore，干净 checkout 无法独立复现；截图仅存于 `docs/evidence/live-real-*.png`；
- 费用为空是因为未配置单价，不等于免费；
- 与 `evaluation-summary.json`（617/620，确定性 0 模型调用）是**两个独立分层**。
