import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  AlertTriangle,
  Archive,
  BarChart3,
  CheckCircle2,
  FileCheck2,
  Files,
  LoaderCircle,
  Moon,
  Plus,
  Save,
  Search,
  Scale,
  Settings,
  ShieldCheck,
  Sparkles,
  Sun,
  Trash2,
  Wifi,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { useAgentStream } from "../useAgentStream";
import { api } from "../api/client";
import { AuditView } from "./AuditView";
import { friendlyProcurementError, procurementApi } from "./api";
import { ComparisonView } from "./ComparisonView";
import { DashboardView } from "./DashboardView";
import {
  NewProcurementConversation,
  ProcurementConversation,
} from "./ProcurementConversation";
import { QuoteWorkspace } from "./QuoteWorkspace";
import { ReportView } from "./ReportView";
import type {
  ProcurementModelConfig,
  ProcurementModelConfigUpdate,
  ProcurementRequest,
  ProcurementStatus,
} from "./types";

type Props = {
  theme: "light" | "dark";
  backendVersion: string;
  maxGlobalSeq?: number;
  onToggleTheme: () => void;
};

type Tab = "quotes" | "compare" | "report" | "audit";
type BusyAction =
  | "conversation"
  | "upload"
  | "analyze"
  | "approve"
  | "no_award"
  | `field:${string}:${string}`;

const STATUS: Record<ProcurementStatus, { label: string; tone: string; step: number }> = {
  draft: { label: "Agent 读取中", tone: "info", step: 1 },
  collecting: { label: "待上传报价", tone: "neutral", step: 1 },
  review: { label: "待复核", tone: "warning", step: 2 },
  ready: { label: "待比价", tone: "info", step: 3 },
  analyzed: { label: "待审批", tone: "warning", step: 4 },
  no_award: { label: "已流标", tone: "neutral", step: 5 },
  approved: { label: "已批准", tone: "success", step: 5 },
};

const STEPS = ["创建需求", "上传报价", "字段复核", "供应商比价", "人工审批"];

const TERMINAL_RUN_STATUSES = [
  "completed",
  "failed",
  "require_human",
  "cancelled",
  "interrupted",
  "budget_stopped",
];

function errorText(error: unknown) {
  return friendlyProcurementError(error instanceof Error ? error.message : String(error || "操作失败"));
}

function requestDate(value: string) {
  return new Date(value).toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
}

const DEFAULT_CONFIG_FORM: ProcurementModelConfigUpdate = {
  provider: "openai",
  model: "gpt-4o-mini",
  base_url: "",
  api_mode: "auto",
  reasoning_effort: "auto",
  input_price_per_million_usd: 0,
  output_price_per_million_usd: 0,
  cached_input_price_per_million_usd: 0,
  max_cost_usd: null,
  ai_review_enabled: false,
  review_provider: "openai",
  review_model: null,
  review_policy: "evidence",
};

function configFormFrom(config: ProcurementModelConfig): ProcurementModelConfigUpdate {
  return {
    provider: config.provider,
    model: config.model,
    base_url: config.base_url || "",
    api_mode: config.api_mode,
    reasoning_effort: config.reasoning_effort,
    input_price_per_million_usd: config.input_price_per_million_usd,
    output_price_per_million_usd: config.output_price_per_million_usd,
    cached_input_price_per_million_usd: config.cached_input_price_per_million_usd,
    max_cost_usd: config.max_cost_usd,
    ai_review_enabled: config.ai_review_enabled,
    review_provider: config.review_provider || "openai",
    review_model: config.review_model,
    review_policy: config.review_policy || "evidence",
  };
}

