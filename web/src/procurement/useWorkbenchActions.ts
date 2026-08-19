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

/**
 * P1-5 拆分 3/5：13 个动作 handler + busy/错误状态 + 删除/配置弹窗状态。
 * 职责：对话创建、报价上传/字段修正/需求修正、分析、AI 任务重试/取消、
 * 恢复、审批/流标/重开、删除任务、保存模型配置，以及「下一步」跳转。
 */
export function useWorkbenchActions(state: WorkbenchState, queries: RequestQueries) {
  const { queryClient, detailQuery, latestAiTaskId, commit } = queries;
  const { selectedId } = state;

  const [busy, setBusy] = useState<string | null>(null);
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
    state.setActionError(null);
    try {
      const accepted = await procurementApi.startConversation(message, files);
      state.setPendingRunId(accepted.run_id);
      state.setShowCreate(false);
      state.navigate({
        view: "tasks",
        task: accepted.purchase_request_id,
        ai: null,
        review: null,
        tab: "quotes",
        orderTask: null,
      });
      await queryClient.invalidateQueries({ queryKey: ["procurement-requests"] });
      await queryClient.invalidateQueries({
        queryKey: ["procurement-request", accepted.purchase_request_id],
      });
    } catch (error) {
      state.setActionError(errorText(error));
    } finally {
      setBusy(null);
    }
  }

  async function uploadQuotes(files: File[]) {
    if (!selectedId) return;
    setBusy("upload");
    state.setActionError(null);
    try {
      for (const file of files) await procurementApi.uploadQuote(selectedId, file);
      const updated = await procurementApi.request(selectedId);
      await commit(updated);
    } catch (error) {
      state.setActionError(errorText(error));
      const updated = await procurementApi.request(selectedId);
      await commit(updated);
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
    state.setActionError(null);
    try {
      await procurementApi.correctField(selectedId, quoteId, field, value, chosenFromConflicts);
      await commit(await procurementApi.request(selectedId));
    } catch (error) {
      state.setActionError(errorText(error));
    } finally {
      setBusy(null);
    }
  }

  async function correctRequirement(
    payload: Parameters<typeof procurementApi.correctRequirement>[1],
  ) {
    if (!selectedId) return;
    setBusy("requirement");
    state.setActionError(null);
    try {
      await commit(await procurementApi.correctRequirement(selectedId, payload));
    } catch (error) {
      state.setActionError(errorText(error));
      throw error;
    } finally {
      setBusy(null);
    }
  }

  async function analyze() {
    if (!selectedId) return;
    setBusy("analyze");
    state.setActionError(null);
    try {
      const accepted = await procurementApi.analyze(selectedId);
      state.setPendingRunId(accepted.run_id);
      await queryClient.invalidateQueries({ queryKey: ["procurement-request", selectedId] });
      await queryClient.invalidateQueries({ queryKey: ["procurement-requests"] });
      await queryClient.invalidateQueries({ queryKey: ["procurement-ai-tasks", selectedId] });
      state.setActiveTab("compare");
    } catch (error) {
      state.setActionError(errorText(error));
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
    state.setActionError(null);
    try {
      const accepted = await procurementApi.resume(selectedId, message);
      state.setPendingRunId(accepted.run_id);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["procurement-request", selectedId] }),
        queryClient.invalidateQueries({ queryKey: ["procurement-requests"] }),
        queryClient.invalidateQueries({ queryKey: ["procurement-run", accepted.run_id] }),
        queryClient.invalidateQueries({ queryKey: ["procurement-messages", accepted.run_id] }),
        queryClient.invalidateQueries({ queryKey: ["procurement-tools", accepted.run_id] }),
      ]);
    } catch (error) {
      state.setActionError(errorText(error));
      throw error;
    }
  }

  async function approve(quoteId: string, note: string) {
    const detail = detailQuery.data;
    if (!selectedId || !detail?.comparison) return;
    setBusy("approve");
    state.setActionError(null);
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
      state.setActionError(errorText(error));
    } finally {
      setBusy(null);
    }
  }

  async function noAward(note: string) {
    const detail = detailQuery.data;
    if (!selectedId || !detail?.comparison) return;
    setBusy("no_award");
    state.setActionError(null);
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
      state.setActionError(errorText(error));
    } finally {
      setBusy(null);
    }
  }

  async function reopen(copyQuotes: boolean) {
    if (!selectedId) return;
    setBusy(copyQuotes ? "reopen_quotes" : "reopen");
    state.setActionError(null);
    try {
      const updated = await procurementApi.reopen(selectedId, copyQuotes);
      await commit(updated);
      state.setSelectedId(updated.id);
      state.setActiveTab("quotes");
    } catch (error) {
      state.setActionError(errorText(error));
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

  function handleNextStep(action: NextStepAction) {
    switch (action.kind) {
      case "quotes":
        state.setActiveTab("quotes");
        break;
      case "compare":
        state.setActiveTab("compare");
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
