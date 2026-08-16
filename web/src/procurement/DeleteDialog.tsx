import { LoaderCircle, Trash2, X } from "lucide-react";

import type { ProcurementRequestSummary } from "./types";

type Props = {
  target: ProcurementRequestSummary;
  busy: boolean;
  error: string | null;
  onClose: () => void;
  onConfirm: () => Promise<void>;
};

/** 删除采购任务确认弹窗（P1-5 从工作台拆出）。 */
export function DeleteDialog({ target, busy, error, onClose, onConfirm }: Props) {
  return (
    <div
      className="proc-drawer-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) onClose();
      }}
    >
      <section className="proc-confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="delete-request-title">
        <header>
          <div><Trash2 size={17} /><h2 id="delete-request-title">删除采购任务</h2></div>
          <button className="proc-icon-button compact" type="button" title="关闭" aria-label="关闭" onClick={onClose} disabled={busy}>
            <X size={16} />
          </button>
        </header>
        <div className="proc-delete-target">
          <strong>{target.reference}</strong>
          <span>{target.title}</span>
        </div>
        <p className="proc-confirm-warning">删除后将移除任务列表中的采购需求、报价、比价快照和审批记录，不能恢复。</p>
        {error ? <p className="proc-form-error" role="alert">{error}</p> : null}
        <footer>
          <button className="proc-button secondary" type="button" onClick={onClose} disabled={busy}>取消</button>
          <button className="proc-button danger" type="button" onClick={() => void onConfirm()} disabled={busy}>
            {busy ? <LoaderCircle className="spin" size={15} /> : <Trash2 size={15} />}删除任务
          </button>
        </footer>
      </section>
    </div>
  );
}
