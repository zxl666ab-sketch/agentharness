import type { CSSProperties, ReactNode } from "react";

import { cn } from "../../lib/utils";

type PageHeaderProps = {
  icon?: ReactNode;
  eyebrow?: ReactNode;
  title: ReactNode;
  subtitle?: ReactNode;
  /** 右侧计数徽标 / 操作区。 */
  aside?: ReactNode;
  className?: string;
};

/** 页头（Phase 3）：图标 + 标题 + 一句话路径说明 + 右侧计数/操作，全中心页统一。 */
export function PageHeader({ icon, eyebrow, title, subtitle, aside, className }: PageHeaderProps) {
  return (
    <header className={cn("proc-page-head", className)}>
      <div className="proc-page-title">
        {icon ? <span className="proc-page-icon" aria-hidden>{icon}</span> : null}
        <div className="proc-page-heading">
          {eyebrow ? <small className="proc-page-eyebrow">{eyebrow}</small> : null}
          <h1>{title}</h1>
          {subtitle ? <p>{subtitle}</p> : null}
        </div>
      </div>
      {aside ? <div className="proc-page-aside">{aside}</div> : null}
    </header>
  );
}

/** 计数徽章：如 "共 12 份"。 */
export function CountBadge({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "danger" | "warning" }) {
  return <span className={cn("proc-count-badge", tone !== "neutral" && `is-${tone}`)}>{children}</span>;
}

type CenterPageProps = {
  header: ReactNode;
  /** 工具条（筛选/上传等）。 */
  toolbar?: ReactNode;
  children: ReactNode;
  className?: string;
};

/** 中心页母版（Phase 3）：页头 + 工具条 + 内容区，宽度节奏统一。 */
export function CenterPage({ header, toolbar, children, className }: CenterPageProps) {
  return (
    <div className={cn("proc-center-page", className)}>
      {header}
      {toolbar ? <div className="proc-page-toolbar">{toolbar}</div> : null}
      <div className="proc-page-body">{children}</div>
    </div>
  );
}

type MasterDetailProps = {
  /** 左列：可扫读的列表。 */
  list: ReactNode;
  /** 右列：详情或引导空态。 */
  detail: ReactNode;
  /** 列表列宽（默认 340px）。 */
  listWidth?: number;
  className?: string;
};

/** 母版-详情两栏（Phase 3）：列表窄栏左对齐扫读，详情栏弹性占据剩余。 */
export function MasterDetail({ list, detail, listWidth = 340, className }: MasterDetailProps) {
  return (
    <div className={cn("proc-master-detail", className)} style={{ "--proc-list-width": `${listWidth}px` } as CSSProperties}>
      <section className="proc-master-detail-list">{list}</section>
      <section className="proc-master-detail-panel">{detail}</section>
    </div>
  );
}

type FilterChipsProps<T extends string> = {
  options: Array<{ value: T; label: string }>;
  value: T;
  onChange: (value: T) => void;
  label?: string;
};

/** 状态筛选胶囊组：中心页统一（proc-filter-chip 钩子保留）。 */
export function FilterChips<T extends string>({ options, value, onChange, label = "状态筛选" }: FilterChipsProps<T>) {
  return (
    <div className="proc-toolbar" role="toolbar" aria-label={label}>
      {options.map((option) => (
        <button
          key={option.value || "all"}
          type="button"
          className={cn("proc-filter-chip", value === option.value && "active")}
          aria-pressed={value === option.value}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

/** 操作反馈条：错误（role=alert）与成功（role=status）统一样式。 */
export function NoticeBar({ error, notice }: { error?: ReactNode; notice?: ReactNode }) {
  return (
    <>
      {error ? <p className="proc-toolbar-error" role="alert">{error}</p> : null}
      {notice ? <p className="proc-toolbar-success" role="status">{notice}</p> : null}
    </>
  );
}
