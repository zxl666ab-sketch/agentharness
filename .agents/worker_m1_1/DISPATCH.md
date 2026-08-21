## 2026-08-19T15:58:54Z
Implement Milestone 1 (Design System & Styling Foundation):
1. **Dependencies**:
   Update `web/package.json` with:
   - `"clsx": "^2.1.1"` and `"tailwind-merge": "^2.6.0"` in `"dependencies"`
   - `"tailwindcss": "^3.4.17"`, `"postcss": "^8.4.49"`, `"autoprefixer": "^10.4.20"` in `"devDependencies"`
   Run `npm install` in `D:\个人通用agentharness\web`.
2. **PostCSS Configuration**:
   Create `web/postcss.config.js` with `tailwindcss` and `autoprefixer` plugins.
3. **Tailwind Configuration**:
   Create `web/tailwind.config.js` mapping `tokens.css` variables (`bg`, `surface`, `text`, `border`, `accent`, `danger`, `warning`, `info`, `effect` colors, typography, radii, shadows, glassmorphism `shadow-glass`, custom `glow-pulse` animation & keyframes, and dark mode `darkMode: ['selector', '[data-theme="dark"]']`).
4. **Utility Functions**:
   Create `web/src/lib/utils.ts` exporting `cn(...inputs: ClassValue[]): string` combining `clsx` and `twMerge`.
5. **CSS Pipeline**:
   Update `web/src/styles/tokens.css` to prepend `@tailwind base; @tailwind components; @tailwind utilities;` and include glassmorphism `.glass-panel` utilities with `@supports (backdrop-filter: blur(12px))`.
6. **ESLint Cleanups**:
   Clean up the 5 unused Lucide icon imports:
   - `web/src/procurement/ProcurementWorkbench.tsx`: remove `ClipboardCheck`, `ShoppingCart`, `Users`, `ScrollText` from imports.
   - `web/src/procurement/WorkbenchHome.tsx`: remove `TrendingUp` from imports.
7. **Semantic Contract Invariant (F10)**:
   Guarantee 100% preservation of all existing semantic selectors, IDs, data-attributes, and ARIA roles.
8. **Verification**:
   Run and verify the following commands in `web/`:
   - `npm test -- --run` (all 80/80 tests pass)
   - `npm run build` (exits 0, assets bundled in dist/)
   - `npm run lint` (exits 0 with 0 errors and 0 warnings)
