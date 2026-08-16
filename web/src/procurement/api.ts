import type {
  AiTaskDetail,
  AiTaskPage,
  AiTaskView,
  AuditEventPage,
  CategoryDistribution,
  ContractActionInput,
  ContractPage,
  ContractStatus,
  ContractView,
  CorrectionPage,
  CreateProcurementRequest,
  EvaluationResult,
  InsightTrendRow,
  InsightsOverview,
  InvoiceActionInput,
  InvoicePage,
  InvoiceStatus,
  InvoiceView,
  OrderPage,
  OrderStatus,
  OrderView,
  PlatformInfo,
  ProcurementAuditReport,
  ProcurementMeta,
  ProcurementModelConfig,
  ProcurementModelConfigUpdate,
  ProcurementOperation,
  ProcurementQuote,
  ProcurementRequest,
  ProcurementRequestSummary,
  ProcurementRunAccepted,
  ReviewActionInput,
  ReviewDetail,
  ReviewPage,
  ReviewStatus,
  SettlementPage,
  SettlementStatus,
  SettlementView,
  SupplierPage,
  SupplierProfile,
  SupplierRankingRow,
  SupplierSaveRequest,
  SupplierStatus,
  SupplierView,
} from "./types";

const FIELD_LABELS: Record<string, string> = {
  title: "任务名称",
  item_name: "物料名称",
  quantity: "采购数量",
  width_mm: "宽度",
  length_mm: "长度",
  thickness_um: "厚度",
  material: "材质",
  color: "颜色",
  print_colors: "印刷色数",
  max_lead_days: "最长交期",
  max_landed_unit_cost: "到货单价上限",
  size_tolerance_mm: "尺寸公差",
  thickness_tolerance_um: "厚度公差",
  required_delivery_date: "要求到货日期",
  destination: "送货地点",
  fx_rates: "汇率",
};

type ValidationIssue = { msg?: string; loc?: Array<string | number> };

function validationMessage(issue: ValidationIssue) {
  const field = String(issue.loc?.at(-1) || "表单");
  const label = FIELD_LABELS[field] || "表单内容";
  const message = String(issue.msg || "");
  if (message.startsWith("Value error, ")) return message.slice(13);
  if (message === "Field required") return `${label}不能为空`;
  if (message.includes("greater than")) return `${label}必须大于允许的最小值`;
  if (message.includes("less than") || message.includes("at most")) return `${label}超过允许范围`;
  if (message.includes("valid date")) return `${label}日期格式无效`;
  if (message.includes("valid")) return `${label}格式无效`;
  return `${label}填写有误`;
}

