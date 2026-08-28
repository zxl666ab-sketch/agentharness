import { useEffect, useRef } from "react";

/**
 * Closes a dialog/drawer when the user presses Escape, unless the close is
 * currently disabled (e.g. an operation is in flight). Shared by every modal
 * in the workbench so keyboard behavior stays consistent.
 *
 * W-M6：处理器（含最新的 disabled/onClose）保存在 ref 中，keydown 监听器
 * 仅在 active 切换时挂/摘一次，不再因父组件每次渲染传入新回调而反复重挂。
 */
export function useEscape(active: boolean, onClose: () => void, disabled = false) {
  const handlerRef = useRef({ active, onClose, disabled });
  handlerRef.current = { active, onClose, disabled };
  useEffect(() => {
    if (!active) return;
    const onKey = (event: KeyboardEvent) => {
      const current = handlerRef.current;
      if (event.key === "Escape" && !current.disabled) current.onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active]);
}
