# 采价台本地发布检查清单

从仓库根目录运行。测试数据只写入 `output/`；不得覆盖归属不明的工作树修改，也不得提交数据库、密钥、完整运行日志或旧真人实验数据。

## 自动门槛

```powershell
uv sync --all-groups --frozen
uv run ruff check .
uv run pytest --cov=agentharness --cov-report=term --cov-fail-under=80 -q
uv build

Set-Location web
npm ci
npm test
npm run lint
Set-Location ..
uv run python scripts/check_web_build_determinism.py

uv run python scripts/evaluate_procurement.py run --output output/procurement-evaluation-v3
uv run python scripts/evaluate_procurement.py verify --input output/procurement-evaluation-v3/raw-results.json
```

必须同时满足：

- 后端覆盖率不低于 80%，Ruff、wheel/sdist 构建通过。
- 前端测试、ESLint、TypeScript/Vite 构建通过，连续两次 Web 构建逐字节一致。
- 冻结集恰为当前 v3 的 31 份、6 种版式；真值 SHA-256 为 `63647f520bff1ab20e9215cc65e1b246a6f27fcf88cdb226fe7eae72fd6c1ffb`。
- 字段抽取不低于 95%、物料匹配不低于 90%、金额 100%、硬约束漏检 0、不合格错误入选 0。
- `docs/evidence/evaluation-summary.json` 与同次冻结复算一致。

GitHub Actions 使用 [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) 执行同类 Python 与 Web 门槛。

## 浏览器闭环

```powershell
uv run python scripts/generate_procurement_demo.py --output output/procurement-demo-v3
uv run agentharness --workspace . --data-dir output/procurement-release-smoke-v3 --port 8768 --no-open
```

在 Chromium 的 1440×900 与 390×844 视口检查：

1. 提交采购目标和 XLSX/PDF 报价后只创建一个采购任务、Session 和 Run。
2. 低置信度字段进入 `require_human`；对话文本不能修改报价，结构化人工修正后 `/analyze` 复用原 Run。
3. 组合分析工具内部完成解析、物料身份匹配、历史、比价、复算与人工选择项，金额可由原件独立复算。
4. 正式决定必须产生 `procurement_approve_supplier` 的 `allow_once` Approval，并绑定当前快照。
5. 修改报价后旧快照和旧审批被拒绝；刷新与进程重启后批准状态、Checkpoint 和报告指纹一致。
6. 最终 Runtime 报告为 `passed`，历史 Verification 失败只在尝试详情中显示，不作为最终红色告警。
7. 浏览器控制台无错误或警告，布局无不可操作的遮挡或文本溢出。

当前合成闭环截图、最小运行汇总和中文报告见 [`docs/evidence/`](evidence/README.md)。

## 故障与安全

- `execution_enabled=False` 时所有采购 POST 返回 403 且不创建 Session、Run、任务或 Artifact。
- 二进制原件按字节保存；公共文本和 JSON 才进入脱敏流程。
- 重复附件和创建审计先完整预检，再以单个事务写入。
- 正式决定、任务冻结和审批审计在同一 SQLite 事务内提交。
- HTTP 取消传播至内部分析/审批任务，响应失败后不会后台继续提交。
- 非法数值、超限 XLSX、否定语义、过期报价和错误物料均有回归用例。

## 可选真实模型

真实模型不属于本地发布完成条件。只有用户明确授权模型、价格、费用上限和网络调用后，才可在全新数据目录设置 `AGENTHARNESS_PROCUREMENT_PROVIDER=openai`，并按目标网关设置 `AGENTHARNESS_PROCUREMENT_REASONING_EFFORT`（例如 `max`）。至少预注册“完整报价直达”“一次人工复核恢复”“冲突/不合格报价淘汰”三类场景；每场保存 Run 报告、采购报告、Checkpoint、Approval、Token、费用与失败记录。

未针对当前代码重新执行并公开可复算证据前，不得沿用历史真实模型回合数、费用或通过结论。

## 最终树检查

- 唯一产品入口是采购工作台；旧通用 Run Composer、Session Sidebar、Tool Timeline、Markdown 消息和独立 Request Form 不存在。
- `react-markdown`、`remark-gfm`、临时简历副本、Playwright 会话、缓存和本地日志未进入 Git。
- `src/agentharness/web_dist/` 与当前 Web 源码一致，README 只链接已跟踪的 `docs/evidence/`。
- 简历与 README 只声明采购产品实际使用且能由当前证据证明的能力，不写 RAG、Redis、Kafka、LangGraph 或多 Agent。
