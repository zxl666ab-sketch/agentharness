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
  titleId: string;
  title: ReactNode;
  subtitle?: ReactNode;
  icon?: ReactNode;
  onClose: () => void;
  closeLabel?: string;
  footer?: ReactNode;
  width?: "md" | "lg";
  children: ReactNode;
  asideRef?: Ref<HTMLElement>;
  initialFocusRef?: { current: HTMLElement | null };
  className?: string;
};

/** 右侧抽屉（Phase 3）：供应商档案 / 模型配置等"表单+详情"场景统一骨架。 */
export function Drawer({
  titleId,
  title,
  subtitle,
  icon,
  onClose,
  closeLabel = "关闭",
  footer,
  width = "md",
  children,
  asideRef,
  initialFocusRef,
  className,
}: Props) {
  const containerRef = useRef<HTMLElement | null>(null);
  const setRef = (node: HTMLElement | null) => {
    containerRef.current = node;
    if (typeof asideRef === "function") asideRef(node);
    else if (asideRef && typeof asideRef === "object") (asideRef as { current: HTMLElement | null }).current = node;
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
        container.querySelector<HTMLElement>(".proc-drawer-body " + FOCUSABLE_SELECTOR)
        ?? container.querySelector<HTMLElement>(FOCUSABLE_SELECTOR);
      (first ?? container)?.focus?.();
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
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
  }, [initialFocusRef, onClose]);

  return (
    <div
      className="proc-drawer-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <aside
        ref={setRef}
        className={cn("proc-drawer", `is-${width}`, className)}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <header className="proc-drawer-head">
          <div className="proc-drawer-title">
            {icon ? <span className="proc-modal-icon is-accent" aria-hidden>{icon}</span> : null}
            <div className="proc-drawer-heading">
              {typeof title === "string" ? <h2 id={titleId}>{title}</h2> : title}
              {subtitle ? <p>{subtitle}</p> : null}
            </div>
          </div>
          <button className="proc-icon-button" type="button" title={closeLabel} aria-label={closeLabel} onClick={onClose}>
            <X size={16} />
          </button>
        </header>
        <div className="proc-drawer-body">{children}</div>
        {footer ? <footer className="proc-drawer-foot">{footer}</footer> : null}
      </aside>
    </div>
  );
}
