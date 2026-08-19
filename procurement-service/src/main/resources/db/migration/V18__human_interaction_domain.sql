CREATE TABLE human_interaction (
    id varchar(32) PRIMARY KEY,
    task_id varchar(32) NOT NULL REFERENCES procurement_task(id) ON DELETE CASCADE,
    run_id varchar(32),
    checkpoint_id varchar(64),
    generation integer NOT NULL,
    question_fingerprint varchar(64) NOT NULL,
    kind varchar(40) NOT NULL,
    question text NOT NULL,
    reason text NOT NULL,
    business_step varchar(80) NOT NULL,
    related_fields json NOT NULL,
    related_artifact_ids json NOT NULL,
    answer_schema json NOT NULL,
    status varchar(20) NOT NULL,
    answer json,
    answer_note text,
    answer_artifact_ids json,
    answered_by varchar(100),
    answered_at datetime(6),
    applied_at datetime(6),
    expires_at datetime(6),
    cancel_reason varchar(500),
    operation_id varchar(36),
    version bigint NOT NULL DEFAULT 0,
    created_at datetime(6) NOT NULL,
    updated_at datetime(6) NOT NULL,
    UNIQUE(task_id, generation, question_fingerprint)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE INDEX idx_human_interaction_task ON human_interaction(task_id, status, created_at DESC);
CREATE INDEX idx_human_interaction_run ON human_interaction(run_id, status);
CREATE UNIQUE INDEX uq_human_interaction_operation ON human_interaction(operation_id);
