import {
  Bot,
  ClipboardCheck,
  LayoutDashboard,
  ListTodo,
  ShoppingCart,
  Users,
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

const PROCUREMENT_ITEMS = [
  { id: "workbench" as const, label: "工作台", icon: LayoutDashboard },
  { id: "tasks" as const, label: "采购任务", icon: ListTodo },
  { id: "suppliers" as const, label: "供应商管理", icon: Users },
  { id: "orders" as const, label: "采购订单", icon: ShoppingCart },
  { id: "ai" as const, label: "AI 任务", icon: Bot },
  { id: "reviews" as const, label: "人工审核", icon: ClipboardCheck },
];

const SYSTEM_ITEMS: Array<{ id: WorkbenchView; label: string; icon: typeof Users }> = [];

function countFor(id: WorkbenchView, aiAttention: number, reviewAttention: number) {
  return id === "ai" ? aiAttention : id === "reviews" ? reviewAttention : 0;
}

export function WorkbenchNavigation({ active, role, aiAttention, reviewAttention, onChange }: Props) {
  const visible = new Set(visibleViews(role));
  const procurement = PROCUREMENT_ITEMS.filter((item) => visible.has(item.id));
  const system = SYSTEM_ITEMS.filter((item) => visible.has(item.id));
  return (
    <nav className="proc-primary-nav" aria-label="采购工作台主导航">
      <div className="proc-primary-nav-label">采购管理</div>
      {procurement.map(({ id, label, icon: Icon }) => {
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
      })}
      {system.length ? (
        <>
          <div className="proc-primary-nav-label">系统管理</div>
          {system.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              type="button"
              className={active === id ? "active" : ""}
              aria-current={active === id ? "page" : undefined}
              onClick={() => onChange(id)}
            >
              <Icon size={17} />
              <span>{label}</span>
            </button>
          ))}
        </>
      ) : null}
    </nav>
  );
}
