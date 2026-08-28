import { BotOff, X } from "lucide-react";
import { useState } from "react";

/**
 * LIVE-1：/api/health 的 Java 服务状态为 ok 并不代表 Agent 可用。当
 * `agent_available=false`（心跳过期/Agent 死亡）时，分析、报价解析、合同
 * 草拟等 AI 任务会停留在“处理中”。此提示条在驾驶舱与任务详情顶部明示
 * 该事实，可关闭；同一挂载周期内记住关闭状态。
 */
export function AgentOfflineNotice() {
  const [dismissed, setDismissed] = useState(false);
  if (dismissed) return null;
  return (
    <section
      className="proc-agent-offline flex items-center gap-2.5 p-3 rounded-lg bg-warning-soft text-warning border border-warning/30 text-xs"
      role="alert"
      aria-label="Agent 服务离线提示"
    >
      <BotOff size={17} className="flex-shrink-0" />
      <span>
        <strong>Agent 服务离线</strong>
        <small className="block opacity-90">
          采购服务在线，但 AI 分析、报价解析等任务可能停滞在“处理中”；Agent 恢复后会自动继续。
        </small>
      </span>
      <button
        type="button"
        className="proc-icon-button ml-auto inline-flex items-center justify-center w-7 h-7 rounded-lg border border-warning/40 text-warning hover:text-text"
        title="关闭提示"
        aria-label="关闭 Agent 离线提示"
        onClick={() => setDismissed(true)}
      >
        <X size={14} />
      </button>
    </section>
  );
}
