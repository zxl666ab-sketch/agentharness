# P3-3 供应商准入 — 设计笔记（降级版，2026-08-16）

> 计划 §5 将 P3-3 标注为**可选**（3–4 天）；按 §6.5 时间盒纪律，本次无人值守执行在 P3-1/P3-2 两个旗舰阶段全部交付后，将 P3-3 降级为「预研笔记 + 面试话术」，不烂尾、不拖延最终报告。本文档即实现蓝图，后续新 goal 可据此直接开工。

## 现状与衔接（动手前先读）

- `supplier/` 已有 5 个文件：`Supplier`（ACTIVE/PAUSED/BLACKLISTED 三态）、`SupplierController`、`SupplierDtos`、`SupplierRepository`、`SupplierService`。
- 绩效口径冻结（`docs/platform-upgrade-design.md` 4.6）：黑名单强制封顶 30 分、活跃度 `min(20, 报价次数×2)`、合作状态 ACTIVE=20 / PAUSED=10 / BLACKLISTED=0——**准入阶段不得改动该口径**，准入只新增"准入档案 + 准入评分卡"，落地到 `supplier.status=ACTIVE` 时复用现有变更链路。
- 黑名单判断已有：`SupplierService` 中 `STATUS_BLACKLISTED` 校验（准入申请若供应商已在黑名单 → 直接拒绝）。
- 复用模式（与 P3-1/P3-2 完全一致）：Agent 只做解析与画像文本，数值/规则全部 Java 权威 + 状态机 + 审计事件 + Outbox AgentCommand。

## 设计蓝图

### 1. 数据模型（V16__supplier_admission.sql 草案）

```sql
CREATE TABLE supplier_admission (
  id VARCHAR(32) PRIMARY KEY,
  supplier_name VARCHAR(300) NOT NULL,           -- 与 supplier.name 按名关联（K1 惯例，不建外键）
  supplier_id VARCHAR(32),                       -- 既有档案命中时回填
  biz_license_no VARCHAR(64),                    -- 营业执照号（UNIQUE）
  license_expiry DATE NOT NULL,
  main_categories VARCHAR(500),
  status VARCHAR(20) NOT NULL,                   -- SUBMITTED/UNDER_REVIEW/APPROVED/REJECTED
  doc_refs JSON,                                 -- 资质文件 artifact 引用（营业执照/证书）
  risk_profile JSON,                             -- Agent 画像摘要（模式 A+C 文本 + 结构化标签）
  scorecard JSON,                                -- Java 评分卡明细（见下）
  reject_reason VARCHAR(500),
  reviewed_by VARCHAR(100), reviewed_at DATETIME,
  created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
  version INT NOT NULL DEFAULT 0                -- 乐观锁，与 invoice/contract 一致
);
```

### 2. 状态机（注册进 `OrderStateMachineConfig.stateMachineRegistry`）

```
SUBMITTED --START_REVIEW--> UNDER_REVIEW --APPROVE--> APPROVED --ACTIVATE--> (supplier.status=ACTIVE)
                            UNDER_REVIEW --REJECT--> REJECTED（原因必填）
SUBMITTED --REJECT--> REJECTED（黑名单/证件过期直拒）
```
- APPROVED 后落地既有 `Supplier.changeStatus(ACTIVE)`（或新建档案），并记 `business_type=supplier_admission` 审计事件（supplier_admission_submitted / admission_under_review / admission_approved / admission_rejected / supplier_activated）。
- 拒绝/审批各状态经状态机校验 + 乐观锁 409，风格与 invoice/contract 一致。

### 3. Agent 边界（模式 A+C，与 P3-1/P3-2 同构）

- Python `qualification_parsing.py`：
  - `parse_qualification`：营业执照/证书 xlsx/pdf 确定性字段抽取（执照号/企业名称/有效期/经营范围）；
  - `build_admission_profile`：风险画像摘要 + 准入推荐理由（模板化文本，**推荐结论只由注入的结构化规则结果决定**，如证件临期/经营范围不符/黑名单命中 → 不建议准入）；
  - `explain_rejection`：拒绝理由的自然语言组织（数字/日期只来自注入的校验结果，评测硬校验）。
- Outbox 扩展：`agent_service.SUPPORTED_OPERATIONS` + `internal_agent.AgentCommandBody` Literal + `SyntheticAgentClient` 增加 `admissionReview`（demo 模式确定性返回）。
- 语义缓存：解析/画像可复用 P2-3 `semantic_cache`（key `semantic:v1:{scope}:{sha256}:{version}`）。

### 4. Java 规则校验 + 评分卡（`SupplierAdmissionPolicy`，纯确定性）

- 必查：执照号格式（18 位统一社会信用代码正则）、有效期（未过期；过期/临期<90 天给风险分）、经营范围关键字 vs 申请类别（不匹配 → 风险分+拒绝）、黑名单（命中 → 直拒）、重复执照号（UNIQUE → 409）；
- 评分卡（100 分制，明细存 JSON）：资质完整 30 / 有效期余量 30 / 经营范围匹配 20 / 既往履约（复用绩效分折算）20；
- 分档：≥80 建议准入 / 60–79 建议人工复核 / <60 不建议；推荐结论文本由 Java 生成，Agent 只负责组织语言（模式 C）。

### 5. 前端：准入中心

- `AdmissionCenter.tsx`：准入列表（状态筛选）/详情（资质字段、风险画像、评分卡明细、拒绝原因）/审批弹窗（通过/退回必填原因）；`SupplierCenter` 加入口；导航「准入中心」+ 角色权限沿用现有 roles。
- `workbenchUrl` 增加 view `admissions`；任务/供应商详情不强制联动（准入与询价任务解耦）。

### 6. 契约 / 评测 / 测试计划

- 契约：`contracts/procurement-workbench.schema.json` + OpenAPI 增加 `AdmissionView/Page/ActionInput/Status` 与 5 个 path（list / submit / detail / startReview / review）；web `types.ts`/`api.ts` 同步；`contracts.test.ts` 对齐。
- 评测：`frozen-evaluation-admission.json`（合成资质场景：正常/临期/过期/经营范围不符/黑名单/重复执照）+ `scripts/evaluate_admission.py`（字段抽取 ≥99% + 画像文本数值引用一致性 + 推荐结论正确性硬校验）；README 标注 synthetic。
- 测试：Java `SupplierAdmissionPolicyTest`（有效期/经营范围/黑名单/评分卡分档）+ 状态机测试；Python `test_qualification_parsing.py`；Web 组件测试；全量门禁 `mvnw test` / `pytest --cov-fail-under=80` / `npm test+lint+build` / `check_web_build_determinism.py`。

## 面试话术（本次未实现，可直接讲设计）

- 「供应商准入同样走『资质文件 → Python 确定性解析 + 画像 → Java 规则校验（有效期/经营范围/黑名单/重复执照）+ 评分卡分档 → 人工审批 → 供应商生效』，与发票三单匹配、合同起草共用同一套状态机 + 审计 + Outbox 架构；准入评分卡的推荐结论由 Java 规则生成，AI 只负责组织语言，杜绝编造。」

## 未实现原因

- 计划 §5：P3-3 明确标注「可选」；§6.5：任务硬时间盒，超时降级为设计笔记不烂尾。
- 本次已交付全部必做阶段（P1 工作台重构 / P2-1 网关熔断 / P2-2 报价纠错 / P2-3 语义缓存 / P3-1 发票三单匹配 / P3-2 合同管理），每个阶段测试全绿 + 独立 commit + 证据落 `docs/evidence/`；P3-3 按下述蓝图可在独立 goal 中 3–4 天内完成。
