# 阶段六 1.8 · 提效验证：真人对照（assisted vs assisted+RAG）

> 状态：**待实测**（无真人操作员执行受控盲测，不得虚构提效比例）

## 协议（复用 `scripts/evaluate_procurement.py human-trial` 测量装置）

同一批采购任务（README human-trial 说明的 6 份预注册报价）跑两组：

1. **无 RAG 的 assisted（基线）**：全新数据目录、**不含任何历史成交**（rag_chunks 为空）→
   `human-trial --mode assisted` 由真人操作采价台，脚本自动计时（比价到决策时长）+ 取证。
2. **带 RAG 的 assisted**：同一批任务、**先灌入 5 条相似历史成交**（`scripts/setup_rag_demo.py` 生成，
   或真实历史）→ 同一 `--mode assisted` 流程，脚本自动计时 + 取证。

指标：任务总耗时、比价到决策时长、翻历史/外部记录次数、参考采纳率（`knowledge_reference_adopted` 事件）。
诚实分层：assisted 本身相对纯人工的提效**不**计入 RAG 增量功劳；RAG 增量只对比「无 RAG assisted」与「带 RAG assisted」。

命令模板：

```powershell
# 基线：无历史
uv run agentharness --workspace . --data-dir output/procurement-human-trial-no-rag --port 8766 --no-open
uv run python scripts/evaluate_procurement.py human-trial --mode assisted --observer 匿名测试员-01 --base-url http://127.0.0.1:8766

# 带 RAG：先造历史（5 条已成交），再跑同一批任务
uv run python scripts/setup_rag_demo.py --data-dir output/procurement-human-trial-with-rag --force
uv run agentharness --workspace . --data-dir output/procurement-human-trial-with-rag --port 8767 --no-open
uv run python scripts/evaluate_procurement.py human-trial --mode assisted --observer 匿名测试员-01 --base-url http://127.0.0.1:8767
```

两份 trial JSON（`output/procurement-evaluation/*-trial.json`）+ 采价台自动审批取证合并后，
按 README 规则复算差异与局限，再写提效结论。

## 当前如实记录（2026-08-07）

- 真人对照：**未执行**。本环境无真人操作员，无法生成有效盲测对照记录；按外部/环境阻塞规则标「待实测」，
  不虚构比例。恢复真人操作条件后按上述协议补做。
- 自动化预跑（机器耗时，**不是真人对照，不构成提效证据**）：
  `scripts/pilot_rag_overhead.py`（3 次，确定性管线，fake-free）：
  - 无历史（无 RAG）管线均值 **10.85 ms**
  - 有历史（带 RAG）管线均值 **11.47 ms**
  - RAG 检索/注入增量 **+0.62 ms（约 +5.7%）**
  - 局限：只测确定性管线机器耗时，不包含真人操作、比价阅读、决策与翻历史次数。
- 参考采纳率（1.6 浏览器演示数据，非真人对照）：展示注入 3 条；viewed/adopted 具体数字以 1.7 报告为准（生成时 0/0，无真人操作，不构成提效证据；如重新导出 1.6 演示数据，应先更新 1.7 报告再引用）。

## 待补项

1. 真人执行上述两组 assisted 盲测；
2. 合并两份 trial JSON 并复算：总耗时、比价到决策时长、翻历史次数、参考采纳率；
3. 写入提效结论（或维持「待实测」）。
