package com.caijiatai.procurement.agent;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.Instant;
import java.util.Map;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

@Entity
@Table(name = "runtime_event")
public class RuntimeEvent {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    @Column(name = "global_seq", nullable = false, unique = true)
    private long globalSeq;
    @Column(name = "task_id", length = 32)
    private String taskId;
    @Column(name = "run_id", length = 32)
    private String runId;
    @Column(nullable = false, length = 100)
    private String type;
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(nullable = false, columnDefinition = "json")
    private Map<String, Object> payload;
    @Column(name = "occurred_at", nullable = false)
    private Instant occurredAt;

    protected RuntimeEvent() {}

    public static RuntimeEvent create(long globalSeq, String taskId, String runId, String type,
            Map<String, Object> payload, Instant occurredAt) {
        var event = new RuntimeEvent();
        event.globalSeq = globalSeq;
        event.taskId = taskId;
        event.runId = runId;
        event.type = type;
        event.payload = payload == null ? Map.of() : new java.util.LinkedHashMap<>(payload);
        event.occurredAt = occurredAt;
        return event;
    }

    public Long getId() { return id; }
    public long getGlobalSeq() { return globalSeq; }
    public String getTaskId() { return taskId; }
    public String getRunId() { return runId; }
    public String getType() { return type; }
    public Map<String, Object> getPayload() { return payload; }
    public Instant getOccurredAt() { return occurredAt; }
}
