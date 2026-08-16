# 公开证据

本目录只包含当前 `ecommerce-packaging-rfq-v3` 合成数据产生的脱敏证据，不包含真实企业、联系人、密钥、SQLite 数据库或完整运行日志。

| 文件 | 证明内容 |
|---|---|
| `evaluation-summary.json` | 31 份冻结报价的可复算指标、验收结论和真值 SHA-256 |
| `workflow-summary.json` | 一次人工字段复核、确定性比价和正式审批的最小闭环事实 |
| `comparison.png` | 三家报价的资格、成本归一化、排序和人工审批入口 |
| `approved-report.png` | 已批准供应商、金额、原件哈希和报告指纹 |
| `runtime-audit.png` | Run、Checkpoint、Verification、Approval、工具与 Token 证据 |
| `approved-report-sample.md` | 浏览器从同一合成任务下载的中文审批报告 |
| `real-model-acceptance.md` | 一次真实模型正常闭环、异常输入和重启恢复的脱敏验收记录 |
| `p1-frontend-usability.md` | P1 工作台可用性升级（共享 viewModel / 下一步引导 / 10 步闭环 / 信息密度）验收，Playwright 走查 22/22 |
| `p2-1-llm-gateway.md` | P2-1 LLM 网关限流/熔断/降级（故障注入全链路演示 + 快照脱敏）验收 |
| `p2-2-conflict-adjudication.md` | P2-2 冲突裁决流程化 + 修正回灌评测集候选验收 |
| `p2-3-semantic-cache.md` | P2-3 语义缓存（版本化 key / TTL / no-op 降级）验收 |
| `p3-1-invoice-three-way.md` | P3-1 发票三单匹配旗舰验收（五服务演示路径 + UI 走查 11/11），截图见 `p31/`（如有） |
| `p3-2-contract-management.md` | P3-2 合同管理旗舰验收（全生命周期 + 真实变更修订 + UI 走查 8/8），截图见 `p32/` |
| `p3-3-supplier-admission-design.md` | P3-3 供应商准入设计笔记（可选阶段降级，实现蓝图） |
| `final-report-p1-p3.md` | P1→P3 全部阶段的最终汇总报告（数字变化 / 证据清单 / 面试话术） |
| `code-audit-report.md` | P1→P3 升级面代码审核报告（3 high / 10 medium 修复清单；见仓库根 `docs/code-audit-report.md`） |

## 复算

```powershell
uv sync --all-groups
uv run python scripts/evaluate_procurement.py run --output output/procurement-evaluation-v3
uv run python scripts/evaluate_procurement.py verify --input output/procurement-evaluation-v3/raw-results.json
```

## 浏览器复现

```powershell
uv run python scripts/generate_procurement_demo.py --output output/procurement-demo-v3
docker compose build
docker compose up -d --no-build
```

打开 `http://127.0.0.1:8741`，提交包含单色印刷约束的采购目标和 `q-alpha`、`q-beta`、`q-theta` 三份报价。在报价复核面板将 `q-theta.supplier_name` 人工修正为“星河包装”，点击“开始比价”，核对当前快照后正式选定“华东优包”。

截图中的供应商、价格、审批人和业务编号均为本地合成演示数据；真实模型验收的运行 ID、Token 和结果见 [`real-model-acceptance.md`](real-model-acceptance.md)。这些证据不代表真实企业上线或未知版式准确率。
