/** Readonly API client — production build never loads fixtures. */

export type RunRow = {
  id: string;
  session_id: string;
  parent_run_id?: string | null;
  root_run_id: string;
  status: string;
  provider?: string;
  model?: string;
  approval?: string;
  cwd?: string;
  delegate_depth?: number;
  depth?: number;
  child_count?: number;
  user_summary?: string | null;
  actor?: string;
  allow_write?: number;
  error?: string | null;
  output_summary?: string | null;
  usage_json?: string;
  metadata_json?: string;
  steps?: number;
  created_at: string;
  updated_at: string;
  finished_at?: string | null;
};

export type HealthResponse = {
  service: string;
  status: string;
  backend_version?: string;
  api_schema_version?: number;
  api_capabilities?: string[];
  web_build_id?: string | null;
  server_started_at?: string;
  data_dir: string;
  max_global_seq: number;
};

export type ToolCallRow = {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
  arguments_raw?: string;
  status: string;
};

export type MessageRow = {
  id: string;
  role: "system" | "user" | "assistant" | "tool";
  content: string;
  tool_call_id?: string | null;
  name?: string | null;
  tool_calls?: ToolCallRow[] | null;
  created_at: string;
};

export type ApprovalRow = {
  id: string;
  run_id: string;
  tool_call_id: string;
  tool_name: string;
  effect: string;
  arguments_summary?: string | null;
  decision?: string | null;
  created_at: string;
  resolved_at?: string | null;
};

export type CheckpointRow = {
  run_id: string;
  phase: string;
  step: number;
  status: string;
  pending_tool_calls: ToolCallRow[];
  completed_tool_call_ids: string[];
  usage: { input_tokens: number; output_tokens: number; total_tokens: number };
  approval_token?: string | null;
  created_at: string;
};

export type SessionRow = {
  id: string;
  title?: string;
  display_title?: string;
  created_at: string;
  updated_at: string;
  latest_status?: string;
  latest_run_id?: string;
  latest_error?: string | null;
  run_count?: number;
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
  started_at?: string | null;
  finished_at?: string | null;
  evaluation?: Record<string, unknown> | null;
};

export type EventRow = {
  schema_version: number;
  event_id: string;
  global_seq: number;
  run_seq: number;
  session_id: string;
  root_run_id: string;
  run_id: string;
  parent_run_id?: string | null;
  span_id?: string | null;
  parent_span_id?: string | null;
  type: string;
  timestamp: string;
  payload: Record<string, unknown>;
};

export type ContextManifestItemRow = {
  section: string;
  source: string;
  content_hash: string;
  token_estimate: number;
  included: boolean;
  reason: string;
  compression: "none" | "summarized" | "externalized" | "excluded";
  artifact_id?: string | null;
  preview?: string;
};

export type ContextManifestRow = {
  schema_version: number;
  run_id: string;
  model_turn: number;
  budget_tokens: number;
  total_tokens: number;
  token_method: string;
  prefix_fingerprint: string;
  compacted: boolean;
  items: ContextManifestItemRow[];
  created_at: string;
  artifact_id?: string | null;
  event_id?: string;
  global_seq?: number;
};

export type EvidenceRefRow = {
  trace_id?: string;
  run_id?: string;
  span_id?: string | null;
  event_id?: string | null;
  artifact_id?: string | null;
  message_id?: string | null;
  source?: string;
  path?: string | null;
  excerpt?: string;
  sequence?: number | null;
};

export type EvaluationCheckRow = {
  id: string;
  category: string;
  status: "passed" | "failed" | "not_configured" | "error";
  expected?: unknown;
  actual?: unknown;
  hard?: boolean;
  score?: number | null;
  evidence?: EvidenceRefRow[];
  failure_category?: string | null;
  recovery_hint?: string | null;
  message?: string;
};

export type EvaluationReportRow = {
  schema_version: number;
  report_id: string;
  trace_id: string;
  run_id: string;
  policy_id: string;
  policy_version: string;
  mode: "scored" | "health_only" | "unscored";
  passed: boolean | null;
  score: number | null;
  checks: EvaluationCheckRow[];
  first_divergence?: EvidenceRefRow | null;
  hard_failures: number;
  passed_count: number;
  failed_count: number;
  not_configured_count: number;
  deterministic?: boolean;
  evaluated_at?: string;
};

export type TraceSpanRow = {
  span_id: string;
  parent_span_id?: string | null;
  kind: string;
  name: string;
  status: string;
  sequence_start: number;
  sequence_end: number;
  duration_ms?: number | null;
  tool_name?: string | null;
  tool_arguments?: Record<string, unknown>;
  event_ids?: string[];
};

