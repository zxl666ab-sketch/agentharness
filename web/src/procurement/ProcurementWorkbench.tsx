import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  AlertTriangle,
  Archive,
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
  Sun,
  Trash2,
  Wifi,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { useAgentStream } from "../useAgentStream";
import { AuditView } from "./AuditView";
import { procurementApi } from "./api";
import { ComparisonView } from "./ComparisonView";
import {
  NewProcurementConversation,
  ProcurementConversation,
} from "./ProcurementConversation";
import { QuoteWorkspace } from "./QuoteWorkspace";
import { ReportView } from "./ReportView";
import { RequirementReview } from "./RequirementReview";
import type {
  ProcurementModelConfig,
  ProcurementModelConfigUpdate,
  ProcurementRequest,
  ProcurementRequestSummary,
  ProcurementStatus,
} from "./types";

type Props = {
  theme: "light" | "dark";
  backendVersion: string;
  onToggleTheme: () => void;
};

type Tab = "quotes" | "compare" | "report" | "audit";

const STATUS: Record<ProcurementStatus, { label: string; tone: string; step: number }> = {
  draft: { label: "Agent 读取中", tone: "info", step: 1 },
  collecting: { label: "待上传报价", tone: "neutral", step: 1 },
  review: { label: "待复核", tone: "warning", step: 2 },
  ready: { label: "待比价", tone: "info", step: 3 },
  analyzed: { label: "待审批", tone: "warning", step: 4 },
  approved: { label: "已批准", tone: "success", step: 5 },
  no_award: { label: "本轮流标", tone: "neutral", step: 5 },
};

const STEPS = ["创建需求", "上传报价", "字段复核", "供应商比价", "人工审批"];

function errorText(error: unknown) {
  return error instanceof Error ? error.message : String(error || "操作失败");
}

function requestDate(value: string) {
  return new Date(value).toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
}

function quantityText(value: number | string) {
  return typeof value === "number" ? value.toLocaleString("zh-CN") : String(value);
}

function specificationText(specifications: ProcurementRequest["specifications"]) {
  const values = Object.entries(specifications || {}).slice(0, 3).map(([key, value]) => {
    if (value && typeof value === "object" && "value" in value) {
      const spec = value as { label?: string; value?: unknown; unit?: string };
      return `${spec.label || key} ${String(spec.value ?? "-")}${spec.unit ? ` ${spec.unit}` : ""}`;
    }
    return `${key} ${String(value ?? "-")}`;
  });
  return values.join(" · ") || "未填写规格";
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
  };
}

