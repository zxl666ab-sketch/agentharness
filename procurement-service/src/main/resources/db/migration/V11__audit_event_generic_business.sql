-- V11: 审计事件通用业务定位（对抗审查 P1 修复）
-- 冻结设计：docs/platform-upgrade-design.md 4.1 V11__audit_event_generic_business.sql
-- 供应商/对账等事件没有任务上下文，task_id 改可空 + 增加通用业务对象定位列（旧行 task_id 保留）。
ALTER TABLE procurement_audit_event
    MODIFY task_id varchar(32) NULL;
ALTER TABLE procurement_audit_event
    ADD COLUMN business_type varchar(30) NULL,   -- supplier / order / settlement / task
    ADD COLUMN business_id varchar(32) NULL;
CREATE INDEX idx_procurement_audit_business ON procurement_audit_event(business_type, business_id, created_at);
