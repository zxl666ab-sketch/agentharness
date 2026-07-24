import { Activity, Moon, Sun, TerminalSquare } from "lucide-react";
import type { AppView } from "../app/urlState";

type Props = {
  view: AppView;
  theme: "light" | "dark";
  connectionStatus: string;
  connectionLabel: string;
  onViewChange: (view: AppView) => void;
  onThemeToggle: () => void;
};

export function AppHeader({
  view,
  theme,
  connectionStatus,
  connectionLabel,
  onViewChange,
  onThemeToggle,
}: Props) {
  return (
    <header className="app-header">
      <div className="product-mark">
        <span className="product-icon"><TerminalSquare size={18} aria-hidden="true" /></span>
        <div>
          <h1>Agent Harness</h1>
          <span>运行与质量工作台</span>
        </div>
      </div>
      <nav className="header-nav" data-testid="header-nav" aria-label="主视图">
        <button
          type="button"
          className={view === "inspector" ? "active" : ""}
          onClick={() => onViewChange("inspector")}
          aria-current={view === "inspector" ? "page" : undefined}
        >
          检查器
        </button>
        <button
          type="button"
          className={view === "eval" ? "active" : ""}
          onClick={() => onViewChange("eval")}
          aria-current={view === "eval" ? "page" : undefined}
          data-testid="nav-eval"
        >
          评测
        </button>
      </nav>
      <div className="header-actions">
        <span className={`live-state ${connectionStatus}`} role="status" aria-live="polite">
          <Activity size={14} aria-hidden="true" />
          {connectionLabel}
        </span>
        <button
          type="button"
          className="icon-button"
          onClick={onThemeToggle}
          aria-label="切换主题"
          title="切换主题"
        >
          {theme === "light" ? <Moon size={17} aria-hidden="true" /> : <Sun size={17} aria-hidden="true" />}
        </button>
      </div>
    </header>
  );
}
