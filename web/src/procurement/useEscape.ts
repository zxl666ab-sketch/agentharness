import { useEffect } from "react";

/**
 * Closes a dialog/drawer when the user presses Escape, unless the close is
 * currently disabled (e.g. an operation is in flight). Shared by every modal
 * in the workbench so keyboard behavior stays consistent.
 */
export function useEscape(active: boolean, onClose: () => void, disabled = false) {
  useEffect(() => {
    if (!active) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !disabled) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active, disabled, onClose]);
}