export function ProcurementWorkbench({
  theme,
  backendVersion,
  maxGlobalSeq = 0,
  onToggleTheme,
}: Props) {
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<Tab>("quotes");
  const [showCreate, setShowCreate] = useState(false);
  const [showDashboard, setShowDashboard] = useState(false);
  const [pendingRunId, setPendingRunId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState<BusyAction | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [showConfig, setShowConfig] = useState(false);
  const [configForm, setConfigForm] = useState<ProcurementModelConfigUpdate>(DEFAULT_CONFIG_FORM);
  const [configBusy, setConfigBusy] = useState(false);
  const [demoBusy, setDemoBusy] = useState<"create" | "clean" | null>(null);
  const [cleanArmed, setCleanArmed] = useState(false);
  const [configError, setConfigError] = useState<string | null>(null);
  const [configNotice, setConfigNotice] = useState<string | null>(null);
  const [actionNotice, setActionNotice] = useState<string | null>(null);
  const configFormReady = useRef(false);

  const metaQuery = useQuery({ queryKey: ["procurement-meta"], queryFn: procurementApi.meta });
  const configQuery = useQuery({ queryKey: ["procurement-config"], queryFn: procurementApi.config });
  const requestsQuery = useQuery({ queryKey: ["procurement-requests"], queryFn: procurementApi.requests });
  const detailQuery = useQuery({
    queryKey: ["procurement-request", selectedId],
    queryFn: () => procurementApi.request(selectedId!),
    enabled: !!selectedId,
  });
  const reportQuery = useQuery({
    queryKey: ["procurement-report", selectedId],
    queryFn: () => procurementApi.report(selectedId!),
    enabled: !!selectedId && activeTab === "report" && !!detailQuery.data?.decision,
  });
  // Start from the last event the backend already has (from /api/health) so a
  // page load does not replay the full event history through SSE.
  const stream = useAgentStream(true, maxGlobalSeq);
  const streamRefreshTimer = useRef<number | null>(null);

  const requests = useMemo(() => requestsQuery.data || [], [requestsQuery.data]);
  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return requests;
    return requests.filter((request) =>
      [request.reference, request.title, request.item_name]
        .join(" ")
        .toLowerCase()
        .includes(query)
    );
  }, [requests, search]);

  useEffect(() => {
    if (!selectedId && !showCreate && requests.length) setSelectedId(requests[0].id);
  }, [requests, selectedId, showCreate]);

  const currentRunId = detailQuery.data?.analysis_run_id || pendingRunId;
  const latestEvent = stream.events.at(-1);
  const refreshKeyRef = useRef<string>("");
  useEffect(() => {
    const key = `${selectedId}:${currentRunId}`;
    if (key !== refreshKeyRef.current) {
      if (streamRefreshTimer.current !== null) {
        window.clearTimeout(streamRefreshTimer.current);
        streamRefreshTimer.current = null;
      }
      refreshKeyRef.current = key;
    }
    if (!latestEvent || latestEvent.run_id !== currentRunId || !selectedId) return;
    if (streamRefreshTimer.current !== null) return;
    streamRefreshTimer.current = window.setTimeout(() => {
      streamRefreshTimer.current = null;
      void Promise.all([
        queryClient.invalidateQueries({ queryKey: ["procurement-request", selectedId] }),
        queryClient.invalidateQueries({ queryKey: ["procurement-requests"] }),
        queryClient.invalidateQueries({ queryKey: ["procurement-run", currentRunId] }),
        queryClient.invalidateQueries({ queryKey: ["procurement-messages", currentRunId] }),
        queryClient.invalidateQueries({ queryKey: ["procurement-tools", currentRunId] }),
        queryClient.invalidateQueries({ queryKey: ["run-report", currentRunId] }),
        queryClient.invalidateQueries({ queryKey: ["run-checkpoint", currentRunId] }),
      ]);
    }, 500);
  }, [currentRunId, latestEvent, queryClient, selectedId]);

  useEffect(() => {
    if (!latestEvent || latestEvent.run_id !== currentRunId) return;
    const payload = latestEvent.payload as { status?: string } | undefined;
    const terminalEvent =
      latestEvent.type === "run_status" &&
      payload?.status &&
      TERMINAL_RUN_STATUSES.includes(payload.status);
    const terminalRunEvent = [
      "run_completed",
      "run_failed",
      "run_cancelled",
      "run_interrupted",
      "run_budget_stopped",
    ].includes(latestEvent.type);
    if (terminalEvent || terminalRunEvent) {
      setBusy((current) => (current === "analyze" ? null : current));
    }
  }, [currentRunId, latestEvent]);

  // Low-frequency poll of the current run while it is active: if the SSE
  // terminal event was missed (reload, reconnect, concurrent runs), the busy
  // state still clears once the request poll observes the terminal status.
  const runStatusQuery = useQuery({
    queryKey: ["procurement-run", currentRunId],
    queryFn: () => api.run(currentRunId!),
    enabled: !!currentRunId && busy === "analyze",
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (!status || TERMINAL_RUN_STATUSES.includes(status)) return false;
      return stream.status === "live" ? 5000 : 750;
    },
  });
  useEffect(() => {
    const status = runStatusQuery.data?.status;
    if (busy === "analyze" && status && TERMINAL_RUN_STATUSES.includes(status)) {
      setBusy(null);
    }
  }, [busy, runStatusQuery.data?.status]);

  useEffect(() => () => {
    if (streamRefreshTimer.current !== null) window.clearTimeout(streamRefreshTimer.current);
  }, []);

  async function commit(updated: ProcurementRequest) {
    queryClient.setQueryData(["procurement-request", updated.id], updated);
    await queryClient.invalidateQueries({ queryKey: ["procurement-requests"] });
    await queryClient.invalidateQueries({ queryKey: ["procurement-report", updated.id] });
    if (updated.analysis_run_id) {
      await queryClient.invalidateQueries({ queryKey: ["procurement-run", updated.analysis_run_id] });
      await queryClient.invalidateQueries({ queryKey: ["procurement-messages", updated.analysis_run_id] });
      await queryClient.invalidateQueries({ queryKey: ["procurement-tools", updated.analysis_run_id] });
      await queryClient.invalidateQueries({ queryKey: ["run-report", updated.analysis_run_id] });
      await queryClient.invalidateQueries({ queryKey: ["run-checkpoint", updated.analysis_run_id] });
    }
  }

  async function startConversation(message: string, files: File[]) {
    setBusy("conversation");
    setActionError(null);
    setActionNotice(null);
    try {
      const accepted = await procurementApi.startConversation(message, files);
      setPendingRunId(accepted.run_id);
      setSelectedId(accepted.purchase_request_id);
      setShowCreate(false);
      setActiveTab("quotes");
      await queryClient.invalidateQueries({ queryKey: ["procurement-requests"] });
      await queryClient.invalidateQueries({
        queryKey: ["procurement-request", accepted.purchase_request_id],
      });
    } catch (error) {
      setActionError(errorText(error));
    } finally {
      setBusy(null);
    }
  }

  async function uploadQuotes(files: File[]) {
    if (!selectedId) return;
    setBusy("upload");
    setActionError(null);
    setActionNotice(null);
    try {
      for (const file of files) await procurementApi.uploadQuote(selectedId, file);
      const updated = await procurementApi.request(selectedId);
      await commit(updated);
    } catch (error) {
      setActionError(errorText(error));
      try {
        const updated = await procurementApi.request(selectedId);
        await commit(updated);
      } catch {
        // Keep the original upload error; a failed refresh must not surface
        // as an unhandled rejection.
      }
    } finally {
      setBusy(null);
    }
  }

  async function correctField(
    quoteId: string,
    field: string,
    value: string | number | boolean | null
  ) {
    if (!selectedId) return;
    setBusy(`field:${quoteId}:${field}`);
    setActionError(null);
    setActionNotice(null);
    try {
      await procurementApi.correctField(selectedId, quoteId, field, value);
      await commit(await procurementApi.request(selectedId));
    } catch (error) {
      setActionError(errorText(error));
      throw error;
    } finally {
      setBusy(null);
    }
  }

  function knowledgeFeedback(chunkId: string, action: "viewed" | "adopted") {
    // Lightweight feedback: never blocks the procurement flow.
    if (!selectedId) return;
    void procurementApi
      .knowledgeFeedback(selectedId, chunkId, action)
      .catch(() => undefined);
  }

  async function analyze() {
    if (!selectedId) return;
    setBusy("analyze");
    setActionError(null);
    setActionNotice(null);
    setActionNotice(null);
    try {
      const accepted = await procurementApi.analyze(selectedId);
      setPendingRunId(accepted.run_id);
      await queryClient.invalidateQueries({ queryKey: ["procurement-request", selectedId] });
      await queryClient.invalidateQueries({ queryKey: ["procurement-requests"] });
      setActiveTab("compare");
    } catch (error) {
      setActionError(errorText(error));
      setBusy(null);
    }
  }

  async function resume(message: string) {
    if (!selectedId) return;
    setActionError(null);
    setActionNotice(null);
    try {
      const accepted = await procurementApi.resume(selectedId, message);
      setPendingRunId(accepted.run_id);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["procurement-request", selectedId] }),
        queryClient.invalidateQueries({ queryKey: ["procurement-requests"] }),
        queryClient.invalidateQueries({ queryKey: ["procurement-run", accepted.run_id] }),
        queryClient.invalidateQueries({ queryKey: ["procurement-messages", accepted.run_id] }),
        queryClient.invalidateQueries({ queryKey: ["procurement-tools", accepted.run_id] }),
      ]);
    } catch (error) {
      setActionError(errorText(error));
      throw error;
    }
  }

  async function approve(quoteId: string, note: string, reviewAck = false) {
    const detail = detailQuery.data;
    if (!selectedId || !detail?.comparison) return;
    setBusy("approve");
    setActionError(null);
    setActionNotice(null);
    try {
      const updated = await procurementApi.approve(selectedId, {
        snapshot_id: detail.comparison.id,
        input_sha256: detail.comparison.input_sha256,
        quote_id: quoteId,
        confirmed: true,
        note,
        review_ack: reviewAck,
      });
      await commit(updated);
      setActiveTab("report");
    } catch (error) {
      setActionError(errorText(error));
    } finally {
      setBusy(null);
    }
  }

  async function noAward(note: string) {
    const detail = detailQuery.data;
    if (!selectedId || !detail?.comparison) return;
    setBusy("no_award");
    setActionError(null);
    setActionNotice(null);
    try {
      const updated = await procurementApi.approve(selectedId, {
        snapshot_id: detail.comparison.id,
        input_sha256: detail.comparison.input_sha256,
        decision: "no_award",
        confirmed: true,
        note,
      });
      await commit(updated);
      setActiveTab("report");
    } catch (error) {
      setActionError(errorText(error));
    } finally {
      setBusy(null);
    }
  }

  const detail = detailQuery.data || null;
  const status = detail ? STATUS[detail.status] : null;

  function openConfig() {
    configFormReady.current = false;
    if (configQuery.data) {
      setConfigForm(configFormFrom(configQuery.data));
      configFormReady.current = true;
    }
    setConfigError(null);
    setConfigNotice(null);
    setShowConfig(true);
  }

  useEffect(() => {
    // Sync the drawer form as soon as the config query resolves, even when the
    // drawer was opened before the response arrived. Without this the form
    // keeps DEFAULT_CONFIG_FORM and saving silently overwrites the real config.
    if (showConfig && !configFormReady.current && configQuery.data) {
      setConfigForm(configFormFrom(configQuery.data));
      configFormReady.current = true;
    }
  }, [configQuery.data, showConfig]);

  function updateConfigField<K extends keyof ProcurementModelConfigUpdate>(
    field: K,
    value: ProcurementModelConfigUpdate[K]
  ) {
    setConfigForm((current) => ({ ...current, [field]: value }));
  }

  async function createDemoTask() {
    setDemoBusy("create");
    setActionError(null);
    setActionNotice(null);
    try {
      const accepted = await procurementApi.createDemo();
      await requestsQuery.refetch();
      setSelectedId(accepted.purchase_request_id);
    } catch (err) {
      setActionError(err instanceof Error ? friendlyProcurementError(err.message) : "演示任务创建失败");
    } finally {
      setDemoBusy(null);
    }
  }

  async function cleanDemoTasks() {
    if (!cleanArmed) {
      setCleanArmed(true);
      window.setTimeout(() => setCleanArmed(false), 4000);
      return;
    }
    setCleanArmed(false);
    setDemoBusy("clean");
    setActionError(null);
    setActionNotice(null);
    setActionNotice(null);
    try {
      const result = await procurementApi.cleanDemo();
      const listResult = await requestsQuery.refetch();
      const removedSelected =
        !!selectedId && !(listResult.data || []).some((row) => row.id === selectedId);
      if (selectedId) {
        // The deleted request must not keep rendering from the stale detail
        // cache (subsequent tabs would 404).
        queryClient.removeQueries({ queryKey: ["procurement-request", selectedId] });
        queryClient.removeQueries({ queryKey: ["procurement-report", selectedId] });
        if (!removedSelected) {
          await queryClient.invalidateQueries({ queryKey: ["procurement-request", selectedId] });
        }
      }
      if (removedSelected) {
        setSelectedId(null);
        setPendingRunId(null);
        setActiveTab("quotes");
      }
      setActionNotice(
        result.skipped > 0
          ? `已清理 ${result.removed} 个演示任务；${result.skipped} 个正在运行的任务已保留。`
          : null
      );
    } catch (err) {
      setActionError(err instanceof Error ? friendlyProcurementError(err.message) : "演示任务清理失败");
    } finally {
      setDemoBusy(null);
    }
  }

  async function saveConfig() {
    setConfigBusy(true);
    setConfigError(null);
    setConfigNotice(null);
    const payload: ProcurementModelConfigUpdate = { ...configForm };
    if (!payload.api_key?.trim()) delete payload.api_key;
    try {
      const updated = await procurementApi.updateConfig(payload);
      queryClient.setQueryData(["procurement-config"], updated);
      setConfigForm(configFormFrom(updated));
      setConfigNotice(
        "已保存到本地会话配置；检测到 .env 模型配置时，重启后仍以 .env 为准。"
      );
    } catch (error) {
      setConfigError(errorText(error));
    } finally {
      setConfigBusy(false);
    }
  }

  return (
    <div className="proc-app">
      <header className="proc-topbar">
        <div className="proc-brand">
          <span><Scale size={20} /></span>
          <div><strong>采价台</strong><small>采购询价与供应商比价</small></div>
        </div>
        <div className="proc-topbar-meta">
          <span className="proc-runtime-state"><Wifi size={14} />采购服务 {backendVersion}</span>
          <button className="proc-icon-button" type="button" title="运营仪表盘" aria-label="运营仪表盘" onClick={() => setShowDashboard((value) => !value)}>
            <BarChart3 size={16} />
          </button>
          <button className="proc-icon-button" type="button" title="API / 模型配置" aria-label="API / 模型配置" onClick={openConfig}>
            <Settings size={16} />
          </button>
          <button className="proc-icon-button" type="button" title="切换主题" aria-label="切换主题" onClick={onToggleTheme}>
            {theme === "light" ? <Moon size={16} /> : <Sun size={16} />}
          </button>
        </div>
      </header>

      <main className="proc-layout">
        <aside className="proc-sidebar">
          <div className="proc-sidebar-head">
            <span>采购任务</span>
            <div className="proc-sidebar-actions">
              <button className="proc-button tiny" type="button" title="一键新建演示任务" disabled={demoBusy !== null} onClick={() => void createDemoTask()}>
                {demoBusy === "create" ? <LoaderCircle className="spin" size={13} /> : <Sparkles size={13} />}演示
              </button>
              <button className={`proc-button tiny ${cleanArmed ? "danger" : ""}`} type="button" title={cleanArmed ? "再次点击确认清理（4 秒后自动取消）" : "一键清理演示任务"} disabled={demoBusy !== null} onClick={() => void cleanDemoTasks()}>
                {demoBusy === "clean" ? <LoaderCircle className="spin" size={13} /> : <Trash2 size={13} />}
                {cleanArmed ? "确认清理？" : "清理"}
              </button>
              <button className="proc-icon-button primary-icon" type="button" title="新建采购对话" aria-label="新建采购对话" onClick={() => { setActionError(null); setSelectedId(null); setShowCreate(true); setShowDashboard(false); }}>
                <Plus size={17} />
              </button>
            </div>
          </div>
          <label className="proc-search">
            <Search size={15} />
            <input aria-label="搜索采购任务" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="编号、任务或物料" />
          </label>
          <div className="proc-request-list">
            {requestsQuery.isPending ? (
              <div className="proc-sidebar-empty"><span>正在加载采购任务…</span></div>
            ) : null}
            {requestsQuery.isError ? (
              <div className="proc-sidebar-empty error" role="alert"><span>采购任务加载失败</span></div>
            ) : null}
            {filtered.map((request) => {
              const itemStatus = STATUS[request.status];
              return (
                <button
                  type="button"
                  className={`proc-request-item ${selectedId === request.id ? "selected" : ""}`}
                  key={request.id}
                  onClick={() => { setSelectedId(request.id); setShowCreate(false); setShowDashboard(false); setActiveTab("quotes"); setActionError(null); }}
                >
                  <span className="proc-request-row"><code>{request.reference}</code><small>{requestDate(request.updated_at)}</small></span>
                  <strong>{request.title}</strong>
                  <span className="proc-request-row"><small>{request.quote_count} 家报价 · {request.status === "draft" ? "待识别" : `${request.quantity.toLocaleString("zh-CN")} 个`}</small><i className={itemStatus.tone}>{itemStatus.label}</i></span>
                </button>
              );
            })}
            {!requestsQuery.isPending && !requestsQuery.isError && !filtered.length ? (
              <div className="proc-sidebar-empty"><Archive size={22} /><span>{search ? "没有匹配任务" : "还没有采购任务"}</span></div>
            ) : null}
          </div>
        </aside>

        <section className="proc-main">
          {actionNotice && !showDashboard ? <p className="proc-inline-success" role="status">{actionNotice}</p> : null}
          {showDashboard ? (
            <DashboardView />
          ) : (
            <>
          {showCreate || (!selectedId && !detail) ? (
            <NewProcurementConversation
              busy={busy === "conversation"}
              error={actionError}
              maxFileBytes={metaQuery.data?.max_file_bytes ?? 5 * 1024 * 1024}
              maxTotalBytes={metaQuery.data?.max_conversation_upload_bytes ?? 20 * 1024 * 1024}
              maxQuotes={metaQuery.data?.max_quotes_per_request ?? 50}
              onStart={startConversation}
            />
          ) : detail ? (
            <>
              <header className="proc-request-head">
                <div className="proc-title-line">
                  <div><span>{detail.reference}</span><h1>{detail.title}</h1></div>
                  {status ? <span className={`proc-status ${status.tone}`}><i />{status.label}</span> : null}
                </div>
                <div className="proc-request-facts">
                  <span><small>物料</small><strong>{detail.status === "draft" ? "待识别" : detail.item_name}</strong></span>
                  <span><small>采购量</small><strong>{detail.status === "draft" ? "待识别" : `${detail.quantity.toLocaleString("zh-CN")} 个`}</strong></span>
                  <span><small>规格</small><strong>{detail.status === "draft" ? "待识别" : `${String(detail.specifications.width_mm)} × ${String(detail.specifications.length_mm)} mm · ${String(detail.specifications.thickness_um)} µm`}</strong></span>
                  <span><small>最长交期</small><strong>{detail.status === "draft" ? "待识别" : `${String(detail.constraints.max_lead_days)} 天`}</strong></span>
                </div>
                <ol className="proc-progress" aria-label="采购进度">
                  {STEPS.map((step, index) => (
                    <li key={step} className={status && index + 1 <= status.step ? "done" : ""}>
                      <span>{index + 1 <= (status?.step || 0) ? <CheckCircle2 size={14} /> : index + 1}</span>
                      <strong>{step}</strong>
                    </li>
                  ))}
                </ol>
              </header>

              <div className="proc-task-body">
                <ProcurementConversation
                  request={detail}
                  streamLive={stream.status === "live"}
                  actionError={actionError}
                  onResume={resume}
                  onRecover={analyze}
                  onOpenComparison={() => setActiveTab("compare")}
                />
                <section className="proc-structured-workspace">
                  <nav className="proc-tabs" aria-label="采购任务视图">
                    <button className={activeTab === "quotes" ? "active" : ""} type="button" onClick={() => setActiveTab("quotes")}><Files size={16} />报价与复核{detail.unresolved_field_count ? <span>{detail.unresolved_field_count}</span> : null}</button>
                    <button className={activeTab === "compare" ? "active" : ""} type="button" onClick={() => setActiveTab("compare")}><Scale size={16} />供应商比价</button>
                    <button className={activeTab === "report" ? "active" : ""} type="button" onClick={() => setActiveTab("report")}><FileCheck2 size={16} />审批报告</button>
                    <button className={activeTab === "audit" ? "active" : ""} type="button" onClick={() => setActiveTab("audit")}><Activity size={16} />运行审计</button>
                  </nav>

                  <div className="proc-tab-content">
                    {activeTab === "quotes" && metaQuery.data ? (
                      <QuoteWorkspace request={detail} meta={metaQuery.data} busy={busy} error={actionError} onUpload={uploadQuotes} onCorrect={correctField} onAnalyze={analyze} />
                    ) : null}
                    {activeTab === "quotes" && metaQuery.isPending ? (
                      <div className="proc-loading-state">正在加载报价字段配置…</div>
                    ) : null}
                    {activeTab === "quotes" && metaQuery.isError ? (
                      <section className="proc-empty-state compact" role="alert">
                        <AlertTriangle size={28} />
                        <h2>报价字段配置加载失败</h2>
                        <p>{errorText(metaQuery.error)}</p>
                        <button className="proc-button secondary" type="button" onClick={() => void metaQuery.refetch()}>重新加载</button>
                      </section>
                    ) : null}
                    {activeTab === "compare" ? (
                      <ComparisonView request={detail} busy={busy} error={actionError} reviewPolicy={configQuery.data?.review_policy || "evidence"} onAnalyze={analyze} onApprove={approve} onNoAward={noAward} onKnowledgeFeedback={knowledgeFeedback} />
                    ) : null}
                    {activeTab === "report" ? (
                      <ReportView request={detail} report={reportQuery.data || null} loading={reportQuery.isPending} error={reportQuery.isError ? errorText(reportQuery.error) : null} />
                    ) : null}
                    {activeTab === "audit" ? <AuditView request={detail} /> : null}
                  </div>
                </section>
              </div>
            </>
          ) : detailQuery.isError && selectedId ? (
            <section className="proc-empty-state first" role="alert">
              <AlertTriangle size={30} />
              <h1>采购任务加载失败</h1>
              <p>{errorText(detailQuery.error)}</p>
              <button className="proc-button secondary" type="button" onClick={() => void detailQuery.refetch()}>重新加载</button>
            </section>
          ) : detailQuery.isPending && selectedId ? (
            <div className="proc-loading-state">正在恢复采购任务…</div>
          ) : null}
            </>
          )}
        </section>
      </main>

      {showConfig ? (
        <div
          className="proc-drawer-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setShowConfig(false);
          }}
        >
          <aside className="proc-config-drawer" role="dialog" aria-modal="true" aria-labelledby="proc-config-title">
            <header className="proc-config-head">
              <div>
                <span className="proc-config-icon"><Settings size={17} /></span>
                <div>
                  <h2 id="proc-config-title">API / 模型配置</h2>
                  <p>仅影响之后新启动的采购 Agent 运行</p>
                </div>
              </div>
              <button className="proc-icon-button compact" type="button" title="关闭配置" aria-label="关闭配置" onClick={() => setShowConfig(false)}>
                <X size={16} />
              </button>
            </header>

            <div className="proc-config-body">
              {configQuery.isPending ? <div className="proc-config-loading"><LoaderCircle className="spin" size={18} />正在读取当前配置…</div> : null}
              {configQuery.isError ? (
                <section className="proc-config-error" role="alert">
                  <strong>配置读取失败</strong>
                  <span>{errorText(configQuery.error)}</span>
                  <button className="proc-button secondary" type="button" onClick={() => void configQuery.refetch()}>重新读取</button>
                </section>
              ) : null}
              {!configQuery.isPending && !configQuery.isError ? (
                <>
                  <section className="proc-config-section">
                    <div className="proc-config-section-title"><strong>模型服务</strong><span>检测到 .env 模型配置时以 .env 为准（本机保存仅当前会话生效）</span></div>
                    <label className="proc-field proc-span-2">
                      <span>Provider</span>
                      <select value={configForm.provider} onChange={(event) => {
                        const provider = event.target.value as ProcurementModelConfigUpdate["provider"];
                        updateConfigField("provider", provider);
                        if (provider === "procurement_fake") updateConfigField("model", "procurement-fake-v1");
                      }}>
                        <option value="procurement_fake">离线演示（Fake Provider）</option>
                        <option value="openai">OpenAI 兼容 API</option>
                      </select>
                    </label>
                    <label className="proc-field proc-span-2">
                      <span>模型名称</span>
                      <input value={configForm.model} disabled={configForm.provider === "procurement_fake"} onChange={(event) => updateConfigField("model", event.target.value)} placeholder="例如 gpt-4o-mini" />
                    </label>
                    <label className="proc-field proc-span-2">
                      <span>API Base URL <small>可选，留空使用官方地址</small></span>
                      <input value={configForm.base_url} disabled={configForm.provider === "procurement_fake"} onChange={(event) => updateConfigField("base_url", event.target.value)} placeholder="例如 https://api.openai.com/v1" />
                    </label>
                    <label className="proc-field proc-span-2">
                      <span>API Key <small>{configQuery.data?.api_key_preview ? `当前 ${configQuery.data.api_key_preview}，留空保持不变` : "不会回显已保存的密钥"}</small></span>
                      <input type="password" autoComplete="new-password" disabled={configForm.provider === "procurement_fake"} value={configForm.api_key || ""} onChange={(event) => updateConfigField("api_key", event.target.value)} placeholder={configQuery.data?.api_key_configured ? "留空保持当前密钥" : "请输入 API Key（可选）"} />
                    </label>
                    <label className="proc-field">
                      <span>API 模式</span>
                      <select value={configForm.api_mode} disabled={configForm.provider === "procurement_fake"} onChange={(event) => updateConfigField("api_mode", event.target.value as ProcurementModelConfigUpdate["api_mode"])}>
                        <option value="auto">自动判断</option>
                        <option value="responses">Responses API</option>
                        <option value="chat">Chat Completions</option>
                      </select>
                    </label>
                    <label className="proc-field">
                      <span>推理强度</span>
                      <select value={configForm.reasoning_effort} disabled={configForm.provider === "procurement_fake"} onChange={(event) => updateConfigField("reasoning_effort", event.target.value as ProcurementModelConfigUpdate["reasoning_effort"])}>
                        <option value="auto">自动</option>
                        <option value="none">none</option>
                        <option value="minimal">minimal</option>
                        <option value="low">low</option>
                        <option value="medium">medium</option>
                        <option value="high">high</option>
                        <option value="max">max</option>
                      </select>
                    </label>
                  </section>

                  <section className={`proc-config-section ${configForm.provider === "procurement_fake" ? "disabled" : ""}`}>
                    <div className="proc-config-section-title"><strong>成本保护</strong><span>按模型价格估算并限制单次 Run</span></div>
                    <label className="proc-field">
                      <span>输入价格（USD / 1M tokens）</span>
                      <input type="number" min="0" step="0.01" disabled={configForm.provider === "procurement_fake"} value={configForm.input_price_per_million_usd ?? ""} onChange={(event) => updateConfigField("input_price_per_million_usd", event.target.value === "" ? null : Number(event.target.value))} />
                    </label>
                    <label className="proc-field">
                      <span>输出价格（USD / 1M tokens）</span>
                      <input type="number" min="0" step="0.01" disabled={configForm.provider === "procurement_fake"} value={configForm.output_price_per_million_usd ?? ""} onChange={(event) => updateConfigField("output_price_per_million_usd", event.target.value === "" ? null : Number(event.target.value))} />
                    </label>
                    <label className="proc-field">
                      <span>缓存输入价格（USD / 1M tokens）</span>
                      <input type="number" min="0" step="0.01" disabled={configForm.provider === "procurement_fake"} value={configForm.cached_input_price_per_million_usd ?? ""} onChange={(event) => updateConfigField("cached_input_price_per_million_usd", event.target.value === "" ? null : Number(event.target.value))} />
                    </label>
                    <label className="proc-field">
                      <span>单次 Run 费用上限（USD）</span>
                      <input type="number" min="0" step="0.01" disabled={configForm.provider === "procurement_fake"} value={configForm.max_cost_usd ?? ""} onChange={(event) => updateConfigField("max_cost_usd", event.target.value === "" ? null : Number(event.target.value))} placeholder="留空表示不限" />
                    </label>
                  </section>

                  <section className="proc-config-section">
                    <label className="proc-field proc-field-checkbox">
                      <input type="checkbox" checked={configForm.ai_review_enabled ?? false} onChange={(event) => updateConfigField("ai_review_enabled", event.target.checked)} />
                      <span>审批前启用独立评审（不阻塞审批，只记录证据）</span>
                    </label>
                    <label className="proc-field">
                      <span>独立评审 Provider</span>
                      <input value={configForm.review_provider || ""} onChange={(event) => updateConfigField("review_provider", event.target.value)} placeholder="例如 openai" />
                    </label>
                    <label className="proc-field">
                      <span>独立评审模型（留空用主模型）</span>
                      <input value={configForm.review_model || ""} onChange={(event) => updateConfigField("review_model", event.target.value)} placeholder="例如 deepseek-v4-flash" />
                    </label>
                    <label className="proc-field">
                      <span>评审策略</span>
                      <select value={configForm.review_policy || "evidence"} onChange={(event) => updateConfigField("review_policy", event.target.value as "off" | "evidence" | "warn" | "gate")}>
                        <option value="off">关闭（不评审）</option>
                        <option value="evidence">仅记录证据</option>
                        <option value="warn">异议告警（不阻塞）</option>
                        <option value="gate">门禁（异议需人工确认）</option>
                      </select>
                    </label>
                  </section>

                  <p className="proc-config-security"><span><ShieldCheck size={15} />密钥保存在本机采购服务配置中，GET 接口只返回脱敏状态。</span></p>
                  {configError ? <p className="proc-form-error" role="alert">{configError}</p> : null}
                  {configNotice ? <p className="proc-form-success" role="status">{configNotice}</p> : null}
                </>
              ) : null}
            </div>
            <footer className="proc-config-actions">
              <button className="proc-button secondary" type="button" onClick={() => setShowConfig(false)}>取消</button>
              <button className="proc-button" type="button" disabled={configBusy || configQuery.isPending || configQuery.isError} onClick={() => void saveConfig()}>
                {configBusy ? <LoaderCircle className="spin" size={15} /> : <Save size={15} />}保存配置
              </button>
            </footer>
          </aside>
        </div>
      ) : null}
    </div>
  );
}
