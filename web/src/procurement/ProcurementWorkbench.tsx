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
  Moon,
  Plus,
  Scale,
  Search,
  Settings,
  Sun,
  Trash2,
  Wifi,
  ChevronUp,
} from "lucide-react";
import { useState } from "react";

import { AuditLogCenter } from "./AuditLogCenter";
import { AuditView } from "./AuditView";
import { AiTaskCenter } from "./AiTaskCenter";
import { AiTaskRecovery } from "./AiTaskRecovery";
import { ComparisonView } from "./ComparisonView";
import { ConfigDrawer } from "./ConfigDrawer";
import { ContractCenter } from "./ContractCenter";
import { DeleteDialog } from "./DeleteDialog";
import { InvoiceCenter } from "./InvoiceCenter";
import { NextStepBar } from "./NextStepBar";
import { OrderCenter } from "./OrderCenter";
import {
  NewProcurementConversation,
  ProcurementConversation,
} from "./ProcurementConversation";
import { QuoteWorkspace } from "./QuoteWorkspace";
import { ReportsCenter } from "./ReportsCenter";
import { ReportView } from "./ReportView";
import { RequirementReview } from "./RequirementReview";
import { ReviewCenter } from "./ReviewCenter";
import { readRole, ROLE_LABELS, type DemoRole, visibleViewOrDefault, writeRole } from "./roles";
import { SupplierCenter } from "./SupplierCenter";
import { SystemInfo } from "./SystemInfo";
import type { ProcurementRequest } from "./types";
import { useEscape } from "./useEscape";
import { errorText, useWorkbenchActions } from "./useWorkbenchActions";
import { useRequestQueries } from "./useRequestQueries";
import { useWorkbenchState } from "./useWorkbenchState";
import {
  CLOSED_LOOP_STEPS,
  closedLoopProgress,
  statusLabel,
  statusTone,
} from "./viewModel";
import { WorkbenchHome } from "./WorkbenchHome";
import { WorkbenchNavigation } from "./WorkbenchNavigation";

type Props = {
  theme: "light" | "dark";
  backendVersion: string;
  onToggleTheme: () => void;
};

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