export function ProcurementWorkbench({ theme, backendVersion, onToggleTheme }: Props) {
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<Tab>("quotes");
  const [showCreate, setShowCreate] = useState(false);
  const [pendingRunId, setPendingRunId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [showConfig, setShowConfig] = useState(false);
  const [configForm, setConfigForm] = useState<ProcurementModelConfigUpdate>(DEFAULT_CONFIG_FORM);
  const [configBusy, setConfigBusy] = useState(false);
  const [configError, setConfigError] = useState<string | null>(null);
  const [configNotice, setConfigNotice] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ProcurementRequestSummary | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

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
  const stream = useAgentStream(true, 0);

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
  useEffect(() => {
    if (!latestEvent || latestEvent.run_id !== currentRunId || !selectedId) return;
    void Promise.all([
      queryClient.invalidateQueries({ queryKey: ["procurement-request", selectedId] }),
      queryClient.invalidateQueries({ queryKey: ["procurement-requests"] }),
      queryClient.invalidateQueries({ queryKey: ["procurement-run", currentRunId] }),
      queryClient.invalidateQueries({ queryKey: ["procurement-messages", currentRunId] }),
      queryClient.invalidateQueries({ queryKey: ["procurement-tools", currentRunId] }),
      queryClient.invalidateQueries({ queryKey: ["run-report", currentRunId] }),
      queryClient.invalidateQueries({ queryKey: ["run-checkpoint", currentRunId] }),
    ]);
  }, [currentRunId, latestEvent, queryClient, selectedId]);

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
    try {
      for (const file of files) await procurementApi.uploadQuote(selectedId, file);
      const updated = await procurementApi.request(selectedId);
      await commit(updated);
    } catch (error) {
      setActionError(errorText(error));
      const updated = await procurementApi.request(selectedId);
      await commit(updated);
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
    try {
      await procurementApi.correctField(selectedId, quoteId, field, value);
      await commit(await procurementApi.request(selectedId));
    } catch (error) {
      setActionError(errorText(error));
    } finally {
      setBusy(null);
    }
  }

  async function correctRequirement(
    payload: Parameters<typeof procurementApi.correctRequirement>[1],
  ) {
    if (!selectedId) return;
    setBusy("requirement");
    setActionError(null);
    try {
      await commit(await procurementApi.correctRequirement(selectedId, payload));
    } catch (error) {
      setActionError(errorText(error));
      throw error;
    } finally {
      setBusy(null);
    }
  }

  async function analyze() {
    if (!selectedId) return;
    setBusy("analyze");
    setActionError(null);
    try {
      const accepted = await procurementApi.analyze(selectedId);
      setPendingRunId(accepted.run_id);
      await queryClient.invalidateQueries({ queryKey: ["procurement-request", selectedId] });
      await queryClient.invalidateQueries({ queryKey: ["procurement-requests"] });
      setActiveTab("compare");
    } catch (error) {
      setActionError(errorText(error));
    } finally {
      setBusy(null);
    }
  }

  async function resume(message: string) {
    if (!selectedId) return;
    setActionError(null);
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

  async function approve(quoteId: string, note: string) {
    const detail = detailQuery.data;
    if (!selectedId || !detail?.comparison) return;
    setBusy("approve");
    setActionError(null);
    try {
      const updated = await procurementApi.approve(selectedId, {
        decision: "approved",
        snapshot_id: detail.comparison.id,
        input_sha256: detail.comparison.input_sha256,
        quote_id: quoteId,
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

  async function noAward(note: string) {
    const detail = detailQuery.data;
    if (!selectedId || !detail?.comparison) return;
    setBusy("no_award");
    setActionError(null);
    try {
      const updated = await procurementApi.approve(selectedId, {
        decision: "no_award",
        snapshot_id: detail.comparison.id,
        input_sha256: detail.comparison.input_sha256,
        quote_id: null,
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

  async function reopen(copyQuotes: boolean) {
    if (!selectedId) return;
    setBusy(copyQuotes ? "reopen_quotes" : "reopen");
    setActionError(null);
    try {
      const updated = await procurementApi.reopen(selectedId, copyQuotes);
      await commit(updated);
      setSelectedId(updated.id);
      setActiveTab("quotes");
    } catch (error) {
      setActionError(errorText(error));
    } finally {
      setBusy(null);
    }
  }

  function openDelete(request: ProcurementRequestSummary) {
    setDeleteTarget(request);
    setDeleteError(null);
  }

  async function deleteRequest() {
    if (!deleteTarget) return;
    const requestId = deleteTarget.id;
    setDeleteBusy(true);
    setDeleteError(null);
    try {
      await procurementApi.deleteRequest(requestId);
      await queryClient.cancelQueries({ queryKey: ["procurement-request", requestId] });
      queryClient.removeQueries({ queryKey: ["procurement-request", requestId] });
      setDeleteTarget(null);
      if (selectedId === requestId) {
        setSelectedId(null);
        setShowCreate(true);
        setActiveTab("quotes");
      }
      await queryClient.invalidateQueries({ queryKey: ["procurement-requests"] });
    } catch (error) {
      setDeleteError(errorText(error));
    } finally {
      setDeleteBusy(false);
    }
  }

  const detail = detailQuery.data || null;
  const status = detail ? STATUS[detail.status] : null;

  function openConfig() {
    if (configQuery.data) setConfigForm(configFormFrom(configQuery.data));
    setConfigError(null);
    setConfigNotice(null);
    setShowConfig(true);
  }

  function updateConfigField<K extends keyof ProcurementModelConfigUpdate>(
    field: K,
    value: ProcurementModelConfigUpdate[K]
  ) {
    setConfigForm((current) => ({ ...current, [field]: value }));
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
      setConfigNotice("已保存到本地服务配置，重启后会自动恢复。 ");
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
            <button className="proc-icon-button primary-icon" type="button" title="新建采购对话" aria-label="新建采购对话" onClick={() => { setActionError(null); setSelectedId(null); setShowCreate(true); }}>
              <Plus size={17} />
            </button>
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
                <div
                  className={`proc-request-item ${selectedId === request.id ? "selected" : ""}`}
                  key={request.id}
                >
                  <button
                    type="button"
                    className="proc-request-item-main"
                    onClick={() => { setSelectedId(request.id); setShowCreate(false); setActiveTab("quotes"); setActionError(null); }}
                  >
                    <span className="proc-request-row"><code>{request.reference}</code><small>{requestDate(request.updated_at)}</small></span>
                    <strong>{request.title}</strong>
                    <span className="proc-request-row"><small>{request.quote_count} 家报价 · {quantityText(request.quantity)} {request.unit}</small><i className={itemStatus.tone}>{itemStatus.label}</i></span>
                  </button>
                  <button
                    className="proc-request-delete proc-icon-button"
                    type="button"
                    title="删除任务"
                    aria-label={`删除任务 ${request.reference}`}
                    onClick={() => openDelete(request)}
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              );
            })}
            {!requestsQuery.isPending && !requestsQuery.isError && !filtered.length ? (
              <div className="proc-sidebar-empty"><Archive size={22} /><span>{search ? "没有匹配任务" : "还没有采购任务"}</span></div>
            ) : null}
          </div>
        </aside>

        <section className="proc-main">
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
                  <span><small>物料</small><strong>{detail.item_name}</strong></span>
                  <span><small>采购量</small><strong>{quantityText(detail.quantity)} {detail.unit}</strong></span>
                  <span><small>规格</small><strong>{specificationText(detail.specifications)}</strong></span>
                  <span><small>最长交期</small><strong>{String(detail.constraints.max_lead_days ?? "-")} 天</strong></span>
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
                      <>
                        <RequirementReview request={detail} busy={busy === "requirement"} error={actionError} onSave={correctRequirement} />
                        <QuoteWorkspace request={detail} meta={metaQuery.data} busy={busy} error={actionError} onUpload={uploadQuotes} onCorrect={correctField} onAnalyze={analyze} />
                      </>
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
                      <ComparisonView
                        request={detail}
                        busy={busy}
                        error={actionError}
                        onAnalyze={analyze}
                        onApprove={approve}
                        onNoAward={noAward}
                        onOpenRequirement={() => setActiveTab("quotes")}
                        onOpenQuotes={() => setActiveTab("quotes")}
                      />
                    ) : null}
                    {activeTab === "report" ? (
                      <ReportView
                        request={detail}
                        report={reportQuery.data || null}
                        loading={reportQuery.isPending}
                        error={reportQuery.isError ? errorText(reportQuery.error) : null}
                        onReopen={reopen}
                      />
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
        </section>
      </main>

      {deleteTarget ? (
        <div
          className="proc-drawer-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget && !deleteBusy) setDeleteTarget(null);
          }}
        >
          <section className="proc-confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="delete-request-title">
            <header>
              <div><Trash2 size={17} /><h2 id="delete-request-title">删除采购任务</h2></div>
              <button className="proc-icon-button compact" type="button" title="关闭" aria-label="关闭" onClick={() => setDeleteTarget(null)} disabled={deleteBusy}>
                <X size={16} />
              </button>
            </header>
            <div className="proc-delete-target">
              <strong>{deleteTarget.reference}</strong>
              <span>{deleteTarget.title}</span>
            </div>
            <p className="proc-confirm-warning">删除后将移除任务列表中的采购需求、报价、比价快照和审批记录，不能恢复。</p>
            {deleteError ? <p className="proc-form-error" role="alert">{deleteError}</p> : null}
            <footer>
              <button className="proc-button secondary" type="button" onClick={() => setDeleteTarget(null)} disabled={deleteBusy}>取消</button>
              <button className="proc-button danger" type="button" onClick={() => void deleteRequest()} disabled={deleteBusy}>
                {deleteBusy ? <LoaderCircle className="spin" size={15} /> : <Trash2 size={15} />}删除任务
              </button>
            </footer>
          </section>
        </div>
      ) : null}

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
                    <div className="proc-config-section-title"><strong>模型服务</strong><span>选择离线演示或 OpenAI 兼容接口</span></div>
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
