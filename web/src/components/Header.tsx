import { Moon, Scale, Settings, Sun, Wifi } from "lucide-react";
import { type DemoRole, ROLE_LABELS } from "../procurement/roles";

export type HeaderProps = {
  theme: "light" | "dark";
  backendVersion: string;
  role: DemoRole;
  configData?: unknown;
  onRoleChange: (role: DemoRole) => void;
  onToggleTheme: () => void;
  onOpenConfig: (config?: unknown) => void;
};

export function Header({
  theme,
  backendVersion,
  role,
  configData,
  onRoleChange,
  onToggleTheme,
  onOpenConfig,
}: HeaderProps) {
  return (
    <header className="proc-topbar h-14 px-5 flex items-center justify-between gap-4 border-b border-border bg-surface/85 backdrop-blur-md sticky top-0 z-30 shadow-xs">
      <div className="proc-brand flex items-center gap-3 min-w-0">
        <span className="w-8 h-8 rounded-lg flex items-center justify-center bg-gradient-to-br from-accent to-teal-600 text-white shadow-xs flex-shrink-0">
          <Scale size={18} />
        </span>
        <div className="flex flex-col min-w-0">
          <strong className="text-sm font-bold text-text leading-tight tracking-tight">采价台</strong>
          <small className="text-[11px] text-text-muted leading-tight truncate max-w-xs">采购询价与供应商比价</small>
        </div>
      </div>
      <div className="proc-topbar-meta flex items-center gap-2.5">
        <RoleSwitcher role={role} onChange={onRoleChange} />
        <span className="proc-runtime-state inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-accent-soft text-accent border border-accent/20">
          <Wifi size={14} />采购服务 {backendVersion}
        </span>
        <button
          className="proc-icon-button w-8 h-8 rounded-lg border border-border bg-surface hover:bg-surface-subtle hover:border-border-strong text-text-secondary hover:text-text flex items-center justify-center transition-all"
          type="button"
          title="API / 模型配置"
          aria-label="API / 模型配置"
          onClick={() => onOpenConfig(configData)}
        >
          <Settings size={16} />
        </button>
        <button
          className="proc-icon-button w-8 h-8 rounded-lg border border-border bg-surface hover:bg-surface-subtle hover:border-border-strong text-text-secondary hover:text-text flex items-center justify-center transition-all"
          type="button"
          title="切换主题"
          aria-label="切换主题"
          onClick={onToggleTheme}
        >
          {theme === "light" ? <Moon size={16} /> : <Sun size={16} />}
        </button>
      </div>
    </header>
  );
}

export function RoleSwitcher({
  role,
  onChange,
}: {
  role: DemoRole;
  onChange: (role: DemoRole) => void;
}) {
  return (
    <label
      className="proc-role-selector inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-surface-subtle border border-border hover:border-border-strong text-xs font-medium text-text-secondary transition-all cursor-pointer"
      title="演示角色切换（K9，纯前端视角控制）"
    >
      <span className="text-text-muted text-[11px]">角色</span>
      <select
        aria-label="演示角色"
        className="bg-transparent border-0 text-text font-medium text-xs focus:ring-0 cursor-pointer p-0"
        value={role}
        onChange={(event) => onChange(event.target.value as DemoRole)}
      >
        {(Object.keys(ROLE_LABELS) as DemoRole[]).map((value) => (
          <option key={value} value={value}>{ROLE_LABELS[value]}</option>
        ))}
      </select>
    </label>
  );
}
