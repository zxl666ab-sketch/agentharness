-- V21: 回填历史审计事件的通用业务定位列（修复 K6 审计中心「业务对象类型」筛选）。
-- 背景：订单/对账事件此前用不带 business_type 的 create() 写入（V11 注释明确 order/settlement 应挂
-- business_type），导致审计页按「采购订单 / 对账单」筛选恒为空。新写入路径已修复，这里补齐存量。
UPDATE procurement_audit_event
SET business_type = 'order',
    business_id = JSON_UNQUOTE(JSON_EXTRACT(payload, '$.order_id'))
WHERE event_type LIKE 'order%'
  AND business_type IS NULL
  AND JSON_UNQUOTE(JSON_EXTRACT(payload, '$.order_id')) IS NOT NULL;

UPDATE procurement_audit_event
SET business_type = 'settlement',
    business_id = JSON_UNQUOTE(JSON_EXTRACT(payload, '$.settlement_id'))
WHERE event_type LIKE 'settlement%'
  AND business_type IS NULL
  AND JSON_UNQUOTE(JSON_EXTRACT(payload, '$.settlement_id')) IS NOT NULL;

UPDATE procurement_audit_event
SET business_type = 'invoice',
    business_id = JSON_UNQUOTE(JSON_EXTRACT(payload, '$.invoice_id'))
WHERE event_type LIKE 'invoice%'
  AND business_type IS NULL
  AND JSON_UNQUOTE(JSON_EXTRACT(payload, '$.invoice_id')) IS NOT NULL;
