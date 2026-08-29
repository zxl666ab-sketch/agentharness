import { useState } from "react";

import { procurementApi } from "./api";
import type {
  ProcurementModelConfig,
  ProcurementModelConfigUpdate,
  ProcurementRequestSummary,
} from "./types";
import type { RequestQueries } from "./useRequestQueries";
import type { WorkbenchState } from "./useWorkbenchState";
import type { NextStepAction } from "./viewModel";

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

export function configFormFrom(config: ProcurementModelConfig): ProcurementModelConfigUpdate {
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

export function errorText(error: unknown) {
  return error instanceof Error ? error.message : String(error || "操作失败");
}

/** 错误来源面板：同一条 actionError 只渲染在触发它的面板，避免一屏重复两条（P-UX③）。 */
export type ActionErrorSource = "conversation" | "workspace" | "requirement" | "decision" | "contract";

/**
 * P1-5 拆分 3/5：13 个动作 handler + busy/错误状态 + 删除/配置弹窗状态。
 * 职责：对话创建、报价上传/字段修正/需求修正、分析、AI 任务重试/取消、
 * 恢复、审批/流标/重开、删除任务、保存模型配置，以及「下一步」跳转。
 */
export function useWorkbenchActions(state: WorkbenchState, queries: RequestQueries) {
  const { queryClient, detailQuery, latestAiTaskId, commit } = queries;
  const { selectedId } = state;

  const [busy, setBusy] = useState<string | null>(null);
  const [actionErrorSource, setActionErrorSource] = useState<ActionErrorSource | null>(null);

  /** 错误只回显到触发它的面板；清空时一并清来源。 */
  function fail(source: ActionErrorSource, error: unknown) {
    setActionErrorSource(source);
    state.setActionError(errorText(error));
  }
  function clearError() {
    setActionErrorSource(null);
    state.setActionError(null);
  }
  const [aiActionBusy, setAiActionBusy] = useState<"retry" | "cancel" | null>(null);
  const [aiActionError, setAiActionError] = useState<string | null>(null);
  const [showConfig, setShowConfig] = useState(false);
  const [configForm, setConfigForm] = useState<ProcurementModelConfigUpdate>(DEFAULT_CONFIG_FORM);
  const [configBusy, setConfigBusy] = useState(false);
  const [configError, setConfigError] = useState<string | null>(null);
  const [configNotice, setConfigNotice] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ProcurementRequestSummary | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [conversationOpen, setConversationOpen] = useState(true);

  async function startConversation(message: string, files: File[]) {
    setBusy("conversation");
    clearError();
    // Optimistic navigation happens before the multipart request completes.
    // The accepting shell gives immediate feedback while Java durably stores
    // the files and returns the real task id in its 202 response.
    state.navigate({
      view: "tasks",
      task: null,
      ai: null,
      review: null,
      tab: "quotes",
      orderTask: null,
    }, false);
    try {
      const accepted = await procurementApi.startConversation(message, files);
      state.setPendingRunId(accepted.run_id);
      state.setPendingOperation({
        operationId: accepted.operation_id,
        taskId: accepted.purchase_request_id,
      });
      state.setShowCreate(false);
      state.navigate({
        view: "tasks",
        task: accepted.purchase_request_id,
        ai: null,
        review: null,
        tab: "quotes",
        orderTask: null,
      });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["procurement-requests"] }),
        queryClient.invalidateQueries({
          queryKey: ["procurement-request", accepted.purchase_request_id],
        }),
      ]);
    } catch (error) {
      fail("conversation", error);
      state.setShowCreate(true);
    } finally {
      setBusy(null);
    }
  }

  async function uploadQuotes(files: File[]) {
    if (!selectedId) return;
    setBusy("upload");
    clearError();
    try {
      // The server receives the full selection as one durable operation.  That
      // prevents a per-file generation bump from staling earlier results and
      // lets the Agent parse the attachment batch concurrently.
      const accepted = await procurementApi.uploadQuotes(selectedId, files);
      state.setPendingOperation({ operationId: accepted.operation_id, taskId: selectedId });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["procurement-requests"] }),
        queryClient.invalidateQueries({ queryKey: ["procurement-request", selectedId] }),
      ]);
    } catch (error) {
      fail("workspace", error);
    } finally {
      setBusy(null);
    }
  }

  async function correctField(
    quoteId: string,
    field: string,
    value: string | number | boolean | null,
    chosenFromConflicts = false,
  ) {
    if (!selectedId) return;
    setBusy(`field:${quoteId}:${field}`);
    clearError();
    try {
      await procurementApi.correctField(selectedId, quoteId, field, value, chosenFromConflicts);
      await commit(await procurementApi.request(selectedId));
    } catch (error) {
      fail("workspace", error);
    } finally {
      setBusy(null);
    }
  }

  async function correctRequirement(
    payload: Parameters<typeof procurementApi.correctRequirement>[1],
  ) {
    if (!selectedId) return;
    setBusy("requirement");
    clearError();
    try {
      await commit(await procurementApi.correctRequirement(selectedId, payload));
    } catch (error) {
      fail("requirement", error);
      throw error;
    } finally {
      setBusy(null);
    }
  }

  async function analyze() {
    if (!selectedId) return;
    setBusy("analyze");
    clearError();
    try {
      const accepted = await procurementApi.analyze(selectedId);
      state.setPendingRunId(accepted.run_id);
      state.setPendingOperation({ operationId: accepted.operation_id, taskId: selectedId });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["procurement-request", selectedId] }),
        queryClient.invalidateQueries({ queryKey: ["procurement-requests"] }),
        queryClient.invalidateQueries({ queryKey: ["procurement-ai-tasks", selectedId] }),
      ]);
      state.setActiveTab("compare");
    } catch (error) {
      fail("workspace", error);
    } finally {
      await queryClient.invalidateQueries({ queryKey: ["procurement-ai-tasks", selectedId] });
      setBusy(null);
    }
  }

  async function retryAiTask() {
    if (!latestAiTaskId || !selectedId) return;
    setAiActionBusy("retry");
    setAiActionError(null);
    try {
      await procurementApi.retryAiTask(latestAiTaskId);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["procurement-ai-tasks", selectedId] }),
        queryClient.invalidateQueries({ queryKey: ["procurement-ai-task", latestAiTaskId] }),
      ]);
    } catch (error) {
      setAiActionError(errorText(error));
    } finally {
      setAiActionBusy(null);
    }
  }

  async function cancelAiTask() {
    if (!latestAiTaskId || !selectedId) return;
    setAiActionBusy("cancel");
    setAiActionError(null);
    try {
      await procurementApi.cancelAiTask(latestAiTaskId);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["procurement-ai-tasks", selectedId] }),
        queryClient.invalidateQueries({ queryKey: ["procurement-ai-task", latestAiTaskId] }),
      ]);
    } catch (error) {
      setAiActionError(errorText(error));
    } finally {
      setAiActionBusy(null);
    }
  }

  async function resume(message: string) {
    if (!selectedId) return;
    clearError();
    try {
      const accepted = await procurementApi.resume(selectedId, message);
      state.setPendingRunId(accepted.run_id);
      state.setPendingOperation({ operationId: accepted.operation_id, taskId: selectedId });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["procurement-request", selectedId] }),
        queryClient.invalidateQueries({ queryKey: ["procurement-requests"] }),
        queryClient.invalidateQueries({ queryKey: ["procurement-run", accepted.run_id] }),
        queryClient.invalidateQueries({ queryKey: ["procurement-messages", accepted.run_id] }),
        queryClient.invalidateQueries({ queryKey: ["procurement-tools", accepted.run_id] }),
      ]);
    } catch (error) {
      fail("conversation", error);
      throw error;
    }
  }

  async function approve(quoteId: string, note: string) {
    const detail = detailQuery.data;
    if (!selectedId || !detail?.comparison) return;
    setBusy("approve");
    clearError();
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
      state.setActiveTab("report");
    } catch (error) {
      fail("decision", error);
    } finally {
      setBusy(null);
    }
  }

  async function noAward(note: string) {
    const detail = detailQuery.data;
    if (!selectedId || !detail?.comparison) return;
    setBusy("no_award");
    clearError();
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
      state.setActiveTab("report");
    } catch (error) {
      fail("decision", error);
    } finally {
      setBusy(null);
    }
  }

  async function reopen(copyQuotes: boolean) {
    if (!selectedId) return;
    setBusy(copyQuotes ? "reopen_quotes" : "reopen");
    clearError();
    try {
      const updated = await procurementApi.reopen(selectedId, copyQuotes);
      await commit(updated);
      state.setSelectedId(updated.id);
      state.setActiveTab("quotes");
    } catch (error) {
      fail("decision", error);
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
        state.setSelectedId(null);
        state.setShowCreate(true);
        state.setActiveTab("quotes");
      }
      await queryClient.invalidateQueries({ queryKey: ["procurement-requests"] });
    } catch (error) {
      setDeleteError(errorText(error));
    } finally {
      setDeleteBusy(false);
    }
  }

  function openConfig(configQueryData: ProcurementModelConfig | undefined) {
    if (configQueryData) setConfigForm(configFormFrom(configQueryData));
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

  /** 「生成合同（AI 草拟）」真实生成：等待草拟操作完成后跳转合同中心（P-UX②）。 */
  async function generateContract() {
    if (!selectedId) return;
    setBusy("contract");
    clearError();
    try {
      await procurementApi.createContractDraft(selectedId);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["procurement-contracts"] }),
        queryClient.invalidateQueries({ queryKey: ["procurement-contracts-tasks"] }),
        queryClient.invalidateQueries({ queryKey: ["procurement-task-contract", selectedId] }),
      ]);
      state.navigate({ view: "contracts", task: null, ai: null, review: null });
    } catch (error) {
      fail("contract", error);
    } finally {
      setBusy(null);
    }
  }

  function handleNextStep(action: NextStepAction) {
    switch (action.kind) {
      case "quotes":
        state.setActiveTab("quotes");
        break;
      case "compare":
        state.setActiveTab("compare");
        break;
      case "analyze":
        void analyze();
        break;
      case "orders":
        state.navigate({ view: "orders", task: null, ai: null, review: null, orderTask: selectedId });
        break;
      case "reviews":
        state.openView("reviews");
        break;
      case "none":
        break;
    }
  }

  return {
    busy,
    actionErrorSource,
    generateContract,
    aiActionBusy,
    aiActionError,
    showConfig,
    configForm,
    configBusy,
    configError,
    configNotice,
    deleteTarget,
    deleteBusy,
    deleteError,
    conversationOpen,
    setConversationOpen,
    setShowConfig,
    setDeleteTarget,
    openDelete,
    deleteRequest,
    openConfig,
    updateConfigField,
    saveConfig,
    startConversation,
    uploadQuotes,
    correctField,
    correctRequirement,
    analyze,
    retryAiTask,
    cancelAiTask,
    resume,
    approve,
    noAward,
    reopen,
    handleNextStep,
  };
}

export type WorkbenchActions = ReturnType<typeof useWorkbenchActions>;
