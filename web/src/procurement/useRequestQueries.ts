import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef } from "react";

import { useAgentStream } from "../useAgentStream";
import { procurementApi } from "./api";
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
  "review", "ready", "analyzed", "approval_pending",
]);
export const COMPLETE_STATUSES = new Set<ProcurementRequestSummary["status"]>([
  "approved", "no_award", "cancelled",
]);

/**
 * P1-5 拆分 2/5：全部 react-query 数据源 + 轮询策略 + 提交/失效。
 * 职责：meta/config/requests/aiTasks/reviews/detail/report/taskOrder 查询、
 * Agent 流事件联动失效、断线轮询，以及 commit() 缓存写回。
 */
export function useRequestQueries(state: WorkbenchState) {
  const queryClient = useQueryClient();
  const { selectedId, activeTab, view, taskFilter, taskPage, search, pendingRunId } = state;

  const metaQuery = useQuery({ queryKey: ["procurement-meta"], queryFn: procurementApi.meta });
  const configQuery = useQuery({ queryKey: ["procurement-config"], queryFn: procurementApi.config });
  const requestsQuery = useQuery({ queryKey: ["procurement-requests"], queryFn: procurementApi.requests });
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
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status && ["draft", "analyzing", "approval_pending"].includes(status)
        ? 1_500
        : false;
    },
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
    refetchInterval: 10_000,
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

  const stream = useAgentStream(true, 0);
  const disconnectPolls = useRef(0);
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

  useEffect(() => {
    if (stream.status !== "error" || !selectedId) {
      disconnectPolls.current = 0;
      return;
    }
    const timer = window.setInterval(() => {
      if (disconnectPolls.current >= 15) {
        window.clearInterval(timer);
        return;
      }
      disconnectPolls.current += 1;
      void Promise.all([
        queryClient.invalidateQueries({ queryKey: ["procurement-request", selectedId] }),
        queryClient.invalidateQueries({ queryKey: ["procurement-requests"] }),
      ]);
    }, 2_000);
    return () => window.clearInterval(timer);
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
    aiTasksQuery,
    aiTaskQuery,
    reportQuery,
    taskOrderQuery,
    commit,
    requests,
    allAiTasks,
    reviews,
    filtered,
    visibleRequests,
    totalTaskPages,
    latestAiTaskId,
    currentRunId,
  };
}

export type RequestQueries = ReturnType<typeof useRequestQueries>;
export type { TaskFilter };
export type { AiTaskDetail };
