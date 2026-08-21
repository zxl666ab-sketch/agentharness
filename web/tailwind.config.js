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
      fontFamily: {
        sans: "var(--font)",
        mono: "var(--mono)",
      },
      fontSize: {
        micro: ["var(--text-micro)", { lineHeight: "1.2" }],
        xs: ["var(--text-xs)", { lineHeight: "1.4" }],
        sm: ["var(--text-sm)", { lineHeight: "1.45" }],
        base: ["var(--text-base)", { lineHeight: "1.5" }],
        lg: ["var(--text-lg)", { lineHeight: "1.5" }],
        xl: ["var(--text-xl)", { lineHeight: "1.4" }],
      },
      borderRadius: {
        DEFAULT: "var(--radius)",
        sm: "var(--radius-sm)",
        md: "10px",
        lg: "var(--radius)",
        xl: "16px",
        "2xl": "20px",
        full: "9999px",
      },
      boxShadow: {
        xs: "var(--shadow-xs)",
        sm: "var(--shadow-sm)",
        md: "var(--shadow-md)",
        lg: "var(--shadow-lg)",
        glass: "0 8px 32px 0 rgba(0, 0, 0, 0.08)",
        "glass-dark": "0 8px 32px 0 rgba(0, 0, 0, 0.37)",
        glow: "0 0 15px -3px var(--accent)",
        "glow-accent": "0 0 15px -3px var(--accent)",
        "glow-pulse": "0 0 20px 2px var(--accent-soft)",
      },
      keyframes: {
        "glow-pulse": {
          "0%, 100%": {
            opacity: "1",
            boxShadow: "0 0 15px 1px var(--accent-soft), 0 0 25px 2px var(--accent)",
          },
          "50%": {
            opacity: "0.85",
            boxShadow: "0 0 6px 0 var(--accent-soft), 0 0 10px 1px var(--accent)",
          },
        },
        "pulse-subtle": {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.6" },
        },
      },
      animation: {
        "glow-pulse": "glow-pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite",
        "pulse-subtle": "pulse-subtle 2s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};
