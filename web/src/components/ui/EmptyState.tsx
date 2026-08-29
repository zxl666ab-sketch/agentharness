import type { ReactNode } from "react";
import { AlertTriangle, Inbox } from "lucide-react";

import { cn } from "../../lib/utils";

type Props = {
  icon?: ReactNode;
  title: ReactNode;
  hint?: ReactNode;
  action?: ReactNode;
  /** inline 用于右栏详情占位；page 用于整页空态；error 用于加载失败。 */
  variant?: "page" | "inline" | "error";
  className?: string;
};

/** 空态三件套（Phase 3）：图标 + 一句话文案 + 引导动作，杜绝"纯空白面板"。 */
export function EmptyState({ icon, title, hint, action, variant = "page", className }: Props) {
  const isPage = variant === "page";
  return (
    <div
      className={cn("proc-empty-state", variant === "error" ? "compact is-error" : `is-${variant}`, className)}
      role={variant === "error" ? "alert" : "status"}
    >
      <span className="proc-empty-symbol" aria-hidden>
        {variant === "error" ? <AlertTriangle size={26} /> : isPage ? <Inbox size={26} /> : icon ?? <Inbox size={26} />}
      </span>
      {typeof title === "string" ? <h2>{title}</h2> : title}
      {hint ? <p>{hint}</p> : null}
      {action}
    </div>
  );
}

type ErrorStateProps = {
  title: ReactNode;
  detail?: ReactNode;
  onRetry?: () => void;
  retryLabel?: string;
  className?: string;
};

/** 加载失败态：图标 + 原因 + 重试按钮（EmptyState 的 error 配方）。 */
export function ErrorState({ title, detail, onRetry, retryLabel = "重新加载", className }: ErrorStateProps) {
  return (
    <EmptyState
      variant="error"
      title={title}
      hint={detail}
      action={onRetry ? <button className="proc-button is-secondary is-sm" type="button" onClick={onRetry}>{retryLabel}</button> : undefined}
      className={className}
    />
  );
}
