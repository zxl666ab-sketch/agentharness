package com.caijiatai.procurement.approval;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.Instant;

@Entity
@Table(name = "pending_decision")
public class PendingDecision {
    @Id
    @Column(length = 32)
    private String id;
    @Column(name = "operation_id", nullable = false, unique = true, length = 36)
    private String operationId;
    @Column(name = "task_id", nullable = false, length = 32)
    private String taskId;
    @Column(name = "run_id", nullable = false, length = 32)
    private String runId;
    @Column(name = "tool_name", nullable = false, length = 100)
    private String toolName;
    @Column(name = "task_version", nullable = false)
    private long taskVersion;
    @Column(name = "snapshot_id", nullable = false, length = 32)
    private String snapshotId;
    @Column(name = "input_sha256", nullable = false, length = 64)
    private String inputSha256;
    @Column(nullable = false, length = 20)
    private String decision;
    @Column(name = "quote_id", length = 32)
    private String quoteId;
    @Column(name = "note_hash", nullable = false, length = 64)
    private String noteHash;
    @Column(name = "approval_id", length = 32)
    private String approvalId;
    @Column(name = "approval_arguments_sha256", length = 64)
    private String approvalArgumentsSha256;
    @Column(name = "approval_decision", length = 64)
    private String approvalDecision;
    @Column(name = "approval_at")
    private Instant approvalAt;
    @Column(nullable = false, length = 30)
    private String status;
    @Column(name = "created_at", nullable = false)
    private Instant createdAt;
    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    protected PendingDecision() {}

    public static PendingDecision create(
            String id,
            String operationId,
            String taskId,
            String runId,
            long taskVersion,
            String snapshotId,
            String inputSha256,
            String decision,
            String quoteId,
            String noteHash) {
        var pending = new PendingDecision();
        pending.id = id;
        pending.operationId = operationId;
        pending.taskId = taskId;
        pending.runId = runId;
        pending.toolName = "procurement_approve_supplier";
        pending.taskVersion = taskVersion;
        pending.snapshotId = snapshotId;
        pending.inputSha256 = inputSha256;
        pending.decision = decision;
        pending.quoteId = quoteId;
        pending.noteHash = noteHash;
        pending.status = "pending";
        pending.createdAt = Instant.now();
        pending.updatedAt = pending.createdAt;
        return pending;
    }

    public String getId() { return id; }
    public String getOperationId() { return operationId; }
    public String getTaskId() { return taskId; }
    public String getRunId() { return runId; }
    public String getToolName() { return toolName; }
    public long getTaskVersion() { return taskVersion; }
    public String getSnapshotId() { return snapshotId; }
    public String getInputSha256() { return inputSha256; }
    public String getDecision() { return decision; }
    public String getQuoteId() { return quoteId; }
    public String getNoteHash() { return noteHash; }
    public String getApprovalId() { return approvalId; }
    public String getApprovalArgumentsSha256() { return approvalArgumentsSha256; }
    public String getApprovalDecision() { return approvalDecision; }
    public Instant getApprovalAt() { return approvalAt; }
    public String getStatus() { return status; }

    public void approve(
            String approvalId,
            String argumentsSha256,
            String approvalDecision,
            Instant approvalAt) {
        this.approvalId = approvalId;
        this.approvalArgumentsSha256 = argumentsSha256;
        this.approvalDecision = approvalDecision;
        this.approvalAt = approvalAt;
        this.status = "approved";
        this.updatedAt = Instant.now();
    }

    public void complete() {
        status = "completed";
        updatedAt = Instant.now();
    }

    public void stale() {
        if (!"completed".equals(status)) {
            status = "stale";
            updatedAt = Instant.now();
        }
    }
}
