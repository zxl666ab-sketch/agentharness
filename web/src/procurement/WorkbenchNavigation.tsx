import {
  BarChart3,
  Bot,
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

  const renderItem = ({ id, label, icon: Icon }: { id: WorkbenchView; label: string; icon: typeof Users }) => {
    const count = countFor(id, aiAttention, reviewAttention);
    return (
      <button
        key={id}
        type="button"
        className={active === id ? "active" : ""}
        aria-current={active === id ? "page" : undefined}
        onClick={() => onChange(id)}
      >
        <Icon size={17} />
        <span>{label}</span>
        {count ? <small>{count > 99 ? "99+" : count}</small> : null}
      </button>
    );
  };

  return (
    <nav className="proc-primary-nav" aria-label="采购工作台主导航">
      <div className="proc-primary-nav-label">采购主线</div>
      <div className="proc-nav-primary-items">{primary.map(renderItem)}</div>
      {fulfillment.length ? (
        <div className="proc-nav-children" aria-label="履约中心二级入口">{fulfillment.map(renderItem)}</div>
      ) : null}
      {business.length ? (
        <details className="proc-nav-group" open={business.some((item) => item.id === active) || undefined}>
          <summary><FolderOpen size={15} /><span>业务资料</span></summary>
          <div>{business.map(renderItem)}</div>
        </details>
      ) : null}
      {management.length ? (
        <details className="proc-nav-group" open={management.some((item) => item.id === active) || undefined}>
          <summary><Wrench size={15} /><span>管理与技术</span></summary>
          <div>{management.map(renderItem)}</div>
        </details>
      ) : null}
    </nav>
  );
}
