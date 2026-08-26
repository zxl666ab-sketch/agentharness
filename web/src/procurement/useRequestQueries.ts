import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef } from "react";

import { useAgentStream } from "../useAgentStream";
import { operationRefetchInterval, procurementApi } from "./api";
import type {
  AiTaskDetail,
  ProcurementRequest,
  ProcurementRequestSummary,
  ReviewView,
} from "./types";
import type { WorkbenchState } from "./useWorkbenchState";
import type { TaskFilter } from "./workbenchUrl";

export const TASK_PAGE_SIZE = 20;
export const ATTENTION_STATUSES = new Set<ProcurementRequestSummary["status"]>([
  "draft", "collecting", "waiting_human", "review", "ready", "analyzed", "approval_pending",
]);
export const COMPLETE_STATUSES = new Set<ProcurementRequestSummary["status"]>([
  "approved", "no_award", "cancelled",
]);
const TRANSIENT_TASK_STATUSES = new Set<ProcurementRequestSummary["status"]>([
  "draft", "collecting", "analyzing", "approval_pending",
]);
const TERMINAL_OPERATION_STATUSES = new Set(["completed", "failed", "cancelled"]);

function taskRefetchInterval(status: ProcurementRequestSummary["status"] | undefined) {
  return status && TRANSIENT_TASK_STATUSES.has(status) ? 750 : false;
}

/**
 * P1-5 拆分 2/5：全部 react-query 数据源 + 轮询策略 + 提交/失效。
 * 职责：meta/config/requests/aiTasks/reviews/detail/report/taskOrder 查询、
 * Agent 流事件联动失效、断线轮询，以及 commit() 缓存写回。
 */
