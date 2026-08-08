# Changelog

All notable changes are documented here. The project follows semantic versioning for public Python and Web API contracts.

## [Unreleased]

### Fixed

- 解析器发票能力：补齐“不能开具增值税专用发票/普通发票”等完整变体，不再被正向“可开/能开”子串误判为可开票；`_infer_common` 反向证据同步覆盖增值税变体。
- 解析器单位换算：报价描述中的“N 丝”按 1 丝 = 10 µm 换算，“cm”尺寸按 ×10 换算为 mm，避免厚度/尺寸硬约束误判。
- 解析器 XLSX 表头检测阈值从 4 降到 3：3-4 列报价表不再被当成键值行，供应商名不会被静默写成第二个表头（如“单价”）。
- 运费推断：排除“运费不含税”（税口径）误触发“运费另计”；推断摘录改为命中原文片段而非硬编码文案。
- 冻结评测验收新增 `false_positive_quote_zero`：误杀合格报价（`false_positive_count > 0`）也会使 CI/验收失败；同步更新 `docs/evidence/evaluation-summary.json`。
- RAG 材质规范化改为与比价层一致的词边界匹配：`pet`/`PET膜` 不再被误判为 PE、`apple` 不再被误判为 PP。
- RAG 知识注入落实“分级注入 top-3”：模型只收到 top-3 紧凑文本（`knowledge_injection`），top-5 参考保留给 UI/审计；注入预算断言针对实际模型载荷。
- 审批审计：正式决策记录的 `actor`/`note` 以采购员提交值为准，模型在审批工具参数中伪造的 actor/note 不再污染审计。
- 引擎压缩：`summarize_history` 任意异常统一包装为 `CompactionError`，压缩失败时跳过压缩继续运行，不再使整条 run failed。
- `cancel-run` 对不存在的 request 返回 404；`start_conversation` 的 `RuntimeError/ValueError` 映射为 409 而非 500。
- `/api/health` 走 `redact_public_obj`，不再泄露 data_dir 绝对路径。
- SQLite 只读连接改用 `as_uri()` 构造，data_dir 含 `#`/`?` 时不再解析失败。
- `.env` 非 UTF-8/损坏时 `load_project_env` 按缺失处理，不再启动崩溃。
- `create_request`/`bind_run` 的建单+审计放入同一事务，避免审计缺失的请求行。
- 运行报告证据改为分页读取全量事件，不再受 10,000 条前缀截断影响。
- 前端：SSE 白名单补 `run_budget_stopped`；分析失败/取消/中断/预算停止后 busy 不再永久卡“分析中”；`require_human` 仅在无比价快照时显示规格澄清框；税率百分比显示消除浮点尾差；报价工作台上传增加前端扩展名/大小/数量校验；Dashboard CSV 导出增加公式注入防护；删除死代码 `TERMINAL_STATUSES`/`eventTone`；审计页运行报告错误走友好文案。
- 真实模型脚本：`verify_live_stage_evidence.py` 的 CLI 预算真正进入 Run（且失败/未审批返回非零退出码）；`run_procurement_live_batch.py`/`run_rag_real_model.py` 在 live 模式强制要求单价与费用上限配置。
- `evaluate_knowledge.py` 默认输出改为 `output/` 并加 `--force`，误跑不再覆写已跟踪证据；`pilot_rag_overhead.py` 删除数据目录需要 `--force`。
- 压测场景生成锚定单一基准日期（`AS_OF`），预期结果不再随生成日期漂移；报价文件名 fallback 去掉“报价单/QUOTATION”后缀，演示输入与真值一致。
- `uv.lock` 重新生成（补 cryptography/cffi/pycparser），CI/Docker/发布的 `--frozen` 恢复可用；README 健康度数字更新为 2026-08-08 复算（324 passed / 1 skipped、覆盖率 81.99%、Web 21 passed）。
- 文档/工具小修：manual-test-kit 去除旧 HEAD 引用与本机绝对路径；`tests/live` 删除对不存在脚本的引用；`analyze_live_batch.py` 无参时取最新 `live-batch-*.json`；`.dockerignore` 排除 `.env`；pyproject 删除重复的 `project.optional-dependencies.dev`。

