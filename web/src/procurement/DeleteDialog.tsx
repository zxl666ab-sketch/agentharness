import { useRef } from "react";

import { AlertTriangle, Trash2 } from "lucide-react";

import { Button, Modal, StatusPill } from "../components/ui";
import type { ProcurementRequestSummary } from "./types";
import { statusLabel, statusTone } from "./viewModel";

type Props = {
  target: ProcurementRequestSummary;
  busy: boolean;
  error: string | null;
  onClose: () => void;
  onConfirm: () => Promise<void>;
};

/** 删除采购任务确认弹窗（Phase 3 统一 Modal 骨架）。 */
export function DeleteDialog({ target, busy, error, onClose, onConfirm }: Props) {
  const dialogRef = useRef<HTMLElement | null>(null);
  const cancelRef = useRef<HTMLButtonElement | null>(null);
  return (
    <Modal
      titleId="delete-request-title"
      title="删除采购任务"
      icon={<Trash2 size={18} />}
      tone="danger"
      busy={busy}
      onClose={onClose}
      dialogRef={dialogRef}
      initialFocusRef={cancelRef}
      footer={
        <>
          <Button variant="secondary" ref={cancelRef} onClick={onClose} disabled={busy}>取消</Button>
          <Button variant="danger" icon={busy ? undefined : <Trash2 size={15} />} loading={busy} onClick={() => void onConfirm()}>确认删除</Button>
        </>
      }
    >
      <div className="proc-dialog-target">
        <span className="proc-dialog-target-head">
          <code className="mono">{target.reference}</code>
          <StatusPill tone={statusTone(target.status)} size="compact">{statusLabel(target.status)}</StatusPill>
        </span>
        <strong>{target.title}</strong>
        <span>{target.quote_count} 家报价 · {target.quantity ? `${target.quantity} ${target.unit ?? ""}` : "待补充"}</span>
      </div>
      <p className="proc-delete-warning">
        <AlertTriangle size={15} />
        <span>删除后将永久移除该任务的采购需求、供应商报价、比价快照及审批记录，此操作<strong>不可恢复</strong>。</span>
      </p>
      {error ? <p className="proc-dialog-error" role="alert">{error}</p> : null}
    </Modal>
  );
}
