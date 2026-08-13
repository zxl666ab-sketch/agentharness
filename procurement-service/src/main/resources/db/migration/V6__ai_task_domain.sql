CREATE TABLE ai_task (
    id varchar(32) NOT NULL,
    business_id varchar(32) NOT NULL,
    task_type varchar(40) NOT NULL,
    generation integer NOT NULL,
    task_version bigint NOT NULL,
    status varchar(30) NOT NULL,
    trace_id varchar(32) NOT NULL,
    current_step varchar(50),
    progress decimal(5, 4) NOT NULL DEFAULT 0,
    retry_count integer NOT NULL DEFAULT 0,
    max_retries integer NOT NULL DEFAULT 3,
    retryable boolean NOT NULL DEFAULT false,
    operation_id varchar(36),
    current_result_id varchar(32),
    input_sha256 varchar(64) NOT NULL,
    idempotency_key varchar(128) NOT NULL,
    stale boolean NOT NULL DEFAULT false,
    stale_reason varchar(100),
    error_category varchar(30),
    error_code varchar(100),
    error_message varchar(1000),
    assignee varchar(100),
    started_at datetime(6),
    finished_at datetime(6),
    created_at datetime(6) NOT NULL,
    updated_at datetime(6) NOT NULL,
    version bigint NOT NULL DEFAULT 0,
    PRIMARY KEY (id),
    CONSTRAINT fk_ai_task_business
        FOREIGN KEY (business_id) REFERENCES procurement_task(id) ON DELETE CASCADE,
    CONSTRAINT fk_ai_task_operation
        FOREIGN KEY (operation_id) REFERENCES agent_command(operation_id) ON DELETE SET NULL,
    CONSTRAINT chk_ai_task_generation CHECK (generation >= 1),
    CONSTRAINT chk_ai_task_progress CHECK (progress >= 0 AND progress <= 1),
    CONSTRAINT chk_ai_task_retries CHECK (retry_count >= 0 AND max_retries >= 0),
    CONSTRAINT chk_ai_task_status CHECK (
        status IN ('PENDING', 'DISPATCHING', 'RUNNING', 'SUCCEEDED', 'FAILED', 'RETRYING', 'CANCELLED')
    ),
    CONSTRAINT chk_ai_task_type CHECK (task_type IN ('QUOTE_ANALYSIS')),
    UNIQUE KEY uq_ai_task_trace (trace_id),
    UNIQUE KEY uq_ai_task_operation (operation_id),
    UNIQUE KEY uq_ai_task_idempotency (
        business_id, generation, task_type, idempotency_key
    ),
    KEY idx_ai_task_queue (status, updated_at, id),
    KEY idx_ai_task_business (business_id, created_at, id),
    KEY idx_ai_task_assignee (assignee, status, updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE ai_task_record (
    id varchar(32) NOT NULL,
    ai_task_id varchar(32) NOT NULL,
    operation_id varchar(36) NOT NULL,
    attempt integer NOT NULL,
    sequence integer NOT NULL,
    step varchar(50) NOT NULL,
    status varchar(30) NOT NULL,
    summary varchar(500),
    error_category varchar(30),
    error_code varchar(100),
    error_message varchar(1000),
    started_at datetime(6),
    finished_at datetime(6),
    duration_ms bigint,
    created_at datetime(6) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_ai_task_record_task
        FOREIGN KEY (ai_task_id) REFERENCES ai_task(id) ON DELETE CASCADE,
    CONSTRAINT fk_ai_task_record_operation
        FOREIGN KEY (operation_id) REFERENCES agent_command(operation_id) ON DELETE CASCADE,
    CONSTRAINT chk_ai_task_record_attempt CHECK (attempt >= 1),
    CONSTRAINT chk_ai_task_record_sequence CHECK (sequence >= 0),
    CONSTRAINT chk_ai_task_record_duration CHECK (duration_ms IS NULL OR duration_ms >= 0),
    CONSTRAINT chk_ai_task_record_step CHECK (
        step IN (
            'INPUT_VALIDATE', 'ARTIFACT_FETCH', 'QUOTE_PARSE',
            'RULE_ANALYSIS', 'EXPLANATION', 'RESULT_PUBLISH'
        )
    ),
    CONSTRAINT chk_ai_task_record_status CHECK (
        status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', 'SKIPPED')
    ),
    UNIQUE KEY uq_ai_task_record_sequence (ai_task_id, attempt, sequence),
    KEY idx_ai_task_record_operation (operation_id, attempt, sequence),
    KEY idx_ai_task_record_created (ai_task_id, created_at, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE ai_result (
    id varchar(32) NOT NULL,
    ai_task_id varchar(32) NOT NULL,
    business_id varchar(32) NOT NULL,
    generation integer NOT NULL,
    input_sha256 varchar(64) NOT NULL,
    result_sha256 varchar(64) NOT NULL,
    raw_result json,
    structured_result json NOT NULL,
    sources json NOT NULL,
    provider varchar(100),
    model varchar(200),
    prompt_version varchar(100) NOT NULL,
    parser_version varchar(100),
    stale boolean NOT NULL DEFAULT false,
    stale_reason varchar(100),
    created_at datetime(6) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_ai_result_task
        FOREIGN KEY (ai_task_id) REFERENCES ai_task(id) ON DELETE CASCADE,
    CONSTRAINT fk_ai_result_business
        FOREIGN KEY (business_id) REFERENCES procurement_task(id) ON DELETE CASCADE,
    CONSTRAINT chk_ai_result_generation CHECK (generation >= 1),
    UNIQUE KEY uq_ai_result_task (ai_task_id),
    UNIQUE KEY uq_ai_result_identity (ai_task_id, result_sha256),
    KEY idx_ai_result_business (business_id, generation, created_at),
    KEY idx_ai_result_input (input_sha256)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

ALTER TABLE ai_task
    ADD CONSTRAINT fk_ai_task_current_result
        FOREIGN KEY (current_result_id) REFERENCES ai_result(id) ON DELETE SET NULL;
