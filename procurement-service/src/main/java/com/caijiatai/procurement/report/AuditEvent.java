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
    @Column(name = "task_id", nullable = false, length = 32)
    private String taskId;
    @Column(name = "quote_id", length = 32)
    private String quoteId;
    @Column(name = "run_id", length = 32)
    private String runId;
    @Column(name = "event_type", nullable = false, length = 100)
    private String eventType;
    @Column(nullable = false, length = 100)
    private String actor;
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(nullable = false, columnDefinition = "jsonb")
    private Map<String, Object> payload = new LinkedHashMap<>();
    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    protected AuditEvent() {}

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

    public String getId() { return id; }
    public String getTaskId() { return taskId; }
    public String getQuoteId() { return quoteId; }
    public String getRunId() { return runId; }
    public String getEventType() { return eventType; }
    public String getActor() { return actor; }
    public Map<String, Object> getPayload() { return payload; }
    public Instant getCreatedAt() { return createdAt; }
}
