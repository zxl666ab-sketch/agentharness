package com.caijiatai.procurement.contract;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import jakarta.persistence.Version;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

/**
 * 合同实体（P3-2）：金额/交期/供应商由定标结果（approved decision + snapshot）注入，
 * 不来自 LLM；条款集为 Agent 草拟的结构化结果（只进草拟文本，不进正式业务字段）；
 * 变更时旧条款快照写入 change_history 留痕。
 */
@Entity
@Table(name = "contract")
public class Contract {
    @Id
    @Column(length = 32)
    private String id;
    @Column(name = "contract_no", nullable = false, unique = true, length = 64)
    private String contractNo;
    @Column(name = "task_id", nullable = false, unique = true, length = 32)
    private String taskId;
    @Column(name = "order_id", length = 32)
    private String orderId;
    @Column(name = "supplier_name", nullable = false, length = 300)
    private String supplierName;
    @Column(name = "item_name", nullable = false, length = 200)
    private String itemName;
    @Column(nullable = false, precision = 60, scale = 18)
    private BigDecimal amount;
    @Column(name = "lead_days", nullable = false)
    private int leadDays;
    @Column(nullable = false, length = 30)
    private String status;
    @JdbcTypeCode(SqlTypes.LONGVARCHAR)
    @Column(name = "draft_text", columnDefinition = "mediumtext")
    private String draftText;
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(columnDefinition = "json")
    private List<Map<String, Object>> clauses;
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(columnDefinition = "json")
    private Map<String, Object> consistency;
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "change_history", columnDefinition = "json")
    private List<Map<String, Object>> changeHistory;
    @Column(length = 2000)
    private String notes;
    @Version
    @Column(nullable = false)
    private long version;
    @Column(name = "created_at", nullable = false)
    private Instant createdAt;
    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;
    @Column(name = "approved_at")
    private Instant approvedAt;

    protected Contract() {}

    public static Contract derive(
            String taskId,
            String contractNo,
            String supplierName,
            String itemName,
            BigDecimal amount,
            int leadDays) {
        var contract = new Contract();
        contract.id = UUID.randomUUID().toString().replace("-", "");
        contract.taskId = taskId;
        contract.contractNo = contractNo;
        contract.supplierName = supplierName;
        contract.itemName = itemName;
        contract.amount = amount;
        contract.leadDays = leadDays;
        contract.status = ContractStatus.DRAFT.wireValue();
        contract.clauses = List.of();
        contract.changeHistory = List.of();
        contract.createdAt = Instant.now();
        contract.updatedAt = contract.createdAt;
        return contract;
    }

    public String getId() { return id; }
    public String getContractNo() { return contractNo; }
    public String getTaskId() { return taskId; }
    public String getOrderId() { return orderId; }
    public String getSupplierName() { return supplierName; }
    public String getItemName() { return itemName; }
    public BigDecimal getAmount() { return amount; }
    public int getLeadDays() { return leadDays; }
    public String getStatus() { return status; }
    public String getDraftText() { return draftText; }
    public List<Map<String, Object>> getClauses() { return clauses; }
    public Map<String, Object> getConsistency() { return consistency; }
    public List<Map<String, Object>> getChangeHistory() { return changeHistory; }
    public String getNotes() { return notes; }
    public long getVersion() { return version; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }
    public Instant getApprovedAt() { return approvedAt; }

    public void applyDraft(String draftText, List<Map<String, Object>> clauses,
            Map<String, Object> consistency, String notes) {
        this.draftText = draftText;
        this.clauses = new ArrayList<>(clauses);
        this.consistency = new LinkedHashMap<>(consistency);
        if (notes != null) {
            this.notes = notes;
        }
        this.updatedAt = Instant.now();
    }

    public void linkOrder(String orderId) {
        this.orderId = orderId;
        this.updatedAt = Instant.now();
    }

    public void submit(String notes) {
        this.status = ContractStatus.PENDING_APPROVAL.wireValue();
        if (notes != null) {
            this.notes = notes;
        }
        this.updatedAt = Instant.now();
    }

    public void approve(String note) {
        this.status = ContractStatus.EFFECTIVE.wireValue();
        this.approvedAt = Instant.now();
        if (note != null) {
            this.notes = note;
        }
        this.updatedAt = Instant.now();
    }

    public void reject(String note) {
        // 驳回语义按来源分流：PENDING_APPROVAL → DRAFT；变更审批（CHANGE_REQUEST）→ 恢复变更前状态
        this.status = previousStatus().wireValue();
        if (note != null) {
            this.notes = note;
        }
        this.updatedAt = Instant.now();
    }

    public void execute() {
        this.status = ContractStatus.EXECUTING.wireValue();
        this.updatedAt = Instant.now();
    }

    public void close(String notes) {
        this.status = ContractStatus.CLOSED.wireValue();
        if (notes != null) {
            this.notes = notes;
        }
        this.updatedAt = Instant.now();
    }

    public void requestChange(String notes, BigDecimal newAmount, Integer newLeadDays) {
        // 变更留痕：变更前状态 + 旧条款快照 + 待定修订值写入历史（应用后标记 applied）
        var snapshot = new ArrayList<>(this.changeHistory);
        var entry = new LinkedHashMap<String, Object>();
        entry.put("captured_at", Instant.now().toString());
        entry.put("reason", notes == null ? "" : notes);
        entry.put("from_status", this.status);
        entry.put("new_amount", newAmount.stripTrailingZeros().toPlainString());
        entry.put("new_lead_days", newLeadDays);
        if (!this.clauses.isEmpty()) {
            entry.put("clauses", new ArrayList<>(this.clauses));
        }
        snapshot.add(entry);
        this.changeHistory = snapshot;
        this.status = ContractStatus.CHANGE_REQUEST.wireValue();
        if (notes != null) {
            this.notes = notes;
        }
        this.updatedAt = Instant.now();
    }

    /** 最近一次变更申请快照（含 from_status / new_amount / new_lead_days）。 */
    private Map<String, Object> latestChange() {
        if (changeHistory == null || changeHistory.isEmpty()) {
            return Map.of();
        }
        return changeHistory.get(changeHistory.size() - 1);
    }

    /** 待定修订金额（变更审批中）：无则返回 null（用当前金额口径校验）。 */
    public BigDecimal pendingAmount() {
        var value = latestChange().get("new_amount");
        if (!(value instanceof String s) || s.isBlank()) {
            return null;
        }
        try {
            return new BigDecimal(s);
        } catch (NumberFormatException error) {
            return null;
        }
    }

    /** 待定修订交期天数（变更审批中）：无则返回 null。 */
    public Integer pendingLeadDays() {
        var value = latestChange().get("new_lead_days");
        return value instanceof Number number ? number.intValue() : null;
    }

    /** 变更批准：把待定金额/交期落到正式字段，并在历史快照标记已应用。 */
    public void applyPendingChange() {
        var pendingAmount = pendingAmount();
        if (pendingAmount != null) {
            this.amount = pendingAmount;
        }
        var pendingLead = pendingLeadDays();
        if (pendingLead != null) {
            this.leadDays = pendingLead;
        }
        if (!changeHistory.isEmpty()) {
            var snapshot = new ArrayList<>(changeHistory);
            var last = new LinkedHashMap<String, Object>(snapshot.get(snapshot.size() - 1));
            last.put("applied", true);
            last.put("applied_at", Instant.now().toString());
            snapshot.set(snapshot.size() - 1, last);
            this.changeHistory = snapshot;
        }
        this.updatedAt = Instant.now();
    }

    /** 驳回目标：CHANGE_REQUEST → 恢复变更前状态（快照缺失兜底 EFFECTIVE）；其余 → DRAFT。 */
    private ContractStatus previousStatus() {
        if (!ContractStatus.CHANGE_REQUEST.wireValue().equals(this.status)) {
            return ContractStatus.DRAFT;
        }
        var from = latestChange().get("from_status");
        if (from instanceof String value) {
            try {
                return ContractStatus.fromWire(value);
            } catch (IllegalArgumentException error) {
                return ContractStatus.EFFECTIVE;
            }
        }
        return ContractStatus.EFFECTIVE;
    }
}
