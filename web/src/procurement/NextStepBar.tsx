import { ArrowRight } from "lucide-react";

import type { OrderView, ProcurementRequest } from "./types";
import { nextStepGuide, type NextStepAction } from "./viewModel";

type Props = {
  request: ProcurementRequest;
  order: OrderView | null;
  onAction: (action: NextStepAction) => void;
};

/** 任务详情头部的「下一步」引导条（P1-2）：状态驱动文案，卡点原因可见，带可执行动作。 */
export function NextStepBar({ request, order, onAction }: Props) {
  const guide = nextStepGuide(request);
  const hint = request.status === "approved" && order
    ? `订单 ${order.order_no} 已生成：收货 → 对账 → 付款`
    : guide.hint;
  return (
    <div className="proc-next-step" aria-label="下一步引导">
      <span className="proc-next-step-label">下一步</span>
      <strong>{hint}</strong>
      {guide.blocker ? (
        <span className="proc-next-step-blocker" role="note">{guide.blocker}</span>
      ) : null}
      {guide.actionLabel ? (
        <button
          type="button"
          className="proc-button compact"
          onClick={() => onAction(guide.action)}
        >
          {guide.actionLabel}
          {guide.action.kind === "orders" ? <ArrowRight size={14} /> : null}
        </button>
      ) : null}
    </div>
  );
}
