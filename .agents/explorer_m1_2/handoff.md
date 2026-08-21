# Handoff Report — Explorer M1-2: CSS Architecture & Tailwind Configuration

- **Role**: `teamwork_preview_explorer` (Explorer M1-2)
- **Target Subsystem**: Design System, CSS Variables, Theme Switching, Tailwind & PostCSS Configuration (`web/src/styles/`, `web/tailwind.config.js`, `web/postcss.config.js`)
- **Parent Conversation ID**: `3da810a9-023c-4067-a202-0c5ab8e27b44`

---

## 1. Observation

### 1.1 Existing CSS Architecture & Import Pipeline
- **`web/src/main.tsx` (lines 5-6)**:
  ```typescript
  import "./styles/tokens.css";
  import "./styles/app.css";
  ```
- **`web/src/App.tsx` (line 8)**:
  ```typescript
  import "./procurement/procurement.css";
  ```
- **File Roles**:
  - `web/src/styles/tokens.css` (128 lines): Root design tokens, color palette, typography, shadows, radii, and global base resets.
  - `web/src/styles/app.css` (254 lines): Effect badge governance styles (`.effect-badge.read`, `.write`, etc.) and durable report viewer (`.run-report`).
  - `web/src/procurement/procurement.css` (2,627 lines): Workbench application layout (`.proc-app`), topbar (`.proc-topbar`), navigation (`.proc-nav-item`), data grids (`.proc-queue-table`), and drawer styles.

### 1.2 CSS Variables in `web/src/styles/tokens.css`
- **Light Theme (`:root`, lines 1-54)**:
  - Typography:
    - `--font`: `"Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", system-ui, sans-serif`
    - `--mono`: `"Cascadia Code", ui-monospace, "SFMono-Regular", Menlo, Monaco, Consolas, "Microsoft YaHei", monospace`
    - Type scale: `--text-xl: 18px`, `--text-lg: 16px`, `--text-base: 14px`, `--text-sm: 13px`, `--text-xs: 12px`, `--text-micro: 11px`
  - Colors:
    - Backgrounds: `--bg: #f8fafc`, `--surface: #ffffff`, `--surface-subtle: #f1f5f9`, `--surface-strong: #e2e8f0`, `--surface-elevated: #ffffff`
    - Text: `--text: #0f172a`, `--text-secondary: #475569`, `--text-muted: #64748b`
    - Borders: `--border: #e2e8f0`, `--border-strong: #cbd5e1`
    - Accent: `--accent: #059669`, `--accent-hover: #047857`, `--accent-strong: #065f46`, `--accent-soft: #ecfdf5`, `--accent-softer: #f0fdf4`
    - Feedback: `--danger: #e11d48`, `--danger-soft: #ffe4e6`, `--warning: #d97706`, `--warning-soft: #fef3c7`, `--info: #2563eb`, `--info-soft: #eff6ff`
    - Effects (Governance): `--effect-read: #475569`, `--effect-read-soft: #f1f5f9`, `--effect-write: #b45309`, `--effect-write-soft: #fef3c7`, `--effect-network: #1d4ed8`, `--effect-network-soft: #eff6ff`, `--effect-danger: #be123c`, `--effect-danger-soft: #ffe4e6`, `--effect-external: #6d28d9`, `--effect-external-soft: #f5f3ff`
  - Shadows:
    - `--shadow-xs: 0 1px 2px 0 rgb(0 0 0 / 0.05)`
    - `--shadow-sm: 0 4px 6px -1px rgb(0 0 0 / 0.07), 0 2px 4px -2px rgb(0 0 0 / 0.07)`
    - `--shadow-md: 0 10px 15px -3px rgb(0 0 0 / 0.08), 0 4px 6px -4px rgb(0 0 0 / 0.04)`
    - `--shadow-lg: 0 20px 25px -5px rgb(0 0 0 / 0.1), 0 8px 10px -6px rgb(0 0 0 / 0.1)`
  - Radii: `--radius: 12px`, `--radius-sm: 8px`

