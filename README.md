# Agent Harness

Agent Harness is a local, long-lived agent runtime with a streaming interactive CLI, a scriptable one-shot command, sandboxed tools, durable SQLite checkpoints, and a readonly Web run inspector.

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

The bare command opens the line-oriented, streaming multi-turn CLI:

```bash
uv run agentharness --provider fake --approval ask --cwd .
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
uv run agentharness --provider openai --model MODEL --approval ask --cwd PATH
uv run agentharness --session SESSION_ID --data-dir PATH
```

Provider selection is explicit `--provider`, then an explicitly exported `OPENAI_API_KEY`, then `ANTHROPIC_API_KEY`, then `fake`. The normal CLI never automatically reads a project or working-directory `.env` file.

## Scriptable commands

Run one task and exit:

```bash
uv run agentharness run "Summarize README.md" \
  --provider fake --approval auto --cwd .
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
- selected event/span payloads;
- conversation transcript as secondary context;
- desktop three-pane and mobile three-tab layouts.

The browser receives only real SQLite/API/SSE data. There is no fixture or mock presentation layer in production.

## Configuration and data

| Environment variable | Purpose |
|---|---|
| `AGENTHARNESS_DATA_DIR` | Default persistent data directory |
| `OPENAI_API_KEY` | OpenAI credential exported by the caller |
| `OPENAI_BASE_URL` | Optional OpenAI-compatible endpoint |
| `OPENAI_MODEL` | Default OpenAI model |
| `ANTHROPIC_API_KEY` | Anthropic credential exported by the caller |
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

- `read_file`, `write_file`, `search_files` — workspace sandbox with traversal and symlink/junction checks; reads and literal searches stream bounded chunks instead of loading whole files;
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
- Providers implement only normalized async `stream(ModelRequest)` plus optional lifecycle close.
- Tools expose `ToolSpec` and async `run(ToolContext, arguments)` plus optional cleanup hooks.
- `Storage` owns SQLite transactions, migrations, artifacts, and recursive redaction at persistence boundaries.
- FastAPI and React are readonly projections over Harness/Storage; they do not execute runs.

Core public contracts live in `agentharness.contracts`: `RunRequest`, `RunResult`, `BudgetConfig`, `Message`, `ModelRequest`, `ModelStreamItem`, `ToolSpec`, `ToolCall`, `ToolResult`, `Checkpoint`, `Usage`, and `EventEnvelope`.

## Offline verification

```bash
uv run pytest -q
uv run ruff check .
uv run pyright
uv run agentharness doctor

cd web
npm run lint
npm run test
npm run build
npx playwright test
```

Live provider smoke tests are optional and must be explicitly enabled with caller-exported credentials. The normal test suite uses only the fake provider, local temporary services, and temporary data directories.

## Compatibility changes from the original prototype

- The Textual fullscreen TUI, snapshot files, dependency, `chat`, `ui`, `run --ui`, and automatic Web/browser lifecycle were removed.
- The bare command is now the supported interactive CLI.
- The standalone observer command is `agentharness web`.
- Web assets are built into and shipped inside the Python wheel.
- `search_files` now treats queries as literal substrings; implicit Python regular-expression execution was removed so searches remain cancellable and resistant to regex denial of service.
- Callers with live async Provider/Browser/MCP resources should use `await harness.aclose()`; synchronous `close()` remains safe when no async resource is open.
