# Changelog

All notable changes are documented here. The project follows semantic versioning for public Python and Web API contracts.

## [Unreleased]

### Added

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
