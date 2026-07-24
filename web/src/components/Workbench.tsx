import { Activity, Clock3, ListTree, PanelRightClose, PanelRightOpen, Zap } from "lucide-react";
import type {
  ApprovalRow,
  CheckpointRow,
  ContextManifestRow,
  EventRow,
  MessageRow,
  RunRow,
  SessionRow,
  TranscriptTurn,
} from "../api/client";
import { runStatusLabel } from "../runs/status";
import { Inspector } from "./Inspector";
import { RunList } from "./RunList";
import { Timeline } from "./Timeline";

export type MobileView = "runs" | "timeline" | "inspector";

type Props = {
  sessions: SessionRow[];
  runs: RunRow[];
  selectedSessionId: string | null;
  selectedRun: RunRow | null;
  selectedRunId: string | null;
  selectedEvent: EventRow | null;
  events: EventRow[];
  messages: MessageRow[];
  approvals: ApprovalRow[];
  checkpoint: CheckpointRow | null;
  tree: RunRow[];
  transcript: TranscriptTurn[];
  contexts: ContextManifestRow[];
  usage: Record<string, unknown>;
  budget: Record<string, unknown>;
  duration: string;
  mobileView: MobileView;
  inspectorCollapsed: boolean;
  runsLoading: boolean;
  runsError: string | null;
  timelineLoading: boolean;
  timelineError: string | null;
  inspectorLoading: boolean;
  inspectorError: string | null;
  onSelectSession: (id: string, latestRunId: string | null) => void;
  onSelectRun: (id: string) => void;
  onSelectEvent: (event: EventRow) => void;
  onMobileViewChange: (view: MobileView) => void;
  onInspectorToggle: () => void;
  onRunUpdated: (run: RunRow) => void;
  onEvaluationComplete: (
    run: RunRow,
    evaluation: Record<string, unknown>,
    turn?: TranscriptTurn
  ) => void;
};

export function Workbench(props: Props) {
  const {
    sessions, runs, selectedSessionId, selectedRun, selectedRunId, selectedEvent,
    events, messages, approvals, checkpoint, tree, transcript, contexts, usage, budget, duration,
    mobileView, inspectorCollapsed, runsLoading, runsError, timelineLoading, timelineError,
    inspectorLoading, inspectorError, onSelectSession, onSelectRun, onSelectEvent,
    onMobileViewChange, onInspectorToggle, onRunUpdated, onEvaluationComplete,
  } = props;

  return (
    <>
      <main className={`workbench ${inspectorCollapsed ? "inspector-collapsed" : ""}`}>
        <section className={`workspace-panel runs-panel ${mobileView === "runs" ? "mobile-visible" : ""}`} data-testid="runs-panel">
          <PanelHeader title="任务" meta={`${sessions.length} 个会话`} />
          <RunList
            sessions={sessions}
            runs={runs}
            selectedId={selectedSessionId}
            onSelect={onSelectSession}
            loading={runsLoading}
            error={runsError}
          />
        </section>

        <section className={`workspace-panel timeline-panel ${mobileView === "timeline" ? "mobile-visible" : ""}`} data-testid="timeline-panel">
          <div className="timeline-heading">
            <PanelHeader title="执行追踪" meta={selectedRun ? `运行 ${selectedRun.id.slice(0, 8)}` : "未选择运行"} />
            <button
              type="button"
              className="inspector-toggle"
              onClick={onInspectorToggle}
              aria-expanded={!inspectorCollapsed}
              aria-controls="inspector-panel"
              title={inspectorCollapsed ? "展开上下文" : "收起上下文"}
            >
              {inspectorCollapsed ? <PanelRightOpen size={17} /> : <PanelRightClose size={17} />}
              <span>{inspectorCollapsed ? "展开上下文" : "收起"}</span>
            </button>
          </div>
          <div className="run-overview" data-testid="run-overview">
            <Metric icon={<Activity size={15} />} label="状态" value={selectedRun ? runStatusLabel(selectedRun) : "-"} />
            <Metric icon={<Clock3 size={15} />} label="耗时" value={duration} />
            <Metric icon={<Zap size={15} />} label="令牌" value={`${numberValue(usage.total_tokens)} / ${numberValue(budget.max_tokens)}`} />
            <Metric icon={<ListTree size={15} />} label="步骤" value={`${selectedRun?.steps || 0} / ${numberValue(budget.max_steps)}`} />
          </div>
          <Timeline
            runId={selectedRunId}
            events={events}
            messages={messages}
            selectedId={selectedEvent?.event_id || null}
            onSelect={onSelectEvent}
            onSelectRun={onSelectRun}
            runStatus={selectedRun?.status || null}
            loading={timelineLoading}
            error={timelineError}
          />
        </section>

        <aside id="inspector-panel" className={`workspace-panel inspector-panel ${mobileView === "inspector" ? "mobile-visible" : ""}`} data-testid="inspector-panel" aria-hidden={inspectorCollapsed}>
          <PanelHeader title={selectedEvent ? "事件详情" : "对话上下文"} meta={selectedEvent?.type || `${transcript.length} 轮`} />
          <Inspector
            run={selectedRun}
            event={selectedEvent}
            tree={tree}
            messages={messages}
            approvals={approvals}
            checkpoint={checkpoint}
            transcript={transcript}
            contexts={contexts}
            onSelectRun={onSelectRun}
            onRunUpdated={onRunUpdated}
            onEvaluationComplete={onEvaluationComplete}
            loading={inspectorLoading}
            error={inspectorError}
          />
        </aside>
      </main>

      <nav className="mobile-tabs" data-testid="mobile-tabs" aria-label="工作区视图">
        <MobileTab active={mobileView === "runs"} onClick={() => onMobileViewChange("runs")} icon={<ListTree size={17} />} label="任务" />
        <MobileTab active={mobileView === "timeline"} onClick={() => onMobileViewChange("timeline")} icon={<Activity size={17} />} label="追踪" />
        <MobileTab active={mobileView === "inspector"} onClick={() => onMobileViewChange("inspector")} icon={<PanelRightOpen size={17} />} label="上下文" />
      </nav>
    </>
  );
}

function PanelHeader({ title, meta }: { title: string; meta: string }) {
  return <div className="panel-header"><h2>{title}</h2><span>{meta}</span></div>;
}

function Metric({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return (
    <div className="metric">
      <span className="metric-icon" aria-hidden="true">{icon}</span>
      <span><small>{label}</small><strong>{value}</strong></span>
    </div>
  );
}

function MobileTab({ active, onClick, icon, label }: { active: boolean; onClick: () => void; icon: React.ReactNode; label: string }) {
  return (
    <button type="button" className={active ? "active" : ""} onClick={onClick} aria-current={active ? "page" : undefined}>
      <span aria-hidden="true">{icon}</span>{label}
    </button>
  );
}

function numberValue(value: unknown): string {
  return typeof value === "number" ? value.toLocaleString() : "-";
}
