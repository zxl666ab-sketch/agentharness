# Frontend Infrastructure & Styling Survey Report (Explorer 1)

**Working Directory**: `D:\个人通用agentharness\web`  
**Report Date**: 2026-08-19  
**Explorer Role**: Infra & Styling Explorer  
**Mission**: In-depth survey of build, package, configuration, and styling infrastructure of the frontend to establish a concrete, robust foundation for **R1 (Design System & Styling Foundation)** and the Cursor/Canvas modernization.

---

## 1. Executive Summary

- **Current State**: The frontend is a React 18 + Vite 5 + TypeScript + TanStack Query application with custom vanilla CSS (`tokens.css`, `app.css`, and a 2,627-line `procurement.css`). All **13 test suites (80 unit tests)** pass with 0 failures under Vitest. Production build (`tsc --noEmit && vite build`) executes cleanly in ~5.7s.
- **Design System Goal**: Upgrade to a modern Cursor/Canvas-inspired AI collaborative workspace featuring:
  - Tailwind CSS + PostCSS + Autoprefixer integration.
  - Deep semantic CSS variable theme system mapping seamlessly across light and dark modes.
  - Dual-pane split canvas layout (AI stream on left, structured procurement canvas on right).
  - Subtle glassmorphism (`backdrop-blur`, alpha borders), glowing pulse accents (`glow-pulse`), and refined typography.
  - **100% preservation** of semantic test selectors (`.proc-*`, `[role="dialog"]`, `[role="alert"]`, `#receive-title`, `aria-*`, and interactive event targets).
- **Core Recommendation**: Install `tailwindcss@^3.4.17`, `postcss@^8.4.49`, `autoprefixer@^10.4.20`, plus helper utilities `clsx` and `tailwind-merge`. Map all existing CSS variable tokens (`--bg`, `--surface`, `--accent`, `--border`, etc.) directly into `tailwind.config.js` theme extensions. This enables both utility classes and existing CSS rules to react dynamically to theme switches without color duplication or Preflight breakage.

---

## 2. Current Frontend & Build Infrastructure Inventory

### 2.1 Package & Tooling Manifest (`web/package.json`)

```json
{
  "name": "agentharness-web",
  "private": true,
  "version": "0.5.0",
  "description": "采价台：采购询价与供应商比价工作台",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -p tsconfig.app.json --noEmit && vite build",
    "lint": "eslint . --ext ts,tsx --max-warnings 0",
    "test": "vitest run",
    "preview": "vite preview"
  },
  "dependencies": {
    "@tanstack/react-query": "^5.51.0",
    "lucide-react": "^0.414.0",
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  },
  "devDependencies": {
    "@types/react": "^18.3.3",
    "@types/react-dom": "^18.3.0",
    "@typescript-eslint/eslint-plugin": "^7.16.0",
    "@typescript-eslint/parser": "^7.16.0",
    "@vitejs/plugin-react": "^4.3.1",
    "eslint": "^8.57.0",
    "eslint-plugin-react-hooks": "^4.6.2",
    "eslint-plugin-react-refresh": "^0.4.8",
    "jsdom": "^24.1.0",
    "typescript": "^5.5.3",
    "vite": "^5.3.4",
    "vitest": "^2.0.3"
  }
}
```

### 2.2 Vite Configuration & Build-ID Hash (`web/vite.config.ts`)

- **Vite Plugins**:
  - `@vitejs/plugin-react` for React JSX transformation & Fast Refresh.
  - Custom plugin `agentharness-build-meta` which writes `dist/build-meta.json` with `web_build_id` and `api_schema_version: 19`.
- **Dynamic Hash Calculation**:
  - `computeWebBuildId()` traverses all files in `web/src/**`, plus `index.html`, `package.json`, `package-lock.json`, `tsconfig.app.json`, `vite.config.ts`, generating a deterministic sha256 build ID: `sha256:${hash.digest("hex").slice(0, 20)}`.
  - Injected constants: `__AGENTHARNESS_WEB_BUILD_ID__`, `__AGENTHARNESS_API_SCHEMA_VERSION__`, `__AGENTHARNESS_ENFORCE_WEB_BUILD_ID__`.
