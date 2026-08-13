package com.caijiatai.procurement.ai;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import jakarta.persistence.Version;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "ai_task")
public class AiTask {
    @Id
    @Column(length = 32)
    private String id;
    @Column(name = "business_id", nullable = false, length = 32)
    private String businessId;
    @Enumerated(EnumType.STRING)
    @Column(name = "task_type", nullable = false, length = 40)
    private AiTaskType taskType;
    @Column(nullable = false)
    private int generation;
    @Column(name = "task_version", nullable = false)
    private long taskVersion;
    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 30)
    private AiTaskStatus status;
    @Column(name = "trace_id", nullable = false, unique = true, length = 32)
    private String traceId;
    @Enumerated(EnumType.STRING)
    @Column(name = "current_step", length = 50)
    private AiTaskStep currentStep;
    @Column(nullable = false, precision = 5, scale = 4)
    private BigDecimal progress;
    @Column(name = "retry_count", nullable = false)
    private int retryCount;
    @Column(name = "max_retries", nullable = false)
    private int maxRetries;
    @Column(nullable = false)
    private boolean retryable;
    @Column(name = "operation_id", unique = true, length = 36)
    private String operationId;
    @Column(name = "current_result_id", length = 32)
    private String currentResultId;
    @Column(name = "input_sha256", nullable = false, length = 64)
    private String inputSha256;
    @Column(name = "idempotency_key", nullable = false, length = 128)
    private String idempotencyKey;
    @Column(nullable = false)
    private boolean stale;
    @Column(name = "stale_reason", length = 100)
    private String staleReason;
    @Enumerated(EnumType.STRING)
    @Column(name = "error_category", length = 30)
    private AiErrorCategory errorCategory;
    @Column(name = "error_code", length = 100)
    private String errorCode;
    @Column(name = "error_message", length = 1000)
    private String errorMessage;
    @Column(length = 100)
    private String assignee;
    @Column(name = "started_at")
    private Instant startedAt;
    @Column(name = "finished_at")
    private Instant finishedAt;
    @Column(name = "created_at", nullable = false)
    private Instant createdAt;
    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;
    @Version
    @Column(nullable = false)
    private long version;

    protected AiTask() {}

    public static AiTask create(
            String businessId,
            AiTaskType taskType,
            int generation,
            long taskVersion,
            String inputSha256,
            String idempotencyKey,
            String assignee,
            int maxRetries) {
        var now = Instant.now();
        var task = new AiTask();
        task.id = UUID.randomUUID().toString().replace("-", "");
        task.businessId = businessId;
        task.taskType = taskType;
        task.generation = generation;
        task.taskVersion = taskVersion;
        task.status = AiTaskStatus.PENDING;
        task.traceId = UUID.randomUUID().toString().replace("-", "");
        task.progress = BigDecimal.ZERO;
        task.maxRetries = maxRetries;
        task.inputSha256 = inputSha256;
        task.idempotencyKey = idempotencyKey;
        task.assignee = assignee;
        task.createdAt = now;
        task.updatedAt = now;
        return task;
    }

    public String getId() { return id; }
    public String getBusinessId() { return businessId; }
    public AiTaskType getTaskType() { return taskType; }
    public int getGeneration() { return generation; }
    public long getTaskVersion() { return taskVersion; }
    public AiTaskStatus getStatus() { return status; }
    public String getTraceId() { return traceId; }
    public AiTaskStep getCurrentStep() { return currentStep; }
    public BigDecimal getProgress() { return progress; }
    public int getRetryCount() { return retryCount; }
    public int getMaxRetries() { return maxRetries; }
    public boolean isRetryable() { return retryable; }
    public String getOperationId() { return operationId; }
    public String getCurrentResultId() { return currentResultId; }
    public String getInputSha256() { return inputSha256; }
    public String getIdempotencyKey() { return idempotencyKey; }
    public boolean isStale() { return stale; }
    public String getStaleReason() { return staleReason; }
    public AiErrorCategory getErrorCategory() { return errorCategory; }
    public String getErrorCode() { return errorCode; }
    public String getErrorMessage() { return errorMessage; }
    public String getAssignee() { return assignee; }
    public Instant getStartedAt() { return startedAt; }
    public Instant getFinishedAt() { return finishedAt; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }
    public long getVersion() { return version; }

    public void bindOperation(String operationId) {
        this.operationId = operationId;
        updatedAt = Instant.now();
    }

    public void dispatching() {
        if (status == AiTaskStatus.PENDING) {
            transition(AiTaskStatus.DISPATCHING);
        }
    }

    public void deliveryDeferred(String message) {
        if (status != AiTaskStatus.DISPATCHING && status != AiTaskStatus.RETRYING) return;
        errorCategory = AiErrorCategory.TRANSPORT;
        errorCode = "AGENT_UNAVAILABLE";
        errorMessage = truncate(message, 1000);
        updatedAt = Instant.now();
    }

    public void running(AiTaskStep step, BigDecimal progress) {
        if (status == AiTaskStatus.DISPATCHING || status == AiTaskStatus.RETRYING) {
            transition(AiTaskStatus.RUNNING);
        } else if (status != AiTaskStatus.RUNNING) {
            throw new IllegalStateException("AI task cannot start from " + status);
        }
        currentStep = step;
        this.progress = bounded(progress);
        if (startedAt == null) {
            startedAt = Instant.now();
        }
        updatedAt = Instant.now();
    }

    public void succeeded(String resultId) {
        if (status == AiTaskStatus.DISPATCHING || status == AiTaskStatus.RETRYING) {
            running(AiTaskStep.RESULT_PUBLISH, new BigDecimal("0.95"));
        }
        transition(AiTaskStatus.SUCCEEDED);
        currentResultId = resultId;
        currentStep = AiTaskStep.RESULT_PUBLISH;
        progress = BigDecimal.ONE;
        retryable = false;
        finishedAt = Instant.now();
    }

    public void failed(
            AiErrorCategory category,
            String code,
            String message,
            boolean canRetry) {
        if (status == AiTaskStatus.PENDING) {
            transition(AiTaskStatus.DISPATCHING);
        }
        if (status != AiTaskStatus.FAILED) {
            transition(AiTaskStatus.FAILED);
        }
        errorCategory = category;
        errorCode = truncate(code, 100);
        errorMessage = truncate(message, 1000);
        retryable = canRetry && retryCount < maxRetries && !stale;
        finishedAt = Instant.now();
    }

    public void retrying(String nextOperationId) {
        if (!retryable || retryCount >= maxRetries || stale) {
            throw new IllegalStateException("AI task is not retryable");
        }
        transition(AiTaskStatus.RETRYING);
        retryCount += 1;
        operationId = nextOperationId;
        errorCategory = null;
        errorCode = null;
        errorMessage = null;
        retryable = false;
        currentStep = null;
        progress = BigDecimal.ZERO;
        finishedAt = null;
    }

    public void cancelled() {
        transition(AiTaskStatus.CANCELLED);
        retryable = false;
        finishedAt = Instant.now();
    }

    public void markStale(String reason) {
        stale = true;
        staleReason = truncate(reason, 100);
        retryable = false;
        if (status != AiTaskStatus.SUCCEEDED && status != AiTaskStatus.CANCELLED) {
            transition(AiTaskStatus.CANCELLED);
            finishedAt = Instant.now();
        }
        updatedAt = Instant.now();
    }

    private void transition(AiTaskStatus target) {
        if (!status.canTransitionTo(target)) {
            throw new IllegalStateException("Invalid AI task transition " + status + " -> " + target);
        }
        status = target;
        updatedAt = Instant.now();
    }

    private BigDecimal bounded(BigDecimal value) {
        if (value == null || value.compareTo(BigDecimal.ZERO) < 0 || value.compareTo(BigDecimal.ONE) > 0) {
            throw new IllegalArgumentException("AI task progress must be between 0 and 1");
        }
        return value;
    }

    private String truncate(String value, int maxLength) {
        if (value == null) return null;
        return value.substring(0, Math.min(value.length(), maxLength));
    }
}
