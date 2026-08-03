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

## 复算

```powershell
uv sync --all-groups
uv run python scripts/evaluate_procurement.py run --output output/procurement-evaluation-v3
uv run python scripts/evaluate_procurement.py verify --input output/procurement-evaluation-v3/raw-results.json
```

## 浏览器复现

```powershell
uv run python scripts/generate_procurement_demo.py --output output/procurement-demo-v3
uv run agentharness --workspace . --data-dir output/procurement-public-evidence/data --port 8768 --no-open
```

打开 `http://127.0.0.1:8768`，提交包含单色印刷约束的采购目标和 `q-alpha`、`q-beta`、`q-theta` 三份报价。在报价复核面板将 `q-theta.supplier_name` 人工修正为“星河包装”，点击“开始比价”，核对当前快照后正式选定“华东优包”。

截图中的供应商、价格、审批人和业务编号均为本地合成演示数据。该证据证明本地闭环、规则结果和恢复记录，不代表真实企业上线、未知版式准确率或显著效率提升。
