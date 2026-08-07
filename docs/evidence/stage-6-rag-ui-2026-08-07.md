# 阶段六 1.6 · 前端呈现（历史成交参考栏）证据（2026-08-07）

## 实现

- `web/src/procurement/KnowledgeReferences.tsx`（新增）：比价页「历史成交参考」区块——供应商 / 成交价 / 到货成本 / 成交日期 / 是否成交 / 来源 `request_reference`；默认展示 top-3，可「展开全部 5 条」到 top-5；空态文案「暂无相似历史成交」；每条带「查看详情 / 有帮助」轻量交互；「查看详情」打开来源弹窗（含来源哈希、规格摘要、价格、备注）。
- `web/src/procurement/ComparisonView.tsx`：空态与已分析态都渲染历史参考区块；新增 `onKnowledgeFeedback` 回调。
- `web/src/procurement/ProcurementWorkbench.tsx`：`knowledgeFeedback` fire-and-forget（不阻塞流程）→ `POST /api/procurement/requests/{id}/knowledge/feedback`（chunk_sha256 + action）。
- `web/src/procurement/types.ts` / `api.ts`：`KnowledgeReference` 类型、反馈 action 与 API 客户端。
- `web/src/procurement/procurement.css`：区块样式。
- `scripts/setup_rag_demo.py`（新增）：离线演示数据生成器（5 条已成交历史 + 匹配/不匹配请求）。

## 硬门槛

| 项 | 结果 |
|---|---|
| web 测试 | 17 passed（4 文件；新增有历史/无历史/展开 top-5/反馈点击） |
| web lint | `npm run lint` 通过（0 warnings） |
| web build | `npm run build` 通过（tsc + vite） |
| web_dist 重建 | 已重建；`scripts/check_web_build_determinism.py` 4 文件 byte-identical |
| Python 全量 | 269 passed / 1 skipped，覆盖率 81.54%（门槛 80%） |
| ruff | `ruff check .` 通过 |

## UI 浏览器验证（Playwright，http://127.0.0.1:8741，数据目录 output/rag-ui-data）

| 状态 | 结果 |
|---|---|
| 有历史 | 匹配请求比价页显示「历史成交参考」，top-3 默认展示（供应商/成交价/到货成本/日期/已成交/来源 RFQ 编号） |
| 展开 top-5 | 点击「展开全部 5 条」→ 5 行全部显示，按钮变「收起」 |
| 查看详情 | 点击后打开详情弹窗（含来源哈希 68be8967…） |
| 反馈点击 | 点击「查看详情」「有帮助」后无 console error；DB 落库 `knowledge_reference_viewed` / `knowledge_reference_adopted`（payload 仅 chunk_id+action） |
| 无历史 | 不匹配请求比价页显示「暂无相似历史成交」 |

截图：`docs/evidence/stage-6-rag-ui-history-2026-08-07.png`、`docs/evidence/stage-6-rag-ui-empty-2026-08-07.png`。

复现命令：

```powershell
uv run python scripts/setup_rag_demo.py --data-dir output/rag-ui-data --force
uv run agentharness --workspace . --data-dir output/rag-ui-data --port 8741 --no-open
# 浏览器打开 http://127.0.0.1:8741
```
