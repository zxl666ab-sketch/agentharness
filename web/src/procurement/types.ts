export const PROCUREMENT_STATUSES = [
  "draft",
  "collecting",
  "review",
  "ready",
  "analyzed",
  "approval_pending",
  "approved",
  "no_award",
  "cancelled",
] as const;

export type ProcurementBusinessStatus = (typeof PROCUREMENT_STATUSES)[number];

// Compatibility only: new execution state belongs to AiTaskStatus, not procurement status.
export type ProcurementStatus = ProcurementBusinessStatus | "analyzing";

export const AI_TASK_STATUSES = [
  "PENDING",
  "DISPATCHING",
  "RUNNING",
  "SUCCEEDED",
  "FAILED",
  "RETRYING",
  "CANCELLED",
] as const;

export type AiTaskStatus = (typeof AI_TASK_STATUSES)[number];

export const AI_STATUS_TRANSITIONS: Record<AiTaskStatus, readonly AiTaskStatus[]> = {
  PENDING: ["DISPATCHING", "CANCELLED"],
  DISPATCHING: ["RUNNING", "FAILED", "CANCELLED"],
  RUNNING: ["SUCCEEDED", "FAILED", "CANCELLED"],
  SUCCEEDED: [],
  FAILED: ["RETRYING", "CANCELLED"],
  RETRYING: ["RUNNING", "FAILED", "CANCELLED"],
  CANCELLED: [],
};

export const AI_TASK_TYPES = ["QUOTE_ANALYSIS"] as const;
export type AiTaskType = (typeof AI_TASK_TYPES)[number];

export const AI_TASK_STEPS = [
  "INPUT_VALIDATE",
  "ARTIFACT_FETCH",
  "QUOTE_PARSE",
  "RULE_ANALYSIS",
  "EXPLANATION",
  "RESULT_PUBLISH",
] as const;
export type AiTaskStep = (typeof AI_TASK_STEPS)[number];

export type AiErrorCategory =
  | "VALIDATION"
  | "BUSINESS"
  | "PROVIDER"
  | "TRANSPORT"
  | "INTERNAL";

export type AiTaskView = {
  ai_task_id: string;
  business_id: string;
  generation: number;
  status: AiTaskStatus;
  task_type: AiTaskType;
  trace_id: string;
  current_step: AiTaskStep | null;
  progress: number;
  retry_count: number;
  max_retries?: number;
  retryable: boolean;
  operation_id?: string | null;
  result_id?: string | null;
  stale: boolean;
  stale_reason?: string | null;
  error_category?: AiErrorCategory | null;
  error_code: string | null;
  error_message?: string | null;
  assignee?: string | null;
  created_at: string;
  updated_at: string;
  started_at?: string | null;
  finished_at?: string | null;
};

export type AiStepStatus = "PENDING" | "RUNNING" | "SUCCEEDED" | "FAILED" | "SKIPPED";

export type AiTaskRecordView = {
  record_id: string;
  ai_task_id: string;
  operation_id: string;
  attempt: number;
  sequence: number;
  step: AiTaskStep;
  status: AiStepStatus;
  summary?: string | null;
  error_category?: AiErrorCategory | null;
  error_code?: string | null;
  error_message?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  duration_ms?: number | null;
  created_at: string;
};

export type AiResultView = {
  ai_result_id: string;
  ai_task_id: string;
  business_id: string;
  generation: number;
  input_sha256: string;
  result_sha256: string;
  raw_result?: Record<string, unknown> | null;
  structured_result: Record<string, unknown>;
  sources: Array<{
    artifact_id: string;
    locator: string;
    excerpt: string;
    confidence: number;
    method: string;
  }>;
  provider?: string | null;
  model?: string | null;
  prompt_version: string;
  parser_version?: string | null;
  stale: boolean;
  stale_reason?: string | null;
  created_at: string;
};

