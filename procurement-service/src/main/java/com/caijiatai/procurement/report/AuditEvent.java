package com.caijiatai.procurement.report;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

@Entity
@Table(name = "procurement_audit_event")
public class AuditEvent {
    @Id
    @Column(length = 32)
    private String id;
    @Column(name = "task_id", length = 32)
    private String taskId;
    @Column(name = "quote_id", length = 32)
    private String quoteId;
    @Column(name = "run_id", length = 32)
    private String runId;
    @Column(name = "business_type", length = 30)
    private String businessType;
    @Column(name = "business_id", length = 32)
    private String businessId;
    @Column(name = "event_type", nullable = false, length = 100)
    private String eventType;
    @Column(nullable = false, length = 100)
    private String actor;
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(nullable = false, columnDefinition = "json")
    private Map<String, Object> payload = new LinkedHashMap<>();
    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    protected AuditEvent() {}

    /** 任务上下文事件（V11 前语义，保持兼容：task 事件填 task_id）。 */
    public static AuditEvent create(
            String taskId,
            String quoteId,
            String runId,
            String type,
            String actor,
            Map<String, Object> payload) {
        var event = new AuditEvent();
        event.id = java.util.UUID.randomUUID().toString().replace("-", "");
        event.taskId = taskId;
        event.quoteId = quoteId;
        event.runId = runId;
        event.eventType = type;
        event.actor = actor;
        event.payload = new LinkedHashMap<>(payload);
        event.createdAt = Instant.now();
        return event;
    }

    /** V11 通用业务事件：task 上下文 + business 定位列（审计写入纪律，冻结设计 4.1）。 */
    public static AuditEvent create(
            String taskId,
            String quoteId,
            String runId,
            String businessType,
            String businessId,
            String type,
            String actor,
            Map<String, Object> payload) {
        var event = create(taskId, quoteId, runId, type, actor, payload);
        event.businessType = businessType;
        event.businessId = businessId;
        return event;
    }

    /** V11 无任务上下文业务事件（供应商等）：business 定位，task_id 为空。 */
    public static AuditEvent forBusiness(
            String businessType,
            String businessId,
            String type,
            String actor,
            Map<String, Object> payload) {
        return create(null, null, null, businessType, businessId, type, actor, payload);
    }

    public String getId() { return id; }
    public String getTaskId() { return taskId; }
    public String getQuoteId() { return quoteId; }
    public String getRunId() { return runId; }
    public String getBusinessType() { return businessType; }
    public String getBusinessId() { return businessId; }
    public String getEventType() { return eventType; }
    public String getActor() { return actor; }
    public Map<String, Object> getPayload() { return payload; }
    public Instant getCreatedAt() { return createdAt; }
}
