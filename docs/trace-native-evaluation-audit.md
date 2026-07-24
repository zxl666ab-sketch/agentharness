# Trace-native evaluation audit

Date: 2026-07-24

## Baseline

The repository was audited before implementation. The worktree already contained broad,
uncommitted user changes across the runtime, context planner, verification, eval, API, CLI,
Web application, tests, packaged Web assets, and acceptance output. Those changes are treated
as the starting point and must not be reset, cleaned, or reformatted as a side effect of this
goal.

Baseline commands:

| Command | Exit | Evidence |
|---|---:|---|
| `uv run pytest -q` | 0 | 311 passed, 2 skipped |
| `uv run ruff check src tests` | 0 | All checks passed |
| `uv run pyright` | 0 | 0 errors, 0 warnings |
| `uv run agentharness doctor` | 0 | SQLite integrity ok, schema 4, Web and browser ready |
| `web: npm run test` | 0 | 8 files, 42 tests passed |
| `web: npm run lint` | 0 | no ESLint findings |
| `web: npm run build` | 0 | TypeScript and Vite build passed |
| `web: npx playwright test` | 0 | desktop, tablet, mobile passed |

The Web unit baseline emits existing React `act` and server-rendered `useLayoutEffect`
warnings. They are warnings rather than gate failures and are retained as baseline noise.

## Existing user changes

The initial `git status --short` was captured before implementation. Existing modified or
new source areas include:

- Runtime/contracts/storage: `contracts.py`, `runtime.py`, `context.py`, `verification.py`,
  `harness.py`, `sqlite.py`, redaction, filesystem and shell tools.
- Eval/API/CLI: eval runner and exports, AI judge, API server/compatibility, CLI workbench,
  companion Web process, view model and command entry points.
- Web: the application shell, inspector, run list, timeline, trace projection, evaluation
  dashboard, compatibility client, split stylesheets, tests and Playwright scenario.
- Tests and artifacts: new verification, context, API grade, file-version and run-end eval
  tests; packaged Web assets; `.playwright-cli` and `output/acceptance-20260724-1900`.

This goal will add focused trace/evaluation modules and tests. It will necessarily integrate
with the already-modified contracts, harness, verification, eval runner/report/baseline,
storage/API/CLI and Web evaluation surfaces. Unrelated user edits remain untouched.

## Capability audit

| Area | Current evidence | Gap to close |
|---|---|---|
| Runtime facts | Versioned `EventEnvelope`, messages, artifacts, context manifests, spans | No canonical trace projection or trace completeness state |
| Tool facts | Calls/arguments in assistant messages; results/errors/artifacts in tool messages/events | No unified pairing, count/argument/result matching, or evidence references |
| Verification | `VerificationLoop` dispatches deterministic/file/command/AI validators | Reconstructs a separate simplified trajectory instead of using a shared evaluator |
| Offline eval | `run_suite` reruns a case, then builds `Trajectory` from result/messages | No immutable snapshot and no side-effect-free offline re-evaluation |
| Deterministic eval | Status/output/budget/required tools and subsequence order | Missing exact/strict/unordered modes, schemas, artifacts, safety and first divergence |
| Diagnosis | Verification feedback has failures and recovery hints | No root-cause contract, abnormal-span classification or read-only probes |
| Judge | One manual sample, fixed prompt, JSON validation and redaction | No rubric version, sampling variance, consistency, calibration, trust state or fallback |
| Regression | New failure, score/token/mean-latency comparisons and CLI exit code | No canonical reports, label/model metrics, percentiles, divergence distribution or Wilson CI |
| Web | Single-run deterministic/AI dashboard plus trace inspector | No evaluation-only overview/trajectory/root-cause/judge/regression views |

## Requirement-to-evidence map

| Requirement | Planned test/evidence |
|---|---|
| Canonical trace projection and compatibility | Unit fixtures for missing/out-of-order/legacy events, parent spans, interruption/resume and partial state; integration projection from a real Harness run |
| End-to-end redaction and artifacts | Sentinel-secret projection/snapshot/API tests and content-addressed trace/snapshot artifacts |
| Shared `TrajectoryEvaluator.evaluate(trace, policy)` | Unit matrix for exact/strict/subset/unordered plus output, JSON, file/artifact, tool, ordering, lifecycle, budget and safety checks |
| Structured checks and first divergence | Assertions on `CheckResult`, `EvidenceRef` and real span/event identifiers for each failure family |
| Health-only/unscored semantics | Contract, runner, API and Web tests proving no configured quality assertion is never rendered as 100/100 |
| Offline replay | Spy adapters/tools/network/workspace tests proving zero calls/writes while two policy versions re-evaluate one snapshot |
| Diagnose to Probe | Real failing trace with invalid arguments and retry; diagnosis category, first divergence, probe source and evidence assertions |
| Trusted Judge | Independent multi-sample fake judge tests, injection corpus, variance/consistency/abstain, failure fallback and hard-rule precedence |
| Calibration | Synthetic-labelled import/export plus accuracy, precision/recall/F1, kappa, Spearman, MAE, consistency and per-task bias tests; production trust remains `unverified` without real labels |
| Regression and CI | Golden trace/case comparisons, group metrics, percentiles, Wilson interval, deliberate CLI gate failure and repaired pass |
| Evaluation Web | Component tests and desktop/tablet/mobile Playwright flows for overview, trajectory, root cause, judge and regression evidence navigation |
| Backward compatibility | Existing full suite plus schema migration tests, legacy report/event projection and opt-out paths |

## Architectural boundary

Runtime remains the producer of facts. Canonical trace is a redacted projection over the
existing Event, Message, Artifact, Context Manifest, Checkpoint and run records. Evaluation,
verification, diagnosis, replay, judge and regression consume the shared trace and evaluation
contracts; they do not introduce a second runtime write protocol or a second persistence
system. Optional adapters are added only where a real alternate implementation is tested.
