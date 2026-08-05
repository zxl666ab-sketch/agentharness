package com.caijiatai.procurement.approval;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.Instant;

@Entity
@Table(name = "procurement_decision")
public class ProcurementDecision {
    @Id
    @Column(length = 32)
    private String id;
    @Column(name = "pending_decision_id", nullable = false, unique = true, length = 32)
    private String pendingDecisionId;
    @Column(name = "task_id", nullable = false, unique = true, length = 32)
    private String taskId;
    @Column(name = "snapshot_id", nullable = false, length = 32)
    private String snapshotId;
    @Column(name = "quote_id", length = 32)
    private String quoteId;
    @Column(name = "run_id", nullable = false, length = 32)
    private String runId;
    @Column(name = "approval_id", nullable = false, length = 32)
    private String approvalId;
    @Column(nullable = false, length = 20)
    private String decision;
    @Column(columnDefinition = "text")
    private String note;
    @Column(nullable = false, length = 100)
    private String actor;
    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    protected ProcurementDecision() {}

    public static ProcurementDecision create(PendingDecision pending, String note, String actor) {
        var decision = new ProcurementDecision();
        decision.id = java.util.UUID.randomUUID().toString().replace("-", "");
        decision.pendingDecisionId = pending.getId();
        decision.taskId = pending.getTaskId();
        decision.snapshotId = pending.getSnapshotId();
        decision.quoteId = pending.getQuoteId();
        decision.runId = pending.getRunId();
        decision.approvalId = pending.getApprovalId();
        decision.decision = pending.getDecision();
        decision.note = note;
        decision.actor = actor;
        decision.createdAt = Instant.now();
        return decision;
    }

    public String getId() { return id; }
    public String getPendingDecisionId() { return pendingDecisionId; }
    public String getTaskId() { return taskId; }
    public String getSnapshotId() { return snapshotId; }
    public String getQuoteId() { return quoteId; }
    public String getRunId() { return runId; }
    public String getApprovalId() { return approvalId; }
    public String getDecision() { return decision; }
    public String getNote() { return note; }
    public String getActor() { return actor; }
    public Instant getCreatedAt() { return createdAt; }
}
