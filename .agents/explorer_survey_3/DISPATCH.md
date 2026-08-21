## 2026-08-19T15:47:41Z
You are Explorer 3 (Test & Semantic Explorer).
Your working directory is `D:\个人通用agentharness\.agents\explorer_survey_3`.
You MUST read `D:\个人通用agentharness\.agents\ORIGINAL_REQUEST.md` before starting work.

Your task is to conduct an in-depth survey of the test suites, verification commands, and semantic contracts in `D:\个人通用agentharness\web`:
1. Inspect all test files in `web/` (e.g. `web/tests/`, `web/src/**/*.spec.ts`, `web/src/**/*.test.ts`, etc.), vitest config, and test setup.
2. List all 13 test files and enumerate every unit test case, noting what selectors, IDs, text contents, classes, test attributes (data-testid, etc.), aria attributes, and DOM structures are being queried or asserted.
3. Check how user interactions, buttons, inputs, modals, tabs, and drawers are simulated and tested.
4. Document every semantic contract that MUST be preserved during the UI refactor to ensure 100% test compatibility and 0 regressions (R4).
5. Write your comprehensive report to `D:\个人通用agentharness\.agents\explorer_survey_3\survey_report.md` and `handoff.md`. Include a progress.md with your liveness timestamp.

When finished, send a message to the orchestrator with the report path and key summary.
