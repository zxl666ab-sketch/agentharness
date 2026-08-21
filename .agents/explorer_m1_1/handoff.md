# Handoff Report: Web Frontend Dependencies & Package Configuration (Milestone 1)

## 1. Observation

### 1.1 Existing Package Configuration & Scripts (`web/package.json`)
Inspected `D:\个人通用agentharness\web\package.json` (lines 1–35):
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

### 1.2 Package Manager & Runtime Environment
- **Node.js**: `v24.16.0`
- **npm**: `11.13.0`
- **Lockfile**: `D:\个人通用agentharness\web\package-lock.json` exists (172,046 bytes).

### 1.3 Baseline Verification Results
1. **`npm test` in `web/`**:
   - Command: `vitest run`
   - Result: Exited with code 0.
   - Output: `Test Files: 13 passed (13)`, `Tests: 80 passed (80)`.
2. **`npm run build` in `web/`**:
   - Command: `tsc -p tsconfig.app.json --noEmit && vite build`
   - Result: Exited with code 0.
   - Output: Vite v5.4.21 transformed 1622 modules, built `dist/` in 6.56s.
3. **`npm run lint` in `web/`**:
   - Command: `eslint . --ext ts,tsx --max-warnings 0`
   - Result: Exited with code 1 due to 5 `@typescript-eslint/no-unused-vars` warnings:
     - `web/src/procurement/ProcurementWorkbench.tsx:21:3` — warning `'ClipboardCheck' is defined but never used`
     - `web/src/procurement/ProcurementWorkbench.tsx:22:3` — warning `'ShoppingCart' is defined but never used`
     - `web/src/procurement/ProcurementWorkbench.tsx:23:3` — warning `'Users' is defined but never used`
     - `web/src/procurement/ProcurementWorkbench.tsx:24:3` — warning `'ScrollText' is defined but never used`
     - `web/src/procurement/WorkbenchHome.tsx:14:3` — warning `'TrendingUp' is defined but never used`

### 1.4 Dependency Dry-Run Installation
Executed `npm install --dry-run tailwindcss@^3.4.17 postcss@^8.4.49 autoprefixer@^10.4.20 clsx@^2.1.1 tailwind-merge@^2.6.0` in `web/`:
- Result: Exited with code 0.
- Resolved versions:
  - `tailwindcss@^3.4.17` (resolved to `3.4.19`)
  - `postcss@^8.4.49` (resolved to `8.5.26`)
  - `autoprefixer@^10.4.20` (resolved to `10.5.4`)
  - `clsx@^2.1.1` (resolved to `2.1.1`)
  - `tailwind-merge@^2.6.0` (resolved to `2.6.1`)
- No peer dependency conflicts or version incompatibilities were encountered.

### 1.5 Web Build ID Hashing (`web/vite.config.ts`)
Lines 19–38 in `web/vite.config.ts` compute `webBuildId` by hashing all files in `src`, `index.html`, `package.json`, `package-lock.json`, `tsconfig.app.json`, and `vite.config.ts`. Modifying `package.json` or `src/lib/utils.ts` will cleanly recompute this build ID during `npm run build`.

---

## 2. Logic Chain

1. **Dependency Categorization**:
   - `clsx` and `tailwind-merge` are imported by application runtime code (specifically `web/src/lib/utils.ts` to export `cn(...)` and any UI component consuming class concatenation). Therefore, they must be added to `"dependencies"`.
   - `tailwindcss`, `postcss`, and `autoprefixer` are CSS build/transpilation plugins consumed by Vite and PostCSS during development and bundling. Therefore, they must be placed in `"devDependencies"`.
2. **Compatibility Assessment**:
   - Node 24.16.0 and npm 11.13.0 with Vite 5.3+ seamlessly support Tailwind CSS 3.4.x via PostCSS 8.4.x / Autoprefixer 10.4.x.
   - The `--dry-run` test confirmed clean resolution of all 5 packages without any peer-dependency flags (`--legacy-peer-deps` or `--force` are not required).
3. **Lint Failure Resolution**:
   - `npm run lint` enforces `--max-warnings 0`.
   - The 5 warnings observed in `ProcurementWorkbench.tsx` (lines 21–24) and `WorkbenchHome.tsx` (line 14) are unused imports from `lucide-react`.
   - Scope item F3 explicitly calls for cleaning these 5 unused icon imports. Cleaning them will immediately bring `npm run lint` to a 0-warning, 0-error passing state.

---

## 3. Caveats

- **Tailwind Version**: Tailwind CSS v3.4.x is specified in the requirements (`tailwindcss@^3.4.17`). Do NOT install Tailwind CSS v4.x (which removes PostCSS plugins and changes syntax). The `^3.4.17` range ensures Tailwind v3.4.17+ is used.
- **Selector Dark Mode**: `tailwind.config.js` must be configured with `darkMode: ['selector', '[data-theme="dark"]']` (or equivalent) to align with existing `:root[data-theme="dark"]` token switching in `tokens.css`.
- **CSS Precedence**: Tailwind directives (`@tailwind base; @tailwind components; @tailwind utilities;`) should be added at the top of `web/src/styles/tokens.css` so that existing CSS variable declarations and semantic class rules continue to work seamlessly.

---

## 4. Conclusion & Actionable Steps for Worker 1

