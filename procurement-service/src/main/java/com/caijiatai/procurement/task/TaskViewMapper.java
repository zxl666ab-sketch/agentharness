package com.caijiatai.procurement.task;

import com.caijiatai.procurement.agent.CanonicalJson;
import com.caijiatai.procurement.approval.ProcurementDecision;
import com.caijiatai.procurement.artifact.ProcurementAttachment;
import com.caijiatai.procurement.comparison.ComparisonSnapshot;
import com.caijiatai.procurement.quote.ProcurementQuote;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.stereotype.Component;

@Component
public final class TaskViewMapper {
    public Map<String, Object> summary(
            ProcurementTask task,
            long quoteCount,
            int unresolvedCount,
            ProcurementDecision decision) {
        var value = base(task);
        value.put("quote_count", quoteCount);
        value.put("unresolved_field_count", unresolvedCount);
        value.put("decision", decision == null ? null : decision(decision));
        return value;
    }

    public Map<String, Object> detail(
            ProcurementTask task,
            List<ProcurementAttachment> attachments,
            List<ProcurementQuote> quotes,
            ComparisonSnapshot snapshot,
            ProcurementDecision decision) {
        int unresolved = quotes.stream().mapToInt(item -> item.reviewFields().size()).sum();
        var value = summary(task, quotes.size(), unresolved, decision);
        value.put("attachments", attachments.stream().map(this::attachment).toList());
        value.put("quotes", quotes.stream().map(this::quote).toList());
        value.put("comparison", snapshot == null ? null : snapshot(snapshot));
        value.put("decision", decision == null ? null : decision(decision));
        return value;
    }

    public Map<String, Object> quote(ProcurementQuote quote) {
        var value = new LinkedHashMap<String, Object>();
        value.put("id", quote.getId());
        value.put("request_id", quote.getTaskId());
        value.put("supplier_name", quote.getSupplierName());
        value.put("source_filename", quote.getSourceFilename());
        value.put("source_kind", quote.getSourceKind());
        value.put("source_artifact_id", quote.getSourceArtifactId());
        value.put("source_sha256", quote.getSourceSha256());
        value.put("extracted", extractedView(quote.getExtracted()));
        value.put("status", quote.getStatus());
        value.put("review_count", quote.getReviewCount());
        value.put("review_fields", quote.reviewFields());
        value.put("parser_version", quote.getParserVersion());
        value.put("processing_ms", quote.getProcessingMs());
        value.put("created_at", quote.getCreatedAt());
        value.put("updated_at", quote.getUpdatedAt());
        return value;
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> extractedView(Map<String, Object> extracted) {
        var view = new LinkedHashMap<String, Object>(extracted);
        var fields = new LinkedHashMap<String, Object>();
        var rawFields = extracted.get("fields");
        if (rawFields instanceof Map<?, ?> raw) {
            ((Map<String, Object>) raw).forEach((name, rawValue) -> {
                var entry = new LinkedHashMap<String, Object>();
                if (rawValue instanceof Map<?, ?> rawEntry) {
                    entry.putAll((Map<String, Object>) rawEntry);
                } else {
                    entry.put("value", rawValue);
                }
                entry.putIfAbsent("confidence", 1);
                entry.putIfAbsent("status", "accepted");
                entry.putIfAbsent("source", Map.of(
                        "document_kind", "", "locator", "", "excerpt", "", "method", ""));
                fields.put(name, entry);
            });
        }
        view.put("fields", fields);
        view.putIfAbsent("review_fields", List.of());
        return view;
    }

    public Map<String, Object> snapshot(ComparisonSnapshot snapshot) {
        var value = new LinkedHashMap<String, Object>();
        value.put("id", snapshot.getId());
        value.put("request_id", snapshot.getTaskId());
        value.put("run_id", snapshot.getRunId());
        value.put("version", snapshot.getSnapshotVersion());
        value.put("input_sha256", snapshot.getInputSha256());
        value.put("result", snapshot.getResult());
        value.put("artifact_id", snapshot.getArtifactId());
        value.put("created_at", snapshot.getCreatedAt());
        return value;
    }

    public Map<String, Object> decision(ProcurementDecision decision) {
        var value = new LinkedHashMap<String, Object>();
        value.put("id", decision.getId());
        value.put("request_id", decision.getTaskId());
        value.put("snapshot_id", decision.getSnapshotId());
        value.put("quote_id", decision.getQuoteId());
        value.put("run_id", decision.getRunId());
        value.put("approval_id", decision.getApprovalId());
        value.put("decision", decision.getDecision());
        value.put("note", decision.getNote());
        value.put("actor", decision.getActor());
        value.put("created_at", decision.getCreatedAt());
        return value;
    }

    private Map<String, Object> base(ProcurementTask task) {
        var value = new LinkedHashMap<String, Object>();
        value.put("id", task.getId());
        value.put("reference", task.getReference());
        value.put("title", task.getTitle());
        value.put("schema_version", task.getSchemaVersion());
        value.put("category", task.getCategory());
        value.put("item_name", task.getItemName());
        value.put("quantity", CanonicalJson.decimal(task.getQuantity()));
        value.put("unit", task.getUnit());
        value.put("specifications", task.getSpecifications());
        value.put("constraints", task.getConstraints());
        value.put("status", task.getStatus());
        value.put("requirement_confirmed", task.isRequirementConfirmed());
        value.put("retryable", task.isRetryable());
        value.put("retry_message", task.getRetryMessage());
        value.put("session_id", task.getSessionId());
        value.put("analysis_run_id", task.getAnalysisRunId());
        value.put("current_snapshot_id", task.getCurrentSnapshotId());
        value.put("approved_quote_id", task.getApprovedQuoteId());
        value.put("generation", task.getGeneration());
        value.put("task_version", task.getVersion());
        value.put("created_at", task.getCreatedAt());
        value.put("updated_at", task.getUpdatedAt());
        return value;
    }

    private Map<String, Object> attachment(ProcurementAttachment attachment) {
        return Map.of(
                "filename", attachment.getFilename(),
                "artifact_id", attachment.getArtifactId(),
                "sha256", attachment.getSha256(),
                "content_type", attachment.getContentType(),
                "size_bytes", attachment.getSizeBytes());
    }
}
