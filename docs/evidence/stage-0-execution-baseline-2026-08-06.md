# 执行起始基线（阶段一前置复算）· 2026-08-06

> 依据 `docs/agent-upgrade-2026-08-05.md` 第 0 节：正式执行阶段一前，在当前 HEAD 重新运行完整验证并记录实际结果；不得直接把历史数字当作当前验证结果。

## 环境

- 当前分支：`codex/缩减版v1只有采购`
- HEAD：`dc5a261`（docs: verify each stage with the real-model path after completion）
- Python：`.venv`（3.11，见 `pyproject.toml`）
- 日期：2026-08-06
- 工作目录：`D:\个人通用agentharness`

## Python 测试与覆盖率

命令：`.venv\Scripts\python.exe -m pytest --cov=agentharness --cov-fail-under=80 -q`

结果：**225 passed, 1 skipped**；总覆盖率 **80.14%**（要求 ≥80%，达标）。

## Python 质量

命令：`.venv\Scripts\python.exe -m ruff check .`

结果：**All checks passed!**

## Web 测试 / lint / build

在 `web` 目录：

- `npm test`：**4 test files / 14 tests passed**（vitest 2.1.9）
- `npm run lint`：**通过**（eslint --max-warnings 0）
- `npm run build`：**通过**（tsc --noEmit + vite build，产物写入 `src/agentharness/web_dist/`）

## 确定性评测（0 模型调用，可复算）

命令：`.venv\Scripts\python.exe scripts\evaluate_procurement.py run`

结果（`output/procurement-evaluation/raw-results.json`）：

- 冻结数据集：31 用例，`truth_sha256=63647f520bff1ab20e9215cc65e1b246a6f27fcf88cdb226fe7eae72fd6c1ffb`，`frozen=True`
- 字段抽取：617/620（99.52%）
- 人工复核后字段：618/620（99.68%）
- 条目匹配：31/31（100%）
- 成本计算：31/31（100%）
- 硬约束漏判：0（期望违规 17，漏判 0，误报 0）
- 无效报价入选：0
- 推荐正确率/一致性：5/5（100%）
- 处理耗时：总 380.72ms，平均 12.28ms/报价
- 模型调用：0 次 / 0 token / $0

验收门（acceptance）：全部 `true`（case_count≥31、layout≥6、字段抽取≥95%、条目匹配≥90%、成本 100%、硬约束漏判 0、无效入选 0）。

## 历史数字 vs 当前复算

| 项 | 文档历史基线 | 本次复算 |
|---|---|---|
| Python 测试 | 225 passed / 1 skipped | 225 passed / 1 skipped ✅ |
| 覆盖率 | 80.6% | 80.14%（≥80 达标）✅ |
| 确定性评测 | 617/620 | 617/620 ✅ |
| CI | python + web 双 job 绿 | 本地复算全绿 ✅ |

## 结论

当前 HEAD 满足阶段一执行前置条件，历史基线数字与本次复算一致（覆盖率 80.6%→80.14% 为精确复算差异，仍高于门槛）。
