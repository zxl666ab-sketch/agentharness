import { Bot, ClipboardCheck, LayoutDashboard, ListTodo } from "lucide-react";

import type { WorkbenchView } from "./workbenchUrl";

type Props = {
  active: WorkbenchView;
  aiAttention: number;
  reviewAttention: number;
  onChange: (view: WorkbenchView) => void;
};

const ITEMS = [
  { id: "workbench" as const, label: "工作台", icon: LayoutDashboard },
  { id: "tasks" as const, label: "采购任务", icon: ListTodo },
  { id: "ai" as const, label: "AI 任务", icon: Bot },
  { id: "reviews" as const, label: "人工审核", icon: ClipboardCheck },
];

export function WorkbenchNavigation({ active, aiAttention, reviewAttention, onChange }: Props) {
  return (
    <nav className="proc-primary-nav" aria-label="采购工作台主导航">
      <div className="proc-primary-nav-label">采购管理</div>
      {ITEMS.map(({ id, label, icon: Icon }) => {
        const count = id === "ai" ? aiAttention : id === "reviews" ? reviewAttention : 0;
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
    </nav>
  );
}