- **Server Proxy**:
  - Dev server runs on port 5173 with proxy `/api` pointing to `http://127.0.0.1:8741`.
- **Test Config**:
  - Vitest environment `jsdom`, includes `src/**/*.test.{ts,tsx}`, setup file `./test/setup.ts`.

### 2.3 TypeScript Configuration (`tsconfig.json`, `tsconfig.app.json`)

- Target: `ES2022`, Module: `ESNext`, `moduleResolution: bundler`.
- Strict mode: `true`, `noEmit: true`, `jsx: react-jsx`.
- `include: ["src"]`, `exclude: ["src/**/*.test.ts", "src/**/*.test.tsx"]`.
- Note: Root config files (`tailwind.config.js`, `postcss.config.js`) will not trigger TS compilation errors when created as JavaScript modules.

### 2.4 ESLint Configuration & Current Audit

- ESLint config: `.eslintrc.cjs` with `eslint:recommended`, `@typescript-eslint/recommended`, `react-hooks/recommended`.
- `npm run lint` enforces `--max-warnings 0`.
- **Audit Discovery**: During survey, `npm run lint` found 5 unused imports:
  - `web/src/procurement/ProcurementWorkbench.tsx`: `ClipboardCheck`, `ShoppingCart`, `Users`, `ScrollText`
  - `web/src/procurement/WorkbenchHome.tsx`: `TrendingUp`
- *Recommendation*: Ensure all refactored code cleans up unused imports/variables to guarantee `npm run lint` passes without warnings.

### 2.5 Test Suite Baseline Verification

Running `npm test -- --run` verified:
- **Test Files**: 13 passed (13 total)
- **Tests**: 80 passed (80 total)
- Suites tested:
  1. `src/api/compatibility.test.ts` (3 tests)
  2. `src/procurement/HumanInteractionPanel.test.tsx` (6 tests)
  3. `src/procurement/centers.test.tsx` (5 tests)
  4. `src/procurement/contractCenter.test.tsx` (3 tests)
  5. `src/procurement/contracts.test.ts` (4 tests)
  6. `src/procurement/invoiceCenter.test.tsx` (4 tests)
  7. `src/procurement/orderCenter.test.tsx` (10 tests)
  8. `src/procurement/procurement.test.tsx` (30 tests)
  9. `src/procurement/roles.test.ts` (1 test)
  10. `src/procurement/systemInfo.test.tsx` (1 test)
  11. `src/procurement/viewModel.test.ts` (5 tests)
  12. `src/procurement/workbenchUrl.test.ts` (6 tests)
  13. `src/useAgentStream.test.ts` (2 tests)

---

## 3. Current Styling Architecture & Token Catalog

### 3.1 CSS File Structure & Imports

Current stylesheets are organized in:
- `src/styles/tokens.css` (imported by `main.tsx:5`): Design tokens, CSS custom properties, global font/reset rules.
- `src/styles/app.css` (imported by `main.tsx:6`): Shared run report (`.run-report`, `.report-*`) and governance effect badges (`.effect-badge`).
- `src/procurement/procurement.css` (imported by `App.tsx:8`): 2,627 lines of procurement workbench component styles.

### 3.2 Design Token Catalog & Theme Switching

#### Light Theme vs Dark Theme Mappings:

