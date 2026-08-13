-- V10: 对账付款域（K8）
-- 冻结设计：docs/platform-upgrade-design.md 4.1 V10__settlement_domain.sql
-- 财务数据禁止级联删除：purchase_settlement 外键 ON DELETE RESTRICT
CREATE TABLE purchase_settlement (
    id varchar(32) PRIMARY KEY,
    order_id varchar(32) NOT NULL REFERENCES purchase_order(id) ON DELETE RESTRICT,  -- RESTRICT：财务记录不可随订单级联消失
    settlement_no varchar(64) NOT NULL UNIQUE,   -- ST-YYYYMMDD-XXXX
    supplier_name varchar(300) NOT NULL,
    total_amount decimal(60,18) NOT NULL,        -- 与订单 landed_total 一致（NULL 时禁止派生，见 4.3）
    status varchar(30) NOT NULL DEFAULT 'UNSETTLED'
        CHECK (status IN ('UNSETTLED','SETTLED','PAID')),
    paid_at datetime(6),
    notes varchar(1000),
    version bigint NOT NULL DEFAULT 0,
    created_at datetime(6) NOT NULL,
    updated_at datetime(6) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE INDEX idx_purchase_settlement_status ON purchase_settlement(status, updated_at);
CREATE INDEX idx_purchase_settlement_order ON purchase_settlement(order_id);
