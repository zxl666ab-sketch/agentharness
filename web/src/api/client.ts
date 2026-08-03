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

export type SessionRow = {
  id: string;
  title?: string | null;
  display_title?: string | null;
  latest_run_id?: string | null;
  latest_status?: string | null;
  latest_error?: string | null;
  run_count?: number;
  created_at: string;
  updated_at: string;
};

export type TranscriptTurn = {
  run_id: string;
  session_id: string;
  user_content: string;
  assistant_content: string;
  status: string;
  error?: string | null;
  provider?: string | null;
  model?: string | null;
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

export type ApprovalRow = {
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

export type ToolResultRow = {
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

export type ToolAttemptRow = {
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

export type RuntimeInfo = {
  execution_enabled: boolean;
  default_provider: string;
  providers: Array<{
    name: string;
    configured: boolean;
    default_model?: string | null;
  }>;
  tools: Array<{
    name: string;
    description: string;
    effect: string;
    version?: string;
    timeout_s?: number;
    replay_policy?: string | null;
  }>;
  workspaces: Array<{ id: string; name: string }>;
  defaults: { approval: "ask"; allow_write: false };
};

export type VerificationInput = {
  output?: {
    contains?: string[];
    not_contains?: string[];
  };
  files?: Array<{
    path: string;
    exists?: boolean;
    contains?: string[];
  }>;
  commands?: Array<{
    command: string;
    contains?: string[];
  }>;
  max_retries?: number;
  on_failure?: "failed" | "require_human";
};

export type CreateRunInput = {
  message: string;
  session_id?: string;
  system?: string;
  model?: string;
  approval?: "ask" | "auto" | "never";
  workspace_id?: string;
  cwd?: string;
  allow_write?: boolean;
  verification?: VerificationInput;
};

export type RunAccepted = {
  run_id: string;
  session_id: string;
  status: "accepted";
  run: RunRow | null;
};

export type ToolRecoveryDecision = "mark_succeeded" | "skip" | "retry";

export type VerificationAttempt = {
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
    status: "passed" | "failed" | "needs_review" | "pending" | "unverified" | "cancelled" | "interrupted";
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
  workspace_changes: Array<{
    invocation_id: string;
    tool: string;
    path?: string | null;
    status: string;
    changed: boolean;
    expected_version?: string | null;
    resulting_version?: string | null;
    arguments_sha256: string;
    artifact_id?: string | null;
    finished_at?: string | null;
  }>;
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
  source: {
    run_updated_at?: string | null;
    max_global_seq: number;
    event_count: number;
    tool_count: number;
    approval_count: number;
    artifact_count: number;
  };
};

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
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

function postJson<T>(path: string, body?: unknown): Promise<T> {
  return requestJson<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
}

export const api = {
  health: () => requestJson<HealthResponse>("/api/health"),
  runtime: () => requestJson<RuntimeInfo>("/api/runtime"),
  sessions: (limit = 200) => requestJson<SessionRow[]>(`/api/sessions?limit=${limit}`),
  transcript: (sessionId: string) =>
    requestJson<TranscriptTurn[]>(`/api/sessions/${sessionId}/transcript`),
  runs: (sessionId?: string, limit = 200) => {
    const query = new URLSearchParams({ limit: String(limit) });
    if (sessionId) query.set("session_id", sessionId);
    return requestJson<RunRow[]>(`/api/runs?${query}`);
  },
  run: (runId: string) => requestJson<RunRow>(`/api/runs/${runId}`),
  report: (runId: string) => requestJson<RunReport>(`/api/runs/${runId}/report`),
  messages: (runId: string) =>
    requestJson<MessageRow[]>(`/api/runs/${runId}/messages`),
  events: (runId: string) =>
    requestJson<EventRow[]>(`/api/runs/${runId}/events?limit=2000`),
  approvals: (runId: string) =>
    requestJson<ApprovalRow[]>(`/api/runs/${runId}/approvals`),
  toolInvocations: (runId: string) =>
    requestJson<ToolInvocationRow[]>(`/api/runs/${runId}/tool-invocations`),
  toolInvocation: (invocationId: string) =>
    requestJson<ToolInvocationRow>(`/api/tool-invocations/${invocationId}`),
  resolveToolRecovery: (
    invocation: ToolInvocationRow,
    decision: ToolRecoveryDecision
  ) =>
    postJson<{
      invocation_id: string;
      run_id: string;
      decision: ToolRecoveryDecision;
      invocation: ToolInvocationRow;
    }>(`/api/tool-invocations/${invocation.id}/resolution`, {
      decision,
      arguments_sha256: invocation.arguments_sha256,
    }),
  createRun: (input: CreateRunInput) => postJson<RunAccepted>("/api/runs", input),
  cancelRun: (runId: string) =>
    postJson<{ run_id: string; run: RunRow }>(`/api/runs/${runId}/cancel`),
  resumeRun: (runId: string, input?: string) =>
    postJson<RunAccepted>(`/api/runs/${runId}/resume`, { input }),
  decideApproval: (
    approval: ApprovalRow,
    decision: "deny" | "allow_once" | "allow_run"
  ) => {
    if (!approval.invocation_id || !approval.arguments_sha256) {
      return Promise.reject(new Error("审批绑定信息缺失，请刷新后重试"));
    }
    return (
    postJson<{ approval_id: string; run_id: string; decision: string }>(
      `/api/approvals/${approval.id}/decision`,
      {
        decision,
        invocation_id: approval.invocation_id,
        arguments_sha256: approval.arguments_sha256,
      }
    )
    );
  },
};