export type AgentTraceRow = {
  trace_id: string;
  run_id: string;
  status: string;
  completeness: "complete" | "partial" | "legacy";
  partial_reasons?: string[];
  provider?: string | null;
  model?: string | null;
  duration_ms?: number | null;
  steps: number;
  event_count: number;
  spans: TraceSpanRow[];
};

export type ProbeFindingRow = {
  probe: string;
  summary: string;
  affected_configuration?: string[];
  evidence?: EvidenceRefRow[];
};

export type DiagnosisReportRow = {
  diagnosis_id: string;
  trace_id: string;
  report_id: string;
  root_cause: string;
  confidence: number;
  first_divergence?: EvidenceRefRow | null;
  evidence: EvidenceRefRow[];
  affected_configuration: string[];
  recommendations: string[];
  probes: ProbeFindingRow[];
  read_only: boolean;
};

export type JudgeSampleRow = {
  sample_id: string;
  score?: number | null;
  passed?: boolean | null;
  confidence?: number;
  rationale?: string;
  evidence?: EvidenceRefRow[];
  dimensions?: Record<string, { score?: number; reason?: string; applicable?: boolean }>;
  abstained?: boolean;
  error?: string | null;
};

export type JudgeEvaluationRow = {
  status: "trusted" | "unverified" | "degraded" | "abstained" | string;
  semantic_evaluation?: Record<string, unknown> | null;
  calibration?: Record<string, unknown> | null;
  samples: JudgeSampleRow[];
  mean_score?: number | null;
  median_score?: number | null;
  variance?: number | null;
  consistency?: number | null;
  attack_resistant?: boolean | null;
  confidence?: number | null;
  dimensions?: Record<string, { score?: number; reason?: string; applicable?: boolean }>;
  provider?: string | null;
  model?: string | null;
};

export type RegressionEvaluationRow = {
  regression_id?: string | null;
  decision_id?: string | null;
  baseline_diff?: Record<string, unknown>;
  report?: Record<string, unknown> | null;
  gate_decision?: Record<string, unknown> | null;
  rerun_statistics?: Record<string, unknown> | null;
};

export type RunEvaluationDetail = {
  schema_version: 2;
  available: boolean;
  run_id: string;
  trace: AgentTraceRow;
  report: EvaluationReportRow | null;
  diagnosis: DiagnosisReportRow | null;
  judge: JudgeEvaluationRow;
  replay: {
    snapshot_id?: string | null;
    artifact_id?: string | null;
    sha256?: string | null;
    evaluation_policy_version?: string | null;
    captured_at?: string | null;
  };
  regression: RegressionEvaluationRow;
  ids: Record<string, string | null>;
  legacy_eval?: Record<string, unknown> | null;
};

const BASE = "";

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

async function postJson<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // Keep the HTTP fallback when the response is not JSON.
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => getJson<HealthResponse>("/api/health"),
  sessions: (limit = 100) => getJson<SessionRow[]>(`/api/sessions?limit=${limit}`),
  session: (id: string) => getJson<SessionRow>(`/api/sessions/${id}`),
  transcript: (sessionId: string) =>
    getJson<TranscriptTurn[]>(`/api/sessions/${sessionId}/transcript`),
  runs: (sessionId?: string, limit = 100, offset = 0) => {
    const q = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    if (sessionId) q.set("session_id", sessionId);
    return getJson<RunRow[]>(`/api/runs?${q}`);
  },
  run: (id: string) => getJson<RunRow>(`/api/runs/${id}`),
  evaluation: (id: string) =>
    getJson<RunEvaluationDetail>(`/api/runs/${id}/evaluation`),
  gradeRun: (id: string, mode: "deterministic" | "ai" = "deterministic") =>
    postJson<{ eval: Record<string, unknown>; run: RunRow }>(
      `/api/runs/${id}/grade`,
      { mode }
    ),
  events: (runId: string, after = 0) =>
    getJson<EventRow[]>(`/api/runs/${runId}/events?after=${after}&limit=2000`),
  tree: (runId: string) => getJson<RunRow[]>(`/api/runs/${runId}/tree`),
  messages: (runId: string) =>
    getJson<MessageRow[]>(`/api/runs/${runId}/messages`),
  contexts: (runId: string) =>
    getJson<ContextManifestRow[]>(`/api/runs/${runId}/contexts`),
  approvals: (runId: string) =>
    getJson<ApprovalRow[]>(`/api/runs/${runId}/approvals`),
  checkpoint: (runId: string) =>
    getJson<CheckpointRow | null>(`/api/runs/${runId}/checkpoint`),
  artifact: (id: string) => getJson<Record<string, unknown>>(`/api/artifacts/${id}`),
};
