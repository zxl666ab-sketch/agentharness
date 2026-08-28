import { useCallback, useEffect, useMemo, useState, type Dispatch, type SetStateAction } from "react";

import {
  readWorkbenchUrl,
  writeWorkbenchUrl,
  type TaskFilter,
  type TaskTab,
  type WorkbenchUrlState,
  type WorkbenchView,
} from "./workbenchUrl";

export type PendingOperation = {
  operationId: string;
  taskId: string;
};

/**
 * P1-5 拆分 1/5：URL 状态 + 视图导航。
 * 职责：读写 workbench URL 状态（view/task/ai/review/tab/filter/q/page/orderTask），
 * 提供 navigate/openView/openTask/openTaskFilter/openCreate/selectAiTask/selectReview，
 * 并同步 popstate 恢复与 URL 写入。
 */
export type WorkbenchState = {
  view: WorkbenchView;
  selectedId: string | null;
  selectedAiId: string | null;
  selectedReviewId: string | null;
  activeTab: TaskTab;
  taskFilter: TaskFilter;
  taskPage: number;
  search: string;
  showCreate: boolean;
  pendingRunId: string | null;
  pendingOperation: PendingOperation | null;
  orderTask: string | null;
  actionError: string | null;
  urlState: WorkbenchUrlState;
  setActiveTab: (tab: TaskTab) => void;
  setTaskFilter: (filter: TaskFilter) => void;
  setTaskPage: Dispatch<SetStateAction<number>>;
  setSearch: (value: string) => void;
  setShowCreate: (value: boolean) => void;
  setPendingRunId: (value: string | null) => void;
  setPendingOperation: (value: PendingOperation | null) => void;
  setSelectedId: (value: string | null) => void;
  setActionError: (value: string | null) => void;
  navigate: (patch: Partial<WorkbenchUrlState>, push?: boolean) => void;
  openView: (view: WorkbenchView) => void;
  openTask: (taskId: string, tab?: TaskTab) => void;
  openTaskFilter: (filter: TaskFilter) => void;
  openCreate: () => void;
  selectAiTask: (id: string | null, push?: boolean) => void;
  selectReview: (id: string | null, push?: boolean) => void;
};

export function useWorkbenchState(): WorkbenchState {
  const initialUrl = useMemo(() => readWorkbenchUrl(), []);
  const [view, setView] = useState<WorkbenchView>(initialUrl.view);
  const [selectedId, setSelectedId] = useState<string | null>(initialUrl.task);
  const [selectedAiId, setSelectedAiId] = useState<string | null>(initialUrl.ai);
  const [selectedReviewId, setSelectedReviewId] = useState<string | null>(initialUrl.review);
  const [activeTab, setActiveTab] = useState<TaskTab>(initialUrl.tab);
  const [taskFilter, setTaskFilter] = useState<TaskFilter>(initialUrl.status);
  const [taskPage, setTaskPage] = useState(initialUrl.page);
  const [showCreate, setShowCreate] = useState(false);
  const [pendingRunId, setPendingRunId] = useState<string | null>(null);
  const [pendingOperation, setPendingOperation] = useState<PendingOperation | null>(null);
  const [orderTask, setOrderTask] = useState<string | null>(initialUrl.orderTask);
  const [search, setSearch] = useState(initialUrl.q);
  const [actionError, setActionError] = useState<string | null>(null);

  const urlState = useMemo<WorkbenchUrlState>(() => ({
    view,
    task: selectedId,
    ai: selectedAiId,
    review: selectedReviewId,
    tab: activeTab,
    status: taskFilter,
    q: search,
    page: taskPage,
    orderTask,
  }), [activeTab, orderTask, search, selectedAiId, selectedId, selectedReviewId, taskFilter, taskPage, view]);

  useEffect(() => writeWorkbenchUrl(urlState), [urlState]);
  useEffect(() => {
    const restore = () => {
      const next = readWorkbenchUrl();
      setView(next.view);
      setSelectedId(next.task);
      setSelectedAiId(next.ai);
      setSelectedReviewId(next.review);
      setActiveTab(next.tab);
      setTaskFilter(next.status);
      setSearch(next.q);
      setTaskPage(next.page);
      setOrderTask(next.orderTask);
      setShowCreate(false);
    };
    window.addEventListener("popstate", restore);
    return () => window.removeEventListener("popstate", restore);
  }, []);

  // W-M6：导航回调全部 useCallback 稳定化，返回对象 useMemo 包裹。
  // 此前每次渲染都产生新函数/新对象，令一切依赖 `state` 的 effect 无谓重跑。
  const navigate = useCallback((patch: Partial<WorkbenchUrlState>, push = true) => {
    const next = { ...urlState, ...patch };
    writeWorkbenchUrl(next, push);
    setView(next.view);
    setSelectedId(next.task);
    setSelectedAiId(next.ai);
    setSelectedReviewId(next.review);
    setActiveTab(next.tab);
    setTaskFilter(next.status);
    setSearch(next.q);
    setTaskPage(next.page);
    setOrderTask(next.orderTask);
  }, [urlState]);

  const openView = useCallback((nextView: WorkbenchView) => {
    setShowCreate(false);
    setActionError(null);
    navigate({
      view: nextView,
      task: nextView === "tasks" ? selectedId : null,
      ai: nextView === "ai" ? selectedAiId : null,
      review: nextView === "reviews" ? selectedReviewId : null,
      page: 0,
      orderTask: null,
    });
  }, [navigate, selectedAiId, selectedId, selectedReviewId]);

  const openTask = useCallback((taskId: string, tab: TaskTab = "quotes") => {
    setShowCreate(false);
    setActionError(null);
    navigate({ view: "tasks", task: taskId, ai: null, review: null, tab, orderTask: null });
  }, [navigate]);

  const openTaskFilter = useCallback((filter: TaskFilter) => {
    setShowCreate(false);
    navigate({ view: "tasks", status: filter, task: null, ai: null, review: null, page: 0, orderTask: null });
  }, [navigate]);

  const openCreate = useCallback(() => {
    setActionError(null);
    setSelectedId(null);
    setShowCreate(true);
    navigate({ view: "tasks", task: null, ai: null, review: null, tab: "quotes", orderTask: null });
  }, [navigate]);

  const selectAiTask = useCallback((aiTaskId: string | null, push = true) => {
    navigate({ view: "ai", task: null, ai: aiTaskId, review: null, orderTask: null }, push);
  }, [navigate]);

  const selectReview = useCallback((reviewId: string | null, push = true) => {
    navigate({ view: "reviews", task: null, ai: null, review: reviewId, orderTask: null }, push);
  }, [navigate]);

  return useMemo<WorkbenchState>(() => ({
    view,
    selectedId,
    selectedAiId,
    selectedReviewId,
    activeTab,
    taskFilter,
    taskPage,
    search,
    showCreate,
    pendingRunId,
    pendingOperation,
    orderTask,
    actionError,
    urlState,
    setActiveTab,
    setTaskFilter,
    setTaskPage,
    setSearch,
    setShowCreate,
    setPendingRunId,
    setPendingOperation,
    setSelectedId,
    setActionError,
    navigate,
    openView,
    openTask,
    openTaskFilter,
    openCreate,
    selectAiTask,
    selectReview,
  }), [
    view,
    selectedId,
    selectedAiId,
    selectedReviewId,
    activeTab,
    taskFilter,
    taskPage,
    search,
    showCreate,
    pendingRunId,
    pendingOperation,
    orderTask,
    actionError,
    urlState,
    navigate,
    openView,
    openTask,
    openTaskFilter,
    openCreate,
    selectAiTask,
    selectReview,
  ]);
}
