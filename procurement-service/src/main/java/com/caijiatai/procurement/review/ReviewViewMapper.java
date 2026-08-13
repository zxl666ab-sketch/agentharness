package com.caijiatai.procurement.review;

import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.stereotype.Component;

@Component
public final class ReviewViewMapper {
    public Map<String, Object> summary(ReviewRecord review) {
        var value = new LinkedHashMap<String, Object>();
        value.put("review_id", review.getId());
        value.put("business_id", review.getBusinessId());
        value.put("ai_task_id", review.getAiTaskId());
        value.put("ai_result_id", review.getAiResultId());
        value.put("status", review.getStatus().name());
        value.put("priority", review.getPriority());
        value.put("risk_flags", review.getRiskFlags());
        value.put("waiting_since", review.getWaitingSince());
        value.put("version", review.getVersion());
        value.put("generation", review.getGeneration());
        value.put("task_version", review.getTaskVersion());
        value.put("snapshot_id", review.getSnapshotId());
        value.put("input_sha256", review.getInputSha256());
        value.put("suggested_quote_id", review.getSuggestedQuoteId());
        value.put("final_quote_id", review.getFinalQuoteId());
        value.put("action", review.getAction() == null ? null : review.getAction().name());
        value.put("reason", review.getReason());
        value.put("actor", review.getActor());
        value.put("revisions", review.getRevisions());
        value.put("evidence_sha256", review.getEvidenceSha256());
        value.put("pending_decision_id", review.getPendingDecisionId());
        value.put("decision_id", review.getDecisionId());
        value.put("stale_reason", review.getStaleReason());
        value.put("acted_at", review.getActedAt());
        value.put("created_at", review.getCreatedAt());
        value.put("updated_at", review.getUpdatedAt());
        return value;
    }
}
