import { AlertTriangle, LoaderCircle, Trash2, X } from "lucide-react";

import type { ProcurementRequestSummary } from "./types";
import { statusLabel, statusTone } from "./viewModel";

type Props = {
  target: ProcurementRequestSummary;
  busy: boolean;
  error: string | null;
  onClose: () => void;
  onConfirm: () => Promise<void>;
};

/** 删除采购任务确认弹窗（现代居中模态设计）。 */
export function DeleteDialog({ target, busy, error, onClose, onConfirm }: Props) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-xs animate-in fade-in duration-150"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) onClose();
      }}
    >
      <section
        className="glass-panel bg-surface border border-border/80 rounded-2xl p-6 shadow-2xl max-w-md w-full mx-auto flex flex-col gap-4 animate-in zoom-in-95 duration-150"
        role="dialog"
        aria-modal="true"
        aria-labelledby="delete-request-title"
      >
        <header className="flex items-center justify-between pb-3 border-b border-border/60">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-danger-soft text-danger flex items-center justify-center">
              <Trash2 size={18} />
            </div>
            <div>
              <h2 id="delete-request-title" className="text-base font-bold text-text">删除采购任务</h2>
            </div>
          </div>
          <button
            className="proc-icon-button compact w-7 h-7 rounded-lg border border-border flex items-center justify-center text-text-muted hover:text-text"
            type="button"
            title="关闭"
            aria-label="关闭"
            onClick={onClose}
            disabled={busy}
          >
            <X size={15} />
          </button>
        </header>

        <div className="p-3.5 rounded-xl bg-surface-subtle border border-border/60 flex flex-col gap-1.5 text-xs">
          <div className="flex items-center justify-between gap-2">
            <code className="font-mono text-xs font-bold text-accent">{target.reference}</code>
            <span className={`proc-status ${statusTone(target.status)} text-[11px] px-2 py-0.5 rounded-full border`}>
              {statusLabel(target.status)}
            </span>
          </div>
          <strong className="text-sm font-semibold text-text leading-snug">{target.title}</strong>
          <span className="text-text-muted text-[11px]">
            {target.quote_count} 家报价 · {target.quantity ? `${target.quantity} ${target.unit ?? ""}` : "待补充"}
          </span>
        </div>

        <div className="flex items-start gap-2.5 p-3 rounded-xl bg-danger-soft/60 border border-danger/25 text-xs text-danger leading-relaxed">
          <AlertTriangle size={16} className="flex-shrink-0 mt-0.5" />
          <p className="m-0 font-medium">
            删除后将永久移除该任务的采购需求、供应商报价、比价快照及审批记录，此操作<strong>不可恢复</strong>。
          </p>
        </div>

        {error ? (
          <p className="proc-form-error text-xs text-danger font-medium p-2.5 rounded-lg bg-danger-soft border border-danger/30" role="alert">
            {error}
          </p>
        ) : null}

        <footer className="flex items-center justify-end gap-2.5 pt-3 border-t border-border/60">
          <button
            className="proc-button secondary px-4 py-2 rounded-lg border border-border text-xs font-medium text-text hover:bg-surface-subtle transition-colors"
            type="button"
            onClick={onClose}
            disabled={busy}
          >
            取消
          </button>
          <button
            className="proc-button danger bg-danger hover:bg-rose-700 text-white px-4 py-2 rounded-lg text-xs font-semibold inline-flex items-center gap-1.5 shadow-sm transition-colors"
            type="button"
            onClick={() => void onConfirm()}
            disabled={busy}
          >
            {busy ? <LoaderCircle className="spin" size={15} /> : <Trash2 size={15} />}
            确认删除
          </button>
        </footer>
      </section>
    </div>
  );
}
