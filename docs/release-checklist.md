# v0.3 Release Checklist

Run from a clean checkout without private task data.

```powershell
uv sync --all-groups
uv run pytest --cov=agentharness --cov-report=term --cov-fail-under=80 -q
uv run ruff check src tests
uv build
uv run python scripts/check_web_build_determinism.py

Set-Location web
npm ci
npm run test
npm run lint
npm run build
```

Then start an isolated local instance:

```powershell
uv run agentharness `
  --data-dir output/release-smoke-data `
  --workspace . `
  --no-open
```

Configure a real OAI/OpenAI-compatible Provider, then verify in a real browser:

1. an OAI task streams, performs at least one real governed tool call, and reaches `completed`;
2. a write task defaults to blocked until “允许修改工作区” is enabled;
3. the write produces an approval card and does nothing before a decision;
4. deny returns a structured tool error;
5. allow-once performs only the requested operation;
6. stop changes an active run to `cancelled` and resume completes it;
7. `../` and absolute working directories are rejected;
8. the desktop layout has no horizontal overflow at supported widths (`>=1024px`).
9. tool rows show validation/execution/result state, attempts and artifact links;
10. an interrupted non-replayable side effect becomes `require_human` and is not rerun.

Release requires a clean Git commit, a versioned wheel/sdist, successful SQLite migration from the previous schema, no known P0/P1, and no tracked `.env`, raw databases, artifacts, PIDs or debug screenshots.
