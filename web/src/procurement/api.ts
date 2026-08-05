import type {
  CreateProcurementRequest,
  EvaluationResult,
  ProcurementAuditReport,
  ProcurementMeta,
  ProcurementModelConfig,
  ProcurementModelConfigUpdate,
  ProcurementOperation,
  ProcurementQuote,
  ProcurementRequest,
  ProcurementRequestSummary,
  ProcurementRunAccepted,
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
    value: string | number | boolean | null
  ) =>
    postJson<ProcurementQuote>(
      `/api/procurement/requests/${requestId}/quotes/${quoteId}/corrections`,
      { field, value }
    ),
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
    const accepted = await requestJson<ProcurementRunAccepted>(
      `/api/procurement/requests/${requestId}/decision`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey() },
        body: JSON.stringify(input),
      }
    );
    await waitForOperation(accepted.operation_id);
    return requestJson<ProcurementRequest>(`/api/procurement/requests/${requestId}`);
  })(),
  reopen: (requestId: string, copyQuotes: boolean) =>
    postJson<ProcurementRequest>(`/api/procurement/requests/${requestId}/reopen`, {
      copy_quotes: copyQuotes,
    }),
  report: (requestId: string) =>
    requestJson<ProcurementAuditReport>(`/api/procurement/requests/${requestId}/report`),
  evaluation: () => requestJson<EvaluationResult>("/api/procurement/evaluation"),
};
