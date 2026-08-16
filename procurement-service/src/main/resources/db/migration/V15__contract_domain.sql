-- P3-2 合同管理：合同实体（金额/交期/供应商由定标结果注入，条款集 JSON，变更留痕）
CREATE TABLE contract (
    id varchar(32) PRIMARY KEY,
    contract_no varchar(64) NOT NULL UNIQUE,
    task_id varchar(32) NOT NULL REFERENCES procurement_task(id) ON DELETE CASCADE,
    order_id varchar(32) NULL REFERENCES purchase_order(id) ON DELETE SET NULL,
    supplier_name varchar(300) NOT NULL,
    item_name varchar(200) NOT NULL,
    amount decimal(60,18) NOT NULL,
    lead_days int NOT NULL,
    status varchar(30) NOT NULL,
    draft_text mediumtext NULL,
    clauses json NULL,
    consistency json NULL,
    change_history json NULL,
    notes varchar(2000) NULL,
    version bigint NOT NULL DEFAULT 0,
    created_at datetime(6) NOT NULL,
    updated_at datetime(6) NOT NULL,
    approved_at datetime(6) NULL,
    UNIQUE KEY uq_contract_task (task_id),
    KEY idx_contract_status (status, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
