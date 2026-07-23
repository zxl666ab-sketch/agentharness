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
  created_at: string;
  updated_at: string;
  latest_status?: string;
  latest_run_id?: string;
  latest_error?: string | null;
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

const BASE = "";

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () =>
    getJson<{ status: string; data_dir: string; max_global_seq: number }>("/api/health"),
  sessions: (limit = 100) => getJson<SessionRow[]>(`/api/sessions?limit=${limit}`),
  session: (id: string) => getJson<SessionRow>(`/api/sessions/${id}`),
  transcript: (sessionId: string) =>
    getJson<TranscriptTurn[]>(`/api/sessions/${sessionId}/transcript`),
  runs: (sessionId?: string, limit = 100) => {
    const q = new URLSearchParams({ limit: String(limit) });
    if (sessionId) q.set("session_id", sessionId);
    return getJson<RunRow[]>(`/api/runs?${q}`);
  },
  run: (id: string) => getJson<RunRow>(`/api/runs/${id}`),
  events: (runId: string, after = 0) =>
    getJson<EventRow[]>(`/api/runs/${runId}/events?after=${after}&limit=2000`),
  tree: (runId: string) => getJson<RunRow[]>(`/api/runs/${runId}/tree`),
  messages: (runId: string) =>
    getJson<MessageRow[]>(`/api/runs/${runId}/messages`),
  approvals: (runId: string) =>
    getJson<ApprovalRow[]>(`/api/runs/${runId}/approvals`),
  checkpoint: (runId: string) =>
    getJson<CheckpointRow>(`/api/runs/${runId}/checkpoint`),
  artifact: (id: string) => getJson<Record<string, unknown>>(`/api/artifacts/${id}`),
};