export function useRequestQueries(state: WorkbenchState) {
  const queryClient = useQueryClient();
  const {
    selectedId,
    activeTab,
    view,
    taskFilter,
    taskPage,
    search,
    pendingRunId,
    pendingOperation,
    setActionError,
    setPendingOperation,
  } = state;

  const metaQuery = useQuery({ queryKey: ["procurement-meta"], queryFn: procurementApi.meta });
  const configQuery = useQuery({ queryKey: ["procurement-config"], queryFn: procurementApi.config });
  const requestsQuery = useQuery({
    queryKey: ["procurement-requests"],
    queryFn: procurementApi.requests,
    // Keep the dashboard quiet when every task is settled.  A task in a
    // transient state opts the list back into second-level synchronization.
    refetchInterval: (query) => (
      !!pendingOperation || query.state.data?.some((request) =>
        TRANSIENT_TASK_STATUSES.has(request.status)
      )
    ) ? 1_500 : false,
  });
  const allAiTasksQuery = useQuery({
    queryKey: ["procurement-ai-tasks"],
    queryFn: () => procurementApi.aiTasks(),
    refetchInterval: view === "ai" ? 3_000 : false,
  });
  const reviewsQuery = useQuery({
    queryKey: ["procurement-reviews"],
    queryFn: () => procurementApi.reviews(),
    refetchInterval: view === "reviews" ? 3_000 : false,
  });
  const detailQuery = useQuery({
    queryKey: ["procurement-request", selectedId],
    queryFn: () => procurementApi.request(selectedId!),
    enabled: !!selectedId,
    refetchInterval: (query) => pendingOperation?.taskId === selectedId
      ? 750
      : taskRefetchInterval(query.state.data?.status),
  });
  const operationQuery = useQuery({
    queryKey: ["procurement-operation", pendingOperation?.operationId],
    queryFn: () => procurementApi.operation(pendingOperation!.operationId),
    enabled: !!pendingOperation,
    refetchInterval: (query) => operationRefetchInterval(query.state.data),
  });
  const interactionsQuery = useQuery({
    queryKey: ["procurement-interactions", selectedId],
    queryFn: () => procurementApi.interactions(selectedId!),
    enabled: !!selectedId,
    refetchInterval: (query) => query.state.data?.some((item) =>
      item.status === "WAITING" || item.status === "ANSWERED"
    ) ? 1_500 : false,
  });
  const aiTasksQuery = useQuery({
    queryKey: ["procurement-ai-tasks", selectedId],
    queryFn: () => procurementApi.aiTasks(selectedId!),
    enabled: !!selectedId,
    refetchInterval: (query) => query.state.data?.items.some((task) =>
      ["PENDING", "DISPATCHING", "RUNNING", "RETRYING"].includes(task.status)
    ) ? 1_500 : false,
  });
  const latestAiTaskId = aiTasksQuery.data?.items[0]?.ai_task_id || null;
  const aiTaskQuery = useQuery({
    queryKey: ["procurement-ai-task", latestAiTaskId],
    queryFn: () => procurementApi.aiTask(latestAiTaskId!),
    enabled: !!latestAiTaskId,
    refetchInterval: (query) => query.state.data &&
      ["PENDING", "DISPATCHING", "RUNNING", "RETRYING"].includes(query.state.data.status)
      ? 1_500
      : false,
  });
  const reportQuery = useQuery({
    queryKey: ["procurement-report", selectedId],
    queryFn: () => procurementApi.report(selectedId!),
    enabled: !!selectedId && activeTab === "report" && !!detailQuery.data?.decision,
  });
  const taskOrderQuery = useQuery({
    queryKey: ["procurement-task-order", selectedId],
    queryFn: async () => {
      const page = await procurementApi.orders(undefined, 0, 100);
      return page.items.find((item) => item.task_id === selectedId) || null;
    },
    enabled: !!selectedId && detailQuery.data?.status === "approved",
    // 任务订单轮询：订单到达 CLOSED 终态（付款后关闭）即停止；尚未生成时继续等待生成。
    refetchInterval: (query) => query.state.data?.status === "CLOSED" ? false : 10_000,
  });
  const taskContractQuery = useQuery({
    queryKey: ["procurement-task-contract", selectedId],
    queryFn: async () => {
      const page = await procurementApi.contracts(undefined, selectedId!, 0, 5);
      return page.items[0] || null;
    },
    enabled: !!selectedId && detailQuery.data?.status === "approved",
    // 任务合同轮询：合同 CLOSED 为终态即停止；尚未起草（null）时继续等待。
    refetchInterval: (query) => query.state.data?.status === "CLOSED" ? false : 5_000,
  });

  const requests = useMemo(() => requestsQuery.data || [], [requestsQuery.data]);
  const allAiTasks = useMemo(() => allAiTasksQuery.data?.items || [], [allAiTasksQuery.data]);
  const reviews = useMemo<ReviewView[]>(() => reviewsQuery.data?.items || [], [reviewsQuery.data]);
  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    return requests.filter((request) => {
      const matchesText = !query || [request.reference, request.title, request.item_name]
        .join(" ")
        .toLowerCase()
        .includes(query);
      const matchesStatus = taskFilter === "all"
        || (taskFilter === "attention" && ATTENTION_STATUSES.has(request.status))
        || (taskFilter === "completed" && COMPLETE_STATUSES.has(request.status))
        || (taskFilter === "active" && !COMPLETE_STATUSES.has(request.status));
      return matchesText && matchesStatus;
    });
  }, [requests, search, taskFilter]);
  const totalTaskPages = Math.max(1, Math.ceil(filtered.length / TASK_PAGE_SIZE));
  const visibleRequests = filtered.slice(taskPage * TASK_PAGE_SIZE, (taskPage + 1) * TASK_PAGE_SIZE);

  useEffect(() => {
    if (taskPage >= totalTaskPages) state.setTaskPage(totalTaskPages - 1);
  }, [taskPage, totalTaskPages, state]);

  useEffect(() => {
    if (view === "tasks" && !selectedId && !state.showCreate && requests.length) {
      state.setSelectedId(requests[0].id);
    }
  }, [requests, selectedId, state, view]);

  useEffect(() => {
    const operation = operationQuery.data;
    if (!pendingOperation || !operation || !TERMINAL_OPERATION_STATUSES.has(operation.status)) {
      return;
    }
    if (operation.status === "failed" && selectedId === pendingOperation.taskId) {
      setActionError(operation.last_error || "异步操作执行失败");
    }
    setPendingOperation(null);
    void Promise.all([
      queryClient.invalidateQueries({ queryKey: ["procurement-requests"] }),
      queryClient.invalidateQueries({ queryKey: ["procurement-request", pendingOperation.taskId] }),
      queryClient.invalidateQueries({ queryKey: ["procurement-ai-tasks"] }),
      queryClient.invalidateQueries({ queryKey: ["procurement-ai-tasks", pendingOperation.taskId] }),
    ]);
  }, [
    operationQuery.data,
    pendingOperation,
    queryClient,
    selectedId,
    setActionError,
    setPendingOperation,
  ]);

  const stream = useAgentStream(true, 0);
  const disconnectPolls = useRef(0);
  const streamRefreshTimer = useRef<number | null>(null);
  const currentRunId = detailQuery.data?.analysis_run_id || pendingRunId;
  const latestEvent = stream.latestEvent;
  useEffect(() => {
    if (!latestEvent || latestEvent.run_id !== currentRunId || !selectedId) return;
    // A streamed run can emit many token/tool events in a burst.  Coalescing
    // them preserves live updates without refetching the same resources once
    // per event.
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
    }, 150);
  }, [currentRunId, latestEvent, queryClient, selectedId]);

  useEffect(() => () => {
    if (streamRefreshTimer.current !== null) {
      window.clearTimeout(streamRefreshTimer.current);
    }
  }, []);

  useEffect(() => {
    if (stream.status !== "error" || !selectedId) {
      disconnectPolls.current = 0;
      return;
    }
    let timer: number | null = null;
    const poll = () => {
      disconnectPolls.current += 1;
      void Promise.all([
        queryClient.invalidateQueries({ queryKey: ["procurement-request", selectedId] }),
        queryClient.invalidateQueries({ queryKey: ["procurement-requests"] }),
      ]);
      // 前 15 次每 2s，之后退避到 10s 持续兜底：断线期间数据保持最终一致，
      // 不再像旧实现那样 30s 后彻底静默放弃（UI 也不会再无声变死）。
      const delay = disconnectPolls.current >= 15 ? 10_000 : 2_000;
      timer = window.setTimeout(poll, delay);
    };
    timer = window.setTimeout(poll, 2_000);
    return () => {
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [queryClient, selectedId, stream.status]);

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

  return {
    queryClient,
    metaQuery,
    configQuery,
    requestsQuery,
    allAiTasksQuery,
    reviewsQuery,
    detailQuery,
    operationQuery,
    interactionsQuery,
    aiTasksQuery,
    aiTaskQuery,
    reportQuery,
    taskOrderQuery,
    taskContractQuery,
    commit,
    requests,
    allAiTasks,
    reviews,
    filtered,
    visibleRequests,
    totalTaskPages,
    latestAiTaskId,
    currentRunId,
    streamStatus: stream.status,
    reconnectStream: stream.reconnect,
  };
}

export type RequestQueries = ReturnType<typeof useRequestQueries>;
export type { TaskFilter };
export type { AiTaskDetail };
