import {
  Activity,
  AlertTriangle,
  Archive,
  Bot,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CheckCircle2,
  FileCheck2,
  Files,
  LoaderCircle,
  Moon,
  Plus,
  Scale,
  Search,
  Settings,
  Sun,
  Trash2,
  Wifi,
  WifiOff,
  ChevronUp,
} from "lucide-react";
import { lazy, Suspense, useCallback, useEffect, useState, type ReactNode } from "react";

import { AuditView } from "./AuditView";
import { AiTaskRecovery } from "./AiTaskRecovery";
import { AgentOfflineNotice } from "./AgentOfflineNotice";
import { ComparisonView } from "./ComparisonView";
import { ConfigDrawer } from "./ConfigDrawer";
import { DeleteDialog } from "./DeleteDialog";
import { HumanInteractionPanel } from "./HumanInteractionPanel";
import { NextStepBar } from "./NextStepBar";
import {
  NewProcurementConversation,
  ProcurementConversation,
} from "./ProcurementConversation";
import { QuoteWorkspace } from "./QuoteWorkspace";
import { ReportView } from "./ReportView";
import { RequirementReview } from "./RequirementReview";
import { readRole, ROLE_LABELS, type DemoRole, visibleViewOrDefault, writeRole } from "./roles";
import type { ProcurementRequest } from "./types";
import { useEscape } from "./useEscape";
import { errorText, useWorkbenchActions } from "./useWorkbenchActions";
import { useRequestQueries } from "./useRequestQueries";
import { useWorkbenchState } from "./useWorkbenchState";
import {
  FULFILLMENT_STEPS,
  PROCUREMENT_DECISION_STEPS,
  actionablePendingReviewCount,
  fulfillmentProgress,
  procurementDecisionProgress,
  statusLabel,
  statusLabelFor,
  statusTone,
} from "./viewModel";
import { WorkbenchHome } from "./WorkbenchHome";
import { WorkbenchNavigation } from "./WorkbenchNavigation";

type Props = {
  theme: "light" | "dark";
  backendVersion: string;
  /** LIVE-1：/api/health 报 Agent 不可用（心跳过期/agent_available=false）。 */
  agentDown?: boolean;
  onToggleTheme: () => void;
};

// Business centers are independent routes.  Deferring them keeps the first
// workbench paint focused on the task dashboard and current task canvas.
const AiTaskCenter = lazy(() => import("./AiTaskCenter").then(({ AiTaskCenter: Component }) => ({ default: Component })));
const AuditLogCenter = lazy(() => import("./AuditLogCenter").then(({ AuditLogCenter: Component }) => ({ default: Component })));
const ContractCenter = lazy(() => import("./ContractCenter").then(({ ContractCenter: Component }) => ({ default: Component })));
const InvoiceCenter = lazy(() => import("./InvoiceCenter").then(({ InvoiceCenter: Component }) => ({ default: Component })));
const OrderCenter = lazy(() => import("./OrderCenter").then(({ OrderCenter: Component }) => ({ default: Component })));
const ReportsCenter = lazy(() => import("./ReportsCenter").then(({ ReportsCenter: Component }) => ({ default: Component })));
const ReviewCenter = lazy(() => import("./ReviewCenter").then(({ ReviewCenter: Component }) => ({ default: Component })));
const SupplierCenter = lazy(() => import("./SupplierCenter").then(({ SupplierCenter: Component }) => ({ default: Component })));
const SystemInfo = lazy(() => import("./SystemInfo").then(({ SystemInfo: Component }) => ({ default: Component })));

function DeferredCenter({ children }: { children: ReactNode }) {
  return <Suspense fallback={<div className="proc-loading-state">正在加载业务中心…</div>}>{children}</Suspense>;
}

function requestDate(value: string) {
  return new Date(value).toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
}

