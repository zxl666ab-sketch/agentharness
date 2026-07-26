import {
  AlertTriangle,
  Check,
  ChevronDown,
  Cpu,
  FolderKanban,
  LockKeyhole,
  MessagesSquare,
  PenLine,
  Play,
  RotateCcw,
  Settings2,
  ShieldCheck,
  Square,
  X,
} from "lucide-react";
import {
  type FormEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import type {
  ApprovalRow,
  CreateRunInput,
  RunRow,
  RuntimeInfo,
} from "../api/client";
import { EffectBadge } from "./EffectBadge";

type ApprovalDecision = "deny" | "allow_once" | "allow_run";

export type ComposerPrefill = { text: string; nonce: number };

type Props = {
  runtime: RuntimeInfo | null;
  selectedSessionId: string | null;
  selectedRun: RunRow | null;
  pendingApproval: ApprovalRow | null;
  prefill?: ComposerPrefill | null;
  onCreate: (input: CreateRunInput) => Promise<void>;
  onCancel: (runId: string) => Promise<void>;
  onResume: (runId: string, input?: string) => Promise<void>;
  onDecision: (approval: ApprovalRow, decision: ApprovalDecision) => Promise<void>;
};

const ACTIVE_STATUSES = new Set(["pending", "running", "waiting_approval"]);
const RESUMABLE_STATUSES = new Set(["cancelled", "interrupted", "require_human"]);

export function RunComposer({
  runtime,
  selectedSessionId,
  selectedRun,
  pendingApproval,
  prefill,
  onCreate,
  onCancel,
  onResume,
  onDecision,
}: Props) {
  const [message, setMessage] = useState("");
  const [model, setModel] = useState("");
  const [workspaceId, setWorkspaceId] = useState("default");
  const [cwd, setCwd] = useState("");
  const [allowWrite, setAllowWrite] = useState(false);
  const [conversationMode, setConversationMode] = useState<"new" | "continue">("new");
  const [showSettings, setShowSettings] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    if (!runtime) return;
    if (!runtime.workspaces.some((item) => item.id === workspaceId)) {
      setWorkspaceId(runtime.workspaces[0]?.id || "default");
    }
  }, [runtime, workspaceId]);

  useEffect(() => {
    setConversationMode(selectedSessionId ? "continue" : "new");
  }, [selectedSessionId]);

  useEffect(() => {
    if (!prefill) return;
    setMessage(prefill.text);
    textareaRef.current?.focus();
  }, [prefill]);

  const openai = runtime?.providers.find((item) => item.name === "openai") || null;
  const selectedWorkspace = useMemo(
    () => runtime?.workspaces.find((item) => item.id === workspaceId) || null,
    [runtime, workspaceId]
  );
  const active = !!selectedRun && ACTIVE_STATUSES.has(selectedRun.status);
  const resumable = !!selectedRun && RESUMABLE_STATUSES.has(selectedRun.status);
  const canSubmit = !!runtime?.execution_enabled && !!message.trim() && !busy;

  async function perform(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit) return;
    const input: CreateRunInput = {
      message: message.trim(),
      model: model.trim() || undefined,
      approval: "ask",
      workspace_id: workspaceId,
      cwd: cwd.trim() || undefined,
      allow_write: allowWrite,
      session_id:
        conversationMode === "continue" && selectedSessionId
          ? selectedSessionId
          : undefined,
    };
    await perform(async () => {
      await onCreate(input);
      setMessage("");
    });
  }

  function handleShortcut(event: ReactKeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || (!event.ctrlKey && !event.metaKey)) return;
    event.preventDefault();
    event.currentTarget.form?.requestSubmit();
  }

  const providerWarning =
    openai && !openai.configured
      ? "OpenAI 尚未配置凭据"
      : null;
  const providerLabel = ["OpenAI", model.trim() || openai?.default_model]
    .filter(Boolean)
    .join(" / ");

  return (
    <section className="run-composer" data-testid="run-composer" aria-label="运行 Agent">
      {pendingApproval ? (
        <div className="approval-card" role="alert" data-testid="approval-prompt">
          <div className="approval-heading">
            <span className="approval-icon"><LockKeyhole size={16} /></span>
            <strong>需要你的批准</strong>
            <span className="approval-hint">Agent 请求执行受治理操作，确认后才会继续</span>
          </div>
          <div className="approval-body">
            <EffectBadge effect={pendingApproval.effect} />
            <code className="approval-tool">{pendingApproval.tool_name}</code>
            <code className="approval-args">
              {pendingApproval.arguments_summary || pendingApproval.effect}
            </code>
          </div>
          <div className="approval-actions">
            <button
              type="button"
              className="deny"
              disabled={busy}
              onClick={() => void perform(() => onDecision(pendingApproval, "deny"))}
            >
              <X size={14} />拒绝
            </button>
            {!pendingApproval.requires_confirmation &&
            pendingApproval.effect !== "destructive" ? (
              <button
                type="button"
                className="allow-run"
                disabled={busy}
                onClick={() => void perform(() => onDecision(pendingApproval, "allow_run"))}
              >
                <ShieldCheck size={14} />本次运行允许
              </button>
            ) : null}
            <button
              type="button"
              className="approve"
              disabled={busy}
              onClick={() => void perform(() => onDecision(pendingApproval, "allow_once"))}
            >
              <Check size={14} />允许一次
            </button>
          </div>
        </div>
      ) : null}

      <div className="composer-shell">
        <form onSubmit={(event) => void submit(event)}>
          <div className="composer-input">
            <textarea
              ref={textareaRef}
              aria-label="任务描述"
              value={message}
              onChange={(event) => setMessage(event.target.value)}
              onKeyDown={handleShortcut}
              placeholder="描述你希望 Agent 完成的任务…"
              rows={3}
              disabled={busy || !runtime?.execution_enabled}
            />
            <span className="composer-shortcut">Ctrl ↵ 运行</span>
          </div>

          {showSettings ? (
            <div className="composer-settings" id="run-settings">
              <div className="settings-heading">
                <div><Settings2 size={15} /><strong>运行设置</strong></div>
                <span>仅作用于下一次运行</span>
              </div>
              <div className="composer-options">
                <label>
                  <span>模型（可选）</span>
                  <input
                    aria-label="模型"
                    value={model}
                    onChange={(event) => setModel(event.target.value)}
                    placeholder={openai?.default_model || "使用 OpenAI 默认值"}
                    disabled={busy}
                  />
                </label>
                <label>
                  <span>工作区</span>
                  <select
                    aria-label="工作区"
                    value={workspaceId}
                    onChange={(event) => setWorkspaceId(event.target.value)}
                    disabled={busy}
                  >
                    {(runtime?.workspaces || []).map((item) => (
                      <option key={item.id} value={item.id}>{item.name}</option>
                    ))}
                  </select>
                </label>
                <label>
                  <span>工作子目录（可选）</span>
                  <input
                    aria-label="工作子目录"
                    value={cwd}
                    onChange={(event) => setCwd(event.target.value)}
                    placeholder="例如 packages/app"
                    disabled={busy}
                  />
                </label>
              </div>
            </div>
          ) : null}

          {providerWarning ? (
            <p className="composer-warning"><AlertTriangle size={14} />{providerWarning}</p>
          ) : null}
          {!runtime?.execution_enabled ? (
            <p className="composer-warning">
              <AlertTriangle size={14} />此服务已禁用 Web 执行。
            </p>
          ) : null}
          {error ? <p className="composer-error" role="alert">{error}</p> : null}

          <div className="composer-toolbar">
            <div className="composer-context">
              <button
                type="button"
                className={`settings-toggle${showSettings ? " active" : ""}`}
                aria-expanded={showSettings}
                aria-controls="run-settings"
                onClick={() => setShowSettings((current) => !current)}
                disabled={busy}
              >
                <Settings2 size={15} />
                <span>设置</span>
                <ChevronDown size={13} />
              </button>
              <span className="context-pill" title={providerLabel || "尚未选择模型"}>
                <Cpu size={14} />{providerLabel || "模型"}
              </span>
              <span className="context-pill" title={selectedWorkspace?.name || workspaceId}>
                <FolderKanban size={14} />{selectedWorkspace?.name || workspaceId}
              </span>
              <button
                type="button"
                className={`mode-toggle${allowWrite ? " enabled" : ""}`}
                aria-pressed={allowWrite}
                title={
                  allowWrite
                    ? "已允许写入，具体写操作仍需逐次审批"
                    : "当前只读，Agent 不能修改工作区"
                }
                onClick={() => setAllowWrite((current) => !current)}
                disabled={busy}
              >
                {allowWrite ? <PenLine size={14} /> : <LockKeyhole size={14} />}
                <span>允许写入</span>
                <i className={`toggle-track${allowWrite ? " on" : ""}`} aria-hidden />
              </button>
              {selectedSessionId ? (
                <button
                  type="button"
                  className={`mode-toggle${conversationMode === "continue" ? " enabled" : ""}`}
                  aria-pressed={conversationMode === "continue"}
                  title={
                    conversationMode === "continue"
                      ? "在当前任务里继续对话"
                      : "作为全新任务开始"
                  }
                  onClick={() =>
                    setConversationMode((current) =>
                      current === "continue" ? "new" : "continue"
                    )
                  }
                  disabled={busy}
                >
                  <MessagesSquare size={14} />
                  <span>{conversationMode === "continue" ? "继续会话" : "新建会话"}</span>
                </button>
              ) : null}
            </div>

            <div className="composer-actions">
              {resumable && selectedRun ? (
                <button
                  type="button"
                  className="secondary-action"
                  disabled={busy}
                  onClick={() => void perform(async () => {
                    await onResume(selectedRun.id, message.trim() || undefined);
                    setMessage("");
                  })}
                >
                  <RotateCcw size={15} /><span>恢复</span>
                </button>
              ) : null}
              {active && selectedRun ? (
                <button
                  type="button"
                  className="stop-action"
                  disabled={busy}
                  onClick={() => void perform(() => onCancel(selectedRun.id))}
                >
                  <Square size={13} /><span>停止</span>
                </button>
              ) : null}
              <button type="submit" className="run-action" disabled={!canSubmit}>
                <Play size={15} fill="currentColor" />
                <span>{busy ? "处理中…" : "运行 Agent"}</span>
              </button>
            </div>
          </div>
        </form>
      </div>
    </section>
  );
}
