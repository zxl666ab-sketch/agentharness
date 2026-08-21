# Scope: Milestone 3 — Dual-Pane AI & Canvas Workspace Layout

## Architecture
Milestone 3 implements the Dual-Pane Workspace for the Procurement Workbench task view.
- Left Pane: AI stream (`ProcurementConversation.tsx`), Human Interaction Panel (`HumanInteractionPanel.tsx`), AI Task Recovery (`AiTaskRecovery.tsx`), agent live status indicator (`glow-pulse`). Default width ~380px (min 320px), resizable/collapsible.
- Right Pane: Structured Canvas Tabs (`QuoteWorkspace.tsx`, `ComparisonView.tsx`, `ReportView.tsx`, `AuditView.tsx`, `NextStepBar.tsx`).
- Workspace Container: `ProcurementWorkbench.tsx` (task detail / workbench view).

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| F7 | Dual-Pane Layout | Split workspace with resizable/collapsible Left AI pane (default ~380px, min 320px) and Right Structured Canvas | M3 | survey / PROJECT.md |
| F8 | Left AI Stream & Interaction | Integrate conversation timeline, inline human interaction form, tool call badges, live agent status pill (`glow-pulse`), and recovery panel | M3 | survey / PROJECT.md |
| F9 | Right Structured Canvas Tabs | Modernize quote extraction cards, supplier comparison matrix with highlighting, formal approval report cards, audit log timeline, and next step workflow actions | M3 | survey / PROJECT.md |
| F10 | Semantic DOM Compatibility | Retain all test attributes (`data-testid`, `data-field`), DOM IDs, ARIA roles, `aria-label`s, and Escape key handlers | M3 | survey / PROJECT.md |

## Milestones / Iteration Tasks
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M3.1 | Exploration & Planning | Survey component architecture and test expectations for dual pane, AI stream, and canvas tabs | none | IN_PROGRESS |
| M3.2 | Worker Implementation | Modernize dual pane layout, left AI panel & recovery, canvas tabs, maintaining full semantic compatibility | M3.1 | PLANNED |
| M3.3 | Review, Challenge & Audit | Multi-agent verification (2 Reviewers, 2 Challengers, 1 Forensic Auditor) | M3.2 | PLANNED |

## Interface & Test Requirements
- Retain all `data-testid` attributes: `conversation-upload`, `quote-upload`, `data-field="..."`, etc.
- Retain DOM IDs: `#proc-conversation-panel`, `#proc-requirement-review-*`, etc.
- Retain ARIA roles: `role="alert"`, `role="dialog"`, `role="status"`, etc.
- Retain aria-labels: `[aria-label="补充澄清信息"]`, `[aria-label="采购任务视图"]`, `[aria-label="采购任务状态筛选"]`, `[aria-label="演示角色"]`, etc.
- Retain keyboard accessibility (Escape key handler to dismiss dialogs / panels).
- Clean `npm run lint`, passing `npm test -- --run`, passing `npm run build`.