| CSS Variable | Light Theme Value | Dark Theme Value (`:root[data-theme="dark"]`) | Purpose |
| :--- | :--- | :--- | :--- |
| `--bg` | `#f8fafc` | `#0b0f17` | Canvas / Page root background |
| `--surface` | `#ffffff` | `#131b2e` | Primary card / panel background |
| `--surface-subtle` | `#f1f5f9` | `#1a243b` | Secondary background, hover states |
| `--surface-strong` | `#e2e8f0` | `#243250` | Dividers, chip backgrounds |
| `--surface-elevated`| `#ffffff` | `#1e2a47` | Modals, drawers, tooltips |
| `--text` | `#0f172a` | `#f8fafc` | Primary text |
| `--text-secondary` | `#475569` | `#94a3b8` | Secondary labels, descriptions |
| `--text-muted` | `#64748b` | `#64748b` | Timestamps, placeholders, hints |
| `--border` | `#e2e8f0` | `#243250` | Default component borders |
| `--border-strong` | `#cbd5e1` | `#334466` | Emphasized borders, active states |
| `--accent` | `#059669` | `#10b981` | Emerald brand accent, active controls |
| `--accent-hover` | `#047857` | `#34d399` | Hover brand accent |
| `--accent-strong` | `#065f46` | `#059669` | High-contrast accent |
| `--accent-soft` | `#ecfdf5` | `#064e3b` | Accent tint background |
| `--accent-softer` | `#f0fdf4` | `#022c22` | Selected card tint background |
| `--danger` / `-soft` | `#e11d48` / `#ffe4e6` | `#fb7185` / `#4c0519` | Critical errors, destructive actions |
| `--warning` / `-soft`| `#d97706` / `#fef3c7` | `#fbbf24` / `#451a03` | Warnings, review required |
| `--info` / `-soft` | `#2563eb` / `#eff6ff` | `#60a5fa` / `#172554` | Information, help, links |
| `--effect-read` | `#475569` / `#f1f5f9` | `#94a3b8` / `#1e293b` | Governance badge: Read |
| `--effect-write` | `#b45309` / `#fef3c7` | `#f59e0b` / `#3b2405` | Governance badge: Write |
| `--effect-network`| `#1d4ed8` / `#eff6ff` | `#60a5fa` / `#172554` | Governance badge: Network |
| `--effect-danger` | `#be123c` / `#ffe4e6` | `#fb7185` / `#4c0519` | Governance badge: Destructive |
| `--effect-external`| `#6d28d9` / `#f5f3ff` | `#a78bfa` / `#2e1065` | Governance badge: External |
| `--radius` / `-sm` | `12px` / `8px` | `12px` / `8px` | Corner radii |
| `--shadow-xs`..`lg` | Soft diffuse shadows | Dark deep shadows | Elevation system |

#### Theme Toggle Mechanism:
In `web/src/App.tsx`:
```tsx
const [theme, setTheme] = useState<"light" | "dark">(() =>
  window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light"
);
useEffect(() => {
  document.documentElement.dataset.theme = theme;
}, [theme]);
```
`document.documentElement.dataset.theme = theme` sets `data-theme="light"` or `data-theme="dark"` on `<html>`.

---

## 4. Tailwind CSS + PostCSS Integration Blueprint (R1 Foundation)

### 4.1 Required Dependencies to Install

Run the following command in `D:\个人通用agentharness\web`:
```bash
npm install -D tailwindcss@^3.4.17 postcss@^8.4.49 autoprefixer@^10.4.20
npm install clsx@^2.1.1 tailwind-merge@^2.6.0
```

### 4.2 Configuration Files Blueprint

#### A. `web/postcss.config.js`
```javascript
export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
};
```

