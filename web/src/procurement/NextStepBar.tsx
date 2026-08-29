import { AlertTriangle, ArrowRight, FileCheck2, LoaderCircle, Sparkles } from "lucide-react";

import type { ContractView, OrderView, ProcurementRequest } from "./types";
import { contractStatusLabel, fulfillmentNextStep, nextStepGuide, type NextStepAction } from "./viewModel";

type Props = {
  request: ProcurementRequest;
  order: OrderView | null;
  contract?: ContractView | null;
  busy?: string | null;
  error?: string | null;
  onAction: (action: NextStepAction) => void;
  onOpenContract?: () => void;
  onGenerateContract?: () => Promise<void>;
};

/** 任务详情头部的「下一步」引导与业务动作联动条（P1-2 聚合优化；P-UX②④ 语义消歧）。 */
export function NextStepBar({ request, order, contract, busy, error, onAction, onOpenContract, onGenerateContract }: Props) {
  const guide = nextStepGuide(request);
  const fulfillment = order ? fulfillmentNextStep(order) : null;
  const hint = request.status === "approved" && order
    ? `${fulfillment?.label}：${fulfillment?.detail}`
    : guide.hint;
  const orderClosed = request.status === "approved" && order?.status === "CLOSED";
  const contractNeedsClose = !!contract && contract.status !== "CLOSED";
  const showGuideAction = !orderClosed && !!guide.actionLabel;

  return (
    <div className="proc-next-step flex flex-col gap-1.5 bg-accent-soft/40 border-b border-border/70 text-xs" aria-label="下一步引导">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2.5 min-w-0 flex-1">
          <span className="proc-next-step-label inline-flex items-center gap-1 bg-accent text-white text-[11px] font-bold px-2.5 py-0.5 rounded-full shadow-2xs flex-shrink-0">
            <Sparkles size={11} />下一步
          </span>
          <strong className="text-text font-semibold truncate text-xs">{hint}</strong>
          {guide.blocker ? (
            <span className="proc-next-step-blocker text-warning text-xs font-medium bg-warning-soft px-2 py-0.5 rounded flex-shrink-0" role="note">
              {guide.blocker}
            </span>
          ) : null}
        </div>

        <div className="flex items-center gap-2 flex-shrink-0">
          {request.status === "approved" && contract ? (
            <button
              type="button"
              title="前往合同中心查看与操作该合同"
              className="proc-button compact inline-flex items-center gap-1.5 px-3 py-1 rounded-lg bg-surface border border-border text-xs font-medium text-text hover:bg-surface-subtle shadow-2xs transition-colors"
              onClick={onOpenContract}
            >
              <FileCheck2 size={13} className="text-accent" />
              合同 {contract.contract_no} · {contractStatusLabel(contract.status)}
            </button>
          ) : null}

          {orderClosed && contractNeedsClose && onOpenContract ? (
            <button
              type="button"
              title="履约已完成，但合同仍在生效/执行中，前往合同中心关闭"
              className="proc-button compact inline-flex items-center gap-1.5 px-3.5 py-1 rounded-lg bg-accent text-white text-xs font-semibold hover:bg-accent-strong shadow-2xs transition-colors"
              onClick={onOpenContract}
            >
              关闭合同
              <ArrowRight size={13} />
            </button>
          ) : null}

          {request.status === "approved" && !contract && onGenerateContract ? (
            <button
              type="button"
              title="调用 AI 草拟合同并跳转合同中心"
              className="proc-button compact inline-flex items-center gap-1.5 px-3 py-1 rounded-lg bg-surface border border-border text-xs font-medium text-text hover:bg-surface-subtle shadow-2xs transition-colors disabled:opacity-60"
              disabled={busy === "contract"}
              onClick={() => void onGenerateContract()}
            >
              {busy === "contract" ? <LoaderCircle className="spin" size={13} /> : <FileCheck2 size={13} className="text-accent" />}
              {busy === "contract" ? "AI 草拟中…" : "生成合同（AI 草拟）"}
            </button>
          ) : null}

          {showGuideAction ? (
            <button
              type="button"
              className="proc-button compact primary inline-flex items-center gap-1.5 px-3.5 py-1 rounded-lg bg-accent text-white text-xs font-semibold hover:bg-accent-strong shadow-2xs transition-colors disabled:opacity-60"
              disabled={guide.action.kind === "analyze" && busy === "analyze"}
              onClick={() => onAction(guide.action)}
            >
              {guide.action.kind === "analyze" && busy === "analyze" ? <LoaderCircle className="spin" size={13} /> : null}
              {guide.actionLabel}
              {guide.action.kind === "orders" ? <ArrowRight size={13} /> : null}
            </button>
          ) : null}
        </div>
      </div>
      {error ? (
        <p className="proc-next-step-error text-danger font-medium flex items-center gap-1.5" role="alert">
          <AlertTriangle size={12} />{error}
        </p>
      ) : null}
    </div>
  );
}
