package com.caijiatai.procurement.review;

import com.caijiatai.procurement.agent.CanonicalJson;
import com.caijiatai.procurement.ai.AiResult;
import com.caijiatai.procurement.ai.AiTask;
import com.caijiatai.procurement.approval.PendingDecision;
import com.caijiatai.procurement.approval.ProcurementDecision;
import com.caijiatai.procurement.comparison.ComparisonSnapshot;
import com.caijiatai.procurement.task.ProcurementTask;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
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
@Table(name = "review_record")
public class ReviewRecord {
    @Id
    @Column(length = 32)
    private String id;
    @Column(name = "business_id", nullable = false, length = 32)
    private String businessId;
    @Column(name = "ai_task_id", nullable = false, length = 32)
    private String aiTaskId;
    @Column(name = "ai_result_id", nullable = false, unique = true, length = 32)
    private String aiResultId;
    @Column(name = "snapshot_id", nullable = false, length = 32)
    private String snapshotId;
    @Column(nullable = false)
    private int generation;
    @Column(name = "task_version", nullable = false)
    private long taskVersion;
    @Column(name = "input_sha256", nullable = false, length = 64)
    private String inputSha256;
    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 30)
    private ReviewStatus status;
    @Column(nullable = false)
    private int priority;
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "risk_flags", nullable = false, columnDefinition = "json")
    private List<String> riskFlags = new ArrayList<>();
    @Column(name = "waiting_since", nullable = false)
    private Instant waitingSince;
    @Column(name = "suggested_quote_id", length = 32)
    private String suggestedQuoteId;
    @Column(name = "final_quote_id", length = 32)
    private String finalQuoteId;
    @Enumerated(EnumType.STRING)
    @Column(length = 40)
    private ReviewAction action;
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(columnDefinition = "json")
    private Map<String, Object> revisions;
    @Column(length = 2000)
    private String reason;
    @Column(length = 100)
    private String actor;
    @Column(name = "evidence_sha256", nullable = false, length = 64)
    private String evidenceSha256;
    @Column(name = "pending_decision_id", unique = true, length = 32)
    private String pendingDecisionId;
    @Column(name = "decision_id", unique = true, length = 32)
    private String decisionId;
    @Column(name = "stale_reason", length = 100)
    private String staleReason;
    @Column(name = "acted_at")
    private Instant actedAt;
    @Column(name = "created_at", nullable = false)
    private Instant createdAt;
    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;
    @Version
    @Column(nullable = false)
    private long version;

    protected ReviewRecord() {}

    public static ReviewRecord pending(
            ProcurementTask business,
            AiTask aiTask,
            AiResult aiResult,
            ComparisonSnapshot snapshot,
            List<String> risks,
            String suggestedQuoteId) {
        var now = Instant.now();
        var review = new ReviewRecord();
        review.id = UUID.randomUUID().toString().replace("-", "");
        review.businessId = business.getId();
        review.aiTaskId = aiTask.getId();
        review.aiResultId = aiResult.getId();
        review.snapshotId = snapshot.getId();
        review.generation = business.getGeneration();
        review.taskVersion = business.getVersion();
        review.inputSha256 = snapshot.getInputSha256();
        review.status = ReviewStatus.PENDING;
        review.riskFlags = new ArrayList<>(risks);
        review.priority = Math.min(100, 50 + risks.size() * 10 + (suggestedQuoteId == null ? 20 : 0));
        review.waitingSince = now;
        review.suggestedQuoteId = suggestedQuoteId;
        review.evidenceSha256 = CanonicalJson.sha256(Map.ofEntries(
                Map.entry("business_id", review.businessId),
                Map.entry("generation", review.generation),
                Map.entry("task_version", review.taskVersion),
                Map.entry("ai_task_id", review.aiTaskId),
                Map.entry("ai_result_id", review.aiResultId),
                Map.entry("ai_result_sha256", aiResult.getResultSha256()),
                Map.entry("snapshot_id", review.snapshotId),
                Map.entry("input_sha256", review.inputSha256),
                Map.entry("suggested_quote_id", suggestedQuoteId == null ? "" : suggestedQuoteId)));
        review.createdAt = now;
        review.updatedAt = now;
        return review;
    }

    public String getId() { return id; }
    public String getBusinessId() { return businessId; }
    public String getAiTaskId() { return aiTaskId; }
    public String getAiResultId() { return aiResultId; }
    public String getSnapshotId() { return snapshotId; }
    public int getGeneration() { return generation; }
    public long getTaskVersion() { return taskVersion; }
    public String getInputSha256() { return inputSha256; }
    public ReviewStatus getStatus() { return status; }
    public int getPriority() { return priority; }
    public List<String> getRiskFlags() { return List.copyOf(riskFlags); }
    public Instant getWaitingSince() { return waitingSince; }
    public String getSuggestedQuoteId() { return suggestedQuoteId; }
    public String getFinalQuoteId() { return finalQuoteId; }
    public ReviewAction getAction() { return action; }
    public Map<String, Object> getRevisions() { return revisions == null ? null : Map.copyOf(revisions); }
    public String getReason() { return reason; }
    public String getActor() { return actor; }
    public String getEvidenceSha256() { return evidenceSha256; }
    public String getPendingDecisionId() { return pendingDecisionId; }
    public String getDecisionId() { return decisionId; }
    public String getStaleReason() { return staleReason; }
    public Instant getActedAt() { return actedAt; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }
    public long getVersion() { return version; }

    public void submit(
            ReviewAction nextAction,
            String quoteId,
            Map<String, Object> revisedValues,
            String actionReason,
            String actionActor,
            PendingDecision pending) {
        action = nextAction;
        finalQuoteId = quoteId;
        revisions = revisedValues == null || revisedValues.isEmpty() ? null : new LinkedHashMap<>(revisedValues);
        reason = blankToNull(actionReason);
        actor = actionActor;
        pendingDecisionId = pending == null ? null : pending.getId();
        actedAt = Instant.now();
        updatedAt = actedAt;
        if (nextAction == ReviewAction.REJECT_AND_RETRY) {
            status = ReviewStatus.REJECTED;
        }
    }

    public void finalizeDecision(ProcurementDecision decision) {
        if (pendingDecisionId == null || !pendingDecisionId.equals(decision.getPendingDecisionId())) return;
        decisionId = decision.getId();
        status = "no_award".equals(decision.getDecision()) ? ReviewStatus.NO_AWARD : ReviewStatus.APPROVED;
        updatedAt = Instant.now();
    }

    public void markStale(String value) {
        if (status == ReviewStatus.PENDING) {
            status = ReviewStatus.STALE;
            staleReason = truncate(value, 100);
            updatedAt = Instant.now();
        }
    }

    private String blankToNull(String value) {
        return value == null || value.isBlank() ? null : value.strip();
    }

    private String truncate(String value, int max) {
        if (value == null) return null;
        return value.substring(0, Math.min(max, value.length()));
    }
}
