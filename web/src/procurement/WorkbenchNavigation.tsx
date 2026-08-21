import {
  BarChart3,
  Bot,
  ChevronDown,
  ClipboardCheck,
  FileSignature,
  FolderOpen,
  LayoutDashboard,
  ListTodo,
  Receipt,
  ScrollText,
  Server,
  ShoppingCart,
  Users,
  Wrench,
} from "lucide-react";
import { useEffect, useState } from "react";

import { type DemoRole, visibleViews } from "./roles";
import type { WorkbenchView } from "./workbenchUrl";

type Props = {
  active: WorkbenchView;
  role: DemoRole;
  aiAttention: number;
  reviewAttention: number;
  onChange: (view: WorkbenchView) => void;
};

const PRIMARY_ITEMS = [
  { id: "workbench" as const, label: "工作台", icon: LayoutDashboard },
  { id: "tasks" as const, label: "采购任务", icon: ListTodo },
  { id: "orders" as const, label: "履约中心", icon: ShoppingCart },
];

const FULFILLMENT_ITEMS = [
  { id: "invoices" as const, label: "发票匹配", icon: Receipt },
];

const BUSINESS_ITEMS = [
  { id: "suppliers" as const, label: "供应商档案", icon: Users },
  { id: "contracts" as const, label: "合同管理", icon: FileSignature },
  { id: "reports" as const, label: "统计报表", icon: BarChart3 },
];

const MANAGEMENT_ITEMS: Array<{ id: WorkbenchView; label: string; icon: typeof Users }> = [
  { id: "ai", label: "AI 任务诊断", icon: Bot },
  { id: "reviews", label: "人工审核", icon: ClipboardCheck },
  { id: "audit", label: "全局审计", icon: ScrollText },
  { id: "system", label: "系统信息", icon: Server },
];

function countFor(id: WorkbenchView, aiAttention: number, reviewAttention: number) {
  return id === "ai" ? aiAttention : id === "reviews" ? reviewAttention : 0;
}

export function WorkbenchNavigation({ active, role, aiAttention, reviewAttention, onChange }: Props) {
  const visible = new Set(visibleViews(role));
  const primary = PRIMARY_ITEMS.filter((item) => visible.has(item.id));
  const fulfillment = FULFILLMENT_ITEMS.filter((item) => visible.has(item.id));
  const business = BUSINESS_ITEMS.filter((item) => visible.has(item.id));
  const management = MANAGEMENT_ITEMS.filter((item) => visible.has(item.id));

  const [businessOpen, setBusinessOpen] = useState(true);
  const [managementOpen, setManagementOpen] = useState(true);

  // Auto-expand group if current active item is inside it
  useEffect(() => {
    if (business.some((item) => item.id === active)) {
      setBusinessOpen(true);
    }
    if (management.some((item) => item.id === active)) {
      setManagementOpen(true);
    }
  }, [active, business, management]);

  const renderItem = ({ id, label, icon: Icon }: { id: WorkbenchView; label: string; icon: typeof Users }, isNested = false) => {
    const count = countFor(id, aiAttention, reviewAttention);
    const isAiOrReview = id === "ai" || id === "reviews";
    return (
      <button
        key={id}
        type="button"
        className={`proc-nav-item flex items-center gap-2.5 px-3 py-2 rounded-lg text-xs font-medium transition-all duration-150 relative group ${
          isNested ? "nested pl-7" : ""
        } ${
          active === id
            ? "active bg-accent-soft text-accent font-semibold shadow-xs"
            : "text-text-secondary hover:text-text hover:bg-surface-subtle"
        }`}
        aria-current={active === id ? "page" : undefined}
        onClick={() => onChange(id)}
      >
        <span className={`proc-nav-icon flex-shrink-0 transition-colors ${active === id ? "text-accent" : "text-text-muted group-hover:text-text"}`}>
          <Icon size={16} />
        </span>
        <span className="proc-nav-label truncate">{label}</span>
        {count ? (
          <span className={`proc-nav-badge ml-auto font-mono text-[10px] font-bold px-1.5 py-0.2 rounded-full border ${
            isAiOrReview
              ? "danger bg-danger-soft text-danger border-danger/30"
              : "warning bg-warning-soft text-warning border-warning/30"
          }`}>
            {count > 99 ? "99+" : count}
          </span>
        ) : null}
      </button>
    );
  };

  return (
    <nav className="proc-primary-nav flex flex-col gap-4 p-3 overflow-y-auto" aria-label="采购工作台主导航">
      <div className="proc-nav-section flex flex-col gap-1">
        <div className="proc-primary-nav-label px-2 py-1 text-[11px] font-bold uppercase tracking-wider text-text-muted">采购主线</div>
        <div className="proc-nav-primary-items flex flex-col gap-1">
          {primary.map((item) => renderItem(item))}
          {fulfillment.length ? (
            <div className="proc-nav-children flex flex-col gap-1" aria-label="履约中心二级入口">
              {fulfillment.map((item) => renderItem(item, true))}
            </div>
          ) : null}
        </div>
      </div>

      {business.length ? (
        <div className="proc-nav-section flex flex-col gap-1">
          <button
            type="button"
            className={`proc-nav-group-toggle flex items-center justify-between w-full px-2 py-1.5 text-xs font-semibold text-text-muted hover:text-text rounded-md transition-colors ${businessOpen ? "open" : ""}`}
            onClick={() => setBusinessOpen((prev) => !prev)}
            aria-expanded={businessOpen}
          >
            <span className="proc-nav-group-title flex items-center gap-2">
              <FolderOpen size={15} />
              <span>业务资料</span>
            </span>
            <ChevronDown size={14} className={`proc-nav-chevron transition-transform duration-200 ${businessOpen ? "open rotate-0" : "-rotate-90"}`} />
          </button>
          {businessOpen ? (
            <div className="proc-nav-group-items flex flex-col gap-1">
              {business.map((item) => renderItem(item, true))}
            </div>
          ) : null}
        </div>
      ) : null}

      {management.length ? (
        <div className="proc-nav-section flex flex-col gap-1">
          <button
            type="button"
            className={`proc-nav-group-toggle flex items-center justify-between w-full px-2 py-1.5 text-xs font-semibold text-text-muted hover:text-text rounded-md transition-colors ${managementOpen ? "open" : ""}`}
            onClick={() => setManagementOpen((prev) => !prev)}
            aria-expanded={managementOpen}
          >
            <span className="proc-nav-group-title flex items-center gap-2">
              <Wrench size={15} />
              <span>管理与技术</span>
            </span>
            <ChevronDown size={14} className={`proc-nav-chevron transition-transform duration-200 ${managementOpen ? "open rotate-0" : "-rotate-90"}`} />
          </button>
          {managementOpen ? (
            <div className="proc-nav-group-items flex flex-col gap-1">
              {management.map((item) => renderItem(item, true))}
            </div>
          ) : null}
        </div>
      ) : null}
    </nav>
  );
}