### Step 1: Update `web/package.json`
Update dependencies and devDependencies in `web/package.json`:

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
    "clsx": "^2.1.1",
    "lucide-react": "^0.414.0",
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "tailwind-merge": "^2.6.0"
  },
  "devDependencies": {
    "@types/react": "^18.3.3",
    "@types/react-dom": "^18.3.0",
    "@typescript-eslint/eslint-plugin": "^7.16.0",
    "@typescript-eslint/parser": "^7.16.0",
    "@vitejs/plugin-react": "^4.3.1",
    "autoprefixer": "^10.4.20",
    "eslint": "^8.57.0",
    "eslint-plugin-react-hooks": "^4.6.2",
    "eslint-plugin-react-refresh": "^0.4.8",
    "jsdom": "^24.1.0",
    "postcss": "^8.4.49",
    "tailwindcss": "^3.4.17",
    "typescript": "^5.5.3",
    "vite": "^5.3.4",
    "vitest": "^2.0.3"
  }
}
```

### Step 2: Install Packages in `web/`
Execute `npm install` in `D:\个人通用agentharness\web` to install the packages and cleanly refresh `package-lock.json`.

### Step 3: Create `web/postcss.config.js`
```javascript
export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
};
```

### Step 4: Create `web/tailwind.config.js`
```javascript
/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: ["selector", '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        bg: "var(--bg)",
        surface: {
          DEFAULT: "var(--surface)",
          subtle: "var(--surface-subtle)",
          strong: "var(--surface-strong)",
          elevated: "var(--surface-elevated)",
        },
        text: {
          DEFAULT: "var(--text)",
          secondary: "var(--text-secondary)",
          muted: "var(--text-muted)",
        },
        border: {
          DEFAULT: "var(--border)",
          strong: "var(--border-strong)",
        },
        accent: {
          DEFAULT: "var(--accent)",
          hover: "var(--accent-hover)",
          strong: "var(--accent-strong)",
          soft: "var(--accent-soft)",
          softer: "var(--accent-softer)",
        },
        danger: {
          DEFAULT: "var(--danger)",
          soft: "var(--danger-soft)",
        },
        warning: {
          DEFAULT: "var(--warning)",
          soft: "var(--warning-soft)",
        },
        info: {
          DEFAULT: "var(--info)",
          soft: "var(--info-soft)",
        },
        effect: {
          read: "var(--effect-read)",
          "read-soft": "var(--effect-read-soft)",
          write: "var(--effect-write)",
          "write-soft": "var(--effect-write-soft)",
          network: "var(--effect-network)",
          "network-soft": "var(--effect-network-soft)",
          danger: "var(--effect-danger)",
          "danger-soft": "var(--effect-danger-soft)",
          external: "var(--effect-external)",
          "external-soft": "var(--effect-external-soft)",
        },
      },
      borderRadius: {
        DEFAULT: "var(--radius)",
        sm: "var(--radius-sm)",
      },
      boxShadow: {
        xs: "var(--shadow-xs)",
        sm: "var(--shadow-sm)",
        md: "var(--shadow-md)",
        lg: "var(--shadow-lg)",
        glass: "0 8px 32px 0 rgba(0, 0, 0, 0.08)",
        "glow-accent": "0 0 15px -3px var(--accent)",
      },
      fontFamily: {
        sans: "var(--font)",
        mono: "var(--mono)",
      },
      animation: {
        "glow-pulse": "glow-pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite",
      },
      keyframes: {
        "glow-pulse": {
          "0%, 100%": { opacity: "1", transform: "scale(1)" },
          "50%": { opacity: ".6", transform: "scale(1.05)" },
        },
      },
    },
  },
  plugins: [],
};
```

### Step 5: Create `web/src/lib/utils.ts`
```typescript
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
```

### Step 6: Inject Tailwind Directives in `web/src/styles/tokens.css`
Prepend the following at line 1 of `web/src/styles/tokens.css`:
```css
@tailwind base;
@tailwind components;
@tailwind utilities;
```

### Step 7: Clean Up 5 Unused Icon Imports
1. In `web/src/procurement/ProcurementWorkbench.tsx`:
   Remove `ClipboardCheck`, `ShoppingCart`, `Users`, `ScrollText` from the `lucide-react` import statement (lines 21–24).
2. In `web/src/procurement/WorkbenchHome.tsx`:
   Remove `TrendingUp` from the `lucide-react` import statement (line 14).

---

## 5. Verification Method

To independently verify the configuration and implementation:

1. **Test Verification**:
   ```bash
   cd D:\个人通用agentharness\web
   npm test
   ```
   *Expected*: All 13 test files and 80 unit tests pass (0 failures).

2. **TypeScript & Vite Build Verification**:
   ```bash
   cd D:\个人通用agentharness\web
   npm run build
   ```
   *Expected*: `tsc` and `vite build` complete with code 0 and emit assets into `dist/`.

3. **Lint Verification**:
   ```bash
   cd D:\个人通用agentharness\web
   npm run lint
   ```
   *Expected*: ESLint exits with code 0 (0 errors, 0 warnings).

4. **Invalidation Conditions**:
   - Any dependency version mismatch or installation error.
   - Any regression in existing 80 unit tests.
   - Any remaining ESLint warnings triggering `--max-warnings 0` failure.
