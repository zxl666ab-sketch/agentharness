export type RunRow = {
  id: string;
  session_id: string;
  root_run_id: string;
  status: string;
  provider?: string | null;
  model?: string | null;
  user_summary?: string | null;
  output_summary?: string | null;
  error?: string | null;
  steps?: number;
  usage_json?: string;
  metadata_json?: string;
  created_at: string;
  updated_at: string;
  finished_at?: string | null;
};

export type MessageRow = {
  id: string;
  role: "system" | "user" | "assistant" | "tool";
  content: string;
  name?: string | null;
  tool_calls?: Array<Record<string, unknown>> | null;
  created_at: string;
};

export type EventRow = {
  event_id: string;
  global_seq: number;
  run_seq: number;
  session_id: string;
  run_id: string;
  type: string;
  timestamp: string;
  payload: Record<string, unknown>;
};

type ApprovalRow = {
  id: string;
  run_id: string;
  tool_call_id: string;
  tool_name: string;
  effect: string;
  requires_confirmation: boolean;
  arguments_summary?: string | null;
  decision?: string | null;
  created_at: string;
  resolved_at?: string | null;
  invocation_id?: string | null;
  arguments_sha256: string;
  status?: "pending" | "resolved" | "expired";
};

type ToolResultRow = {
  content: string;
  is_error: boolean;
  artifact_id?: string | null;
  duration_ms?: number | null;
  attempts: number;
  error_code?: string | null;
  error_category?: string | null;
  retryable: boolean;
  recovery_hint?: string | null;
};

type ToolAttemptRow = {
  id: string;
  invocation_id: string;
  attempt: number;
  status: string;
  error_code?: string | null;
  error_category?: string | null;
  started_at: string;
  finished_at?: string | null;
  duration_ms?: number | null;
};

export type ToolInvocationRow = {
  id: string;
  run_id: string;
  step: number;
  ordinal: number;
  provider_call_id: string;
  tool_name: string;
  tool_version: string;
  status: string;
  effect: string;
  replay_policy: string;
  arguments: Record<string, unknown>;
  arguments_sha256: string;
  attempt_count: number;
  result?: ToolResultRow | null;
  error_code?: string | null;
  error_category?: string | null;
  attempts_audit?: ToolAttemptRow[];
  created_at: string;
  updated_at: string;
};

export type HealthResponse = {
  service: string;
  status: string;
  backend_version: string;
  api_schema_version: number;
  api_capabilities: string[];
  web_build_id?: string | null;
  data_dir: string;
  max_global_seq: number;
};

type VerificationAttempt = {
  attempt: number;
  step: number;
  validators: string[];
  max_retries: number;
  started_at?: string | null;
  finished_at?: string | null;
  action: string;
  passed: boolean;
  failures: Array<{
    validator?: string;
    error_code?: string;
    message?: string;
    evidence?: Record<string, unknown>;
    retryable?: boolean;
    recovery_hint?: string | null;
  }>;
  evidence: Record<string, unknown>;
  started_event_id?: string | null;
  result_event_id?: string | null;
};

export type RunReport = {
  schema_version: number;
  run_id: string;
  session_id: string;
  as_of?: string | null;
  evidence_sha256: string;
  run: RunRow;
  conclusion: {
    status: "passed" | "failed" | "needs_review" | "pending" | "unverified" | "cancelled" | "interrupted" | "budget_stopped";
    label: string;
    verified: boolean;
    reason: string;
  };
  verification: {
    configured: boolean;
    policy?: {
      validators?: Array<Record<string, unknown>>;
      max_retries?: number;
      on_exhausted?: string;
    } | null;
    attempts: VerificationAttempt[];
    failure_reasons: string[];
  };
  tools: ToolInvocationRow[];
  approvals: ApprovalRow[];
  artifacts: Array<{
    id: string;
    sha256: string;
    content_type?: string | null;
    size_bytes?: number | null;
    summary?: string | null;
    created_at: string;
  }>;
  usage: Record<string, unknown>;
  events: EventRow[];
  events_total: number;
  events_truncated: boolean;
  source: {
    run_updated_at?: string | null;
    max_global_seq: number;
    event_count: number;
    tool_count: number;
    approval_count: number;
    artifact_count: number;
  };
};

async function requestJson<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // Keep the HTTP status when the response is not JSON.
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

/** Read-only API surface used by the procurement workbench. */
export const api = {
  health: () => requestJson<HealthResponse>("/api/health"),
  run: (runId: string) => requestJson<RunRow>(`/api/runs/${runId}`),
  report: (runId: string) => requestJson<RunReport>(`/api/runs/${runId}/report`),
  events: (runId: string, offset = 0, limit = 500) =>
    requestJson<{
      items: EventRow[];
      total: number;
      offset: number;
      has_more: boolean;
    }>(`/api/runs/${runId}/events?offset=${offset}&limit=${limit}`),
  checkpoint: (runId: string) =>
    requestJson<Record<string, unknown> | null>(`/api/runs/${runId}/checkpoint`),
  messages: (runId: string) =>
    requestJson<MessageRow[]>(`/api/runs/${runId}/messages`),
  toolInvocations: (runId: string) =>
    requestJson<ToolInvocationRow[]>(`/api/runs/${runId}/tool-invocations`),
};
