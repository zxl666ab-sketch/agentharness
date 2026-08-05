package com.caijiatai.procurement.task;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import jakarta.persistence.Version;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

@Entity
@Table(name = "procurement_task")
public class ProcurementTask {
    @Id
    @Column(length = 32)
    private String id;
    @Column(nullable = false, unique = true, length = 64)
    private String reference;
    @Column(nullable = false, length = 200)
    private String title;
    @Column(nullable = false, length = 100)
    private String category;
    @Column(name = "item_name", nullable = false, length = 200)
    private String itemName;
    @Column(nullable = false, precision = 60, scale = 18)
    private BigDecimal quantity;
    @Column(nullable = false, length = 50)
    private String unit;
    @Column(name = "schema_version", nullable = false)
    private int schemaVersion;
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(nullable = false, columnDefinition = "jsonb")
    private Map<String, Object> specifications = new LinkedHashMap<>();
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(nullable = false, columnDefinition = "jsonb")
    private Map<String, Object> constraints = new LinkedHashMap<>();
    @Column(nullable = false, length = 40)
    private String status;
    @Column(nullable = false)
    private boolean retryable;
    @Column(name = "requirement_confirmed", nullable = false)
    private boolean requirementConfirmed;
    @Column(name = "retry_message", length = 500)
    private String retryMessage;
    @Column(name = "session_id", length = 32)
    private String sessionId;
    @Column(name = "analysis_run_id", length = 32)
    private String analysisRunId;
    @Column(name = "current_snapshot_id", length = 32)
    private String currentSnapshotId;
    @Column(name = "approved_quote_id", length = 32)
    private String approvedQuoteId;
    @Column(nullable = false)
    private int generation;
    @Version
    @Column(nullable = false)
    private long version;
    @Column(name = "created_at", nullable = false)
    private Instant createdAt;
    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    protected ProcurementTask() {}

    public static ProcurementTask draft(String message) {
        var task = new ProcurementTask();
        task.id = java.util.UUID.randomUUID().toString().replace("-", "");
        task.reference = "RFQ-" + java.time.LocalDate.now(java.time.ZoneOffset.UTC).toString().replace("-", "")
                + "-" + task.id.substring(0, 6).toUpperCase(java.util.Locale.ROOT);
        task.title = message.strip().replaceAll("\\s+", " ");
        if (task.title.length() > 80) {
            task.title = task.title.substring(0, 80);
        }
        task.category = "ecommerce_packaging";
        task.itemName = "待识别";
        task.quantity = BigDecimal.ONE;
        task.unit = "piece";
        task.schemaVersion = 1;
        task.status = TaskStatus.DRAFT.wireValue();
        task.generation = 1;
        task.createdAt = Instant.now();
        task.updatedAt = task.createdAt;
        return task;
    }

    public static ProcurementTask structured(
            int schemaVersion,
            String title,
            String category,
            String itemName,
            BigDecimal quantity,
            String unit,
            Map<String, Object> specifications,
            Map<String, Object> constraints) {
        var task = draft(title);
        task.applyRequirement(
                schemaVersion, title, category, itemName, quantity, unit, specifications, constraints);
        task.requirementConfirmed = true;
        task.status = TaskStatus.COLLECTING.wireValue();
        return task;
    }

    public static ProcurementTask reopenFrom(ProcurementTask source) {
        return structured(
                source.schemaVersion,
                source.title + "（重新询价）",
                source.category,
                source.itemName,
                source.quantity,
                source.unit,
                source.specifications,
                source.constraints);
    }

    public String getId() { return id; }
    public String getReference() { return reference; }
    public String getTitle() { return title; }
    public String getCategory() { return category; }
    public String getItemName() { return itemName; }
    public BigDecimal getQuantity() { return quantity; }
    public String getUnit() { return unit; }
    public int getSchemaVersion() { return schemaVersion; }
    public Map<String, Object> getSpecifications() { return specifications; }
    public Map<String, Object> getConstraints() { return constraints; }
    public String getStatus() { return status; }
    public boolean isRetryable() { return retryable; }
    public boolean isRequirementConfirmed() { return requirementConfirmed; }
    public String getRetryMessage() { return retryMessage; }
    public String getSessionId() { return sessionId; }
    public String getAnalysisRunId() { return analysisRunId; }
    public String getCurrentSnapshotId() { return currentSnapshotId; }
    public String getApprovedQuoteId() { return approvedQuoteId; }
    public int getGeneration() { return generation; }
    public long getVersion() { return version; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }

    public void markRetryable(String message) {
        retryable = true;
        retryMessage = message;
        updatedAt = Instant.now();
    }

    public void clearRetryable() {
        retryable = false;
        retryMessage = null;
        updatedAt = Instant.now();
    }

    public void requireRequirementReview() {
        requirementConfirmed = false;
        updatedAt = Instant.now();
    }

    public void confirmRequirement() {
        requirementConfirmed = true;
        updatedAt = Instant.now();
    }

    public void restoreReadyAfterFailedAnalysis() {
        if (TaskStatus.ANALYZING.wireValue().equals(status)) {
            status = TaskStatus.READY.wireValue();
            updatedAt = Instant.now();
        }
    }

    public void bindAgent(String sessionId, String runId) {
        this.sessionId = sessionId;
        this.analysisRunId = runId;
        updatedAt = Instant.now();
    }

    public void setStatus(TaskStatus status) {
        this.status = status.wireValue();
        updatedAt = Instant.now();
    }

    public void applyRequirement(
            int schemaVersion,
            String title,
            String category,
            String itemName,
            BigDecimal quantity,
            String unit,
            Map<String, Object> specifications,
            Map<String, Object> constraints) {
        this.schemaVersion = schemaVersion;
        this.title = title;
        this.category = category;
        this.itemName = itemName;
        this.quantity = quantity;
        this.unit = unit;
        this.specifications = new LinkedHashMap<>(specifications);
        this.constraints = new LinkedHashMap<>(constraints);
        updatedAt = Instant.now();
    }

    public void invalidateAnalysis() {
        currentSnapshotId = null;
        approvedQuoteId = null;
        generation += 1;
        status = TaskStatus.READY.wireValue();
        updatedAt = Instant.now();
    }

    public void useSnapshot(String snapshotId) {
        currentSnapshotId = snapshotId;
        status = TaskStatus.ANALYZED.wireValue();
        updatedAt = Instant.now();
    }

    public void finalizeDecision(String quoteId, boolean noAward) {
        approvedQuoteId = quoteId;
        status = noAward ? TaskStatus.NO_AWARD.wireValue() : TaskStatus.APPROVED.wireValue();
        updatedAt = Instant.now();
    }
}
