# Changelog

All notable changes are documented here. The project follows semantic versioning for public Python and Web API contracts.

## [Unreleased]

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