export type AiTaskDetail = AiTaskView & {
  records: AiTaskRecordView[];
  result: AiResultView | null;
};

export type AiTaskPage = {
  items: AiTaskView[];
  page: number;
  size: number;
  total: number;
};

export const REVIEW_STATUSES = ["PENDING", "APPROVED", "REJECTED", "NO_AWARD", "STALE"] as const;
export type ReviewStatus = (typeof REVIEW_STATUSES)[number];

export const REVIEW_ACTIONS = [
  "APPROVE_SUGGESTION",
  "REVISE_AND_APPROVE",
  "REJECT_AND_RETRY",
  "NO_AWARD",
] as const;
export type ReviewAction = (typeof REVIEW_ACTIONS)[number];

export type ReviewView = {
  review_id: string;
  business_id: string;
  ai_task_id: string;
  ai_result_id: string;
  status: ReviewStatus;
  priority: number;
  risk_flags: string[];
  waiting_since: string;
  version: number;
  generation?: number;
  task_version?: number;
  snapshot_id?: string;
  input_sha256?: string;
  suggested_quote_id?: string | null;
  final_quote_id?: string | null;
  action?: ReviewAction | null;
  reason?: string | null;
  actor?: string | null;
  revisions?: Record<string, unknown> | null;
  evidence_sha256?: string | null;
  pending_decision_id?: string | null;
  decision_id?: string | null;
  stale_reason?: string | null;
  acted_at?: string | null;
  created_at: string;
  updated_at: string;
};

export type ReviewPage = {
  items: ReviewView[];
  page: number;
  size: number;
  total: number;
};

export type ReviewDetail = ReviewView & {
  ai_result: AiResultView;
  comparison: ComparisonSnapshot;
  history: ReviewView[];
};

export type ReviewActionInput = {
  action: ReviewAction;
  expected_version: number;
  actor: string;
  final_quote_id?: string | null;
  revisions?: Record<string, unknown>;
  reason?: string | null;
};

export type RequirementSpecification = {
  label: string;
  type: "number" | "text" | "boolean";
  value?: string | number | boolean;
  unit?: string;
  match: "exact" | "tolerance" | "range" | "gte" | "lte";
  priority: "hard" | "preference";
  tolerance?: string | number;
  min?: string | number;
  max?: string | number;
};

export type FieldMeta = {
  label: string;
  kind: "text" | "currency" | "decimal" | "integer" | "rate" | "boolean" | "date";
  required: boolean;
};

export type QuoteField = {
  value: string | number | boolean | null;
  confidence: number;
  status: "accepted" | "needs_review" | "corrected";
  original_value?: string | number | boolean | null;
  source: {
    document_kind: string;
    locator: string;
    excerpt: string;
    method: string;
  };
  conflicts?: Array<{
    value: string | number | boolean | null;
    confidence: number;
    source: {
      document_kind: string;
      locator: string;
      excerpt: string;
      method: string;
    };
  }>;
  correction?: { actor: string; corrected_at: string };
};

export type ProcurementQuote = {
  id: string;
  request_id: string;
  supplier_name: string;
  source_filename: string;
  source_kind: "xlsx" | "pdf";
  source_artifact_id: string;
  source_sha256: string;
  extracted: {
    schema_version: number;
    parser_version: string;
    document_kind: string;
    fields: Record<string, QuoteField>;
    specifications?: Record<string, QuoteField & { label?: string; unit?: string }>;
    processing_ms: number;
  };
  status: "needs_review" | "ready";
  review_count: number;
  review_fields: string[];
  parser_version: string;
  processing_ms: number;
  created_at: string;
  updated_at: string;
};

