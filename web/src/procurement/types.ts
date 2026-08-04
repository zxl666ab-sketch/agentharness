export type ProcurementStatus =
  | "draft"
  | "collecting"
  | "review"
  | "ready"
  | "analyzed"
  | "approved"
  | "no_award";

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
  session_id: string;
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
  purchase_request_id: string;
  session_id: string;
  run_id: string;
  status: "accepted";
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
