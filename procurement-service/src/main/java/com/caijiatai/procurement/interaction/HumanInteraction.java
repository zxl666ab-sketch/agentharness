package com.caijiatai.procurement.interaction;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import jakarta.persistence.Version;
import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

@Entity
@Table(name = "human_interaction")
public class HumanInteraction {
    @Id
    @Column(length = 32)
    private String id;
    @Column(name = "task_id", nullable = false, length = 32)
    private String taskId;
    @Column(name = "run_id", length = 32)
    private String runId;
    @Column(name = "checkpoint_id", length = 64)
    private String checkpointId;
    @Column(nullable = false)
    private int generation;
    @Column(name = "question_fingerprint", nullable = false, length = 64)
    private String questionFingerprint;
    @Column(nullable = false, length = 40)
    private String kind;
    @Column(nullable = false, columnDefinition = "text")
    private String question;
    @Column(nullable = false, columnDefinition = "text")
    private String reason;
    @Column(name = "business_step", nullable = false, length = 80)
    private String businessStep;
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "related_fields", nullable = false, columnDefinition = "json")
    private List<String> relatedFields = new ArrayList<>();
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "related_artifact_ids", nullable = false, columnDefinition = "json")
    private List<String> relatedArtifactIds = new ArrayList<>();
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "answer_schema", nullable = false, columnDefinition = "json")
    private Map<String, Object> answerSchema = new LinkedHashMap<>();
    @Column(nullable = false, length = 20)
    private String status;
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(columnDefinition = "json")
    private Object answer;
    @Column(name = "answer_note", columnDefinition = "text")
    private String answerNote;
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "answer_artifact_ids", columnDefinition = "json")
    private List<String> answerArtifactIds;
    @Column(name = "answered_by", length = 100)
    private String answeredBy;
    @Column(name = "answered_at")
    private Instant answeredAt;
    @Column(name = "applied_at")
    private Instant appliedAt;
    @Column(name = "expires_at")
    private Instant expiresAt;
    @Column(name = "cancel_reason", length = 500)
    private String cancelReason;
    @Column(name = "operation_id", length = 36)
    private String operationId;
    @Version
    @Column(nullable = false)
    private long version;
    @Column(name = "created_at", nullable = false)
    private Instant createdAt;
    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    protected HumanInteraction() {}

    public static HumanInteraction waiting(
            String taskId, String runId, String checkpointId, int generation,
            String fingerprint, String kind, String question, String reason,
            String businessStep, List<String> relatedFields, List<String> relatedArtifactIds,
            Map<String, Object> answerSchema, Instant expiresAt) {
        var value = new HumanInteraction();
        value.id = UUID.randomUUID().toString().replace("-", "");
        value.taskId = taskId;
        value.runId = runId;
        value.checkpointId = checkpointId;
        value.generation = generation;
        value.questionFingerprint = fingerprint;
        value.kind = kind;
        value.question = question;
        value.reason = reason;
        value.businessStep = businessStep;
        value.relatedFields = new ArrayList<>(relatedFields);
        value.relatedArtifactIds = new ArrayList<>(relatedArtifactIds);
        value.answerSchema = new LinkedHashMap<>(answerSchema);
        value.status = HumanInteractionStatus.WAITING.name();
        value.expiresAt = expiresAt;
        value.createdAt = Instant.now();
        value.updatedAt = value.createdAt;
        return value;
    }

    public String getId() { return id; }
    public String getTaskId() { return taskId; }
    public String getRunId() { return runId; }
    public String getCheckpointId() { return checkpointId; }
    public int getGeneration() { return generation; }
    public String getQuestionFingerprint() { return questionFingerprint; }
    public String getKind() { return kind; }
    public String getQuestion() { return question; }
    public String getReason() { return reason; }
    public String getBusinessStep() { return businessStep; }
    public List<String> getRelatedFields() { return relatedFields; }
    public List<String> getRelatedArtifactIds() { return relatedArtifactIds; }
    public Map<String, Object> getAnswerSchema() { return answerSchema; }
    public String getStatus() { return status; }
    public Object getAnswer() { return answer; }
    public String getAnswerNote() { return answerNote; }
    public List<String> getAnswerArtifactIds() { return answerArtifactIds; }
    public String getAnsweredBy() { return answeredBy; }
    public Instant getAnsweredAt() { return answeredAt; }
    public Instant getAppliedAt() { return appliedAt; }
    public Instant getExpiresAt() { return expiresAt; }
    public String getCancelReason() { return cancelReason; }
    public String getOperationId() { return operationId; }
    public long getVersion() { return version; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }

    public void answer(Object answer, String note, List<String> artifactIds, String actor, String operationId) {
        requireStatus(HumanInteractionStatus.WAITING);
        this.answer = answer;
        answerNote = note;
        answerArtifactIds = new ArrayList<>(artifactIds);
        answeredBy = actor;
        answeredAt = Instant.now();
        this.operationId = operationId;
        status = HumanInteractionStatus.ANSWERED.name();
        updatedAt = answeredAt;
    }

    public void applied() {
        if (HumanInteractionStatus.APPLIED.name().equals(status)) return;
        requireStatus(HumanInteractionStatus.ANSWERED);
        appliedAt = Instant.now();
        status = HumanInteractionStatus.APPLIED.name();
        updatedAt = appliedAt;
    }

    public void stale() {
        if (!HumanInteractionStatus.WAITING.name().equals(status)) return;
        status = HumanInteractionStatus.STALE.name();
        updatedAt = Instant.now();
    }

    public void cancel(String reason) {
        requireStatus(HumanInteractionStatus.WAITING);
        cancelReason = reason;
        status = HumanInteractionStatus.CANCELLED.name();
        updatedAt = Instant.now();
    }

    public void expire() {
        if (!HumanInteractionStatus.WAITING.name().equals(status)) return;
        status = HumanInteractionStatus.EXPIRED.name();
        updatedAt = Instant.now();
    }

    private void requireStatus(HumanInteractionStatus expected) {
        if (!expected.name().equals(status)) {
            throw new IllegalStateException("interaction status is " + status);
        }
    }
}