### Added



- Run timeline endpoint `GET /api/runs/{id}/timeline` and a timeline panel in 运行审计：事件与工具调用合并排序，附每工具耗时/尝试次数/错误码，用于失败归因。
- Usage & cost dashboard：`GET /api/runs`（列表，支持 session/status 过滤）与 `GET /api/metrics/summary`（Token/成本/耗时/缓存命中/预算告警聚合），前端新增“运营仪表盘”页与 CSV 导出。
- Prompt / 工具 Schema / 解析器 / 规则集版本化：每次采购 Run 记录 prompt 与工具 schema 的版本与 SHA-256 指纹，运行报告输出 `versions` 段，运行审计展示“本次运行配置”。
- 独立评审产品化：`review_policy`（off / evidence / warn / gate）配置项；warn/gate 在审批提交前运行独立评审，gate 在评审异议时要求采购员显式勾选确认（`review_ack`），评审事件记录策略与是否审批前。

### Fixed

- 工具结果超过内联预算被截断后，阶段机 `advance_on_result` 不再因非法 JSON 失效：`_invocation_stage` 对截断内容做 stage 标记回退解析，修复“对话/演示流审批永远 409”的卡死（回归测试：截断 capture 结果仍能完成审批）。
- 非回环绑定（`--host 0.0.0.0` 等）未显式 `--allow-remote-execution` 时直接拒绝启动，避免读接口/SSE 在无认证下暴露。
- API Key 改为加密落盘（Fernet + 数据目录内密钥文件），配置文件不再出现明文；旧明文文件仍可读取迁移。
- 审批与修正/导入的 TOCTOU：写事务内重检 decision，`commit_decision` 的 UPDATE 增加状态守卫；并发重复审批的 `sqlite3.IntegrityError` 归一为 409。
- 对话流附件被解析为报价后不再残留 staged 记录（attachments 与 quotes 重复展示）。
- 快照失效后新建运行增加确定性兜底守护：run 结束后仍无快照则直接重跑确定性流水线。
- 解析器内置墙钟预算（`time_budget_s`，API 与对话路径默认 10s），解析线程即使不可取消也会自行中止。
- 报价导入在审批后返回 409（与修正接口一致）；模型配置抽屉在加载完成前打开时同步真实配置、加载中禁用保存；审批后字段修正入口只读化并为待复核字段提供取消按钮；清理演示任务后失效详情缓存并重置选中项；比价页审批后高亮实际批准供应商并在页脚显示名称。
- 采购审计报告接口统一走 public_redact；RAG LIKE 回退转义 `%`/`_`；`supports_invoice` 解析优先识别“可开/专票”正特征；移除 `correct_field` 中不可达的 RAG 同步调用（工具函数保留并继续有测试覆盖）。
- 前端小修：清理成功提示不再用红色错误样式、审批弹窗关闭重开重置、分析中按钮保持禁用直到 run 终态、`waiting_approval` 可停止、通用 API 网络错误中文提示、draft 报价面板显示“待 Agent 结构化需求”、RunReport 审批数 0 时显示 `N / —`、上传失败兜底请求不再产生 unhandled rejection、SSE 刷新定时器随任务切换重新武装。
- 需求规格方向：系统提示词（`procurement-prompt-v2`）与 capture 工具 schema 显式锁定“规格按 宽×长×高 书写，第一个数字是宽度、第二个是长度”，修复真实模型把 `400x300x250mm` 捕获成宽 300 / 长 400、导致三家报价全部被“尺寸超差”误淘汰的问题；fake provider 提取方向加回归测试锁定。
- 厚度公差上限从 100µm 放宽到 5000µm（`api/procurement.py` 与 `service._validated_requirement` 同步），瓦楞纸箱等厚材质的 500µm 公差不再被拒绝，capture schema 同步说明允许范围。
- 模型配置一致性：检测到 .env 模型配置（`OPENAI_API_KEY`/`OPENAI_BASE_URL`/`OPENAI_MODEL` 或 `AGENTHARNESS_PROCUREMENT_MODEL` 等）时，启动以 .env 为准——忽略并停止写入本地 `procurement-model-config.json`；前端配置抽屉同步显示 .env 值并标注“以 .env 为准”；保存空白 API Key 时回退读取环境变量（不再用旧密钥屏蔽 env）。