function quantityUnitText(value: number | string | null, unit: string | null) {
  if (value == null || String(value).trim() === "" || !unit?.trim()) return "待补充";
  const quantity = typeof value === "number" ? value.toLocaleString("zh-CN") : String(value);
  return `${quantity} ${unit}`;
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

/** 工作台布局壳 + 视图分发（P1-5 拆分后仅保留组合职责）。 */
export function ProcurementWorkbench({ theme, backendVersion, agentDown = false, onToggleTheme }: Props) {
  const state = useWorkbenchState();
  const queries = useRequestQueries(state);
  const actions = useWorkbenchActions(state, queries);
  const [role, setRole] = useState<DemoRole>(() => readRole());
  const [taskListCollapsed, setTaskListCollapsed] = useState(false);
  const {
    view, selectedId, selectedAiId, selectedReviewId, activeTab, taskFilter, taskPage,
    search, showCreate, orderTask, invoiceOrder, actionError,
    setTaskFilter, setTaskPage, setSearch, setActiveTab, openView, openTask, openTaskFilter,
    openCreate, selectAiTask, selectReview, navigate,
  } = state;
  const {
    metaQuery, configQuery, requestsQuery, allAiTasksQuery, reviewsQuery, detailQuery, interactionsQuery,
    aiTaskQuery, reportQuery, taskOrderQuery, taskContractQuery, requests, allAiTasks, reviews,
    filtered, visibleRequests, totalTaskPages,
  } = queries;
  const {
    busy, actionErrorSource, generateContract, aiActionBusy, aiActionError, showConfig, configForm, configBusy, configError,
    configNotice, deleteTarget, deleteBusy, deleteError, conversationOpen,
    setConversationOpen, setShowConfig, setDeleteTarget, openDelete, deleteRequest,
    openConfig, updateConfigField, saveConfig, startConversation, uploadQuotes, correctField,
    correctRequirement, analyze, retryAiTask, cancelAiTask, resume, approve, noAward, reopen,
    handleNextStep,
  } = actions;

  useEscape(!!deleteTarget, () => setDeleteTarget(null), deleteBusy);
  useEscape(showConfig, () => setShowConfig(false), configBusy);
  useEffect(() => {
    const allowedView = visibleViewOrDefault(role, view);
    if (allowedView !== view) openView(allowedView);
  }, [openView, role, view]);

  const detail = detailQuery.data || null;
  const taskOrder = taskOrderQuery.data ?? null;
  const taskContract = taskContractQuery.data ?? null;
  const latestInteraction = interactionsQuery.data?.[0] ?? null;
  const structuredInteractionActive = interactionsQuery.data?.some((item) =>
    item.status === "WAITING" || item.status === "ANSWERED"
  ) ?? false;
  const fulfillmentStage = detail?.status === "approved";
  const progressSteps = fulfillmentStage ? FULFILLMENT_STEPS : PROCUREMENT_DECISION_STEPS;
  const progressCurrent = detail
    ? fulfillmentStage ? fulfillmentProgress(taskOrder) : procurementDecisionProgress(detail.status)
    : 0;
  useEffect(() => {
    if (detail?.id) setConversationOpen(true);
  }, [detail?.id, setConversationOpen]);

  // P-UX⑧：订单卡 → 发票中心跨中心直达（稳定引用，保住 OrderCard 的 memo）。
  const openInvoiceForOrder = useCallback((order: { id: string }) => {
    navigate({ view: "invoices", task: null, ai: null, review: null, orderTask: null, invoiceOrder: order.id });
  }, [navigate]);

  return (
    <div className="proc-app">
      <header className="proc-topbar h-14 px-5 flex items-center justify-between gap-4 border-b border-border bg-surface/85 backdrop-blur-md sticky top-0 z-30 shadow-xs">
        <div className="proc-brand flex items-center gap-3 min-w-0">
          <span className="w-8 h-8 rounded-lg flex items-center justify-center bg-gradient-to-br from-accent to-teal-600 text-white shadow-xs flex-shrink-0"><Scale size={18} /></span>
          <div className="flex flex-col min-w-0">
            <strong className="text-sm font-bold text-text leading-tight tracking-tight">采价台</strong>
            <small className="text-[11px] text-text-muted leading-tight truncate max-w-xs">采购询价与供应商比价</small>
          </div>
        </div>
        <div className="proc-topbar-meta flex items-center gap-2.5">
          <label className="proc-role-selector inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-surface-subtle border border-border hover:border-border-strong text-xs font-medium text-text-secondary transition-all cursor-pointer" title="演示角色切换（K9，纯前端视角控制）">
            <span className="text-text-muted text-[11px]">角色</span>
            <select
              aria-label="演示角色"
              className="bg-transparent border-0 text-text font-medium text-xs focus:ring-0 cursor-pointer p-0"
              value={role}
              onChange={(event) => {
                const next = event.target.value as DemoRole;
                setRole(next);
                writeRole(next);
                const nextView = visibleViewOrDefault(next, view);
                if (nextView !== view) openView(nextView);
              }}
            >
              {(Object.keys(ROLE_LABELS) as DemoRole[]).map((value) => (
                <option key={value} value={value}>{ROLE_LABELS[value]}</option>
              ))}
            </select>
          </label>
          <span className="proc-runtime-state inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-accent-soft text-accent border border-accent/20"><Wifi size={14} />采购服务 {backendVersion}</span>
          {queries.streamStatus !== "live" ? (
            <span
              className="proc-stream-state inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-warning-soft text-warning border border-warning/30"
              role="status"
              aria-label="实时事件流状态"
            >
              <WifiOff size={14} />
              {queries.streamStatus === "error" ? "实时连接已断开" : "实时连接中…"}
              {queries.streamStatus === "error" ? (
                <button
                  type="button"
                  className="underline underline-offset-2 font-semibold hover:text-warning"
                  onClick={queries.reconnectStream}
                >
                  立即重连
                </button>
              ) : null}
            </span>
          ) : null}
          <button className="proc-icon-button w-8 h-8 rounded-lg border border-border bg-surface hover:bg-surface-subtle hover:border-border-strong text-text-secondary hover:text-text flex items-center justify-center transition-all" type="button" title="API / 模型配置" aria-label="API / 模型配置" onClick={() => openConfig(configQuery.data)}>
            <Settings size={16} />
          </button>
          <button className="proc-icon-button w-8 h-8 rounded-lg border border-border bg-surface hover:bg-surface-subtle hover:border-border-strong text-text-secondary hover:text-text flex items-center justify-center transition-all" type="button" title="切换主题" aria-label="切换主题" onClick={onToggleTheme}>
            {theme === "light" ? <Moon size={16} /> : <Sun size={16} />}
          </button>
        </div>
      </header>

      <main className={`proc-layout ${view === "tasks" && !taskListCollapsed ? "with-tasks" : ""}`}>
        <aside className="proc-rail">
          <WorkbenchNavigation
            active={view}
            role={role}
            aiAttention={allAiTasks.filter((task) => task.status === "FAILED" || task.stale).length}
            reviewAttention={actionablePendingReviewCount(reviews, requests)}
            onChange={openView}
          />
          <div className="proc-rail-status flex items-center gap-1.5 text-xs font-semibold" title={agentDown ? "Java 采购服务在线，但 Agent 心跳已过期（分析类任务可能停滞）" : undefined}>
            <Wifi size={14} />
            <span className={agentDown ? "inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-warning-soft text-warning border border-warning/30" : undefined}>
              {agentDown ? "服务在线 · Agent 离线" : "服务在线"}
            </span>
            <small>{backendVersion}</small>
          </div>
        </aside>

        {view === "tasks" && !taskListCollapsed ? <aside className="proc-sidebar">
          <div className="proc-sidebar-head">
            <span>采购任务</span>
            <div style={{ display: "flex", gap: "6px", alignItems: "center" }}>
              {selectedId ? (
                <button
                  className="proc-icon-button compact"
                  type="button"
                  title="收起任务列表"
                  aria-label="收起任务列表"
                  onClick={() => setTaskListCollapsed(true)}
                >
                  <ChevronLeft size={16} />
                </button>
              ) : null}
              <button className="proc-icon-button primary-icon compact" type="button" title="新建采购对话" aria-label="新建采购对话" onClick={openCreate}>
                <Plus size={16} />
              </button>
            </div>
          </div>
          <div className="proc-task-filters" aria-label="采购任务状态筛选">
            {(["all", "attention", "active", "completed"] as const).map((filter) => (
              <button key={filter} type="button" className={taskFilter === filter ? "active" : ""} onClick={() => { setTaskFilter(filter); setTaskPage(0); }}>
                {{ all: "全部", attention: "待办", active: "进行中", completed: "已结束" }[filter]}
              </button>
            ))}
          </div>
          <label className="proc-search">
            <Search size={15} />
            <input aria-label="搜索采购任务" value={search} onChange={(event) => { setSearch(event.target.value); setTaskPage(0); }} placeholder="编号、任务或物料" />
          </label>
          <div className="proc-request-list">
            {requestsQuery.isPending ? (
              <div className="proc-sidebar-empty"><span>正在加载采购任务…</span></div>
            ) : null}
            {requestsQuery.isError ? (
              <div className="proc-sidebar-empty error" role="alert"><span>采购任务加载失败</span></div>
            ) : null}
            {visibleRequests.map((request) => (
              <div
                className={`proc-request-item ${selectedId === request.id ? "selected" : ""}`}
                key={request.id}
              >
                <button
                  type="button"
                  className="proc-request-item-main"
                  onClick={() => openTask(request.id)}
                >
                  <span className="proc-request-row"><code>{request.reference}</code><small>{requestDate(request.updated_at)}</small></span>
                  <strong>{request.title}</strong>
                  <span className="proc-request-row"><small>{request.quote_count} 家报价 · {quantityUnitText(request.quantity, request.unit)}</small><i className={statusTone(request.status)}>{statusLabel(request.status)}</i></span>
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
            ))}
            {!requestsQuery.isPending && !requestsQuery.isError && !filtered.length ? (
              <div className="proc-sidebar-empty"><Archive size={22} /><span>{search ? "没有匹配任务" : "还没有采购任务"}</span></div>
            ) : null}
          </div>
          {filtered.length ? (
            <footer className="proc-task-pagination">
              <button type="button" title="上一页" aria-label="上一页" disabled={taskPage === 0} onClick={() => setTaskPage((page) => Math.max(0, page - 1))}><ChevronLeft size={15} /></button>
              <span>{taskPage + 1} / {totalTaskPages}</span>
              <button type="button" title="下一页" aria-label="下一页" disabled={taskPage + 1 >= totalTaskPages} onClick={() => setTaskPage((page) => Math.min(totalTaskPages - 1, page + 1))}><ChevronRight size={15} /></button>
            </footer>
          ) : null}
        </aside> : null}

        <section className={`proc-main ${view !== "tasks" ? "proc-scroll-page" : ""}`}>
          {view === "workbench" ? (
            <WorkbenchHome
              role={role}
              requests={requests}
              aiTasks={allAiTasks}
              reviews={reviews}
              loading={requestsQuery.isPending || allAiTasksQuery.isPending || reviewsQuery.isPending}
              agentDown={agentDown}
              onOpenTask={openTask}
              onOpenTasks={openTaskFilter}
              onOpenCreate={openCreate}
              onOpenView={openView}
              onOpenOrders={() => openView("orders")}
            />
          ) : view === "ai" ? (
            <DeferredCenter><AiTaskCenter
              requests={requests}
              tasks={allAiTasks}
              loading={allAiTasksQuery.isPending}
              error={allAiTasksQuery.isError ? errorText(allAiTasksQuery.error) : null}
              selectedId={selectedAiId}
              onSelect={selectAiTask}
              onOpenTask={openTask}
            /></DeferredCenter>
          ) : view === "reviews" ? (
            <DeferredCenter><ReviewCenter
              requests={requests}
              reviews={reviews}
              loading={reviewsQuery.isPending}
              error={reviewsQuery.isError ? errorText(reviewsQuery.error) : null}
              selectedId={selectedReviewId}
              onSelect={selectReview}
              onOpenTask={openTask}
            /></DeferredCenter>
          ) : view === "suppliers" ? (
            <DeferredCenter><SupplierCenter onOpenTask={openTask} /></DeferredCenter>
          ) : view === "orders" ? (
            <DeferredCenter><OrderCenter highlightTaskId={orderTask} onBackToTask={(taskId) => openTask(taskId)} onOpenInvoice={openInvoiceForOrder} /></DeferredCenter>
          ) : view === "invoices" ? (
            <DeferredCenter><InvoiceCenter focusOrderId={invoiceOrder} /></DeferredCenter>
          ) : view === "contracts" ? (
            <DeferredCenter><ContractCenter /></DeferredCenter>
          ) : view === "reports" ? (
            <DeferredCenter><ReportsCenter /></DeferredCenter>
          ) : view === "audit" ? (
            <DeferredCenter><AuditLogCenter /></DeferredCenter>
          ) : view === "system" ? (
            <DeferredCenter><SystemInfo /></DeferredCenter>
          ) : busy === "conversation" ? (
            <section className="proc-empty-state first" role="status" aria-live="polite">
              <LoaderCircle className="spin text-accent" size={30} />
              <h1>正在创建采购任务</h1>
              <p>报价已受理，正在持久化文件并安排后台并发解析。</p>
            </section>
          ) : showCreate || (!selectedId && !detail) ? (
            <div className="flex-1 w-full min-h-full flex items-center justify-center p-4 sm:p-8 bg-surface-subtle/30 overflow-auto">
              <NewProcurementConversation
                busy={busy === "conversation"}
                error={actionErrorSource === "conversation" ? actionError : null}
                maxFileBytes={metaQuery.data?.max_file_bytes ?? 5 * 1024 * 1024}
                maxTotalBytes={metaQuery.data?.max_conversation_upload_bytes ?? 20 * 1024 * 1024}
                maxQuotes={metaQuery.data?.max_quotes_per_request ?? 50}
                onStart={startConversation}
              />
            </div>
          ) : detail ? (
            <>
              <header className="proc-request-head flex flex-col bg-surface border-b border-border">
                <div className="proc-title-line flex flex-wrap items-center justify-between gap-3">
                  <div className="flex items-center gap-3 min-w-0 flex-1">
                    {taskListCollapsed ? (
                      <button
                        className="proc-icon-button compact w-8 h-8 rounded-lg border border-border flex items-center justify-center text-text-muted hover:text-text"
                        type="button"
                        title="展开任务列表"
                        aria-label="展开任务列表"
                        onClick={() => setTaskListCollapsed(false)}
                      >
                        <ChevronRight size={16} />
                      </button>
                    ) : null}
                    <div className="flex flex-col gap-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <code className="font-mono text-xs font-bold text-accent bg-accent-soft px-2 py-0.5 rounded border border-accent/20">
                          {detail.reference}
                        </code>
                      </div>
                      <h1 className="text-lg font-bold text-text tracking-tight truncate">{detail.title}</h1>
                    </div>
                  </div>
                  <span className={`proc-status ${statusTone(detail.status)} text-xs font-semibold px-3 py-1 rounded-full border inline-flex items-center gap-1.5 flex-shrink-0`}>
                    <i className="w-2 h-2 rounded-full bg-current" />{statusLabelFor(detail)}
                  </span>
                </div>

                <div className="proc-request-facts flex flex-wrap items-center gap-2 text-xs">
                  <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-surface-subtle border border-border/60 text-text">
                    <small className="text-text-muted">物料</small>
                    <strong className="font-semibold">{detail.item_name}</strong>
                  </span>
                  <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-surface-subtle border border-border/60 text-text">
                    <small className="text-text-muted">采购量</small>
                    <strong className="font-semibold font-mono">{quantityUnitText(detail.quantity, detail.unit)}</strong>
                  </span>
                  <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-surface-subtle border border-border/60 text-text">
                    <small className="text-text-muted">规格</small>
                    <strong className="font-semibold">{specificationText(detail.specifications)}</strong>
                  </span>
                  <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-surface-subtle border border-border/60 text-text">
                    <small className="text-text-muted">最长交期</small>
                    <strong className="font-semibold font-mono">{String(detail.constraints.max_lead_days ?? "-")} 天</strong>
                  </span>
                </div>

                <div className="flex flex-col gap-1.5 pt-2 border-t border-border/40">
                  <div className="proc-progress-context text-[11px] font-medium text-text-muted">
                    <span>{fulfillmentStage ? "采购方案已确认 · 当前履约阶段" : "当前采购决策阶段"}</span>
                  </div>
                  <ol className={`proc-progress ${fulfillmentStage ? "fulfillment" : "decision"} flex items-center gap-2 overflow-x-auto w-full list-none p-0 m-0`} aria-label={fulfillmentStage ? "履约进度" : "采购决策进度"}>
                    {progressSteps.map((step, index) => {
                      const position = index + 1;
                      const done = position < progressCurrent || (fulfillmentStage && position === 6 && taskOrder?.settlement?.status === "PAID");
                      const current = position === progressCurrent && !done;
                      return (
                        <li key={step} className={`flex items-center gap-2 text-xs font-medium whitespace-nowrap ${done ? "done text-accent font-semibold" : current ? "current text-info font-bold" : "text-text-muted"}`} aria-current={current ? "step" : undefined}>
                          <span className={`w-5 h-5 rounded-full flex items-center justify-center text-[11px] font-mono font-bold border ${done ? "bg-accent-soft text-accent border-accent/40" : current ? "bg-info-soft text-info border-info ring-2 ring-info/20" : "bg-surface-subtle text-text-muted border-border"}`}>
                            {done ? <CheckCircle2 size={13} /> : position}
                          </span>
                          <strong>{step}</strong>
                          {index < progressSteps.length - 1 ? <span className="text-border text-xs px-1">→</span> : null}
                        </li>
                      );
                    })}
                  </ol>
                </div>
              </header>

              {/* LIVE-1：任务详情内的 Agent 离线降级提示（可关闭）。 */}
              {agentDown ? <AgentOfflineNotice /> : null}
              {latestInteraction ? (
                <HumanInteractionPanel interaction={latestInteraction} />
              ) : detail.status === "waiting_human" && interactionsQuery.isError ? (
                <section className="proc-interaction-load-error" role="alert">
                  <AlertTriangle size={18} />
                  <div><strong>待回答问题加载失败</strong><p>{errorText(interactionsQuery.error)}</p></div>
                  <button className="proc-button secondary" type="button" onClick={() => void interactionsQuery.refetch()}>重新加载</button>
                </section>
              ) : null}

              <NextStepBar
                request={detail}
                order={taskOrder ?? null}
                contract={taskContract ?? null}
                busy={busy}
                error={actionErrorSource === "contract" ? actionError : null}
                onAction={handleNextStep}
                onOpenContract={() => openView("contracts")}
                onGenerateContract={generateContract}
              />

              {aiTaskQuery.data ? (
                <AiTaskRecovery
                  task={aiTaskQuery.data}
                  busy={aiActionBusy}
                  error={aiActionError}
                  onRetry={retryAiTask}
                  onCancel={cancelAiTask}
                  onSupplement={() => setActiveTab("quotes")}
                />
              ) : null}

              <div className={`proc-task-body ${conversationOpen ? "conv-open" : "conv-collapsed"}`}>
                <div className={`proc-conversation-shell ${conversationOpen ? "open" : "collapsed"}`}>
                  <button
                    type="button"
                    className={`proc-conversation-toggle ${conversationOpen ? "open" : ""}`}
                    aria-expanded={conversationOpen}
                    aria-controls="proc-conversation-panel"
                    onClick={() => setConversationOpen((open) => !open)}
                  >
                    <Bot size={15} />
                    <strong>Agent 会话</strong>
                    <span className="proc-conversation-toggle-status"><i className={statusTone(detail.status)} />{statusLabelFor(detail)}</span>
                    {conversationOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                  </button>
                  {conversationOpen ? (
                    <div id="proc-conversation-panel">
                      <ProcurementConversation
                        request={detail}
                        structuredInteractionActive={structuredInteractionActive}
                        actionError={actionErrorSource === "conversation" ? actionError : null}
                        onResume={resume}
                        onRecover={analyze}
                        onOpenComparison={() => setActiveTab("compare")}
                      />
                    </div>
                  ) : null}
                </div>
                <section className="proc-structured-workspace">
                  <nav className="proc-tabs" aria-label="采购任务视图">
                    <button className={activeTab === "quotes" ? "active" : ""} type="button" onClick={() => navigate({ tab: "quotes" })}><Files size={16} />报价与复核{detail.unresolved_field_count ? <span>{detail.unresolved_field_count}</span> : null}</button>
                    <button className={activeTab === "compare" ? "active" : ""} type="button" onClick={() => navigate({ tab: "compare" })}><Scale size={16} />供应商比价</button>
                    <button className={activeTab === "report" ? "active" : ""} type="button" onClick={() => navigate({ tab: "report" })}><FileCheck2 size={16} />审批报告</button>
                    <button className={activeTab === "audit" ? "active" : ""} type="button" onClick={() => navigate({ tab: "audit" })}><Activity size={16} />运行审计</button>
                  </nav>

                  <div className="proc-tab-content">
                    {activeTab === "quotes" && metaQuery.data ? (
                      <>
                        <RequirementReview request={detail} busy={busy === "requirement"} error={actionErrorSource === "requirement" ? actionError : null} onSave={correctRequirement} />
                        <QuoteWorkspace request={detail} meta={metaQuery.data} busy={busy} error={actionErrorSource === "workspace" ? actionError : null} onUpload={uploadQuotes} onCorrect={correctField} onAnalyze={analyze} />
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
                        error={actionErrorSource === "decision" ? actionError : null}
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
        <DeleteDialog
          target={deleteTarget}
          busy={deleteBusy}
          error={deleteError}
          onClose={() => setDeleteTarget(null)}
          onConfirm={deleteRequest}
        />
      ) : null}

      {showConfig ? (
        <ConfigDrawer
          query={configQuery}
          form={configForm}
          busy={configBusy}
          error={configError}
          notice={configNotice}
          onClose={() => setShowConfig(false)}
          onFieldChange={updateConfigField}
          onSave={saveConfig}
        />
      ) : null}
    </div>
  );
}
