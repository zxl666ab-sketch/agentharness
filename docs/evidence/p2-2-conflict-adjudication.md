# P2-2 冲突裁决流程化 + 修正回灌评测集 — 验收证据（2026-08-16）

> 依据 `docs/interview-upgrade-execution-plan.md` §4 P2-2。commit 见 `git log -1`。

## 实现

| 能力 | 位置 | 说明 |
|---|---|---|
| 候选值单选 | `QuoteWorkspace.tsx` FieldEditor | 冲突字段渲染 `.proc-conflict-chip` 候选按钮（来自 `field.conflicts`，含来源小字），点击即提交修正；仍可手输 |
| chosen_from_conflicts 落库 | Flyway `V13__quote_correction_conflict_flag.sql` + `QuoteCorrection` | 新列 `chosen_from_conflicts boolean NOT NULL DEFAULT FALSE`；审计事件 `quote_field_corrected` 附带该标记 |
| 服务端候选校验 | `CorrectionConflictPolicy` + `ProcurementTaskService.correctQuote` | 标记为候选选择时，所选值必须命中 `conflicts[].value`（字符串规范化比较，数字/字符串同候选，null 拒绝），否则 400 `chosen_value_not_in_conflicts` |
| 只读回灌接口 | `GET /api/procurement/corrections`（page/size） | `CorrectionView`（task/quote/供应商/字段/新旧值/候选标记/操作人/时间），contracts 已同步 |
| 导出脚本 | `scripts/export_corrections_to_eval.py` | 拉取全部修正 → `procurement-service/src/main/resources/frozen/frozen-evaluation-corrections.json`（新文件，冻结资源不动，README 标注 synthetic；按 created_at+id 稳定排序，每次全量重建；items 内容幂等，`exported_at` 为导出元数据每次变化） |

## 验收

- ✅ 冲突字段可点选候选值完成修正（Web 单测：点击 chip → `onCorrect(quote, field, value, true)`）
- ✅ 导出脚本可重跑（单测：两次 `build_export` 的 items 内容一致；稳定排序验证；`exported_at` 元数据除外）
- ✅ `frozen-evaluation-corrections.json` 已按当前演示数据生成并提交（当前 0 条修正记录；有新修正后重跑脚本更新）
- ✅ README「冲突裁决与修正回灌（P2-2）」说明回灌流程
- ✅ 冻结资源（`frozen-evaluation.json` / `frozen-evaluation-ext.json` / 黄金契约）零改动

## 数字变化

- Java 测试：137 → **142**（+5：`CorrectionConflictPolicyTest` 3 个 + 既有 GatewayStatusViewTest 复跑；另加 2 个 Java 编译修复）
- Web 测试：55 → **56**（+1 候选 chip 交互）
- 迁移：+1（V13）；contracts：+QuoteCorrectionInput / CorrectionView / CorrectionPage + 2 个 path

## 面试话术更新点

- 「冲突字段不用再手敲——候选值以 chip 形式单选，点击即提交，后端校验所选值确实来自证据候选并落库 chosen_from_conflicts 标记；这些人工修正可以一键导出成评测集扩展候选（新文件，审核后才启用），修正经验回流到评测」