export type ComparisonQuote = {
  quote_id: string;
  supplier_name: string;
  eligible: boolean;
  exclusion_reasons: Array<{ code: string; message: string }>;
  warnings: string[];
  match: {
    item: string;
    quoted_description: string;
    passed: boolean;
    spec_checks: Array<{
      field: string;
      label?: string;
      expected: string;
      actual: string;
      tolerance: string;
      match?: string;
      priority?: string;
      passed: boolean;
    }>;
  };
  commercial: {
    moq: number;
    lead_time_days: number;
    tax_rate: string;
    tax_included: boolean;
    shipping_included: boolean;
    supports_invoice: boolean;
    payment_terms?: string | null;
    valid_until?: string | null;
  };
  cost: {
    quote_currency: string;
    base_currency: string;
    fx_rate: string;
    quoted_price: string;
    price_basis: number;
    normalized_unit_quote_currency: string;
    goods_before_tax_quote_currency: string;
    tax_quote_currency: string;
    freight_quote_currency: string;
    landed_total_quote_currency: string;
    landed_total_base: string;
    landed_unit_base: string;
  };
  rank: number | null;
  score: string | null;
};

export type ComparisonResult = {
  schema_version: number;
  ruleset_version: string;
  request_id: string;
  base_currency: string;
  quantity: number;
  quotes: ComparisonQuote[];
  eligible_count: number;
  excluded_count: number;
  recommended_quote_id: string | null;
  recommendation_explanation: string[];
  approval?: {
    id: string;
    invocation_id: string;
    arguments_sha256: string;
    status: string;
  };
};

export type ComparisonSnapshot = {
  id: string;
  request_id: string;
  run_id: string;
  version: number;
  input_sha256: string;
  result: ComparisonResult;
  artifact_id: string;
  created_at: string;
};

export type ProcurementDecision = {
  id: string;
  request_id: string;
  snapshot_id: string;
  quote_id: string | null;
  run_id: string;
  approval_id: string;
  decision: "approved" | "no_award";
  note?: string | null;
  actor: string;
  created_at: string;
};

export type ProcurementRequestSummary = {
  id: string;
  reference: string;
  title: string;
  schema_version?: 1 | 2;
  category: string;
  item_name: string;
  quantity: number | string;
  unit: string;
  specifications: Record<string, string | number | boolean | RequirementSpecification>;
  constraints: Record<string, unknown>;
  status: ProcurementStatus;
  requirement_confirmed: boolean;
  session_id: string | null;
  analysis_run_id?: string | null;
  current_snapshot_id?: string | null;
  approved_quote_id?: string | null;
  quote_count: number;
  unresolved_field_count: number;
  decision?: ProcurementDecision | null;
  created_at: string;
  updated_at: string;
};

export type ProcurementRequest = ProcurementRequestSummary & {
  attachments: Array<{
    filename: string;
    artifact_id: string;
    sha256: string;
    content_type: string;
    size_bytes: number;
  }>;
  quotes: ProcurementQuote[];
  comparison: ComparisonSnapshot | null;
  decision: ProcurementDecision | null;
};

export type ProcurementRunAccepted = {
  operation_id: string;
  purchase_request_id: string;
  session_id: string | null;
  run_id: string | null;
  status: "accepted";
  location: string;
};

export type ProcurementOperation = {
  operation_id: string;
  operation_type: string;
  aggregate_id: string;
  generation: number;
  expected_task_version: number;
  payload_sha256: string;
  status: "pending" | "dispatching" | "accepted" | "completed" | "failed";
  attempt_count: number;
  retryable: boolean;
  last_error?: string | null;
  result?: Record<string, unknown> | null;
};

export type ProcurementMeta = {
  category: string;
  categories?: string[];
  requirement_schema_versions?: number[];
  parser_version: string;
  ruleset_version: string;
  ruleset_versions?: string[];
  max_file_bytes: number;
  max_conversation_upload_bytes: number;
  max_quotes_per_request: number;
  allowed_extensions: string[];
  field_meta: Record<string, FieldMeta>;
};