- Re-analysis after a quote correction is now deterministic: “开始比价” regenerates the comparison snapshot even when the resumed model refuses to repeat the analysis tool. The backend resumes with an explicit “snapshot invalidated, must re-run” instruction, exposes `requires_reanalysis` in the agent state, and falls back to the deterministic pipeline (with a UI-refresh run event) when the resumed run ends without a fresh snapshot.
- Quote correction inline editors now collapse after a successful save instead of staying open on already-accepted fields.
- Draft requests no longer render placeholder “1 个” / “undefined” facts; the header and sidebar show 待识别 while the agent is still reading.
- Run reports label `require_human` conclusions as “待人工处理原因” instead of “失败原因”, and the frontend RunReport type now includes the `require_human` status the backend actually returns.
- The “开始比价” button is disabled and labeled “已比价” once a valid comparison snapshot exists, so it no longer silently no-ops.

### Changed

- Correcting the shipping fee on a quote whose “是否含运费” is 是 now shows an inline hint explaining that the freight is already included and will not be added again.
- The comparison table column “成本指数” is renamed “性价比指数” with a tooltip explaining the formula (higher is better).
- The agent system prompt and capture-tool schema now pin the `fx_rates` direction (1 unit of the quoted currency → base-currency units), preventing real models from inverting e.g. USD/CNY=7.2 when the base currency is USD.

### Added

- Procurement sourcing workbench for ecommerce packaging: structured requests, bounded XLSX/text-PDF quote imports, evidence-backed fields, confidence review and manual corrections.
- Deterministic Decimal landed-cost normalization, hard-constraint qualification, immutable supplier-comparison snapshots and mandatory one-time human supplier approval.
- Procurement analysis linkage to Harness runs, terminal checkpoints, approvals, original/snapshot artifacts, event evidence and restart-stable audit reports.
- Frozen 31-quote truth set spanning six independent XLSX/PDF layouts and 22 anomaly or boundary combinations, plus reproducible demo generation and extraction/matching/cost/constraint/recommendation/time/model-cost metrics.
- Controlled one-tester blind comparison with server-derived assisted evidence. A single real-model full-chain run record exists in the workspace (screenshots under `docs/evidence/`), but it is not yet organized into a reproducible public evaluation, so no real-model accuracy or cost claim is made; the 617/620 frozen metrics are deterministic and call no model.
- Procurement approval recovery that resumes the public Harness run once when a successful approval lacks the exact verification marker, plus a guard that blocks supplier selection until the current run has successfully verified the comparison.

### Changed

- Procurement is now the only public product line. Generic Shell, Docker, browser, MCP, memory, delegate and skills modules, together with their generic Web control routes and tests, are no longer shipped. The Run/Checkpoint/Approval/Event/Artifact runtime remains only as the procurement audit and recovery substrate.
- Historical SQLite migrations remain readable for existing databases; they are compatibility data, not active product capabilities.

