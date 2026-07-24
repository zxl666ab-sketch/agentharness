# Agent Harness

Agent Harness is a local, long-lived agent runtime with a streaming interactive CLI, a scriptable one-shot command, sandboxed tools, durable SQLite checkpoints, and a Web run inspector.

The runtime is native `asyncio`; it does not depend on LangChain, LangGraph, or Deep Agents.

## Requirements

- Python 3.11+
- [uv](https://github.com/astral-sh/uv)
- Node.js 20+ to rebuild or test the Web inspector
- Optional provider environment variables for live OpenAI or Anthropic runs

Install the Python environment, build the packaged Web assets, and install the isolated Chromium runtime used by the Browser tool:

```bash
uv sync --all-groups
cd web
npm ci
npm run build
cd ..
uv run playwright install chromium
```

For Web end-to-end development tests, also install the Node Playwright browser version:

```bash
cd web
npx playwright install chromium
cd ..
```

## Start an interactive session

The bare command opens the interactive multi-turn CLI, starts the Web Inspector on the same data directory, and opens it in the default browser. It loads the nearest project `.env` (cwd and parents), then picks provider/model from flags, environment (`.env`), saved `/config` profile, or `fake`:

```bash
uv run agentharness
```

Offline / scripted smoke without live credentials:

```bash
uv run agentharness --provider fake
```

The same Harness and event loop stay alive across turns, so sessions, Browser contexts, MCP connections, approvals, and cancellation behave consistently. Commands available at the prompt:

| Command | Behavior |
|---|---|
| `/new` | Start the next turn in a new session |
| `/sessions` | List recent persistent sessions |
| `/use <id-or-prefix>` | Continue one uniquely matching session |
| `/help` | Show prompt commands |
| `/quit` | Close tools, providers, SQLite, and exit |

`Ctrl+C` interrupts only the active run, kills its process/browser tree, leaves a resumable checkpoint, and returns to the prompt. EOF exits cleanly.

Common launch options:

```bash
uv run agentharness --provider openai --model MODEL
uv run agentharness --session SESSION_ID --data-dir PATH
uv run agentharness --approval auto --cwd PATH
uv run agentharness --no-web
uv run agentharness --no-open --web-port 8741
```

Provider selection order: explicit `--provider` ? `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` from the process env (including auto-loaded `.env`) ? saved profile ? `fake`.

Model defaults: `--model` ? profile ? `OPENAI_MODEL` / `ANTHROPIC_MODEL` ? provider default.

Disable `.env` loading with `AGENTHARNESS_NO_DOTENV=1`, or point at a file with `AGENTHARNESS_ENV_FILE=/path/to/.env`.

## Scriptable commands

Run one task and exit:

```bash
uv run agentharness run "Summarize README.md"
uv run agentharness run "Summarize README.md" --provider fake --approval auto
```

Exit codes:

| Code | Meaning |
|---|---|
| `0` | Run completed |
| `1` | Run failed, timed out, was cancelled, or was interrupted |
| `2` | CLI usage error |
| `130` | One-shot process received `Ctrl+C` |

Operations:

```bash
uv run agentharness runs --limit 20
uv run agentharness resume RUN_ID
uv run agentharness cancel RUN_ID
uv run agentharness doctor
```

`cancel` is cross-process: the owning runtime observes a durable stop request, kills child processes and Browser contexts, checkpoints the run, and finishes it as cancelled. Missing and terminal runs fail with a concise nonzero diagnostic.

## Readonly Web run inspector

Build the assets once, then start the foreground server:

```bash
uv run agentharness web --host 127.0.0.1 --port 8741
```

The command fails clearly if the port is occupied and shuts down on `Ctrl+C`. The Web interface is run-first rather than conversation-first:

- recent parent/child run navigation and status filtering;
- real-time and historical event timeline;
- model rounds, tool calls, redacted arguments and results;
- approval decisions and effects;
- elapsed time, steps, token usage, stored budgets, provider/model, and cwd;
- checkpoint phase, usage, completed/pending tools;
- per-model-turn Context Manifest with System, Workspace Rules, Skills, Memories,
  Messages, Tool Schemas, token/source/inclusion reasons, compaction, artifact links,
  context budget, and stable prefix fingerprint;
- verification phases showing execution → validation → corrective feedback → re-validation;
- selected event/span payloads;
- conversation transcript as secondary context;
- desktop three-pane and mobile three-tab layouts.

The browser receives only real SQLite/API/SSE data. There is no fixture or mock presentation layer in production.

## Configuration and data

| Environment variable | Purpose |
|---|---|
| `AGENTHARNESS_DATA_DIR` | Default persistent data directory |
| `AGENTHARNESS_NO_DOTENV` | Set to `1` to skip auto-loading project `.env` |
| `AGENTHARNESS_ENV_FILE` | Optional explicit `.env` path (still respects existing process env) |
| `OPENAI_API_KEY` | OpenAI / OpenAI-compatible API key |
| `OPENAI_BASE_URL` | Optional OpenAI-compatible endpoint |
| `OPENAI_MODEL` | Default OpenAI model |
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `ANTHROPIC_BASE_URL` | Optional Anthropic endpoint |
| `ANTHROPIC_MODEL` | Default Anthropic model |

Default data directory: `~/.agentharness`.

It contains:

- `agentharness.db` in SQLite WAL mode;
- content-addressed, redacted `artifacts/`;
- isolated Playwright profiles under `browser_profiles/`.

Use a dedicated temporary `--data-dir` for tests. `doctor` reports SQLite integrity/schema, Web build readiness, Browser runtime readiness, providers, tools, sessions, runs, and event sequence health without printing credential values.

## Tools and safety

Built-in tools:

- `read_file`, `write_file`, `search_files` — workspace sandbox with traversal and symlink/junction checks; reads and literal searches stream bounded chunks instead of loading whole files. `read_file` returns a model-visible SHA-256 file version; `write_file(expected_version=...)` rejects stale overwrites with structured re-read guidance. Omitting `expected_version` remains compatible with legacy callers;
- `shell` — destructive approval, detached stdin, minimal environment allow-list, bounded streaming output, timeout, and cross-platform process-tree kill;
- `http_request` — bounded local or remote HTTP requests;
- `browser` — isolated persistent Playwright contexts with per-action timeouts and cleanup;
- `mcp` — stdio/HTTP MCP connections with fault and log isolation;
- `memory_store`, `memory_search` — explicit SQLite FTS memory;
- `list_skills` — safe `SKILL.md` discovery;
- `delegate` — real child runs, readonly by default, bounded depth/concurrency.

Approval policy:

| Effect | `ask` | `auto` | `never` |
|---|---|---|---|
| pure / workspace read | allow | allow | allow |
| workspace write / process / network | prompt | allow | deny |
| destructive | prompt once or deny | still prompt | deny |

Every Shell command is classified as destructive. Child delegates cannot write unless the parent explicitly grants it and the parent itself has write permission.

All persistent or observable sinks use the Harness redactor: SQLite scalars and JSON, messages, checkpoints, approvals, memories, events, SSE, API responses, artifacts, MCP logs, and Web data. Structured Authorization/API-key/password fields, nested containers, bytes, sets, and injected sentinel secrets are recursively masked. Shell subprocesses do not inherit provider or arbitrary `AGENTHARNESS_*` secrets.

## Readonly API

The server binds to `127.0.0.1` by default. All `POST`, `PUT`, `PATCH`, and `DELETE` requests return `405`.

- `GET /api/health`
- `GET /api/sessions`
- `GET /api/sessions/{id}`
- `GET /api/sessions/{id}/transcript`
- `GET /api/runs`
- `GET /api/runs/{id}`
- `GET /api/runs/{id}/events`
- `GET /api/runs/{id}/tree`
- `GET /api/runs/{id}/messages`
- `GET /api/runs/{id}/contexts`
- `GET /api/runs/{id}/approvals`
- `GET /api/runs/{id}/checkpoint`
- `GET /api/artifacts/{id}`
- `GET /api/stream` using ordered `global_seq`, `Last-Event-ID`/`after`, replay, dedupe, and heartbeat

## Python API and architecture

```python
from agentharness import Harness, RunRequest

harness = Harness(data_dir="~/.agentharness")
result = await harness.run(RunRequest(message="...", provider="fake"))
follow_up = await harness.run(
    RunRequest(message="continue", session_id=result.session_id, provider="fake")
)
await harness.cancel(follow_up.run_id)
await harness.aclose()
```

Stable boundaries:

- `Harness` owns lifecycle, registration, and readonly queries.
- `RunEngine` owns run state, budgets, checkpoints, stop propagation, scheduling, and events.
- `ContextPlanner.plan(...) -> ContextBundle` owns Workspace rule discovery, pinned
  Skill/Memory selection, deterministic stable prefix organization, budget compaction,
  artifact externalization, and the redacted `ContextManifest` persisted for each model turn.
- `VerificationLoop.evaluate(...) -> VerificationDecision` owns deterministic/file/command/
  independent-AI validator dispatch and returns `pass`, `retry(feedback)`,
  `require_human`, or `stop`; `RunEngine` invokes it only at candidate completion points.
- Providers implement only normalized async `stream(ModelRequest)` plus optional lifecycle close.
- Tools expose `ToolSpec` and async `run(ToolContext, arguments)` plus optional cleanup hooks.
- `Storage` owns SQLite transactions, migrations, artifacts, and recursive redaction at persistence boundaries.
- FastAPI and React are observer projections over Harness/Storage; they do not execute runs. The only write action is deterministic manual grading of an existing terminal run.

Core public contracts live in `agentharness.contracts`: `RunRequest`, `RunResult`,
`BudgetConfig`, `Message`, `ModelRequest`, `ModelStreamItem`, `ContextBundle`,
`ContextManifest`, `VerificationPolicy`, `VerificationDecision`, `ToolSpec`, `ToolCall`,
`ToolResult`, `Checkpoint`, `Usage`, and `EventEnvelope`.

### Candidate verification feedback loop

Verification is opt-in, so existing runs complete exactly as before. A policy can compose
existing deterministic `eval_assert` rules, sandboxed file conditions, governed commands,
and an independent read-only AI evaluator:

```python
from agentharness import Harness, RunRequest
from agentharness.contracts import VerificationCheck, VerificationPolicy

result = await harness.run(RunRequest(
    message="Implement the change and prove it",
    verification=VerificationPolicy(
        validators=[
            VerificationCheck(kind="file", path="src/result.py", contains=["def main"]),
            VerificationCheck(kind="command", command="uv run pytest -q"),
        ],
        max_retries=2,
        on_exhausted="require_human",
    ),
))
```

Command checks are synthetic Shell tool calls: they retain destructive Effect classification,
Approval, Sandbox, cancellation, output Artifact, and event semantics. They are never private
`subprocess` calls. A failed candidate receives a redacted structured user feedback message and
continues within the same token/step/wall-time/delegate budgets. Exhaustion becomes `failed` or
the resumable `require_human` status; it is never silently completed. AI checks require a
separate provider name and adapter from the executor.

## Offline verification

```bash
uv run pytest -q
uv run ruff check src tests
uv run pyright
uv run agentharness doctor

cd web
npm run lint
npm run test
npm run build
npx playwright test
```

Live provider smoke tests are optional and must be explicitly enabled with caller-exported credentials. The normal test suite uses only the fake provider, local temporary services, and temporary data directories.

## Offline eval suites

Headless regression for agent behavior: load a YAML/JSON/JSONL suite, run each case through the real Harness with the fake (or live) provider, grade deterministically, and emit JSON + JUnit reports.

### Minimal suite (YAML)

```yaml
name: demo
defaults:
  provider: fake
cases:
  - id: hello
    prompt: "[fake:text]hello eval"
    assert:
      status: completed
      contains: ["hello eval"]
      max_steps: 10
```

Assertions supported: `status`, `contains`, `contains_any`, `regex`, `tools_used`, `tools_order` (subsequence), `max_tokens`, `max_steps`, `max_latency_s`, optional `rubric` (LLM judge, default off).

### CLI

```bash
# Offline smoke (no API key)
uv run agentharness eval evals/smoke.yaml \
  --report-json output/eval-smoke.json \
  --report-junit output/eval-smoke.xml

# Keep trajectories for the Web Inspector
uv run agentharness eval evals/smoke.yaml \
  --data-dir output/eval-data \
  --report-json output/eval-smoke.json

# Compare against a prior JSON report
uv run agentharness eval evals/smoke.yaml \
  --baseline output/eval-smoke.json \
  --fail-on-regression \
  --min-pass-rate 1.0
```

Exit codes: `0` all cases passed (and no regression when `--fail-on-regression`); `1` case/grader failure or regression gate; `2` suite/CLI/baseline config error.

Default `--data-dir` is a temp directory that is deleted after the run — deep-linking trajectories is expected to fail in that mode. Pass a stable `--data-dir` when you want to open failed cases in the Web UI.

### Web Eval view

1. Generate a JSON report (and optional matching `--data-dir`).
2. Start the inspector against the same data dir:

```bash
uv run agentharness web --data-dir output/eval-data
```

3. Open the **Eval** tab, load `output/eval-smoke.json` (file picker or paste).
4. Failed rows with a `run_id` offer **查看轨迹**, which switches to the existing Run Inspector (`?run=<run_id>`). Eval does not re-implement the Timeline.

Optional second JSON file loads as baseline for new-failure / score-drop / token / latency summaries.


### Single-run eval (`eval_assert`)

For ad-hoc grading of one `Harness.run` (no suite file), pass assertions in request metadata:

```python
result = await harness.run(RunRequest(
    message="[fake:text]hello",
    provider="fake",
    metadata={"eval_assert": {"status": "completed", "contains": ["hello"]}},
))
run = harness.get_run(result.run_id)
print(run["metadata_json"])  # includes eval.passed / score / reasons
```

- Key name is fixed: `metadata["eval_assert"]` (aligned with suite `AssertionSpec`).
- On run completion, deterministic graders write `metadata["eval"]` (schema_version=1).
- Web **Run Inspector** (not the Eval page) shows passed / score / reasons from that field only; opening a trace never re-grades.
- Every turn in **上下文** has **评测 / 重新评分**. With **AI 评测** off (the default), it runs deterministic assertions or a free terminal-health check. With AI on, it additionally scores result, process, safety, efficiency, and user experience through the current active profile.
- Manual grading calls the narrow `POST /api/runs/{run_id}/grade` action with `mode=deterministic|ai`. All other Web write methods remain rejected.
- Offline `agentharness eval` suites remain separate: suite DSL + JSON/JUnit reports; single-run scores live on the run row for Inspector.

## Compatibility changes from the original prototype

- The Textual fullscreen TUI, snapshot files, dependency, `chat`, `ui`, and `run --ui` were removed.
- The bare command is now the supported interactive CLI.
- The standalone observer command remains `agentharness web`; interactive bare launch manages a companion Web process automatically.
- Web assets are built into and shipped inside the Python wheel.
- `search_files` now treats queries as literal substrings; implicit Python regular-expression execution was removed so searches remain cancellable and resistant to regex denial of service.
- Callers with live async Provider/Browser/MCP resources should use `await harness.aclose()`; synchronous `close()` remains safe when no async resource is open.