- **Dark Theme (`:root[data-theme="dark"]`, lines 56-95)**:
  - Backgrounds: `--bg: #0b0f17`, `--surface: #131b2e`, `--surface-subtle: #1a243b`, `--surface-strong: #243250`, `--surface-elevated: #1e2a47`
  - Text: `--text: #f8fafc`, `--text-secondary: #94a3b8`, `--text-muted: #64748b`
  - Borders: `--border: #243250`, `--border-strong: #334466`
  - Accent: `--accent: #10b981`, `--accent-hover: #34d399`, `--accent-strong: #059669`, `--accent-soft: #064e3b`, `--accent-softer: #022c22`
  - Feedback: `--danger: #fb7185`, `--danger-soft: #4c0519`, `--warning: #fbbf24`, `--warning-soft: #451a03`, `--info: #60a5fa`, `--info-soft: #172554`
  - Effects: `--effect-read: #94a3b8`, `--effect-read-soft: #1e293b`, `--effect-write: #f59e0b`, `--effect-write-soft: #3b2405`, `--effect-network: #60a5fa`, `--effect-network-soft: #172554`, `--effect-danger: #fb7185`, `--effect-danger-soft: #4c0519`, `--effect-external: #a78bfa`, `--effect-external-soft: #2e1065`
  - Shadows:
    - `--shadow-xs: 0 1px 2px 0 rgb(0 0 0 / 0.3)`
    - `--shadow-sm: 0 4px 6px -1px rgb(0 0 0 / 0.4), 0 2px 4px -2px rgb(0 0 0 / 0.4)`
    - `--shadow-md: 0 10px 15px -3px rgb(0 0 0 / 0.5), 0 4px 6px -4px rgb(0 0 0 / 0.5)`
    - `--shadow-lg: 0 20px 25px -5px rgb(0 0 0 / 0.6), 0 8px 10px -6px rgb(0 0 0 / 0.6)`

### 1.3 Theme Switching Mechanism in `web/src/App.tsx`
- **Lines 30-36**:
  ```typescript
  const [theme, setTheme] = useState<"light" | "dark">(() =>
    window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light"
  );
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);
  ```
- **Behavior**: When theme is toggled, `document.documentElement` (`<html data-theme="dark">` or `<html data-theme="light">`) receives the attribute `data-theme`.

### 1.4 Test Baseline Execution
- Command executed: `npm test -- --run` in `D:\个人通用agentharness\web`
- Result: **13 test files passed, 80 tests passed with 0 failures** in 14.52s.

---

## 2. Logic Chain

1. **CSS Variable Binding**:
   - `tokens.css` already encapsulates the entire design token hierarchy in CSS custom properties (`var(--bg)`, `var(--surface)`, `var(--text)`, etc.).
   - Directly extending Tailwind's `theme.extend` with these CSS custom properties allows all Tailwind utilities (`bg-surface`, `text-text-secondary`, `border-border`, `accent-accent`, etc.) to automatically respond to `:root[data-theme="dark"]` with zero runtime CSS overhead and no duplicate color definitions.

2. **Dark Mode Selector Strategy**:
   - `App.tsx` mutates `document.documentElement.dataset.theme` (i.e. `<html data-theme="...">`).
   - In Tailwind CSS v3.4+, setting `darkMode: ['selector', '[data-theme="dark"]']` (or `['class', '[data-theme="dark"]']`) causes all `dark:` utility variants to target `[data-theme="dark"] .dark\:...` which seamlessly activates when `data-theme="dark"` is set on `<html>`.

3. **CSS Pipeline Injection Points**:
   - Placing `@tailwind base;` at the beginning of `web/src/styles/tokens.css` applies Tailwind's Preflight reset at the root level.
   - Placing `@tailwind components;` and `@tailwind utilities;` in `tokens.css` right after the `:root` variables and custom base resets ensures:
     1. Normalization happens first.
     2. App-specific base rules (`html, body, #root { overflow: hidden; }`, custom scrollbars, and `button:focus-visible`) override Preflight defaults without regressions.
     3. Tailwind utility classes receive proper utility layer precedence.
     4. Existing semantic rules in `app.css` and `procurement.css` remain fully functional and co-exist without naming conflicts.

4. **Glassmorphism & Glow-Pulse Animations**:
   - Glassmorphism requires `backdrop-filter: blur(...)` combined with translucent background and subtle borders.
   - Providing both Tailwind composable classes (`backdrop-blur-md bg-surface/80 border border-border/60 shadow-glass`) and a dedicated `.glass-panel` class in CSS guarantees maximum flexibility across new and existing views.
   - `glow-pulse` keyframes cycling box-shadow and opacity with `--accent` and `--accent-soft` create the signature Cursor/Canvas AI accent animation required by R1.

---

## 3. Caveats

