# Scope: Milestone 1 — Design System & Styling Foundation

## Architecture
- Root frontend package in `web/`
- Styling stack: Tailwind CSS 3.4.x, PostCSS 8.4.x, Autoprefixer 10.4.x, clsx 2.1.x, tailwind-merge 2.6.x
- Design token integration: `web/src/styles/tokens.css` mapped to Tailwind colors/radii/shadows/etc.
- Theme switching support: `:root[data-theme="dark"]`
- Utility helper: `web/src/lib/utils.ts` exporting `cn()`
- Lint cleanups: 5 unused Lucide icon imports across components

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | F1 | Install & configure tailwindcss, postcss, autoprefixer, clsx, tailwind-merge in web/ | M1 | PROJECT.md |
| 2 | F2 | Configure tailwind.config.js & postcss.config.js with token variables, dark theme, glow-pulse, glassmorphism | M1 | PROJECT.md |
| 3 | F3 | Implement web/src/lib/utils.ts (cn helper), inject Tailwind directives in CSS, clean 5 unused icon imports | M1 | PROJECT.md |
| 4 | F10 | Preserve 100% semantic selectors, ARIA roles, and DOM attributes | M1 | PROJECT.md |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| 1 | M1: Design System & Styling Foundation | F1, F2, F3, F10 | none | DONE |

## Interface Contracts
### `web/src/lib/utils.ts`
- Exports: `export function cn(...inputs: ClassValue[]): string`
- Dependencies: `clsx`, `tailwind-merge`

### `tailwind.config.js`
- Content paths: `./index.html`, `./src/**/*.{js,ts,jsx,tsx}`
- Dark mode: `['selector', '[data-theme="dark"]']`
- Theme extensions: colors mapped to `var(--...)` tokens, `glow-pulse` animation & keyframes, glassmorphism utilities

### `web/src/styles/tokens.css` / CSS Pipeline
- `@tailwind base;`
- `@tailwind components;`
- `@tailwind utilities;`
- Existing design tokens preserved
