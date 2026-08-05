# Security Policy

## Supported versions

| Version | Security fixes |
|---|---|
| 0.3.x | Supported |
| 0.2.x | Best effort |

## Reporting a vulnerability

Do not publish credentials, private traces, personal paths, or exploit details in a
public issue. Use the repository's private security-advisory channel when available.
Otherwise contact the maintainer privately and include:

- affected version and platform;
- a minimal reproduction using synthetic procurement files;
- the expected and observed permission boundary;
- whether quote files, credentials, network access, cost, approval state, or duplicate
  side effects are involved.

You should receive an acknowledgement within seven days. Fix timing depends on severity
and reproducibility. A path/approval escape, quote-parser data leak, or repeatable
duplicate supplier decision blocks a release claim.

## Product security boundary

采价台是本地、单用户的采购应用，不是多租户隔离边界。采购文件、浏览器请求、模型输出和供应商字段都视为不可信输入。

- 服务默认只绑定 `127.0.0.1`。只有显式使用 `--allow-remote-execution` 才能在非回环地址启用执行；此模式必须置于已认证的反向代理之后。
- `--workspace` 仍接受为旧启动脚本的兼容参数，但采购 Agent 不向模型暴露工作区工具；路径 sandbox 能阻止遍历和常见符号链接逃逸，但不是操作系统级隔离。
- Agent 只装载四个采购白名单工具：读取需求、结构化采集、确定性分析和供应商审批；没有 Shell、Docker、浏览器自动化、MCP、长期 Memory 或通用 Delegate 工具。
- 报价只接受受限的 `.xlsx` 与文本型 `.pdf`。文件大小、数量、ZIP 条目、工作表、行列、页数和提取字符数均有上限；加密、空文本和扫描件默认拒绝。
- 金额、币种、税费、运费、资格条件、排序和最终推荐由后端 `Decimal` 规则计算。模型不能通过自由文本改写报价事实；正式供应商决定必须由采购员一次性确认。
- 原件和分析快照写入内容寻址 Artifact Store，并保存 SHA-256、来源定位、修正记录和规则版本。事件、消息、工具结果和公开 API 先经过脱敏；API Key 不写入 SQLite、Run、日志、Artifact 或前端响应。
- SQLite WAL、Run lease、Checkpoint 和追加式事件使审批与分析可恢复；不确定的非幂等结果会停在人工复核，不自动重放。
- 唯一的外部网络信任边界是显式配置的模型 Provider。采购工具不提供 URL 抓取、自动询价、自动下单、付款或 ERP 写入。

完整威胁、控制和残余风险见 [docs/threat-model.md](docs/threat-model.md)。

## Secret handling

API keys are read from environment variables or the local model-configuration file and
are never returned by the Web API. Keep the process environment, configuration file and
`.env` readable only by the local operator.

Never publish the raw SQLite database, artifact directory, `.env`, model traces, logs,
messages, checkpoints, or uploaded quote files.
