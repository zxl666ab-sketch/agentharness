import { useCallback, useRef, useState, type PointerEvent as ReactPointerEvent, type KeyboardEvent as ReactKeyboardEvent } from "react";

export const CONV_MIN_WIDTH = 220;
export const CONV_MAX_WIDTH = 520;
export const CONV_DEFAULT_WIDTH = 300;

type Props = {
  width: number;
  /** 拖动中持续回调（父级写 CSS 变量实时布局）。 */
  onChange: (width: number) => void;
  /** 拖动/键盘调整结束时的最终值（父级持久化）。 */
  onCommit: (width: number) => void;
  /** 拖拽态开关：父级据此禁用布局过渡与文本选择。 */
  onDraggingChange: (dragging: boolean) => void;
};

function clampWidth(value: number) {
  return Math.max(CONV_MIN_WIDTH, Math.min(CONV_MAX_WIDTH, Math.round(value)));
}

/**
 * Agent 会话面板与工作区之间的拖拽分隔条：
 * 指针拖动 / 左右方向键 / Home·End 调宽，双击复位。纯受控组件，宽度状态在父级。
 */
export function ConversationResizer({ width, onChange, onCommit, onDraggingChange }: Props) {
  const [dragging, setDragging] = useState(false);
  // 拖拽态用 ref 跟踪：pointermove 可能先于 React 重渲到达，state 闭包会丢事件
  const draggingRef = useRef(false);
  const boundsRef = useRef<DOMRect | null>(null);
  const lastRef = useRef(width);

  const apply = useCallback((next: number, commit: boolean) => {
    const value = clampWidth(next);
    lastRef.current = value;
    onChange(value);
    if (commit) onCommit(value);
  }, [onChange, onCommit]);

  const onPointerDown = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    const container = event.currentTarget.parentElement;
    if (!container) return;
    event.preventDefault();
    boundsRef.current = container.getBoundingClientRect();
    lastRef.current = width;
    draggingRef.current = true;
    setDragging(true);
    onDraggingChange(true);
    // 指针移出分隔条后仍持续跟踪，直到 pointerup
    event.currentTarget.setPointerCapture(event.pointerId);
  }, [onDraggingChange, width]);

  const onPointerMove = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    if (!draggingRef.current || !boundsRef.current) return;
    // 面板占容器左列：宽 = 指针 x - 容器左缘（拖到哪面板就多宽）
    apply(event.clientX - boundsRef.current.left, false);
  }, [apply]);

  const endDrag = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    if (!draggingRef.current) return;
    draggingRef.current = false;
    setDragging(false);
    onDraggingChange(false);
    boundsRef.current = null;
    try { event.currentTarget.releasePointerCapture(event.pointerId); } catch { /* 已释放则忽略 */ }
    onCommit(lastRef.current);
  }, [onCommit, onDraggingChange]);

  const onKeyDown = useCallback((event: ReactKeyboardEvent<HTMLDivElement>) => {
    const step = event.shiftKey ? 48 : 16;
    if (event.key === "ArrowLeft") apply(width - step, true);
    else if (event.key === "ArrowRight") apply(width + step, true);
    else if (event.key === "Home") apply(CONV_MIN_WIDTH, true);
    else if (event.key === "End") apply(CONV_MAX_WIDTH, true);
    else return;
    event.preventDefault();
  }, [apply, width]);

  return (
    <div
      className={`proc-conv-resizer ${dragging ? "is-dragging" : ""}`}
      role="separator"
      aria-orientation="vertical"
      aria-label="调整 Agent 会话面板宽度"
      aria-valuemin={CONV_MIN_WIDTH}
      aria-valuemax={CONV_MAX_WIDTH}
      aria-valuenow={clampWidth(width)}
      tabIndex={0}
      title="拖动调整会话面板宽度（左右方向键微调，Shift 加速；双击复位）"
      data-testid="conversation-resizer"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      onDoubleClick={() => apply(CONV_DEFAULT_WIDTH, true)}
      onKeyDown={onKeyDown}
    />
  );
}