export type ProcurementModelConfig = {
  provider: "procurement_fake" | "openai";
  model: string;
  base_url: string | null;
  api_mode: "auto" | "chat" | "responses";
  reasoning_effort: "auto" | "none" | "minimal" | "low" | "medium" | "high" | "max";
  api_key_configured: boolean;
  api_key_preview: string | null;
  input_price_per_million_usd: number | null;
  output_price_per_million_usd: number | null;
  cached_input_price_per_million_usd: number | null;
  max_cost_usd: number | null;
};

export type ProcurementModelConfigUpdate = {
  provider: ProcurementModelConfig["provider"];
  model: string;
  base_url: string;
  api_key?: string;
  api_mode: ProcurementModelConfig["api_mode"];
  reasoning_effort: ProcurementModelConfig["reasoning_effort"];
  input_price_per_million_usd: number | null;
  output_price_per_million_usd: number | null;
  cached_input_price_per_million_usd: number | null;
  max_cost_usd: number | null;
};

export type CreateProcurementRequest = {
  schema_version?: 1 | 2;
  title: string;
  category: string;
  item_name: string;
  quantity: number | string;
  unit: string;
  specifications: Record<string, string | number | boolean | RequirementSpecification>;
  constraints: {
    base_currency: string;
    fx_rates: Record<string, number>;
    max_lead_days: number;
    invoice_required: boolean;
    size_tolerance_mm?: number;
    thickness_tolerance_um?: number;
    max_landed_unit_cost?: number;
    destination: string;
    required_delivery_date?: string;
  };
};

export type SupplierStatus = "ACTIVE" | "PAUSED" | "BLACKLISTED";

export type SupplierPerformance = {
  level: string;
  score: string;
  win_rate_score: string;
  activity_score: string;
  status_score: string;
  base_score: string;
};

export type SupplierView = {
  id: string;
  name: string;
  contact_person: string | null;
  phone: string | null;
  email: string | null;
  address: string | null;
  main_categories: string | null;
  status: SupplierStatus;
  notes: string | null;
  cooperation_status: string;
  quote_count: number;
  win_count: number;
  win_rate: string;
  performance: SupplierPerformance;
  created_at: string;
  updated_at: string;
};

export type SupplierSaveRequest = {
  name?: string;
  contact_person?: string | null;
  phone?: string | null;
  email?: string | null;
  address?: string | null;
  main_categories?: string | null;
  status?: SupplierStatus | null;
  notes?: string | null;
};

export type SupplierPage = {
  items: SupplierView[];
  page: number;
  size: number;
  total: number;
};

export type SupplierProfileQuote = {
  quote_id: string;
  task_id: string;
  task_reference: string | null;
  item_name: string | null;
  source_filename: string;
  created_at: string;
};

export type SupplierProfile = {
  id: string;
  name: string;
  contact_person: string | null;
  phone: string | null;
  email: string | null;
  address: string | null;
  main_categories: string | null;
  status: SupplierStatus;
  notes: string | null;
  cooperation_status: string;
  quote_count: string;
  win_count: string;
  win_rate: string;
  performance: SupplierPerformance;
  items: string[];
  recent_quotes: SupplierProfileQuote[];
  created_at: string;
  updated_at: string;
};

export type OrderStatus = "PENDING_SHIPMENT" | "SHIPPED" | "RECEIVED" | "CLOSED";
export type SettlementStatus = "UNSETTLED" | "SETTLED" | "PAID";

export type OrderArtifact = {
  id: string;
  kind: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  sha256: string;
  created_at: string;
};

export type OrderSettlement = {
  id: string;
  settlement_no: string;
  total_amount: string;
  status: SettlementStatus;
  paid_at: string | null;
  notes: string | null;
  version: number;
  created_at: string;
  updated_at: string;
};

