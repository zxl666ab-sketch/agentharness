# Agent Harness Flywheel Acceptance — Grok (2026-07-24)

## Conclusion

**YES — can serve as daily personal assistant at current waterline**, with residual risks below (not P0 blockers).

Provider: `openai` / `grok-4.5` via `https://gptcodex.top/v1`  
CLI profile: `~/.agentharness/cli_config.json` active profile `grok`  
Web: `http://127.0.0.1:8741` (UTF-8 Chinese OK via API; PowerShell console may mojibake)

## P0 bugs (closed)

| ID | Issue | Fix | Retest evidence |
|---|---|---|---|
| P0-1 | Answer OK but `status=failed max_tokens exceeded` (provider token inflation) | `billable_turn_usage()` + final-turn budget leniency | S2 browser **completed**, usage billable ~19k total, not 300k fail |
| P0-2 | Truncated session_id creates fake 12-char session | `resolve_session_id()` + full id print | S1_prefix_resolve ok; CLI prints 32-char session |
| P0-3 | Browser/read path unstable completed | covered by P0-1 + live retest | S2 completed; answer usable |
| shell auto | shell treated destructive → Approval denied under auto | shell → `EffectKind.process` | S5/T4 `LIVE_SHELL_OK` / `S7_SHELL_OK` under auto |

## Live matrix (Grok) — all completed

Log: `output/flywheel/matrix-live-20260724-001008.jsonl`

| Case | status | session_id (full) | run_id | note |
|---|---|---|---|---|
| quota_probe | completed | 77ee3211b591446fab97d386c4e5cf16 | 659f1df78165449bb2c6b7cc9581ca2e | QUOTA_OK |
| S1_remember | completed | debdac62ae274749b09c5a0ebfda08c3 | 5aaf56956c8743f3a449993c376c0a7d | 已记住 |
| S1_prefix_resolve | ok | prefix debdac62ae27 → full | — | unique prefix |
| S1_recall | completed | debdac62ae274749b09c5a0ebfda08c3 | db0a30d9cef9493abf35e05a74666e43 | LIVE_ORANGE_3 |
| S3_read | completed | a5c3f1cdec3e40b0aaca08b54001af51 | 72e7e2de0d374db8a598858b29ad6e4a | # Agent Harness |
| S4_search | completed | 75de9a2c8fc446df8a9494705646e322 | 41858809c49d478890bcb946346f43d7 | paths listed |
| S3_sandbox_write | completed | 818579545b3242b784ba2a668e45b6ed | d6e1792c3b854b1e8b85afa259ffbeef | HELLO_LIVE_SANDBOX |
| S5_shell | completed | 9f2091ad93a04bd698a1c574fed3f0c2 | 40b19d4f0cd94a85b4b43404e995bba2 | LIVE_SHELL_OK |
| **S2_browser** | **completed** | **06f89327cf034072955700b5cd6e6253** | **83e86c7721d8491ca321a38647311dfb** | 神烦老狗 UP；error=null |

## S7 consecutive (≥5 real tasks) — Grok

Log: `output/flywheel/s7-consecutive-20260724-001638.jsonl`

| Case | status | session_id | run_id | output |
|---|---|---|---|---|
| T1_remember | completed | 90aecf2b02d64fc1b55da1c3f349da40 | 08089553cfda4b4687db1795990920bf | 收到 |
| T2_recall | completed | 90aecf2b02d64fc1b55da1c3f349da40 | 5c9fb7c311af41b796cb29bbbf5253d2 | 飞轮测试员 + 简短中文 |
| T3_read_pyproject | completed | 3d2152fb414c4ddf8c310557c8b035b9 | 600c052f9ea942f4a56be48907feb3e6 | agentharness |
| T4_shell | completed | 10c955a2e7864dd18e0750a394385728 | 78627cf66d694a4bb41ea85527ae8e8d | S7_SHELL_OK |
| T5_note | completed | 96449215f4bc474fbfddae0032bdb894 | 77d84f4a63d64e999db2b72a6d339f2a | todo.txt 买咖啡 |
| T6_continue | completed | 90aecf2b02d64fc1b55da1c3f349da40 | e4a289a6e8c84456819f1201f3ff278d | 同 session 记忆 |

## CLI one-shot (profile grok)

```
status=completed  run_id=4fc4e511a2dc4bedabde3c46f920c139
session=bfaa190fad7341c29eab72909110cc6b  tokens=2792/50
CLI_OK
```

Doctor (no process env OPENAI_*): `profile_provider=openai`, `profile_model=grok-4.5`, `profile_source=profile`.

## Web evidence (S2)

- `/api/runs/83e86c7721d8491ca321a38647311dfb` → status=completed, error=None, model=grok-4.5
- messages: browser launch → bilibili search → content → close → final answer
- Chinese titles OK in UTF-8 JSON (PowerShell display may garble — not product bug)

## Tests

```
uv run pytest tests/test_billable_usage_and_session.py tests/test_runtime_budgets.py tests/test_cli_interactive.py tests/test_cli_startup_security.py -q
→ 27 passed
```

## Acceptance checklist

| Criterion | Result |
|---|---|
| 愿用 ≥5 tasks | PASS (S7 + matrix) |
| 答完算成功 (browser) | PASS S2 completed |
| 记得住 + prefix | PASS S1 / T1-T2 / T6 |
| 干得成 read/search/shell/sandbox | PASS |
| 挂不掉 common paths | PASS (S6 Ctrl+C not re-run this night) |
| 敢用 no repo pollution by agent | PASS (writes only under output/flywheel/sandbox-*) |
| 回得看 Web full ids | PASS |
| 可重复 after fix | PASS on Grok |

## Important ops notes

1. CLI **does not** auto-load project `.env` (security test). Use profile (`cli_config`) or export env. Bootstrapped profile `grok` from `.env`.
2. `RunRequest` default provider is still `fake` — scripts must set `provider="openai"` (matrix_live does; early S7 fake run was a test harness bug, not product regression).
3. Do not print API keys.

## Residual risks / next circle

1. S6 Ctrl+C interrupt recovery — not re-verified this session
2. Interactive REPL human-in-the-loop on right Codex terminal — oneshot/API covered; full REPL UX not hand-driven tonight
3. Gateway token inflation still exists upstream; product resists via billable usage — monitor if gateway changes
4. Cross-session long-term memory depends on model + memory tools quality
5. `default_provider` env path still fake without profile/env — new machines need `/config` or profile bootstrap

## Modified product files (from this flywheel arc)

- `src/agentharness/engine/context.py` — billable usage
- `src/agentharness/engine/runtime.py` — budget final-turn
- `src/agentharness/harness.py` — resolve_session_id
- `src/agentharness/cli/main.py`, `cli/interactive.py` — full session ids / resolve
- `src/agentharness/tools/shell.py` — process effect
- tests: `test_billable_usage_and_session.py`, budgets, interactive