/** 工作台布局壳 + 视图分发（P1-5 拆分后仅保留组合职责）。 */
export function ProcurementWorkbench({ theme, backendVersion, onToggleTheme }: Props) {
  const state = useWorkbenchState();
  const queries = useRequestQueries(state);
  const actions = useWorkbenchActions(state, queries);
  const [role, setRole] = useState<DemoRole>(() => readRole());
  const {
    view, selectedId, selectedAiId, selectedReviewId, activeTab, taskFilter, taskPage,
    search, showCreate, orderTask, actionError,
    setTaskFilter, setTaskPage, setSearch, setActiveTab, openView, openTask, openTaskFilter,
    openCreate, selectAiTask, selectReview, navigate,
  } = state;
  const {
    metaQuery, configQuery, requestsQuery, allAiTasksQuery, reviewsQuery, detailQuery,
    aiTaskQuery, reportQuery, taskOrderQuery, taskContractQuery, requests, allAiTasks, reviews,
    filtered, visibleRequests, totalTaskPages,
  } = queries;
  const {
    busy, aiActionBusy, aiActionError, showConfig, configForm, configBusy, configError,
    configNotice, deleteTarget, deleteBusy, deleteError, conversationOpen,
    setConversationOpen, setShowConfig, setDeleteTarget, openDelete, deleteRequest,
    openConfig, updateConfigField, saveConfig, startConversation, uploadQuotes, correctField,
    correctRequirement, analyze, retryAiTask, cancelAiTask, resume, approve, noAward, reopen,
    handleNextStep,
  } = actions;

  useEscape(!!deleteTarget, () => setDeleteTarget(null), deleteBusy);
  useEscape(showConfig, () => setShowConfig(false), configBusy);

  const detail = detailQuery.data || null;
  const taskOrder = taskOrderQuery.data ?? null;
  const taskContract = taskContractQuery.data ?? null;
  const progressDone = detail ? closedLoopProgress(detail.status, taskOrder) : 0;

  return (
    <div className="proc-app">
      <header className="proc-topbar">
        <div className="proc-brand">
          <span><Scale size={20} /></span>
          <div><strong>采价台</strong><small>采购询价与供应商比价</small></div>
        </div>
        <div className="proc-topbar-meta">
          <label className="proc-role-selector" title="演示角色切换（K9，纯前端视角控制）">
            <span>角色</span>
            <select
              aria-label="演示角色"
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
          <span className="proc-runtime-state"><Wifi size={14} />采购服务 {backendVersion}</span>
          <button className="proc-icon-button" type="button" title="API / 模型配置" aria-label="API / 模型配置" onClick={() => openConfig(configQuery.data)}>
            <Settings size={16} />
          </button>
          <button className="proc-icon-button" type="button" title="切换主题" aria-label="切换主题" onClick={onToggleTheme}>
            {theme === "light" ? <Moon size={16} /> : <Sun size={16} />}
          </button>
        </div>
      </header>

      <main className={`proc-layout ${view === "tasks" ? "with-tasks" : ""}`}>
        <aside className="proc-rail">
          <WorkbenchNavigation
            active={view}
            role={role}
            aiAttention={allAiTasks.filter((task) => task.status === "FAILED" || task.stale).length}
            reviewAttention={reviews.filter((review) => review.status === "PENDING").length}
            onChange={openView}
          />
          <div className="proc-rail-status">
            <Wifi size={14} /><span>服务在线</span><small>{backendVersion}</small>
          </div>
        </aside>

        {view === "tasks" ? <aside className="proc-sidebar">
          <div className="proc-sidebar-head">
            <span>采购任务</span>
            <button className="proc-icon-button primary-icon" type="button" title="新建采购对话" aria-label="新建采购对话" onClick={openCreate}>
              <Plus size={17} />
            </button>
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
                  <span className="proc-request-row"><small>{request.quote_count} 家报价 · {quantityText(request.quantity)} {request.unit}</small><i className={statusTone(request.status)}>{statusLabel(request.status)}</i></span>
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

        <section className="proc-main">
          {view === "workbench" ? (
            <WorkbenchHome
              role={role}
              requests={requests}
              aiTasks={allAiTasks}
              reviews={reviews}
              loading={requestsQuery.isPending || allAiTasksQuery.isPending || reviewsQuery.isPending}
              onCreate={openCreate}
              onOpenTask={openTask}
              onOpenTasks={openTaskFilter}
              onOpenView={openView}
              onOpenOrders={() => openView("orders")}
              onOpenSuppliers={() => openView("suppliers")}
              onOpenReports={() => openView("reports")}
            />
          ) : view === "ai" ? (
            <AiTaskCenter
              requests={requests}
              tasks={allAiTasks}
              loading={allAiTasksQuery.isPending}
              error={allAiTasksQuery.isError ? errorText(allAiTasksQuery.error) : null}
              selectedId={selectedAiId}
              onSelect={selectAiTask}
              onOpenTask={openTask}
            />
          ) : view === "reviews" ? (
            <ReviewCenter
              requests={requests}
              reviews={reviews}
              loading={reviewsQuery.isPending}
              error={reviewsQuery.isError ? errorText(reviewsQuery.error) : null}
              selectedId={selectedReviewId}
              onSelect={selectReview}
              onOpenTask={openTask}
            />
          ) : view === "suppliers" ? (
            <SupplierCenter onOpenTask={openTask} />
          ) : view === "orders" ? (
            <OrderCenter highlightTaskId={orderTask} onBackToTask={(taskId) => openTask(taskId)} />
          ) : view === "invoices" ? (
            <InvoiceCenter />
          ) : view === "contracts" ? (
            <ContractCenter />
          ) : view === "reports" ? (
            <ReportsCenter />
          ) : view === "audit" ? (
            <AuditLogCenter />
          ) : view === "system" ? (
            <SystemInfo />
          ) : showCreate || (!selectedId && !detail) ? (
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
                  <span className={`proc-status ${statusTone(detail.status)}`}><i />{statusLabel(detail.status)}</span>
                </div>
                <div className="proc-request-facts">
                  <span><small>物料</small><strong>{detail.item_name}</strong></span>
                  <span><small>采购量</small><strong>{quantityText(detail.quantity)} {detail.unit}</strong></span>
                  <span><small>规格</small><strong>{specificationText(detail.specifications)}</strong></span>
                  <span><small>最长交期</small><strong>{String(detail.constraints.max_lead_days ?? "-")} 天</strong></span>
                </div>
                <ol className="proc-progress" aria-label="采购进度">
                  {CLOSED_LOOP_STEPS.map((step, index) => {
                    const done = index + 1 <= progressDone;
                    return (
                      <li key={step} className={done ? "done" : ""}>
                        <span>{done ? <CheckCircle2 size={14} /> : index + 1}</span>
                        <strong>{step}</strong>
                      </li>
                    );
                  })}
                </ol>
              </header>

              <NextStepBar request={detail} order={taskOrder ?? null} onAction={handleNextStep} />

              {detail.status === "approved" ? (
                <div className="proc-contract-entry">
                  {taskContract ? (
                    <span><FileCheck2 size={14} />合同 {taskContract.contract_no} · {taskContract.status === "EFFECTIVE" ? "已生效" : taskContract.status === "EXECUTING" ? "执行中" : taskContract.status === "CLOSED" ? "已关闭" : taskContract.status === "PENDING_APPROVAL" ? "待审批" : taskContract.status === "CHANGE_REQUEST" ? "变更审批" : "草拟中"}</span>
                  ) : (
                    <span><FileCheck2 size={14} />定标完成，可生成合同（金额/交期/供应商自动注入）</span>
                  )}
                  <button
                    type="button"
                    className="proc-button compact"
                    onClick={() => openView("contracts")}
                  >
                    {taskContract ? "合同中心 →" : "生成合同（AI 草拟）"}
                  </button>
                </div>
              ) : null}

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

              <div className="proc-task-body">
                <div className="proc-conversation-shell">
                  <button
                    type="button"
                    className={`proc-conversation-toggle ${conversationOpen ? "open" : ""}`}
                    aria-expanded={conversationOpen}
                    aria-controls="proc-conversation-panel"
                    onClick={() => setConversationOpen((open) => !open)}
                  >
                    <Bot size={15} />
                    <strong>采购 Agent 对话</strong>
                    <span className="proc-conversation-toggle-status"><i className={statusTone(detail.status)} />{statusLabel(detail.status)}</span>
                    {conversationOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                  </button>
                  {conversationOpen ? (
                    <div id="proc-conversation-panel">
                      <ProcurementConversation
                        request={detail}
                        actionError={actionError}
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
