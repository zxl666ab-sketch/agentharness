CREATE TABLE procurement_task (
    id varchar(32) PRIMARY KEY,
    reference varchar(64) NOT NULL UNIQUE,
    title varchar(200) NOT NULL,
    category varchar(100) NOT NULL,
    item_name varchar(200) NOT NULL,
    quantity decimal(60, 18) NOT NULL CHECK (quantity > 0),
    unit varchar(50) NOT NULL,
    schema_version integer NOT NULL CHECK (schema_version IN (1, 2)),
    specifications json NOT NULL,
    constraints json NOT NULL,
    status varchar(40) NOT NULL,
    retryable boolean NOT NULL DEFAULT false,
    retry_message varchar(500),
    session_id varchar(32),
    analysis_run_id varchar(32),
    current_snapshot_id varchar(32),
    approved_quote_id varchar(32),
    generation integer NOT NULL DEFAULT 1,
    version bigint NOT NULL DEFAULT 0,
    created_at datetime(6) NOT NULL,
    updated_at datetime(6) NOT NULL
)
 ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX idx_procurement_task_updated ON procurement_task(updated_at DESC);

CREATE TABLE business_artifact (
    id varchar(34) PRIMARY KEY,
    owner_prefix varchar(32) NOT NULL CHECK (owner_prefix = 'java-business'),
    kind varchar(80) NOT NULL,
    task_id varchar(32) REFERENCES procurement_task(id) ON DELETE CASCADE,
    sha256 varchar(64) NOT NULL,
    locator varchar(300) NOT NULL,
    filename varchar(255) NOT NULL,
    content_type varchar(150) NOT NULL,
    size_bytes bigint NOT NULL CHECK (size_bytes >= 0),
    metadata json NOT NULL DEFAULT (JSON_OBJECT()),
    created_at datetime(6) NOT NULL
)
 ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX idx_business_artifact_task ON business_artifact(task_id, created_at);
CREATE INDEX idx_business_artifact_sha ON business_artifact(sha256);
CREATE INDEX idx_business_artifact_locator ON business_artifact(locator);

CREATE TABLE procurement_attachment (
    id varchar(32) PRIMARY KEY,
    task_id varchar(32) NOT NULL REFERENCES procurement_task(id) ON DELETE CASCADE,
    artifact_id varchar(34) NOT NULL REFERENCES business_artifact(id),
    filename varchar(255) NOT NULL,
    sha256 varchar(64) NOT NULL,
    content_type varchar(150) NOT NULL,
    size_bytes bigint NOT NULL,
    created_at datetime(6) NOT NULL,
    UNIQUE(task_id, sha256)
)
 ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE procurement_quote (
    id varchar(32) PRIMARY KEY,
    task_id varchar(32) NOT NULL REFERENCES procurement_task(id) ON DELETE CASCADE,
    source_artifact_id varchar(34) NOT NULL REFERENCES business_artifact(id),
    supplier_name varchar(300) NOT NULL,
    source_filename varchar(255) NOT NULL,
    source_kind varchar(20) NOT NULL,
    source_sha256 varchar(64) NOT NULL,
    extracted json NOT NULL,
    status varchar(40) NOT NULL,
    review_count integer NOT NULL DEFAULT 0,
    parser_version varchar(100) NOT NULL,
    processing_ms decimal(20, 3) NOT NULL,
    created_at datetime(6) NOT NULL,
    updated_at datetime(6) NOT NULL,
    UNIQUE(task_id, source_sha256)
)
 ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX idx_procurement_quote_task ON procurement_quote(task_id, created_at);

