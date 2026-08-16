package com.caijiatai.procurement.invoice;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import jakarta.persistence.Version;
import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

/**
 * 发票实体（P3-1）：登记自 Agent 解析结果，三单匹配（PO/GRN/Invoice）建立其上。
 * 金额字段一律 BigDecimal；状态机流转由 InvoiceStateMachineConfig 校验。
 */
@Entity
@Table(name = "invoice")
public class Invoice {
    @Id
    @Column(length = 32)
    private String id;
    @Column(name = "order_id", nullable = false, length = 32)
    private String orderId;
    @Column(name = "invoice_code", length = 64)
    private String invoiceCode;
    @Column(name = "invoice_no", nullable = false, length = 64)
    private String invoiceNo;
    @Column(name = "issue_date")
    private LocalDate issueDate;
    @Column(precision = 60, scale = 18)
    private BigDecimal quantity;
    @Column(length = 50)
    private String unit;
    @Column(name = "unit_price", precision = 60, scale = 18)
    private BigDecimal unitPrice;
    @Column(name = "amount_excluding_tax", precision = 60, scale = 18)
    private BigDecimal amountExcludingTax;
    @Column(name = "tax_amount", precision = 60, scale = 18)
    private BigDecimal taxAmount;
    @Column(name = "total_amount", nullable = false, precision = 60, scale = 18)
    private BigDecimal totalAmount;
    @Column(name = "tax_rate", precision = 10, scale = 6)
    private BigDecimal taxRate;
    @Column(name = "supplier_name", nullable = false, length = 300)
    private String supplierName;
    @Column(name = "artifact_id", nullable = false, length = 32)
    private String artifactId;
    @Column(name = "source_sha256", nullable = false, length = 64)
    private String sourceSha256;
    @Column(name = "parser_version", nullable = false, length = 100)
    private String parserVersion;
    @Column(nullable = false, length = 30)
    private String status;
    @Column(name = "match_result", columnDefinition = "json")
    private Map<String, Object> matchResult;
    @Column(name = "match_explanation", columnDefinition = "json")
    private Map<String, Object> matchExplanation;
    @Column(length = 2000)
    private String notes;
    @Version
    @Column(nullable = false)
    private long version;
    @Column(name = "created_at", nullable = false)
    private Instant createdAt;
    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;
    @Column(name = "matched_at")
    private Instant matchedAt;
    @Column(name = "reconciled_at")
    private Instant reconciledAt;

    protected Invoice() {}

    public static Invoice register(
            String orderId,
            String invoiceNo,
            String invoiceCode,
            LocalDate issueDate,
            BigDecimal quantity,
            String unit,
            BigDecimal unitPrice,
            BigDecimal amountExcludingTax,
            BigDecimal taxAmount,
            BigDecimal totalAmount,
            BigDecimal taxRate,
            String supplierName,
            String artifactId,
            String sourceSha256,
            String parserVersion) {
        var invoice = new Invoice();
        invoice.id = UUID.randomUUID().toString().replace("-", "");
        invoice.orderId = orderId;
        invoice.invoiceNo = invoiceNo;
        invoice.invoiceCode = invoiceCode;
        invoice.issueDate = issueDate;
        invoice.quantity = quantity;
        invoice.unit = unit;
        invoice.unitPrice = unitPrice;
        invoice.amountExcludingTax = amountExcludingTax;
        invoice.taxAmount = taxAmount;
        invoice.totalAmount = totalAmount;
        invoice.taxRate = taxRate;
        invoice.supplierName = supplierName;
        invoice.artifactId = artifactId;
        invoice.sourceSha256 = sourceSha256;
        invoice.parserVersion = parserVersion;
        invoice.status = InvoiceStatus.REGISTERED.wireValue();
        invoice.createdAt = Instant.now();
        invoice.updatedAt = invoice.createdAt;
        return invoice;
    }

    public String getId() { return id; }
    public String getOrderId() { return orderId; }
    public String getInvoiceCode() { return invoiceCode; }
    public String getInvoiceNo() { return invoiceNo; }
    public LocalDate getIssueDate() { return issueDate; }
    public BigDecimal getQuantity() { return quantity; }
    public String getUnit() { return unit; }
    public BigDecimal getUnitPrice() { return unitPrice; }
    public BigDecimal getAmountExcludingTax() { return amountExcludingTax; }
    public BigDecimal getTaxAmount() { return taxAmount; }
    public BigDecimal getTotalAmount() { return totalAmount; }
    public BigDecimal getTaxRate() { return taxRate; }
    public String getSupplierName() { return supplierName; }
    public String getArtifactId() { return artifactId; }
    public String getSourceSha256() { return sourceSha256; }
    public String getParserVersion() { return parserVersion; }
    public String getStatus() { return status; }
    public Map<String, Object> getMatchResult() { return matchResult; }
    public Map<String, Object> getMatchExplanation() { return matchExplanation; }
    public String getNotes() { return notes; }
    public long getVersion() { return version; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }
    public Instant getMatchedAt() { return matchedAt; }
    public Instant getReconciledAt() { return reconciledAt; }

    public void applyMatchResult(boolean matched, Map<String, Object> matchResult, Map<String, Object> explanation) {
        this.status = matched ? InvoiceStatus.MATCHED.wireValue() : InvoiceStatus.DIFF_HOLD.wireValue();
        this.matchResult = new LinkedHashMap<>(matchResult);
        this.matchExplanation = explanation == null ? null : new LinkedHashMap<>(explanation);
        this.matchedAt = Instant.now();
        this.updatedAt = Instant.now();
    }

    public void applyExplanation(Map<String, Object> explanation) {
        this.matchExplanation = new LinkedHashMap<>(explanation);
        this.updatedAt = Instant.now();
    }

    public void applyHumanCorrection(
            BigDecimal quantity, BigDecimal unitPrice, BigDecimal amountExcludingTax,
            BigDecimal taxAmount, BigDecimal totalAmount, BigDecimal taxRate, String notes) {
        this.quantity = quantity;
        this.unitPrice = unitPrice;
        this.amountExcludingTax = amountExcludingTax;
        this.taxAmount = taxAmount;
        this.totalAmount = totalAmount;
        this.taxRate = taxRate;
        if (notes != null) {
            this.notes = notes;
        }
        this.updatedAt = Instant.now();
    }

    public void forceMatch(String notes) {
        this.status = InvoiceStatus.MATCHED.wireValue();
        this.notes = notes;
        this.matchedAt = Instant.now();
        this.updatedAt = Instant.now();
    }

    public void voidInvoice(String notes) {
        this.status = InvoiceStatus.VOIDED.wireValue();
        if (notes != null) {
            this.notes = notes;
        }
        this.updatedAt = Instant.now();
    }

    public void reconcile() {
        this.status = InvoiceStatus.RECONCILED.wireValue();
        this.reconciledAt = Instant.now();
        this.updatedAt = Instant.now();
    }
}
