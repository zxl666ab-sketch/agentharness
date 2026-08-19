package com.caijiatai.procurement.agent;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

@Entity
@Table(name = "agent_command")
public class AgentCommand {
    @Id
    @Column(name = "operation_id", length = 36)
    private String operationId;
    @Column(name = "operation_type", nullable = false, length = 60)
    private String operationType;
    @Column(name = "aggregate_id", nullable = false, length = 32)
    private String aggregateId;
    @Column(nullable = false)
    private int generation;
    @Column(name = "expected_task_version", nullable = false)
    private long expectedTaskVersion;
    @Column(name = "payload_sha256", nullable = false, length = 64)
    private String payloadSha256;
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(nullable = false, columnDefinition = "json")
    private Map<String, Object> payload = new LinkedHashMap<>();
    @Column(nullable = false, length = 30)
    private String status;
    @Column(name = "attempt_count", nullable = false)
    private int attemptCount;
    @Column(name = "next_attempt_at", nullable = false)
    private Instant nextAttemptAt;
    @Column(name = "accepted_at", nullable = false)
    private Instant acceptedAt;
    @Column(name = "completed_at")
    private Instant completedAt;
    @Column(name = "published_at")
    private Instant publishedAt;
    @Column(name = "last_error", length = 1000)
    private String lastError;
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(columnDefinition = "json")
    private Map<String, Object> result;

    protected AgentCommand() {}

    public static AgentCommand accept(
            String operationType,
            String aggregateId,
            int generation,
            long expectedTaskVersion,
            Map<String, Object> payload) {
        return accept(UUID.randomUUID().toString(), operationType, aggregateId, generation, expectedTaskVersion, payload);
    }

    public static AgentCommand accept(
            String operationId,
            String operationType,
            String aggregateId,
            int generation,
            long expectedTaskVersion,
            Map<String, Object> payload) {
        var command = new AgentCommand();
        command.operationId = operationId;
        command.operationType = operationType;
        command.aggregateId = aggregateId;
        command.generation = generation;
        command.expectedTaskVersion = expectedTaskVersion;
        command.payload = new LinkedHashMap<>(payload);
        command.payloadSha256 = CanonicalJson.sha256(command.payload);
        command.status = "pending";
        command.acceptedAt = Instant.now();
        command.nextAttemptAt = command.acceptedAt;
        return command;
    }

    public String getOperationId() { return operationId; }
    public String getOperationType() { return operationType; }
    public String getAggregateId() { return aggregateId; }
    public int getGeneration() { return generation; }
    public long getExpectedTaskVersion() { return expectedTaskVersion; }
    public String getPayloadSha256() { return payloadSha256; }
    public Map<String, Object> getPayload() { return payload; }
    public String getStatus() { return status; }
    public int getAttemptCount() { return attemptCount; }
    public Instant getNextAttemptAt() { return nextAttemptAt; }
    public Instant getAcceptedAt() { return acceptedAt; }
    public Instant getCompletedAt() { return completedAt; }
    public Instant getPublishedAt() { return publishedAt; }
    public String getLastError() { return lastError; }
    public Map<String, Object> getResult() { return result; }

    public void dispatching() {
        status = "dispatching";
        attemptCount += 1;
    }

    public void accepted(Map<String, Object> result) {
        status = "accepted";
        this.result = result == null ? null : new LinkedHashMap<>(result);
        lastError = null;
        nextAttemptAt = Instant.now().plusSeconds(1);
    }

    public void published() {
        status = "published";
        publishedAt = Instant.now();
        lastError = null;
        nextAttemptAt = Instant.now();
    }

    public void republished() {
        attemptCount += 1;
        published();
    }

    public void complete(Map<String, Object> result) {
        status = "completed";
        this.result = result == null ? Map.of() : new LinkedHashMap<>(result);
        completedAt = Instant.now();
        lastError = null;
    }

    public boolean retry(String error, int maxAttempts) {
        if (attemptCount >= maxAttempts) {
            fail(error);
            return false;
        }
        status = "pending";
        lastError = error == null ? "agent unavailable" : error.substring(0, Math.min(error.length(), 1000));
        long delay = Math.min(60, 1L << Math.min(attemptCount, 6));
        nextAttemptAt = Instant.now().plusSeconds(delay);
        return true;
    }

    public void defer(long seconds) {
        nextAttemptAt = Instant.now().plusSeconds(Math.max(0, seconds));
    }

    public void requeue() {
        if (!"failed".equals(status) && !"cancelled".equals(status)) {
            return;
        }
        status = "pending";
        nextAttemptAt = Instant.now();
        completedAt = null;
        publishedAt = null;
        lastError = null;
    }

    public void fail(String error) {
        status = "failed";
        lastError = error == null ? "agent command failed" : error.substring(0, Math.min(error.length(), 1000));
        completedAt = Instant.now();
    }

    public void cancel() {
        status = "cancelled";
        completedAt = Instant.now();
        lastError = null;
    }
}