- Procurement Run reports with explicit accepted/failed/human-review/unverified conclusions, output verification attempts, complete tool and approval audits, referenced Artifacts, usage, event trace and a reproducible public-evidence SHA-256.
- Auto-compaction: when live history crosses `context_compact_ratio × max_context_tokens` (default 80%), the engine folds old message groups into a rolling model-written summary rendered in the stable prefix. Tool pairs stay atomic, the latest user goal and the newest groups stay verbatim, originals are externalized to an artifact, and the compacted view is checkpointed for resume. Every failure path degrades to the planner's externalization fallback.
- `context_compacted` event (applied/skipped, tokens before, coverage, artifact id) surfaced in the Web activity feed.
- Prompt-cache metrics: the OpenAI adapter reads `cached_tokens` from both Chat Completions and Responses usage shapes; `Usage` gains cumulative/per-turn `cached_input_tokens` and a serialized `cache_hit_rate`; provider attempts record per-attempt cache reads; the Web run header shows the hit rate.
- Cache-aware cost: optional `PricingConfig.cached_input_per_million_usd` prices cached input tokens at the discounted rate in run cost estimates and budgets.

## [0.3.0] - 2026-07-25

### Changed

- Replaced the CLI and readonly inspection console with one Web-first task workspace.
- Added background Web run ownership, immediate run identities, SSE output, interactive approvals, stop and resume.
- Restricted Web runs to configured workspace roots and relative subdirectories; new runs default to readonly and `approval=ask`.
- Reduced `Harness` to the Agent Runtime facade and moved deterministic/independent-model verification into the core engine.
- Added a standalone Web launcher and disabled execution by default on non-loopback binds.
- Consolidated production model execution on the OpenAI adapter; compatible gateways use the same path.

### Removed

- CLI commands, profiles, terminal workbench and keyring-specific configuration.
- Eval/Judge/Diagnosis/Replay/Regression modules, datasets, reports and dashboards.
- Canonical evaluation trace projection, redacted evidence exporter and the old Web Inspector UI.
- Dependencies used only by the removed surfaces (`typer`, `rich`, `prompt-toolkit`, `keyring`, Eval DSL libraries and React Virtuoso).
- Non-OpenAI support, production Fake Provider registration, Provider selection and cross-Provider fallback.

### Compatibility

- Historical SQLite migrations and Agent run data remain readable.
- `Harness.run/resume/cancel`, Provider and Tool protocols remain available.
- v0.3 is intentionally breaking for CLI and Eval imports.

## [0.2.0] - 2026-07-25

### Added

- Versioned, fingerprinted single/multi-turn eval suites with isolated workspaces, exact approvals, local HTTP fixtures, provenance, cost, integrity, JSON and JUnit reports.
- Twelve-case offline core suite and six-case manual live milestone suite.
- Provider retry with exponential backoff/jitter, no replay after partial output, explicit-only fallback, and attempt provenance.
- Run leases, heartbeats, process-loss recovery, pin/unpin, storage statistics, dry-run/apply GC, orphan artifact collection, and explicit SQLite compact.
- Governed long-term memory scope, dedupe, update/delete, expiry, use count, BM25/freshness ranking, and mandatory mutation confirmation.
- OS keyring credential references and verified legacy plaintext migration.
- Token pricing, estimated USD usage, strict cost budgets, and unknown-price failure behavior.
- Redacted run export with private-body/path/credential removal and SHA-256 manifest verification.
- Explicit Local/Docker Shell executors with hardened Docker defaults and doctor diagnostics.
- Web provenance views for cost, fingerprints, provider attempts, recovery, and safety decisions.
- MIT license, security policy, architecture, threat model, bilingual README, deterministic Web build checker, and 80% CI coverage gate.

### Changed

- Provider over-budget output now fails deterministically instead of being accepted as complete.
- Public API/SSE output hides personal absolute paths while preserving internal context fingerprints and historical SQLite compatibility.
- Web unit tests no longer emit React act or SSR layout-effect warnings.
- Web build identity is derived from source content, so identical source produces byte-identical artifacts.

### Compatibility

- Existing `Harness`, CLI commands, `RunRequest` defaults, ToolSpec/tool-call arguments, and historical SQLite data remain supported.
- Schema migrations are forward-only and applied transactionally.
- Source + `uv` remains the supported distribution model for v0.2.
