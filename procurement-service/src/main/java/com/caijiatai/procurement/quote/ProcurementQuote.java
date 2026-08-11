package com.caijiatai.procurement.quote;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

@Entity
@Table(name = "procurement_quote")
public class ProcurementQuote {
    @Id
    @Column(length = 32)
    private String id;
    @Column(name = "task_id", nullable = false, length = 32)
    private String taskId;
    @Column(name = "source_artifact_id", nullable = false, length = 34)
    private String sourceArtifactId;
    @Column(name = "supplier_name", nullable = false, length = 300)
    private String supplierName;
    @Column(name = "source_filename", nullable = false, length = 255)
    private String sourceFilename;
    @Column(name = "source_kind", nullable = false, length = 20)
    private String sourceKind;
    @Column(name = "source_sha256", nullable = false, length = 64)
    private String sourceSha256;
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(nullable = false, columnDefinition = "json")
    private Map<String, Object> extracted = new LinkedHashMap<>();
    @Column(nullable = false, length = 40)
    private String status;
    @Column(name = "review_count", nullable = false)
    private int reviewCount;
    @Column(name = "parser_version", nullable = false, length = 100)
    private String parserVersion;
    @Column(name = "processing_ms", nullable = false, precision = 20, scale = 3)
    private BigDecimal processingMs;
    @Column(name = "created_at", nullable = false)
    private Instant createdAt;
    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    protected ProcurementQuote() {}

    public static ProcurementQuote create(
            String taskId,
            String artifactId,
            String supplierName,
            String filename,
            String sourceKind,
            String sourceSha256,
            Map<String, Object> extracted,
            String status,
            String parserVersion,
            BigDecimal processingMs) {
        var quote = new ProcurementQuote();
        quote.id = java.util.UUID.randomUUID().toString().replace("-", "");
        quote.taskId = taskId;
        quote.sourceArtifactId = artifactId;
        quote.supplierName = supplierName;
        quote.sourceFilename = filename;
        quote.sourceKind = sourceKind;
        quote.sourceSha256 = sourceSha256;
        quote.extracted = new LinkedHashMap<>(extracted);
        quote.status = status;
        quote.parserVersion = parserVersion;
        quote.processingMs = processingMs;
        quote.createdAt = Instant.now();
        quote.updatedAt = quote.createdAt;
        return quote;
    }

    public ProcurementQuote copyTo(String newTaskId, String newArtifactId) {
        return create(
                newTaskId,
                newArtifactId,
                supplierName,
                sourceFilename,
                sourceKind,
                sourceSha256,
                extracted,
                status,
                parserVersion,
                processingMs);
    }

    public String getId() { return id; }
    public String getTaskId() { return taskId; }
    public String getSourceArtifactId() { return sourceArtifactId; }
    public String getSupplierName() { return supplierName; }
    public String getSourceFilename() { return sourceFilename; }
    public String getSourceKind() { return sourceKind; }
    public String getSourceSha256() { return sourceSha256; }
    public Map<String, Object> getExtracted() { return extracted; }
    public String getStatus() { return status; }
    public int getReviewCount() { return reviewCount; }
    public String getParserVersion() { return parserVersion; }
    public BigDecimal getProcessingMs() { return processingMs; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }

    public void correct(String field, Object value) {
        var fields = extracted.get("fields");
        if (!(fields instanceof Map<?, ?> rawFields)) {
            throw new IllegalStateException("Quote extraction has no fields object");
        }
        @SuppressWarnings("unchecked")
        var mutableFields = (Map<String, Object>) rawFields;
        var entry = mutableFields.get(field);
        if (!(entry instanceof Map<?, ?> rawEntry)) {
            throw new IllegalArgumentException("Unknown quote field: " + field);
        }
        @SuppressWarnings("unchecked")
        var mutableEntry = (Map<String, Object>) rawEntry;
        mutableEntry.putIfAbsent("original_value", mutableEntry.get("value"));
        mutableEntry.put("value", value);
        mutableEntry.put("status", "corrected");
        mutableEntry.put("confidence", 1);
        var pending = extracted.get("review_fields");
        if (pending instanceof java.util.List<?> values) {
            extracted.put("review_fields", values.stream()
                    .map(String::valueOf)
                    .filter(item -> !item.equals(field))
                    .toList());
        }
        supplierName = "supplier_name".equals(field) ? String.valueOf(value) : supplierName;
        reviewCount += 1;
        status = reviewFields().isEmpty() ? "ready" : "needs_review";
        updatedAt = Instant.now();
    }

    public java.util.List<String> reviewFields() {
        var explicit = extracted.get("review_fields");
        if (explicit instanceof java.util.List<?> values) {
            return values.stream().map(String::valueOf).toList();
        }
        var fields = extracted.get("fields");
        if (!(fields instanceof Map<?, ?> rawFields)) {
            return java.util.List.of();
        }
        return rawFields.entrySet().stream()
                .filter(item -> item.getValue() instanceof Map<?, ?> entry
                        && ("needs_review".equals(entry.get("status"))
                            || entry.get("value") == null
                            || number(entry.get("confidence")).compareTo(new BigDecimal("0.8")) < 0))
                .map(item -> String.valueOf(item.getKey()))
                .toList();
    }

    private BigDecimal number(Object value) {
        try {
            return new BigDecimal(String.valueOf(value));
        } catch (NumberFormatException error) {
            return BigDecimal.ZERO;
        }
    }
}
