import { useEffect, useRef, type RefObject } from "react";

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "textarea:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(", ");

/**
 * 弹窗焦点管理（无障碍）：
 * - 打开时把焦点移入弹窗（默认首个可聚焦控件；危险确认弹窗可传 initialFocus
 *   指向取消按钮，避免回车误触危险操作）；
 * - Tab/Shift+Tab 在弹窗内循环，不穿透遮罩聚焦背景元素；
 * - 关闭时把焦点还原到打开前的触发元素。
 * Escape 关闭仍由 useEscape 负责，两者互不冲突。
 */
export function useModalFocus(
  open: boolean,
  containerRef: RefObject<HTMLElement | null>,
  initialFocusRef?: RefObject<HTMLElement | null>,
) {
  const restoreRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    restoreRef.current = (document.activeElement as HTMLElement | null) ?? null;
    const container = containerRef.current;
    const target =
      initialFocusRef?.current ??
      container?.querySelector<HTMLElement>(FOCUSABLE_SELECTOR) ??
      container;
    target?.focus();

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Tab") return;
      const node = containerRef.current;
      if (!node) return;
      const focusable = Array.from(
        node.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR),
      ).filter((element) => !element.hasAttribute("disabled"));
      if (focusable.length === 0) {
        event.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && (active === first || !node.contains(active))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (active === last || !node.contains(active))) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      restoreRef.current?.focus?.();
      restoreRef.current = null;
    };
    // containerRef/initialFocusRef 是稳定的 ref 对象；仅随 open 变化重新挂接。
  }, [open, containerRef, initialFocusRef]);
}
