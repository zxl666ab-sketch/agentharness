# Agent Harness v0.3

Agent Harness 是一个本地、自托管、Web-first 的 Agent Runtime。用户从浏览器提交任务、查看流式输出、批准受治理操作、停止或恢复运行；Web 直接调用 `Harness`，不经过 CLI。

项目当前只聚焦 Agent 执行闭环：

- 原生 `asyncio` Agent 循环与多轮会话；
- OpenAI 与 OpenAI-compatible Provider；
- 文件、Shell、HTTP、Browser、MCP、Memory、Skills、Delegate 工具；
- Context Planner、预算、自动上下文压缩（滚动摘要）、prompt cache 命中率与缓存感知成本、OpenAI Provider retry、确定性与独立模型验证；
- SQLite 运行状态、Checkpoint、租约和进程丢失恢复；
- JSON Schema 工具参数校验、调用预算、持久化执行状态和副作用恢复治理；
- 工作区隔离、审批、egress/SSRF 防护、脱敏；
- Web 创建任务、SSE 流式事件、审批、停止和恢复。

项目不包含 CLI、Eval/Judge/Regression 平台或旧的只读检查器。

## 快速开始

要求 Python 3.11+、[uv](https://github.com/astral-sh/uv) 和 Node.js 20+。

```powershell
uv sync --all-groups
Set-Location web
npm ci
npm run build
Set-Location ..
uv run agentharness --workspace .
```

默认打开 `http://127.0.0.1:8741`。不希望自动打开浏览器时：

```powershell
uv run agentharness --workspace . --no-open
```

当前只提供最小宽度 `1024px` 的桌面 Web 工作台，不再提供移动端布局。

可重复提供多个授权工作区：

```powershell
uv run agentharness `
  --workspace D:\project-a `
  --workspace D:\project-b
```

网页请求只能选择这些工作区及其相对子目录，不能提交任意绝对路径或使用 `..` 越界。

## Provider 配置

使用环境变量或项目 `.env`：

```dotenv
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4o-mini

# OpenAI-compatible 网关可选
OPENAI_BASE_URL=https://example.com/v1
OPENAI_API_MODE=chat

```

产品运行时只注册 `openai`。未配置 `OPENAI_API_KEY` 时网页会显示配置告警，任务不会回退到离线或其他 Provider。OpenAI-compatible 网关仍通过同一个适配器接入。

## Web 运行模型

新任务默认：

- `approval=ask`；
- `allow_write=false`；
- 最多 30 步、10 分钟、100k tokens；
- 每轮最多 16 个工具调用、每次运行最多 128 个、最多 4 个安全工具并发；
- 工作目录限制在服务启动时授权的 workspace root 内。

启用“允许修改工作区”只授予运行级写权限，具体写文件、Shell、网络等动作仍按效果类型进入审批。Shell 永远被视为 destructive；长期记忆修改要求单独确认。

上下文管理：会话历史超过上下文预算的 80%（`context_compact_ratio`）时，引擎自动把旧消息组压缩成滚动摘要并继续执行——工具调用对保持原子、最新用户目标与最近若干组保持原文、原始消息外部化为 artifact 供审计，压缩后的视图随 checkpoint 持久化，恢复运行时直接生效。摘要调用失败时自动降级为按预算外部化，不会让本可完成的运行失败。Provider 返回的 prompt cache 命中会被记入 `usage.cache_hit_rate`，配置 `cached_input_per_million_usd` 后成本估算按缓存折扣价计费。

网页支持：

- 新会话或继续当前会话；
- 实时文本与运行状态；
- `allow_once`、`allow_run`、`deny` 审批；
- 停止活动运行；
- 从 `cancelled`、`interrupted`、`require_human` 恢复；
- 查看历史会话和最近工具/验证活动。

## 安全边界

- 默认只绑定 `127.0.0.1`。
- 非回环绑定默认禁用执行端点；必须显式使用 `--allow-remote-execution`，并在前面部署认证代理。
- 路径 sandbox 是路径治理，不是 OS 隔离。
- Local Shell 仍拥有当前用户的宿主机权限。处理不可信任务时应配置 Docker 执行器。
- Browser 使用隔离 profile，不使用个人浏览器 profile。
- HTTP/Browser/MCP egress 默认阻断私网、回环和重定向绕过。
- `.env`、SQLite、artifact 和浏览器 profile 不应公开。

完整边界见 [威胁模型](docs/threat-model.md)。

## Web API

核心接口：

```text
GET  /api/health
GET  /api/runtime
POST /api/runs
POST /api/runs/{id}/cancel
POST /api/runs/{id}/resume
POST /api/approvals/{id}/decision
GET  /api/sessions
GET  /api/sessions/{id}/transcript
GET  /api/runs/{id}/messages
GET  /api/runs/{id}/events
GET  /api/runs/{id}/approvals
GET  /api/runs/{id}/tool-invocations
GET  /api/tool-invocations/{id}
GET  /api/artifacts/{id}
GET  /api/stream
```

PUT、PATCH、DELETE 以及未列入白名单的 POST 会返回 `405`。

## Python API

Web 是产品入口，`Harness` 仍保留为稳定的嵌入式 Runtime API：

```python
from agentharness import Harness, RunRequest

harness = Harness(data_dir=".agentharness")
result = await harness.run(RunRequest(
    message="Inspect this workspace",
    cwd=".",
    allow_write=False,
))
await harness.aclose()
```

## 验证

```powershell
uv run pytest --cov=agentharness --cov-report=term --cov-fail-under=80 -q
uv run ruff check src tests
uv build

Set-Location web
npm run test
npm run lint
npm run build
```

发布步骤见 [docs/release-checklist.md](docs/release-checklist.md)，架构见 [docs/architecture.md](docs/architecture.md)。

## License

[MIT](LICENSE)
