CREATE TABLE review_record (
    id varchar(32) NOT NULL,
    business_id varchar(32) NOT NULL,
    ai_task_id varchar(32) NOT NULL,
    ai_result_id varchar(32) NOT NULL,
    snapshot_id varchar(32) NOT NULL,
    generation integer NOT NULL,
    task_version bigint NOT NULL,
    input_sha256 varchar(64) NOT NULL,
    status varchar(30) NOT NULL,
    priority integer NOT NULL DEFAULT 50,
    risk_flags json NOT NULL,
    waiting_since datetime(6) NOT NULL,
    suggested_quote_id varchar(32),
    final_quote_id varchar(32),
    action varchar(40),
    revisions json,
    reason varchar(2000),
    actor varchar(100),
    evidence_sha256 varchar(64) NOT NULL,
    pending_decision_id varchar(32),
    decision_id varchar(32),
    stale_reason varchar(100),
    acted_at datetime(6),
    created_at datetime(6) NOT NULL,
    updated_at datetime(6) NOT NULL,
    version bigint NOT NULL DEFAULT 0,
    PRIMARY KEY (id),
    CONSTRAINT fk_review_business
        FOREIGN KEY (business_id) REFERENCES procurement_task(id) ON DELETE CASCADE,
    CONSTRAINT fk_review_ai_task
        FOREIGN KEY (ai_task_id) REFERENCES ai_task(id) ON DELETE CASCADE,
    CONSTRAINT fk_review_ai_result
        FOREIGN KEY (ai_result_id) REFERENCES ai_result(id) ON DELETE CASCADE,
    CONSTRAINT fk_review_snapshot
        FOREIGN KEY (snapshot_id) REFERENCES comparison_snapshot(id) ON DELETE CASCADE,
    CONSTRAINT fk_review_suggested_quote
        FOREIGN KEY (suggested_quote_id) REFERENCES procurement_quote(id) ON DELETE SET NULL,
    CONSTRAINT fk_review_final_quote
        FOREIGN KEY (final_quote_id) REFERENCES procurement_quote(id) ON DELETE SET NULL,
    CONSTRAINT fk_review_pending_decision
        FOREIGN KEY (pending_decision_id) REFERENCES pending_decision(id) ON DELETE SET NULL,
    CONSTRAINT fk_review_decision
        FOREIGN KEY (decision_id) REFERENCES procurement_decision(id) ON DELETE SET NULL,
    CONSTRAINT chk_review_generation CHECK (generation >= 1),
    CONSTRAINT chk_review_priority CHECK (priority >= 0 AND priority <= 100),
    CONSTRAINT chk_review_status CHECK (
        status IN ('PENDING', 'APPROVED', 'REJECTED', 'NO_AWARD', 'STALE')
    ),
    CONSTRAINT chk_review_action CHECK (
        action IS NULL OR action IN (
            'APPROVE_SUGGESTION', 'REVISE_AND_APPROVE', 'REJECT_AND_RETRY', 'NO_AWARD'
        )
    ),
    UNIQUE KEY uq_review_ai_result (ai_result_id),
    UNIQUE KEY uq_review_pending_decision (pending_decision_id),
    UNIQUE KEY uq_review_decision (decision_id),
    KEY idx_review_queue (status, priority DESC, waiting_since, id),
    KEY idx_review_business (business_id, created_at, id),
    KEY idx_review_evidence (input_sha256, generation)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