#### B. `web/tailwind.config.js`
```javascript
/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ['class', '[data-theme="dark"]'],
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        bg: 'var(--bg)',
        surface: {
          DEFAULT: 'var(--surface)',
          subtle: 'var(--surface-subtle)',
          strong: 'var(--surface-strong)',
          elevated: 'var(--surface-elevated)',
        },
        text: {
          DEFAULT: 'var(--text)',
          secondary: 'var(--text-secondary)',
          muted: 'var(--text-muted)',
        },
        border: {
          DEFAULT: 'var(--border)',
          strong: 'var(--border-strong)',
        },
        accent: {
          DEFAULT: 'var(--accent)',
          hover: 'var(--accent-hover)',
          strong: 'var(--accent-strong)',
          soft: 'var(--accent-soft)',
          softer: 'var(--accent-softer)',
        },
        danger: {
          DEFAULT: 'var(--danger)',
          soft: 'var(--danger-soft)',
        },
        warning: {
          DEFAULT: 'var(--warning)',
          soft: 'var(--warning-soft)',
        },
        info: {
          DEFAULT: 'var(--info)',
          soft: 'var(--info-soft)',
        },
        effect: {
          read: 'var(--effect-read)',
          'read-soft': 'var(--effect-read-soft)',
          write: 'var(--effect-write)',
          'write-soft': 'var(--effect-write-soft)',
          network: 'var(--effect-network)',
          'network-soft': 'var(--effect-network-soft)',
          danger: 'var(--effect-danger)',
          'danger-soft': 'var(--effect-danger-soft)',
          external: 'var(--effect-external)',
          'external-soft': 'var(--effect-external-soft)',
        },
      },
      fontFamily: {
        sans: 'var(--font)',
        mono: 'var(--mono)',
      },
      borderRadius: {
        DEFAULT: 'var(--radius-sm)',
        card: 'var(--radius)',
      },
      boxShadow: {
        xs: 'var(--shadow-xs)',
        sm: 'var(--shadow-sm)',
        md: 'var(--shadow-md)',
        lg: 'var(--shadow-lg)',
        glow: '0 0 15px -3px var(--accent)',
        'glow-strong': '0 0 25px -2px var(--accent)',
        glass: '0 8px 32px 0 rgba(0, 0, 0, 0.12)',
      },
      keyframes: {
        'glow-pulse': {
          '0%, 100%': {
            opacity: '1',
            boxShadow: '0 0 10px 2px rgba(16, 185, 129, 0.4)',
          },
          '50%': {
            opacity: '0.6',
            boxShadow: '0 0 20px 4px rgba(16, 185, 129, 0.7)',
          },
        },
        'fade-in': {
          '0%': { opacity: '0', transform: 'translateY(4px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        'slide-in-right': {
          '0%': { transform: 'translateX(100%)' },
          '100%': { transform: 'translateX(0)' },
        },
      },
      animation: {
        'glow-pulse': 'glow-pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'fade-in': 'fade-in 0.2s ease-out',
        'slide-in-right': 'slide-in-right 0.25s ease-out',
      },
      backdropBlur: {
        xs: '2px',
        md: '12px',
        xl: '20px',
      },
    },
  },
  plugins: [],
};
```

#### C. `web/src/styles/tokens.css` (Tailwind Directives & Glassmorphism Utilities)
Add `@tailwind base; @tailwind components; @tailwind utilities;` at the top of the CSS pipeline (or in a dedicated `tailwind.css` imported in `main.tsx`).
Include custom utility classes:
```css
@layer utilities {
  .glass-panel {
    background-color: var(--surface);
    backdrop-filter: blur(12px);
    border: 1px solid var(--border);
  }
  .glass-card {
    background-color: var(--surface-subtle);
    backdrop-filter: blur(8px);
    border: 1px solid var(--border);
  }
  .glow-pulse {
    animation: glow-pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite;
  }
}
```

#### D. Classname Helper `web/src/lib/utils.ts`
```typescript
import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
```

### 4.3 Safe Coexistence with Legacy CSS Rules

1. **Preflight Compatibility**:
   Tailwind's Preflight sets default margins, borders, button backgrounds, and table borders.
   Because `tokens.css` already sets sensible global resets (`* { box-sizing: border-box; }`, `button, input { font: inherit; }`), Tailwind's Preflight integrates harmoniously.
2. **Namespace Preservation**:
   All `.proc-*` and `.report-*` and `.effect-badge` classes remain defined in `procurement.css` and `app.css`.
   Components can combine semantic classes for tests (e.g. `className="proc-stat-card ... hover:border-accent ..."`) with modern Tailwind utility classes for responsive layout, glassmorphism, flex/grid alignment, and glow effects.
3. **Zero Color Hardcoding**:
   By using `bg-surface`, `text-text-secondary`, `border-border`, `accent-accent`, colors automatically respond when `data-theme="dark"` is set on `document.documentElement`, ensuring dark/light theme switching works flawlessly everywhere.

---

## 5. Component Styling & Test Selectors Preservation Matrix

To satisfy **R4 (Test & Semantic Compatibility Preservation)**, every semantic element ID, role, aria attribute, and class selected by the 13 unit test files MUST be preserved.

### 5.1 Critical Test Selector Catalog

