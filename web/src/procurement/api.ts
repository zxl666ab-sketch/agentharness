import type {
  EvaluationResult,
  ProcurementAuditReport,
  ProcurementMeta,
  ProcurementModelConfig,
  ProcurementModelConfigUpdate,
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

/** Convert provider gateway failures into an actionable message for buyers. */
export function friendlyProcurementError(message: string): string {
  if (message.trim().toLowerCase() === "your request was blocked.") {
    return "模型网关拒绝了带工具调用的采购分析。请在模型配置中切换支持工具调用的模型或 API 模式，然后点击“从持久化状态重新分析”；采购需求和附件已保留。";
  }
  return message;
}

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
      const body = (await response.json()) as { detail?: string | ValidationIssue[] };
      if (typeof body.detail === "string") message = body.detail;
      if (Array.isArray(body.detail)) message = body.detail.map(validationMessage).join("；");
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

function fileBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("无法读取报价文件，请检查文件是否可访问"));
    reader.onload = () => {
      const value = String(reader.result || "");
      const separator = value.indexOf(",");
      if (separator < 0) reject(new Error("无法编码文件"));
      else resolve(value.slice(separator + 1));
    };
    reader.readAsDataURL(file);
  });
}

export const procurementApi = {
  meta: () => requestJson<ProcurementMeta>("/api/procurement/meta"),
  config: () => requestJson<ProcurementModelConfig>("/api/procurement/config"),
  updateConfig: (input: ProcurementModelConfigUpdate) =>
    postJson<ProcurementModelConfig>("/api/procurement/config", input),
  requests: () => requestJson<ProcurementRequestSummary[]>("/api/procurement/requests?limit=200"),
  request: (requestId: string) =>
    requestJson<ProcurementRequest>(`/api/procurement/requests/${requestId}`),
  async startConversation(message: string, files: File[]) {
    const attachments = await Promise.all(
      files.map(async (file) => ({
        filename: file.name,
        content_base64: await fileBase64(file),
      }))
    );
    return postJson<ProcurementRunAccepted>("/api/procurement/conversations", {
      message,
      attachments,
      actor: "采购员",
    });
  },
  resume: (requestId: string, message: string) =>
    postJson<ProcurementRunAccepted>(
      `/api/procurement/requests/${requestId}/resume`,
      { message }
    ),
  async uploadQuote(requestId: string, file: File) {
    return postJson<ProcurementQuote>(`/api/procurement/requests/${requestId}/quotes`, {
      filename: file.name,
      content_base64: await fileBase64(file),
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
      { field, value, actor: "采购员" }
    ),
  analyze: (requestId: string) =>
    postJson<ProcurementRunAccepted>(`/api/procurement/requests/${requestId}/analyze`),
  cancelRun: (requestId: string) =>
    postJson<{ request_id: string; run_id: string; status: string }>(
      `/api/procurement/requests/${requestId}/cancel-run`
    ),
  approve: (
    requestId: string,
    input: {
      snapshot_id: string;
      input_sha256: string;
      decision?: "approved" | "no_award";
      quote_id?: string;
      confirmed: boolean;
      note?: string;
    }
  ) =>
    postJson<ProcurementRequest>(`/api/procurement/requests/${requestId}/decision`, {
      ...input,
      actor: "采购员",
    }),
  report: (requestId: string) =>
    requestJson<ProcurementAuditReport>(`/api/procurement/requests/${requestId}/report`),
  evaluation: () => requestJson<EvaluationResult>("/api/procurement/evaluation"),
};