CREATE TABLE quote_correction (
    id varchar(32) PRIMARY KEY,
    task_id varchar(32) NOT NULL REFERENCES procurement_task(id) ON DELETE CASCADE,
    quote_id varchar(32) NOT NULL REFERENCES procurement_quote(id) ON DELETE CASCADE,
    field_name varchar(100) NOT NULL,
    old_value json,
    new_value json,
    actor varchar(100) NOT NULL,
    created_at datetime(6) NOT NULL
)
 ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE agent_binding (
    id varchar(32) PRIMARY KEY,
    task_id varchar(32) NOT NULL REFERENCES procurement_task(id) ON DELETE CASCADE,
    session_id varchar(32) NOT NULL,
    run_id varchar(32) NOT NULL,
    generation integer NOT NULL,
    created_at datetime(6) NOT NULL,
    UNIQUE(task_id, generation),
    UNIQUE(run_id)
)
 ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE comparison_snapshot (
    id varchar(32) PRIMARY KEY,
    task_id varchar(32) NOT NULL REFERENCES procurement_task(id) ON DELETE CASCADE,
    run_id varchar(32) NOT NULL,
    snapshot_version integer NOT NULL,
    task_version bigint NOT NULL,
    input_sha256 varchar(64) NOT NULL,
    result json NOT NULL,
    artifact_id varchar(34) NOT NULL REFERENCES business_artifact(id),
    created_at datetime(6) NOT NULL,
    UNIQUE(task_id, snapshot_version)
)
 ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE pending_decision (
    id varchar(32) PRIMARY KEY,
    operation_id varchar(36) NOT NULL UNIQUE,
    task_id varchar(32) NOT NULL REFERENCES procurement_task(id) ON DELETE CASCADE,
    run_id varchar(32) NOT NULL,
    tool_name varchar(100) NOT NULL CHECK (tool_name = 'procurement_approve_supplier'),
    task_version bigint NOT NULL,
    snapshot_id varchar(32) NOT NULL REFERENCES comparison_snapshot(id),
    input_sha256 varchar(64) NOT NULL,
    decision varchar(20) NOT NULL CHECK (decision IN ('approved', 'no_award')),
    quote_id varchar(32) REFERENCES procurement_quote(id),
    note_hash varchar(64) NOT NULL,
    approval_id varchar(32),
    approval_arguments_sha256 varchar(64),
    approval_decision varchar(20),
    approval_at datetime(6),
    status varchar(30) NOT NULL,
    created_at datetime(6) NOT NULL,
    updated_at datetime(6) NOT NULL
)
 ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX idx_pending_decision_task ON pending_decision(task_id, created_at DESC);

CREATE TABLE procurement_decision (
    id varchar(32) PRIMARY KEY,
    pending_decision_id varchar(32) NOT NULL UNIQUE REFERENCES pending_decision(id),
    task_id varchar(32) NOT NULL UNIQUE REFERENCES procurement_task(id) ON DELETE CASCADE,
    snapshot_id varchar(32) NOT NULL REFERENCES comparison_snapshot(id),
    quote_id varchar(32) REFERENCES procurement_quote(id),
    run_id varchar(32) NOT NULL,
    approval_id varchar(32) NOT NULL,
    decision varchar(20) NOT NULL CHECK (decision IN ('approved', 'no_award')),
    note text,
    actor varchar(100) NOT NULL,
    created_at datetime(6) NOT NULL
)
 ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE procurement_audit_event (
    id varchar(32) PRIMARY KEY,
    task_id varchar(32) NOT NULL REFERENCES procurement_task(id) ON DELETE CASCADE,
    quote_id varchar(32) REFERENCES procurement_quote(id),
    run_id varchar(32),
    event_type varchar(100) NOT NULL,
    actor varchar(100) NOT NULL,
    payload json NOT NULL,
    created_at datetime(6) NOT NULL
)
 ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX idx_procurement_audit_task ON procurement_audit_event(task_id, created_at, id);

CREATE TABLE agent_command (
    operation_id varchar(36) PRIMARY KEY,
    operation_type varchar(60) NOT NULL,
    aggregate_id varchar(32) NOT NULL REFERENCES procurement_task(id) ON DELETE CASCADE,
    generation integer NOT NULL,
    expected_task_version bigint NOT NULL,
    payload_sha256 varchar(64) NOT NULL,
    payload json NOT NULL,
    status varchar(30) NOT NULL,
    attempt_count integer NOT NULL DEFAULT 0,
    next_attempt_at datetime(6) NOT NULL,
    accepted_at datetime(6) NOT NULL,
    completed_at datetime(6),
    last_error varchar(1000),
    result json
)
 ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX idx_agent_command_dispatch ON agent_command(status, next_attempt_at, accepted_at);
CREATE UNIQUE INDEX uq_agent_command_generation ON agent_command(aggregate_id, generation, operation_type, payload_sha256);

CREATE TABLE idempotency_record (
    scope varchar(80) NOT NULL,
    idempotency_key varchar(200) NOT NULL,
    payload_sha256 varchar(64) NOT NULL,
    operation_id varchar(36),
    http_status integer,
    response json,
    created_at datetime(6) NOT NULL,
    expires_at datetime(6) NOT NULL,
    PRIMARY KEY(scope, idempotency_key)
)
 ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE runtime_report_projection (
    id varchar(32) PRIMARY KEY,
    task_id varchar(32) NOT NULL REFERENCES procurement_task(id) ON DELETE CASCADE,
    run_id varchar(32) NOT NULL,
    evidence_sha256 varchar(64) NOT NULL,
    report json NOT NULL,
    created_at datetime(6) NOT NULL,
    UNIQUE(task_id, run_id, evidence_sha256)
)
 ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