function httpError(status: number) {
  return {
    400: "请求内容无效",
    404: "未找到请求的采购数据",
    408: "报价处理超时",
    409: "当前业务状态不允许此操作",
    413: "上传文件过大",
    422: "表单校验未通过",
    500: "采购服务发生内部错误",
  }[status] || `采购服务请求失败（${status}）`;
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, init);
  } catch {
    throw new Error("网络连接失败，请确认采购服务已启动");
  }
  if (!response.ok) {
    let message = httpError(response.status);
    try {
      const body = (await response.json()) as {
        detail?: string | ValidationIssue[];
        message?: string;
        field_errors?: Array<{ field: string; message: string }>;
      };
      if (typeof body.message === "string") message = body.message;
      else if (typeof body.detail === "string") message = body.detail;
      if (Array.isArray(body.field_errors) && body.field_errors.length) {
        message = body.field_errors.map((item) => `${FIELD_LABELS[item.field] || item.field}：${item.message}`).join("；");
      } else if (Array.isArray(body.detail)) {
        message = body.detail.map(validationMessage).join("；");
      }
    } catch {
      // Preserve the HTTP status when the body is not JSON.
    }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

function postJson<T>(path: string, body?: unknown): Promise<T> {
  return requestJson<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
}

function idempotencyKey() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `web-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

async function waitForOperation(operationId: string, timeoutMs = 30_000) {
  const deadline = Date.now() + timeoutMs;
  let delay = 250;
  while (Date.now() < deadline) {
    const operation = await requestJson<ProcurementOperation>(
      `/api/procurement/operations/${operationId}`
    );
    if (operation.status === "completed") return operation;
    if (operation.status === "failed") {
      throw new Error(operation.last_error || "异步操作执行失败");
    }
    await new Promise((resolve) => window.setTimeout(resolve, delay));
    delay = Math.min(1_500, Math.round(delay * 1.5));
  }
  return null;
}

export const procurementApi = {
  meta: () => requestJson<ProcurementMeta>("/api/procurement/meta"),
  config: () => requestJson<ProcurementModelConfig>("/api/procurement/config"),
  updateConfig: (input: ProcurementModelConfigUpdate) =>
    postJson<ProcurementModelConfig>("/api/procurement/config", input),
  requests: () => requestJson<ProcurementRequestSummary[]>("/api/procurement/requests?limit=200"),
  request: (requestId: string) =>
    requestJson<ProcurementRequest>(`/api/procurement/requests/${requestId}`),
  aiTasks: (businessId?: string) => {
    const query = new URLSearchParams({ page: "0", size: "100" });
    if (businessId) query.set("business_id", businessId);
    return requestJson<AiTaskPage>(`/api/procurement/ai-tasks?${query}`);
  },
  aiTask: (aiTaskId: string) =>
    requestJson<AiTaskDetail>(`/api/procurement/ai-tasks/${aiTaskId}`),
  retryAiTask: (aiTaskId: string) =>
    requestJson<AiTaskView>(`/api/procurement/ai-tasks/${aiTaskId}/retry`, {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey() },
    }),
  cancelAiTask: (aiTaskId: string) =>
    requestJson<AiTaskView>(`/api/procurement/ai-tasks/${aiTaskId}/cancel`, {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey() },
    }),
  reviews: (status?: ReviewStatus, page = 0, size = 100) => {
    const query = new URLSearchParams({ page: String(page), size: String(size) });
    if (status) query.set("status", status);
    return requestJson<ReviewPage>(`/api/procurement/reviews?${query}`);
  },
  review: (reviewId: string) =>
    requestJson<ReviewDetail>(`/api/procurement/reviews/${reviewId}`),
  submitReview: (reviewId: string, input: ReviewActionInput) =>
    requestJson<ReviewDetail>(`/api/procurement/reviews/${reviewId}/actions`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey() },
      body: JSON.stringify(input),
    }),
  deleteRequest: (requestId: string) =>
    requestJson<{ request_id: string; reference: string; deleted: boolean }>(
      `/api/procurement/requests/${requestId}`,
      { method: "DELETE" }
    ),
  createRequest: (input: CreateProcurementRequest) =>
    postJson<ProcurementRequest>("/api/procurement/requests", input),
  async startConversation(message: string, files: File[]) {
    const form = new FormData();
    form.append("message", message);
    files.forEach((file) => form.append("files", file, file.name));
    const accepted = await requestJson<ProcurementRunAccepted>("/api/procurement/conversations", {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey() },
      body: form,
    });
    await waitForOperation(accepted.operation_id);
    return accepted;
  },
  resume: (requestId: string, message: string) =>
    postJson<ProcurementRunAccepted>(
      `/api/procurement/requests/${requestId}/resume`,
      { message }
    ),
  async uploadQuote(requestId: string, file: File) {
    const form = new FormData();
    form.append("file", file, file.name);
    return requestJson<ProcurementRunAccepted>(`/api/procurement/requests/${requestId}/quotes`, {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey() },
      body: form,
    });
  },
  correctField: (
    requestId: string,
    quoteId: string,
    field: string,
    value: string | number | boolean | null,
    chosenFromConflicts?: boolean,
  ) =>
    postJson<ProcurementQuote>(
      `/api/procurement/requests/${requestId}/quotes/${quoteId}/corrections`,
      { field, value, chosen_from_conflicts: chosenFromConflicts ?? false }
    ),
  corrections: (page = 0, size = 50) => {
    const query = new URLSearchParams({ page: String(page), size: String(size) });
    return requestJson<CorrectionPage>(`/api/procurement/corrections?${query}`);
  },
  invoices: (status?: InvoiceStatus, orderId?: string, page = 0, size = 50) => {
    const query = new URLSearchParams({ page: String(page), size: String(size) });
    if (status) query.set("status", status);
    if (orderId) query.set("order_id", orderId);
    return requestJson<InvoicePage>(`/api/procurement/invoices?${query}`);
  },
  invoice: (id: string) => requestJson<InvoiceView>(`/api/procurement/invoices/${id}`),
  uploadInvoice: async (orderId: string, file: File) => {
    const form = new FormData();
    form.append("order_id", orderId);
    form.append("file", file, file.name);
    const accepted = await requestJson<ProcurementRunAccepted>("/api/procurement/invoices", {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey() },
      body: form,
    });
    await waitForOperation(accepted.operation_id);
    return accepted;
  },
  invoiceAction: (id: string, action: "void" | "correct" | "force_match" | "reconcile", input: InvoiceActionInput) =>
    requestJson<InvoiceView>(`/api/procurement/invoices/${id}/actions?action=${action}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    }),
  contracts: (status?: ContractStatus, taskId?: string, page = 0, size = 50) => {
    const query = new URLSearchParams({ page: String(page), size: String(size) });
    if (status) query.set("status", status);
    if (taskId) query.set("task_id", taskId);
    return requestJson<ContractPage>(`/api/procurement/contracts?${query}`);
  },
  contract: (id: string) => requestJson<ContractView>(`/api/procurement/contracts/${id}`),
  createContractDraft: async (taskId: string) => {
    const accepted = await requestJson<ProcurementRunAccepted>("/api/procurement/contracts", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey() },
      body: JSON.stringify({ task_id: taskId }),
    });
    await waitForOperation(accepted.operation_id);
    return accepted;
  },
  contractAction: (
    id: string,
    action: "submit" | "approve" | "reject" | "execute" | "close" | "request_change",
    input: ContractActionInput
  ) =>
    requestJson<ContractView>(`/api/procurement/contracts/${id}/actions?action=${action}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    }),
  correctRequirement: (requestId: string, input: CreateProcurementRequest) =>
    requestJson<ProcurementRequest>(
      `/api/procurement/requests/${requestId}/requirement`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      }
    ),
  analyze: async (requestId: string) => {
    const accepted = await requestJson<ProcurementRunAccepted>(`/api/procurement/requests/${requestId}/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey() },
      body: "{}",
    });
    await waitForOperation(accepted.operation_id);
    return accepted;
  },
  approve: (
    requestId: string,
    input: {
      decision?: "approved" | "no_award";
      snapshot_id: string;
      input_sha256: string;
      quote_id: string | null;
      confirmed: boolean;
      note?: string;
    }
  ) => (async () => {
    const accepted = await requestJson<
      ProcurementRunAccepted | { request_id: string; decision_id: string; status: string }
    >(
      `/api/procurement/requests/${requestId}/decision`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey() },
        body: JSON.stringify(input),
      }
    );
    // 已存在正式决定时 Java 直接返回 {decision_id,status}，无需等待操作；
    // 否则等待 202 OperationAccepted 完成。
    if ("operation_id" in accepted && accepted.operation_id) {
      await waitForOperation(accepted.operation_id);
    }
    return requestJson<ProcurementRequest>(`/api/procurement/requests/${requestId}`);
  })(),
  reopen: (requestId: string, copyQuotes: boolean) =>
    postJson<ProcurementRequest>(`/api/procurement/requests/${requestId}/reopen`, {
      copy_quotes: copyQuotes,
    }),
  report: (requestId: string) =>
    requestJson<ProcurementAuditReport>(`/api/procurement/requests/${requestId}/report`),
  evaluation: () => requestJson<EvaluationResult>("/api/procurement/evaluation"),
  suppliers: (q?: string, status?: SupplierStatus, page = 0, size = 50) => {
    const query = new URLSearchParams({ page: String(page), size: String(size) });
    if (q?.trim()) query.set("q", q.trim());
    if (status) query.set("status", status);
    return requestJson<SupplierPage>(`/api/procurement/suppliers?${query}`);
  },
  createSupplier: (input: SupplierSaveRequest) =>
    postJson<SupplierView>("/api/procurement/suppliers", input),
  updateSupplier: (id: string, input: SupplierSaveRequest) =>
    requestJson<SupplierView>(`/api/procurement/suppliers/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    }),
  deleteSupplier: (id: string) =>
    requestJson<{ deleted: boolean }>(`/api/procurement/suppliers/${id}`, {
      method: "DELETE",
    }),
  supplierProfile: (id: string) =>
    requestJson<SupplierProfile>(`/api/procurement/suppliers/${id}/profile`),
  orders: (status?: OrderStatus, page = 0, size = 50) => {
    const query = new URLSearchParams({ page: String(page), size: String(size) });
    if (status) query.set("status", status);
    return requestJson<OrderPage>(`/api/procurement/orders?${query}`);
  },
  order: (id: string) => requestJson<OrderView>(`/api/procurement/orders/${id}`),
  transitionOrder: (
    id: string,
    input: {
      action: "ship" | "receive" | "close";
      received_quantity?: string | null;
      arrival_date?: string | null;
      notes?: string | null;
    }
  ) => postJson<OrderView>(`/api/procurement/orders/${id}/transition`, input),
  settlements: (status?: SettlementStatus, page = 0, size = 50) => {
    const query = new URLSearchParams({ page: String(page), size: String(size) });
    if (status) query.set("status", status);
    return requestJson<SettlementPage>(`/api/procurement/settlements?${query}`);
  },
  transitionSettlement: (
    id: string,
    input: { action: "settle" | "pay"; paid_at?: string | null; notes?: string | null }
  ) => postJson<SettlementView>(`/api/procurement/settlements/${id}/transition`, input),
  insightsOverview: () => requestJson<InsightsOverview>("/api/procurement/insights/overview"),
  insightsTrend: (months = 6) =>
    requestJson<InsightTrendRow[]>(`/api/procurement/insights/trend?months=${months}`),
  insightsSupplierRanking: (limit = 10) =>
    requestJson<SupplierRankingRow[]>(`/api/procurement/insights/supplier-ranking?limit=${limit}`),
  insightsCategories: () => requestJson<CategoryDistribution>("/api/procurement/insights/categories"),
  auditEvents: (filters: {
    type?: string;
    actor?: string;
    business_type?: string;
    task_id?: string;
    page?: number;
    size?: number;
  }) => {
    const query = new URLSearchParams({ page: String(filters.page ?? 0), size: String(filters.size ?? 50) });
    if (filters.type?.trim()) query.set("type", filters.type.trim());
    if (filters.actor?.trim()) query.set("actor", filters.actor.trim());
    if (filters.business_type?.trim()) query.set("business_type", filters.business_type.trim());
    if (filters.task_id?.trim()) query.set("task_id", filters.task_id.trim());
    return requestJson<AuditEventPage>(`/api/procurement/audit-events?${query}`);
  },
  platform: () => requestJson<PlatformInfo>("/api/procurement/platform"),
};
