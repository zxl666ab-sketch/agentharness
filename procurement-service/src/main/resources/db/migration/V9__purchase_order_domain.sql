-- V9: 采购订单域（K2）
-- 冻结设计：docs/platform-upgrade-design.md 4.1 V9__purchase_order_domain.sql
CREATE TABLE purchase_order (
    id varchar(32) PRIMARY KEY,
    task_id varchar(32) NOT NULL REFERENCES procurement_task(id) ON DELETE CASCADE,
    order_no varchar(64) NOT NULL UNIQUE,     -- PO-YYYYMMDD-XXXX
    supplier_name varchar(300) NOT NULL,
    item_name varchar(200) NOT NULL,
    quantity decimal(60,18) NOT NULL CHECK (quantity > 0),
    unit varchar(50) NOT NULL,
    landed_total decimal(60,18),              -- 到货总价（含税运费汇率）
    status varchar(30) NOT NULL DEFAULT 'PENDING_SHIPMENT'
        CHECK (status IN ('PENDING_SHIPMENT','SHIPPED','RECEIVED','CLOSED')),
    received_quantity decimal(60,18),
    arrival_date datetime(6),
    notes varchar(1000),
    version bigint NOT NULL DEFAULT 0,        -- 乐观锁
    created_at datetime(6) NOT NULL,
    updated_at datetime(6) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE INDEX idx_purchase_order_status ON purchase_order(status, updated_at);
CREATE INDEX idx_purchase_order_task ON purchase_order(task_id, created_at);
CREATE UNIQUE INDEX uq_purchase_order_task ON purchase_order(task_id);   -- 惰性派生并发防重
