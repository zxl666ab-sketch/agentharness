import { useEffect, useRef, type ReactNode, type Ref } from "react";
import { X } from "lucide-react";

import { cn } from "../../lib/utils";

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "textarea:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(", ");

type Props = {
  /** 标题元素 id：保持 aria-labelledby 契约（如 #pay-title）。 */
  titleId: string;
  title: ReactNode;
  icon?: ReactNode;
  tone?: "accent" | "danger" | "warning";
  onClose: () => void;
  closeLabel?: string;
  footer?: ReactNode;
  size?: "sm" | "md" | "lg";
  busy?: boolean;
  children: ReactNode;
  className?: string;
  dialogRef?: Ref<HTMLElement>;
  /** 危险操作弹窗把初始焦点放「取消」，防止回车误触。 */
  initialFocusRef?: { current: HTMLElement | null };
  /** false 时 Esc/backdrop 不再关闭（提交中需保留输入的场景自行控制）。 */
  dismissible?: boolean;
};

/**
 * 居中弹窗（Phase 3）：焦点圈定/还原 + Esc/backdrop 关闭 + 标题主体页脚三段式。
 * 初始焦点优先级：initialFocusRef > autoFocus（子元素已接管则不抢焦点）> 首个可聚焦元素。
 */
export function Modal({
  titleId,
  title,
  icon,
  tone = "accent",
  onClose,
  closeLabel = "关闭",
  footer,
  size = "md",
  busy = false,
  children,
  className,
  dialogRef,
  initialFocusRef,
  dismissible = true,
}: Props) {
  const containerRef = useRef<HTMLElement | null>(null);
  const setRef = (node: HTMLElement | null) => {
    containerRef.current = node;
    if (typeof dialogRef === "function") dialogRef(node);
    else if (dialogRef && typeof dialogRef === "object") (dialogRef as { current: HTMLElement | null }).current = node;
  };

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const restore = document.activeElement as HTMLElement | null;
    const preferred = initialFocusRef?.current;
    if (preferred && !preferred.hasAttribute("disabled")) {
      preferred.focus();
    } else if (!container.contains(document.activeElement)) {
      const first =
        container.querySelector<HTMLElement>(".proc-modal-body " + FOCUSABLE_SELECTOR)
        ?? container.querySelector<HTMLElement>(FOCUSABLE_SELECTOR);
      (first ?? container)?.focus?.();
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        if (!busy && dismissible) onClose();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR))
        .filter((element) => !element.hasAttribute("disabled"));
      if (!focusable.length) {
        event.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && (active === first || !container.contains(active))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (active === last || !container.contains(active))) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      restore?.focus?.();
    };
    // onClose/busy 变化不需要重挂焦点管理，用 ref 读取最新值即可。
  }, [busy, dismissible, initialFocusRef, onClose]);

  return (
    <div
      className={cn("proc-modal-backdrop", `is-${tone}`)}
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy && dismissible) onClose();
      }}
    >
      <section
        ref={setRef}
        className={cn("proc-modal", `is-${size}`, className)}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <header className="proc-modal-head">
          <div className="proc-modal-title">
            {icon ? <span className={`proc-modal-icon is-${tone}`} aria-hidden>{icon}</span> : null}
            {typeof title === "string" ? <h2 id={titleId}>{title}</h2> : (
              // 自定义标题节点必须自带 id={titleId}
              title
            )}
          </div>
          <button
            className="proc-icon-button"
            type="button"
            title={closeLabel}
            aria-label={closeLabel}
            onClick={onClose}
            disabled={busy}
          >
            <X size={16} />
          </button>
        </header>
        <div className="proc-modal-body">{children}</div>
        {footer ? <footer className="proc-modal-foot">{footer}</footer> : null}
      </section>
    </div>
  );
}
