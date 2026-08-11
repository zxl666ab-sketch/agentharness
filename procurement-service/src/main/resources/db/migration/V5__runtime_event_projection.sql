CREATE TABLE runtime_event (
    id bigint NOT NULL AUTO_INCREMENT,
    global_seq bigint NOT NULL,
    task_id varchar(32),
    run_id varchar(32),
    type varchar(100) NOT NULL,
    payload json NOT NULL,
    occurred_at datetime(6) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_runtime_event_global_seq (global_seq),
    KEY idx_runtime_event_task (task_id, global_seq)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