| Component / File | Tested Selector / Attribute | Test Suite File | Requirement / Behavior |
| :--- | :--- | :--- | :--- |
| `WorkbenchNavigation.tsx` | `role="admin"`, `role="buyer"`, `role="approver"` | `procurement.test.tsx:217` | Role selection & permission filtering |
| `WorkbenchNavigation.tsx` | Nav item buttons: `"工作台"`, `"AI任务"`, `"复核中心"`, `"订单中心"`, `"合同中心"`, `"发票中心"`, `"供应商库"`, `"审计日志"`, `"系统配置"` | `procurement.test.tsx` | View routing |
| `WorkbenchHome.tsx` | `.proc-cockpit-stats`, `aria-label="核心指标看板"` | `procurement.test.tsx`, `centers.test.tsx` | KPI cockpit stats container |
| `WorkbenchHome.tsx` | `.proc-home-section`, `.proc-stat-card` | `procurement.test.tsx:952` | Home section & card clicks |
| `ProcurementWorkbench.tsx` | `.proc-request-item` | `procurement.test.tsx` | Task item list selection |
| `HumanInteractionPanel.tsx` | `form`, `input[type="number"]`, `input[type="file"]`, `[aria-label="补充澄清信息"]`, `[role="alert"]` | `HumanInteractionPanel.test.tsx` | Clarification form submit, error alerts, validation |
| `AiTaskRecovery.tsx` / `centers` | `[role="dialog"]`, `.proc-inline-error` | `centers.test.tsx:279,337` | Modal confirmation & inline error display |
| `OrderCenter.tsx` | `.proc-order-card`, `.proc-settlement-row`, `#receive-title`, `section:has(#receive-title)`, `[role="alert"]`, `[role="status"]` | `orderCenter.test.tsx:205,208,219,377` | Order cards, settlement rows, receive goods modal & status |
| `ContractCenter.tsx` | `[role="dialog"]`, button `"发起变更"`, `"确认发起变更"` | `contractCenter.test.tsx:143` | Contract change request dialog |
| `InvoiceCenter.tsx` | `.proc-invoice-actions`, `[role="dialog"]`, button `"手工改单"`, `"强制通过"` | `invoiceCenter.test.tsx:131,169` | Invoice 3-way match action bar & correction dialog |
| `ComparisonView.tsx` | `details.proc-evidence-panel`, `.proc-conflict-chip` | `procurement.test.tsx:1114,1161` | Deterministic comparison proofs & conflict chips |
| `DeleteDialog.tsx` | `[role="dialog"]` | `procurement.test.tsx:970` | Delete confirmation modal |
| `EffectBadge.tsx` | `.effect-badge.read`, `.write`, `.network`, `.danger`, `.external` | `procurement.test.tsx`, `systemInfo.test.tsx` | Governance effect badges |
| `RunReport.tsx` | `.run-report.passed`, `.failed`, `.needs_review`, `.report-heading`, `.report-metrics` | `procurement.test.tsx` | Evidence verification report container |

---

## 6. Detailed Recommendations for R1 (Implementation Guide)

### Step 1: Install Dependencies
```bash
cd D:\个人通用agentharness\web
npm install -D tailwindcss@^3.4.17 postcss@^8.4.49 autoprefixer@^10.4.20
npm install clsx@^2.1.1 tailwind-merge@^2.6.0
```

### Step 2: Add Config Files
1. Create `web/postcss.config.js` with `tailwindcss` and `autoprefixer`.
2. Create `web/tailwind.config.js` with full CSS variable mapping, `data-theme="dark"` selector dark mode, glow-pulse keyframes, glassmorphism utilities.
3. Create `web/src/lib/utils.ts` exporting `cn()` helper.

### Step 3: Inject Tailwind Directives
In `web/src/styles/tokens.css` or a new `web/src/styles/tailwind.css`:
```css
@tailwind base;
@tailwind components;
@tailwind utilities;
```
Ensure `main.tsx` imports it before other component styles.

### Step 4: Fix Existing ESLint Warnings
In `web/src/procurement/ProcurementWorkbench.tsx` and `web/src/procurement/WorkbenchHome.tsx`, remove the 5 unused icon imports (`ClipboardCheck`, `ShoppingCart`, `Users`, `ScrollText`, `TrendingUp`) so `npm run lint` achieves 0 warnings.

### Step 5: Verification Gate
- Run `npm test -- --run` to verify all 13 test files / 80 unit tests pass 100%.
- Run `npm run build` to verify TypeScript typecheck (`tsc -p tsconfig.app.json --noEmit`) and Vite bundle generation succeed with zero errors.
- Run `npm run lint` to verify 0 warnings.