1. **Tailwind Alpha Opacity with Hex Variables**:
   - When CSS variables in `tokens.css` hold hex strings (e.g. `#ffffff`), Tailwind's slash-opacity syntax (`bg-surface/80`) requires modern browser `color-mix()` support or explicit rgba glassmorphism definitions.
   - We provide both Tailwind `shadow-glass` extensions and standard `.glass-panel` / `.glass-card` CSS rules with `@supports (backdrop-filter: blur(12px))` fallback to guarantee cross-browser visual fidelity.
2. **Read-Only Explorer Discipline**:
   - In accordance with the Explorer protocol, all specifications below are structured proposals for the Worker agent to implement in M1.

---

## 4. Conclusion & Complete Specifications

### 4.1 PostCSS Configuration (`web/postcss.config.js`)

```javascript
/** @type {import('postcss-load-config').Config} */
export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
};
```

---

### 4.2 Tailwind CSS Configuration (`web/tailwind.config.js`)

```javascript
/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: ['selector', '[data-theme="dark"]'],
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
      fontSize: {
        micro: ['var(--text-micro)', { lineHeight: '1.2' }],
        xs: ['var(--text-xs)', { lineHeight: '1.4' }],
        sm: ['var(--text-sm)', { lineHeight: '1.45' }],
        base: ['var(--text-base)', { lineHeight: '1.5' }],
        lg: ['var(--text-lg)', { lineHeight: '1.5' }],
        xl: ['var(--text-xl)', { lineHeight: '1.4' }],
      },
      borderRadius: {
        DEFAULT: 'var(--radius)',
        sm: 'var(--radius-sm)',
        md: '10px',
        lg: 'var(--radius)',
        xl: '16px',
        '2xl': '20px',
        full: '9999px',
      },
      boxShadow: {
        xs: 'var(--shadow-xs)',
        sm: 'var(--shadow-sm)',
        md: 'var(--shadow-md)',
        lg: 'var(--shadow-lg)',
        glass: '0 8px 32px 0 rgba(0, 0, 0, 0.08)',
        'glass-dark': '0 8px 32px 0 rgba(0, 0, 0, 0.37)',
        glow: '0 0 15px -3px var(--accent)',
        'glow-pulse': '0 0 20px 2px var(--accent-soft)',
      },
      keyframes: {
        'glow-pulse': {
          '0%, 100%': {
            opacity: '1',
            boxShadow: '0 0 15px 1px var(--accent-soft), 0 0 25px 2px var(--accent)',
          },
          '50%': {
            opacity: '0.85',
            boxShadow: '0 0 6px 0 var(--accent-soft), 0 0 10px 1px var(--accent)',
          },
        },
        'pulse-subtle': {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.6' },
        },
      },
      animation: {
        'glow-pulse': 'glow-pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'pulse-subtle': 'pulse-subtle 2s ease-in-out infinite',
      },
    },
  },
  plugins: [],
};
```

---

### 4.3 `web/src/styles/tokens.css` Integrated Specification

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

:root {
  --font: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", system-ui, sans-serif;
  --mono: "Cascadia Code", ui-monospace, "SFMono-Regular", Menlo, Monaco, Consolas, "Microsoft YaHei", monospace;

  /* Type scale */
  --text-xl: 18px;
  --text-lg: 16px;
  --text-base: 14px;
  --text-sm: 13px;
  --text-xs: 12px;
  --text-micro: 11px;

  --bg: #f8fafc;
  --surface: #ffffff;
  --surface-subtle: #f1f5f9;
  --surface-strong: #e2e8f0;
  --surface-elevated: #ffffff;
  --text: #0f172a;
  --text-secondary: #475569;
  --text-muted: #64748b;
  --border: #e2e8f0;
  --border-strong: #cbd5e1;
  --accent: #059669;
  --accent-hover: #047857;
  --accent-strong: #065f46;
  --accent-soft: #ecfdf5;
  --accent-softer: #f0fdf4;
  --danger: #e11d48;
  --danger-soft: #ffe4e6;
  --warning: #d97706;
  --warning-soft: #fef3c7;
  --info: #2563eb;
  --info-soft: #eff6ff;

  /* Governance language — one color per effect kind */
  --effect-read: #475569;
  --effect-read-soft: #f1f5f9;
  --effect-write: #b45309;
  --effect-write-soft: #fef3c7;
  --effect-network: #1d4ed8;
  --effect-network-soft: #eff6ff;
  --effect-danger: #be123c;
  --effect-danger-soft: #ffe4e6;
  --effect-external: #6d28d9;
  --effect-external-soft: #f5f3ff;

  --shadow-xs: 0 1px 2px 0 rgb(0 0 0 / 0.05);
  --shadow-sm: 0 4px 6px -1px rgb(0 0 0 / 0.07), 0 2px 4px -2px rgb(0 0 0 / 0.07);
  --shadow-md: 0 10px 15px -3px rgb(0 0 0 / 0.08), 0 4px 6px -4px rgb(0 0 0 / 0.04);
  --shadow-lg: 0 20px 25px -5px rgb(0 0 0 / 0.1), 0 8px 10px -6px rgb(0 0 0 / 0.1);
  --radius: 12px;
  --radius-sm: 8px;
  color-scheme: light;
}