export type OrderView = {
  id: string;
  task_id: string;
  order_no: string;
  supplier_name: string;
  item_name: string;
  quantity: string;
  unit: string;
  landed_total: string | null;
  status: OrderStatus;
  received_quantity: string | null;
  arrival_date: string | null;
  notes: string | null;
  version: number;
  task_reference: string | null;
  task_title: string | null;
  artifacts: OrderArtifact[];
  settlement: OrderSettlement | null;
  created_at: string;
  updated_at: string;
};

export type OrderPage = {
  items: OrderView[];
  page: number;
  size: number;
  total: number;
};

export type SettlementView = {
  id: string;
  order_id: string;
  settlement_no: string;
  supplier_name: string;
  total_amount: string;
  status: SettlementStatus;
  paid_at: string | null;
  notes: string | null;
  version: number;
  order_no: string | null;
  task_id: string | null;
  created_at: string;
  updated_at: string;
};

export type SettlementPage = {
  items: SettlementView[];
  page: number;
  size: number;
  total: number;
};

export type ProcurementAuditReport = {
  schema_version: number;
  evidence_sha256: string;
  request: ProcurementRequestSummary;
  quotes: ProcurementQuote[];
  comparison: ComparisonSnapshot | null;
  decision: ProcurementDecision | null;
  execution_artifacts?: Array<{
    kind: "purchase_order_draft" | "supplier_confirmation_email" | string;
    artifact_id: string;
    sha256: string;
    filename: string;
    content_type: string;
    summary: string;
  }>;
  supplier_history?: {
    request_id: string;
    suppliers: Array<{
      quote_id: string;
      supplier_name: string;
      approved_purchase_count: number;
      records: Array<{
        request_reference: string;
        decision_at: string;
        decision: string;
      }>;
      evidence: string;
    }>;
  };
  audit_events: Array<{
    id: string;
    request_id: string;
    quote_id?: string | null;
    run_id?: string | null;
    type: string;
    actor: string;
    payload: Record<string, unknown>;
    created_at: string;
  }>;
  runtime: {
    session_id: string;
    run_id?: string | null;
    checkpoint_endpoint?: string | null;
    report_endpoint?: string | null;
  };
};

export type EvaluationMetrics = {
  field_extraction: { accuracy: number; correct: number; total: number };
  post_review_fields: { accuracy: number; correct: number; total: number };
  item_matching: { accuracy: number; correct: number; total: number };
  cost_calculation: { accuracy: number; correct: number; total: number };
  hard_constraint_miss: {
    miss_rate: number;
    missed: number;
    expected_violations: number;
    false_positive_count: number;
  };
  incorrect_eligible_selection: { count: number };
  recommendation_accuracy: {
    rate: number;
    correct_runs: number;
    total_runs: number;
    expected_quote_id: string | null;
    observed_quote_id: string | null;
  };
  recommendation_consistency: {
    rate: number;
    consistent_runs: number;
    total_runs: number;
    recommended_quote_id: string | null;
  };
  manual_review: {
    reviewed_fields: number;
    total_fields: number;
    field_rate: number;
    reviewed_quotes: number;
    total_quotes: number;
    quote_rate: number;
  };
  risk_control: { unresolved_eligible_quote_count: number };
  processing: { total_ms: number; average_ms_per_quote: number };
  model_usage: { calls: number; tokens: number; estimated_cost_usd: number; note: string };
};

export type EvaluationResult = {
  schema_version: number;
  dataset: string;
  dataset_label: string;
  truth_sha256: string;
  frozen: boolean;
  case_count: number;
  anomaly_coverage: { count: number; types: string[] };
  approaches: {
    deterministic_baseline: {
      label: string;
      definition: string;
      metrics: EvaluationMetrics;
      raw: Record<string, unknown>;
    };
    agent_assisted: {
      label: string;
      definition: string;
      metrics: EvaluationMetrics;
      raw: Record<string, unknown>;
    };
    human: { label: string; status: string; note: string };
  };
  metrics: EvaluationMetrics;
  acceptance: Record<string, boolean>;
  limitations: string[];
};
