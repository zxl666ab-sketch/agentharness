import type { ReactNode } from "react";

import { cn } from "../../lib/utils";

export type StatusTone = "success" | "info" | "warning" | "danger" | "neutral" | "accent";

const TONE_TITLE: Partial<Record<StatusTone, string>> = {
  success: "已完成 / 正常",
  info: "进行中",
  warning: "需要关注",
  danger: "异常 / 受阻",
  neutral: "未激活 / 已终结",
};

type Props = {
  tone: StatusTone | string;
  children: ReactNode;
  /** compact 用于列表行内；regular 用于页头与详情头。 */
  size?: "compact" | "regular";
  title?: string;
  className?: string;
};

/** 状态胶囊（Phase 3）：色点 + 语义色 + 文案，颜色永远不是唯一线索。 */
export function StatusPill({ tone, children, size = "regular", title, className }: Props) {
  const known = tone in TONE_TITLE;
  return (
    <span
      className={cn("proc-status", tone, size === "compact" && "is-compact", className)}
      title={title || (known ? TONE_TITLE[tone as StatusTone] : undefined)}
    >
      <i aria-hidden />
      {children}
    </span>
  );
}
