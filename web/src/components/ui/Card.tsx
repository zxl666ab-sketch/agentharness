import type { ReactNode } from "react";

import { cn } from "../../lib/utils";

type CardProps = {
  children: ReactNode;
  className?: string;
  /** section 头部（图标 + 标题 + 计数），不传则无头。 */
  head?: { icon?: ReactNode; title: ReactNode; aside?: ReactNode };
  /** flush：内容自带内边距（表格类）。 */
  padded?: boolean;
};

/** 玻璃卡片（Phase 3）：统一圆角/边框/阴影/头部排布。 */
export function Card({ children, className, head, padded = true }: CardProps) {
  return (
    <section className={cn("proc-card", !padded && "is-flush", className)}>
      {head ? (
        <header className="proc-card-head">
          <div className="proc-card-head-title">
            {head.icon ? <span className="proc-card-head-icon" aria-hidden>{head.icon}</span> : null}
            <h3>{head.title}</h3>
          </div>
          {head.aside ? <div className="proc-card-head-aside">{head.aside}</div> : null}
        </header>
      ) : null}
      {padded ? <div className="proc-card-body">{children}</div> : children}
    </section>
  );
}

type ListRowProps = {
  onClick?: () => void;
  selected?: boolean;
  children: ReactNode;
  className?: string;
};

/** 扫读行：左对齐、主信息突出、次要信息一行元数据（母版列表统一行式）。 */
export function ListRow({ onClick, selected = false, children, className }: ListRowProps) {
  const content = (
    <>
      {selected ? <span className="proc-row-rail" aria-hidden /> : null}
      {children}
    </>
  );
  if (onClick) {
    return (
      <button type="button" className={cn("proc-list-row", selected && "selected", className)} onClick={onClick}>
        {content}
      </button>
    );
  }
  return <div className={cn("proc-list-row", selected && "selected", className)}>{content}</div>;
}

/** 标签-值对：元数据网格统一用（label 上、value 下，左对齐）。 */
export function Fact({ label, children, mono = false, title }: { label: ReactNode; children: ReactNode; mono?: boolean; title?: string }) {
  return (
    <span className="proc-fact" title={title}>
      <small>{label}</small>
      <strong className={mono ? "mono" : undefined}>{children}</strong>
    </span>
  );
}