:root[data-theme="dark"] {
  --bg: #0b0f17;
  --surface: #131b2e;
  --surface-subtle: #1a243b;
  --surface-strong: #243250;
  --surface-elevated: #1e2a47;
  --text: #f8fafc;
  --text-secondary: #94a3b8;
  --text-muted: #64748b;
  --border: #243250;
  --border-strong: #334466;
  --accent: #10b981;
  --accent-hover: #34d399;
  --accent-strong: #059669;
  --accent-soft: #064e3b;
  --accent-softer: #022c22;
  --danger: #fb7185;
  --danger-soft: #4c0519;
  --warning: #fbbf24;
  --warning-soft: #451a03;
  --info: #60a5fa;
  --info-soft: #172554;

  --effect-read: #94a3b8;
  --effect-read-soft: #1e293b;
  --effect-write: #f59e0b;
  --effect-write-soft: #3b2405;
  --effect-network: #60a5fa;
  --effect-network-soft: #172554;
  --effect-danger: #fb7185;
  --effect-danger-soft: #4c0519;
  --effect-external: #a78bfa;
  --effect-external-soft: #2e1065;

  --shadow-xs: 0 1px 2px 0 rgb(0 0 0 / 0.3);
  --shadow-sm: 0 4px 6px -1px rgb(0 0 0 / 0.4), 0 2px 4px -2px rgb(0 0 0 / 0.4);
  --shadow-md: 0 10px 15px -3px rgb(0 0 0 / 0.5), 0 4px 6px -4px rgb(0 0 0 / 0.5);
  --shadow-lg: 0 20px 25px -5px rgb(0 0 0 / 0.6), 0 8px 10px -6px rgb(0 0 0 / 0.6);
  color-scheme: dark;
}

* { box-sizing: border-box; }
html, body, #root { width: 100%; height: 100%; min-height: 0; margin: 0; overflow: hidden; }
body {
  overflow: hidden;
  background: var(--bg);
  color: var(--text);
  font-family: var(--font);
  font-size: var(--text-base);
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
}
button, input, select, textarea { font: inherit; }
button { cursor: pointer; }
button:disabled { cursor: not-allowed; opacity: .48; }
button, input, select, textarea { outline: none; }
button:focus-visible, input:focus-visible, select:focus-visible, textarea:focus-visible,
summary:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
code, pre, .mono { font-family: var(--mono); }
code { font-size: .92em; }
::selection { background: var(--accent-soft); }
::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb {
  border: 3px solid transparent;
  border-radius: 999px;
  background: var(--border-strong);
  background-clip: padding-box;
}

/* Glassmorphism utility components */
.glass-panel {
  background: var(--surface);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-sm);
}
@supports (backdrop-filter: blur(12px)) or (-webkit-backdrop-filter: blur(12px)) {
  :root .glass-panel {
    background: rgba(255, 255, 255, 0.78);
  }
  :root[data-theme="dark"] .glass-panel {
    background: rgba(19, 27, 46, 0.78);
  }
}
```

---

## 5. Verification Method

To independently verify this design and integration:

1. **Verify Existing Tests**:
   ```bash
   cd D:\个人通用agentharness\web
   npm test -- --run
   ```
   *Expected*: All 13 test files and 80 unit tests pass.

2. **Verify Configuration Integrity**:
   - Inspect `web/tailwind.config.js` and `web/postcss.config.js` against the specifications in Section 4.
   - Run `npm run build` after dependency installation to verify that Vite + PostCSS generates the CSS bundle with zero warnings or errors.

3. **Verify Theme Switching & Animations**:
   - In browser / Playwright, toggle `data-theme="dark"` on `document.documentElement` and verify that all `var(--...)` tokens and `dark:` utility variants update synchronously.
   - Verify `animate-glow-pulse` produces glowing emerald pulse accents on AI stream badges and active state indicators.
