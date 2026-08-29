package com.caijiatai.procurement.ai;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "ai_task_record")
public class AiTaskRecord {
    @Id
    @Column(length = 32)
    private String id;
    @Column(name = "ai_task_id", nullable = false, length = 32)
    private String aiTaskId;
    @Column(name = "operation_id", nullable = false, length = 36)
    private String operationId;
    @Column(nullable = false)
    private int attempt;
    @Column(nullable = false)
    private int sequence;
    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 50)
    private AiTaskStep step;
    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 30)
    private AiStepStatus status;
    @Column(length = 500)
    private String summary;
    @Enumerated(EnumType.STRING)
    @Column(name = "error_category", length = 30)
    private AiErrorCategory errorCategory;
    @Column(name = "error_code", length = 100)
    private String errorCode;
    @Column(name = "error_message", length = 1000)
    private String errorMessage;
    @Column(name = "started_at")
    private Instant startedAt;
    @Column(name = "finished_at")
    private Instant finishedAt;
    @Column(name = "duration_ms")
    private Long durationMs;
    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    protected AiTaskRecord() {}

    public static AiTaskRecord create(
            String aiTaskId,
            String operationId,
            int attempt,
            int sequence,
            AiTaskStep step,
            AiStepStatus status,
            String summary,
            AiErrorCategory errorCategory,
            String errorCode,
            String errorMessage,
            Instant startedAt,
            Instant finishedAt) {
        var record = new AiTaskRecord();
        record.id = UUID.randomUUID().toString().replace("-", "");
        record.aiTaskId = aiTaskId;
        record.operationId = operationId;
        record.attempt = attempt;
        record.sequence = sequence;
        record.step = step;
        record.status = status;
        record.summary = truncate(summary, 500);
        record.errorCategory = errorCategory;
        record.errorCode = truncate(errorCode, 100);
        record.errorMessage = truncate(errorMessage, 1000);
        record.startedAt = startedAt;
        record.finishedAt = finishedAt;
        record.durationMs = duration(startedAt, finishedAt);
        record.createdAt = Instant.now();
        return record;
    }

    /**
     * Terminal reconciliation: an open (PENDING/RUNNING) step must never outlive its
     * task. The creation-time placeholder at sequence 0 is only a "waiting for Agent"
     * promise, so a task that reached SUCCEEDED/FAILED/CANCELLED closes it instead of
     * leaving the workbench spinner turning forever.
     *
     * @return true when this record was still open and has now been closed.
     */
    public boolean close(
            AiStepStatus terminal,
            String summary,
            AiErrorCategory errorCategory,
            String errorCode,
            String errorMessage,
            Instant finishedAt) {
        if (status != AiStepStatus.PENDING && status != AiStepStatus.RUNNING) return false;
        if (terminal != AiStepStatus.FAILED) {
            // Only a failure may rewrite the error triple; a closed success stays clean.
            this.errorCategory = null;
            this.errorCode = null;
            this.errorMessage = null;
        } else {
            this.errorCategory = errorCategory;
            this.errorCode = truncate(errorCode, 100);
            this.errorMessage = truncate(errorMessage, 1000);
        }
        this.status = terminal;
        if (summary != null && !summary.isBlank()) this.summary = truncate(summary, 500);
        var started = startedAt == null ? createdAt : startedAt;
        this.startedAt = started;
        this.finishedAt = finishedAt;
        this.durationMs = duration(started, finishedAt);
        return true;
    }

    public static AiTaskRecord pending(String aiTaskId, String operationId, int attempt) {
        return create(
                aiTaskId,
                operationId,
                attempt,
                0,
                AiTaskStep.INPUT_VALIDATE,
                AiStepStatus.PENDING,
                "等待 Agent 接收任务",
                null,
                null,
                null,
                null,
                null);
    }

    public String getId() { return id; }
    public String getAiTaskId() { return aiTaskId; }
    public String getOperationId() { return operationId; }
    public int getAttempt() { return attempt; }
    public int getSequence() { return sequence; }
    public AiTaskStep getStep() { return step; }
    public AiStepStatus getStatus() { return status; }
    public String getSummary() { return summary; }
    public AiErrorCategory getErrorCategory() { return errorCategory; }
    public String getErrorCode() { return errorCode; }
    public String getErrorMessage() { return errorMessage; }
    public Instant getStartedAt() { return startedAt; }
    public Instant getFinishedAt() { return finishedAt; }
    public Long getDurationMs() { return durationMs; }
    public Instant getCreatedAt() { return createdAt; }

    private static Long duration(Instant startedAt, Instant finishedAt) {
        if (startedAt == null || finishedAt == null) return null;
        return Math.max(0, java.time.Duration.between(startedAt, finishedAt).toMillis());
    }

    private static String truncate(String value, int maxLength) {
        if (value == null) return null;
        return value.substring(0, Math.min(value.length(), maxLength));
    }
}
