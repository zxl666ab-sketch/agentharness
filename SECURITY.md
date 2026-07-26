# Security Policy

## Supported versions

| Version | Security fixes |
|---|---|
| 0.3.x | Supported |
| 0.2.x | Best effort |

## Reporting a vulnerability

Do not publish credentials, private traces, personal paths, or exploit details in a public issue.

Use the repository's private security-advisory channel when available. Otherwise contact the maintainer privately and include:

- affected version and platform;
- minimal reproduction using synthetic data;
- expected and observed permission boundary;
- whether files, credentials, network, memory, cost, or duplicate side effects are involved.

You should receive an acknowledgement within seven days. A fix timeline depends on severity and reproducibility. P0/P1 issues block a v0.3 release claim.

## Security boundary

Agent Harness is a local application, not a multi-tenant security boundary.

- The default Web/API listener is localhost only. Non-loopback binds disable execution unless explicitly overridden.
- Web requests can select only operator-configured workspace roots and relative subdirectories.
- Filesystem tools provide path governance. They do not isolate the Python process from the host OS.
- Local Shell executes on the host after governance and approval. Use Docker mode for container isolation.
- Docker security depends on the local Docker daemon and configured image. A Docker daemon is privileged infrastructure.
- Model providers, MCP servers, and explicitly authorized network origins are external trust boundaries.
- Redaction reduces accidental disclosure; it is not a substitute for avoiding private source data in public artifacts.

See [docs/threat-model.md](docs/threat-model.md) for threats, controls, and residual risks.

## Secret handling

API keys are read from environment variables or a local `.env` file and are never returned by the Web API. Keep the process environment and `.env` readable only by the local operator.

Never publish the raw SQLite database, browser profile, artifact directory, `.env`, logs, messages, or checkpoints.
