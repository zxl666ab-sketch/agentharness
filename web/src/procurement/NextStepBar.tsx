import { AlertTriangle, ArrowRight, FileCheck2 } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "../components/ui";
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

/**
 * 任务详情的「当前动作」条（痛点⑥重做）：
 * 一行主线 = 当前该做的事 + 唯一主按钮；辅助动作降级为次按钮；卡点单独一行说明。
 */
export function NextStepBar({ request, order, contract, busy, error, onAction, onOpenContract, onGenerateContract }: Props) {
  const guide = nextStepGuide(request);
  const fulfillment = order ? fulfillmentNextStep(order) : null;
  const approved = request.status === "approved";
  const orderClosed = approved && order?.status === "CLOSED";
  const contractNeedsClose = !!contract && contract.status !== "CLOSED";

  // 当前动作：approved 后以履约阶段为准（不再显示"订单已生成"这类完成态口号）。
  const actionLabel = approved && fulfillment && fulfillment.label !== "已完成"
    ? fulfillment.label
    : approved && fulfillment?.label === "已完成"
      ? "履约已完成"
      : guide.hint;
  const actionDetail = approved && fulfillment ? fulfillment.detail : null;

  // 唯一主按钮：状态机里"下一步能点的那个"。
  type Primary = { key: string; label: string; icon?: ReactNode; loading?: boolean; run: () => void };
  const primary: Primary | null = orderClosed && contractNeedsClose && onOpenContract
    ? { key: "close-contract", label: "关闭合同", icon: <FileCheck2 size={14} />, run: () => onOpenContract() }
    : approved
      ? { key: "orders", label: fulfillment?.label === "已完成" ? "查看订单履约" : "前往订单履约", icon: <ArrowRight size={14} />, run: () => onAction({ kind: "orders" }) }
      : guide.actionLabel
        ? {
          key: guide.action.kind,
          label: guide.actionLabel,
          icon: <ArrowRight size={14} />,
          loading: guide.action.kind === "analyze" && busy === "analyze",
          run: () => onAction(guide.action),
        }
        : null;

  return (
    <div className="proc-next-step" aria-label="下一步引导">
      <div className="proc-next-step-main">
        <span className="proc-next-step-label">当前动作</span>
        <div className="proc-next-step-copy">
          <strong>{actionLabel}</strong>
          {actionDetail ? <small>{actionDetail}</small> : null}
        </div>
        <div className="proc-next-step-actions">
          {approved && contract && !orderClosed ? (
            <Button size="sm" variant="plain" icon={<FileCheck2 size={14} />} title="前往合同中心查看与操作该合同" onClick={onOpenContract}>
              合同 {contract.contract_no} · {contractStatusLabel(contract.status)}
            </Button>
          ) : null}
          {approved && !contract && !orderClosed && onGenerateContract ? (
            <Button
              size="sm"
              variant="secondary"
              loading={busy === "contract"}
              icon={busy === "contract" ? undefined : <FileCheck2 size={14} />}
              title="调用 AI 草拟合同并跳转合同中心"
              onClick={() => void onGenerateContract()}
            >
              {busy === "contract" ? "AI 草拟中…" : "生成合同（AI 草拟）"}
            </Button>
          ) : null}
          {primary ? (
            <Button
              size="sm"
              variant={approved && fulfillment?.label === "已完成" ? "secondary" : "primary"}
              icon={primary.icon}
              loading={primary.loading}
              onClick={primary.run}
            >
              {primary.label}
            </Button>
          ) : null}
        </div>
      </div>
      {!orderClosed && guide.blocker ? (
        <p className="proc-next-step-blocker" role="note">
          <AlertTriangle size={13} aria-hidden />
          {guide.blocker}
        </p>
      ) : null}
      {error ? (
        <p className="proc-next-step-error" role="alert">
          <AlertTriangle size={13} aria-hidden />{error}
        </p>
      ) : null}
    </div>
  );
}
