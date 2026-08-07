export type ProcurementStatus =
  | "draft"
  | "collecting"
  | "review"
  | "ready"
  | "analyzed"
  | "no_award"
  | "approved";

export type FieldMeta = {
  label: string;
  kind: "text" | "currency" | "decimal" | "integer" | "rate" | "boolean" | "date";
  required: boolean;
};

export type QuoteField = {
  value: string | number | boolean | null;
  confidence: number;
  status: "accepted" | "needs_review" | "corrected";
  /** Unknown label/value pairs captured from the source document (read-only). */
  informational?: boolean;
  /** Original source label for informational fields. */
  label?: string;
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
    informational_fields?: Record<string, QuoteField>;
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
      expected: string;
      actual: string;
      tolerance: string;
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
  approval_id: string | null;
  decision: "approved" | "no_award";
  note?: string | null;
  actor: string;
  created_at: string;
};

export type ProcurementRequestSummary = {
  id: string;
  reference: string;
  title: string;
  category: "ecommerce_packaging";
  item_name: string;
  quantity: number;
  unit: "piece";
  specifications: Record<string, string | number>;
  constraints: Record<string, unknown>;
  status: ProcurementStatus;
  session_id: string;
  analysis_run_id?: string | null;
  current_snapshot_id?: string | null;
  approved_quote_id?: string | null;
  quote_count: number;
  unresolved_field_count: number;
  decision?: ProcurementDecision | null;
  knowledge_references?: KnowledgeReference[];
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

export type KnowledgeReference = {
  chunk_id: string;
  chunk_sha256: string;
  request_reference: string;
  decision_at: string;
  supplier_name: string;
  item_name: string;
  specification_summary: string;
  unit_price: string;
  currency: string;
  landed_unit_cost: string;
  lead_days: number | null;
  moq: number | null;
  decision: string;
  source_sha256: string;
  score: string;
  quality_flags: string[];
  note?: string | null;
  text: string;
};

export type KnowledgeFeedbackAction = "viewed" | "adopted";

export type ProcurementRunAccepted = {
  purchase_request_id: string;
  session_id: string;
  run_id: string;
  status: "accepted";
};

export type ProcurementMeta = {
  category: string;
  parser_version: string;
  ruleset_version: string;
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
  ai_review_enabled: boolean;
  review_provider: string | null;
  review_model: string | null;
  review_policy: "off" | "evidence" | "warn" | "gate";
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
  ai_review_enabled?: boolean;
  review_provider?: string | null;
  review_model?: string | null;
  review_policy?: "off" | "evidence" | "warn" | "gate";
};

export type CreateProcurementRequest = {
  title: string;
  category: "ecommerce_packaging";
  item_name: string;
  quantity: number;
  unit: "piece";
  specifications: {
    width_mm: number;
    length_mm: number;
    thickness_um: number;
    material: string;
    color: string;
    print_colors: number;
  };
  constraints: {
    base_currency: string;
    fx_rates: Record<string, number>;
    max_lead_days: number;
    invoice_required: boolean;
    size_tolerance_mm: number;
    thickness_tolerance_um: number;
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


export type ProcurementPurchaseOrder = {
  id: string;
  po_number: string;
  request_id: string;
  reference?: string | null;
  title?: string | null;
  item_name?: string | null;
  quantity?: number | null;
  unit?: string | null;
  supplier_name?: string | null;
  quote_id?: string | null;
  currency?: string | null;
  unit_price_base?: string | null;
  total_amount_base?: string | null;
  snapshot_id?: string | null;
  snapshot_version?: number | null;
  input_sha256?: string | null;
  approval_id?: string | null;
  decision_id?: string | null;
  created_at: string;
  evidence_sha256: string;
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
