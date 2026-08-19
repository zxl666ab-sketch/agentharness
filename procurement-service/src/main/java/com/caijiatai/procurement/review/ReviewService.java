package com.caijiatai.procurement.review;

import com.caijiatai.procurement.agent.CanonicalJson;
import com.caijiatai.procurement.agent.IdempotencyRecord;
import com.caijiatai.procurement.agent.IdempotencyRecordRepository;
import com.caijiatai.procurement.ai.AiResult;
import com.caijiatai.procurement.ai.AiResultRepository;
import com.caijiatai.procurement.ai.AiTask;
import com.caijiatai.procurement.ai.AiTaskRepository;
import com.caijiatai.procurement.ai.AiTaskStatus;
import com.caijiatai.procurement.ai.AiTaskViewMapper;
import com.caijiatai.procurement.api.ApiException;
import com.caijiatai.procurement.approval.ApprovalService;
import com.caijiatai.procurement.approval.PendingDecisionRepository;
import com.caijiatai.procurement.cache.TaskContextCache;
import com.caijiatai.procurement.comparison.ComparisonSnapshot;
import com.caijiatai.procurement.comparison.ComparisonSnapshotRepository;
import com.caijiatai.procurement.report.AuditEvent;
import com.caijiatai.procurement.report.AuditEventRepository;
import com.caijiatai.procurement.task.ProcurementDtos;
import com.caijiatai.procurement.task.ProcurementTask;
import com.caijiatai.procurement.task.ProcurementTaskRepository;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.UUID;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Sort;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class ReviewService {
    private final ProcurementTaskRepository businessTasks;
    private final AiTaskRepository aiTasks;
    private final AiResultRepository aiResults;
    private final ComparisonSnapshotRepository snapshots;
    private final ReviewRecordRepository reviews;
    private final IdempotencyRecordRepository idempotency;
    private final ApprovalService approvals;
    private final PendingDecisionRepository pendingDecisions;
    private final AuditEventRepository audit;
    private final TaskContextCache contextCache;
    private final ReviewViewMapper views;
    private final AiTaskViewMapper aiViews;

    public ReviewService(
            ProcurementTaskRepository businessTasks,
            AiTaskRepository aiTasks,
            AiResultRepository aiResults,
            ComparisonSnapshotRepository snapshots,
            ReviewRecordRepository reviews,
            IdempotencyRecordRepository idempotency,
            ApprovalService approvals,
            PendingDecisionRepository pendingDecisions,
            AuditEventRepository audit,
            TaskContextCache contextCache,
            ReviewViewMapper views,
            AiTaskViewMapper aiViews) {
        this.businessTasks = businessTasks;
        this.aiTasks = aiTasks;
        this.aiResults = aiResults;
        this.snapshots = snapshots;
        this.reviews = reviews;
        this.idempotency = idempotency;
        this.approvals = approvals;
        this.pendingDecisions = pendingDecisions;
        this.audit = audit;
        this.contextCache = contextCache;
        this.views = views;
        this.aiViews = aiViews;
    }

    @Transactional
    public ReviewRecord createPending(
            ProcurementTask business,
            String operationId,
            AiResult result,
            ComparisonSnapshot snapshot) {
        var aiTask = aiTasks.findByOperationId(operationId)
                .orElseThrow(() -> conflict("review_ai_task_missing", "分析操作缺少 AI 任务"));
        var existing = reviews.findByAiResultId(result.getId());
        if (existing.isPresent()) return existing.get();
        if (business.getGeneration() != aiTask.getGeneration()
                || business.getGeneration() != result.getGeneration()
                || !Objects.equals(business.getCurrentSnapshotId(), snapshot.getId())
                || aiTask.getStatus() != AiTaskStatus.SUCCEEDED
                || aiTask.isStale()
                || result.isStale()) {
            throw conflict("stale_review_evidence", "AI 结果与当前采购证据不一致，不能进入审核");
        }
        var riskFlags = stringList(result.getStructuredResult().get("risk_flags"));
        if (integer(snapshot.getResult().get("eligible_count")) == 0
                && !riskFlags.contains("NO_ELIGIBLE_QUOTES")) {
            riskFlags.add("NO_ELIGIBLE_QUOTES");
        }
        var suggested = nullableText(snapshot.getResult().get("recommended_quote_id"));
        var review = reviews.save(ReviewRecord.pending(
                business, aiTask, result, snapshot, riskFlags, suggested));
        audit.save(AuditEvent.create(
                business.getId(), suggested, snapshot.getRunId(), "human_review_queued", "system",
                Map.of(
                        "review_id", review.getId(),
                        "ai_result_id", result.getId(),
                        "snapshot_id", snapshot.getId(),
                        "evidence_sha256", review.getEvidenceSha256())));
        return review;
    }

    @Transactional(readOnly = true)
    public Map<String, Object> list(ReviewStatus status, int page, int size) {
        var pageable = PageRequest.of(
                Math.max(0, page),
                Math.min(100, Math.max(1, size)),
                Sort.by(Sort.Order.desc("priority"), Sort.Order.asc("waitingSince"), Sort.Order.asc("id")));
        Specification<ReviewRecord> spec = (root, query, cb) -> cb.conjunction();
        if (status != null) {
            spec = spec.and((root, query, cb) -> cb.equal(root.get("status"), status));
        }
        var result = reviews.findAll(spec, pageable);
        return Map.of(
                "items", result.getContent().stream().map(views::summary).toList(),
                "page", result.getNumber(),
                "size", result.getSize(),
                "total", result.getTotalElements());
    }

    @Transactional(readOnly = true)
    public Map<String, Object> detail(String reviewId) {
        var review = reviews.findById(reviewId)
                .orElseThrow(() -> notFound("review_not_found", "未找到人工审核记录"));
        var result = aiResults.findById(review.getAiResultId())
                .orElseThrow(() -> conflict("review_evidence_missing", "审核引用的 AI 结果不存在"));
        var snapshot = snapshots.findByIdAndTaskId(review.getSnapshotId(), review.getBusinessId())
                .orElseThrow(() -> conflict("review_evidence_missing", "审核引用的比价快照不存在"));
        var value = new LinkedHashMap<>(views.summary(review));
        value.put("ai_result", aiViews.result(result));
        value.put("comparison", Map.ofEntries(
                Map.entry("id", snapshot.getId()),
                Map.entry("request_id", snapshot.getTaskId()),
                Map.entry("run_id", snapshot.getRunId()),
                Map.entry("version", snapshot.getSnapshotVersion()),
                Map.entry("input_sha256", snapshot.getInputSha256()),
                Map.entry("result", snapshot.getResult()),
                Map.entry("artifact_id", snapshot.getArtifactId()),
                Map.entry("created_at", snapshot.getCreatedAt())));
        value.put("history", reviews.findByBusinessIdOrderByCreatedAtAsc(review.getBusinessId()).stream()
                .map(views::summary).toList());
        return value;
    }

    @Transactional
    public Map<String, Object> action(
            String reviewId,
            ReviewDtos.ActionRequest body,
            String idempotencyKey) {
        var key = normalizeKey(idempotencyKey);
        var fingerprint = new LinkedHashMap<String, Object>();
        fingerprint.put("review_id", reviewId);
        fingerprint.put("action", body.action().name());
        fingerprint.put("expected_version", body.expectedVersion());
        fingerprint.put("actor", body.actor().strip());
        fingerprint.put("final_quote_id", body.finalQuoteId() == null ? "" : body.finalQuoteId());
        fingerprint.put("revisions", body.revisions() == null ? Map.of() : body.revisions());
        fingerprint.put("reason", body.reason() == null ? "" : body.reason().strip());
        var payloadSha = CanonicalJson.sha256(fingerprint);
        var scope = "review_action:" + reviewId;
        var replay = idempotency.findById(new IdempotencyRecord.Key(scope, key));
        if (replay.isPresent()) {
            if (!replay.get().getPayloadSha256().equals(payloadSha)) {
                throw conflict("idempotency_payload_conflict", "同一幂等键已用于不同审核动作");
            }
            return detail(reviewId);
        }

        var initial = reviews.findById(reviewId)
                .orElseThrow(() -> notFound("review_not_found", "未找到人工审核记录"));
        var business = businessTasks.lockById(initial.getBusinessId())
                .orElseThrow(() -> notFound("task_not_found", "未找到采购任务"));
        var review = reviews.lockById(reviewId)
                .orElseThrow(() -> notFound("review_not_found", "未找到人工审核记录"));
        if (review.getVersion() != body.expectedVersion()) {
            throw conflict("review_version_conflict", "审核记录已被其他操作修改，请刷新后重试");
        }
        if (review.getStatus() != ReviewStatus.PENDING || review.getAction() != null) {
            throw conflict("review_already_actioned", "该审核已提交处理，不能重复变更");
        }
        validateCurrentEvidence(review, business);
        validateAction(review, body);

        ApprovalService.RequestResult approval = null;
        if (body.action() != ReviewAction.REJECT_AND_RETRY) {
            var decision = body.action() == ReviewAction.NO_AWARD ? "no_award" : "approved";
            var quoteId = decision.equals("approved") ? selectedQuote(review, body) : null;
            approval = approvals.request(
                    business.getId(),
                    new ProcurementDtos.Decision(
                            decision,
                            review.getSnapshotId(),
                            review.getInputSha256(),
                            quoteId,
                            true,
                            body.reason()),
                    "review:" + reviewId + ":" + key);
            review.submit(
                    body.action(), quoteId, body.revisions(), body.reason(), body.actor().strip(), approval.pending());
            if (approval.decision() != null) review.finalizeDecision(approval.decision());
        } else {
            review.submit(
                    body.action(), null, body.revisions(), body.reason(), body.actor().strip(), null);
            business.prepareReviewRetry();
            contextCache.evict(business.getId());
        }
        var operationId = approval != null && approval.command() != null
                ? approval.command().getOperationId() : review.getId();
        idempotency.save(IdempotencyRecord.reserve(scope, key, payloadSha, operationId));
        audit.save(AuditEvent.create(
                business.getId(), review.getFinalQuoteId(), business.getAnalysisRunId(), "human_review_action_submitted",
                body.actor().strip(),
                Map.of(
                        "review_id", review.getId(),
                        "action", body.action().name(),
                        "evidence_sha256", review.getEvidenceSha256())));
        return detail(reviewId);
    }

    @Transactional
    public void markBusinessStale(String businessId, String reason) {
        for (var review : reviews.findByBusinessIdAndStatus(businessId, ReviewStatus.PENDING)) {
            review.markStale(reason);
        }
    }

    @Transactional
    public void finalizeDecision(com.caijiatai.procurement.approval.ProcurementDecision decision) {
        var bound = reviews.findByPendingDecisionId(decision.getPendingDecisionId());
        if (bound.isPresent()) {
            bound.get().finalizeDecision(decision);
            return;
        }
        var candidates = reviews.findByBusinessIdAndStatus(decision.getTaskId(), ReviewStatus.PENDING);
        for (int index = candidates.size() - 1; index >= 0; index--) {
            var review = candidates.get(index);
            if (review.getSnapshotId().equals(decision.getSnapshotId())) {
                review.finalizeDirectDecision(decision);
                return;
            }
        }
    }

    @Transactional
    public void failPendingDecision(String operationId, String reason) {
        var failureReason = reason == null || reason.isBlank()
                ? "Agent 未返回正式决定" : reason.strip();
        var pending = pendingDecisions.findByOperationId(operationId).orElse(null);
        if (pending == null || "completed".equals(pending.getStatus())) {
            return;
        }
        var firstFailure = !"stale".equals(pending.getStatus());
        pending.stale();
        reviews.findByPendingDecisionId(pending.getId())
                .ifPresent(review -> review.markStale(failureReason));
        var business = businessTasks.lockById(pending.getTaskId()).orElse(null);
        if (business == null) {
            return;
        }
        business.restoreAnalyzedAfterFailedApproval(pending.getSnapshotId());
        contextCache.evict(business.getId());
        if (firstFailure) {
            audit.save(AuditEvent.create(
                    business.getId(), pending.getQuoteId(), pending.getRunId(),
                    "procurement_decision_failed", "agent",
                    Map.of(
                            "pending_decision_id", pending.getId(),
                            "operation_id", operationId,
                            "error", failureReason)));
        }
    }

    private void validateCurrentEvidence(ReviewRecord review, ProcurementTask business) {
        var aiTask = aiTasks.findById(review.getAiTaskId())
                .orElseThrow(() -> conflict("review_evidence_missing", "审核引用的 AI 任务不存在"));
        var result = aiResults.findById(review.getAiResultId())
                .orElseThrow(() -> conflict("review_evidence_missing", "审核引用的 AI 结果不存在"));
        var snapshot = snapshots.findByIdAndTaskId(review.getSnapshotId(), business.getId())
                .orElseThrow(() -> conflict("stale_review_evidence", "审核引用的比价快照已失效"));
        if (business.getGeneration() != review.getGeneration()
                || business.getVersion() != review.getTaskVersion()
                || !Objects.equals(business.getCurrentSnapshotId(), review.getSnapshotId())
                || !snapshot.getInputSha256().equals(review.getInputSha256())
                || aiTask.isStale()
                || result.isStale()) {
            review.markStale("INPUT_OR_SNAPSHOT_CHANGED");
            throw conflict("stale_review_evidence", "采购输入或比价快照已变化，请刷新审核队列");
        }
    }

    private void validateAction(ReviewRecord review, ReviewDtos.ActionRequest body) {
        var reason = body.reason() == null ? "" : body.reason().strip();
        switch (body.action()) {
            case APPROVE_SUGGESTION -> {
                if (review.getSuggestedQuoteId() == null) {
                    throw unprocessable("suggestion_missing", "当前没有可批准的 AI 建议供应商");
                }
                if (body.finalQuoteId() != null
                        && !review.getSuggestedQuoteId().equals(body.finalQuoteId())) {
                    throw unprocessable("suggestion_changed", "批准建议时不能替换 AI 建议供应商");
                }
            }
            case REVISE_AND_APPROVE -> {
                if (body.finalQuoteId() == null || body.finalQuoteId().isBlank()) {
                    throw unprocessable("final_quote_required", "修改后通过必须选择最终供应商");
                }
                if (body.revisions() == null || body.revisions().isEmpty() || reason.isBlank()) {
                    throw unprocessable("revision_reason_required", "修改后通过必须记录人工值和理由");
                }
            }
            case REJECT_AND_RETRY -> {
                if (reason.isBlank()) {
                    throw unprocessable("rejection_reason_required", "驳回重跑必须填写理由");
                }
            }
            case NO_AWARD -> {
                if (reason.isBlank()) {
                    throw unprocessable("no_award_reason_required", "流标必须填写原因");
                }
            }
        }
    }

    private String selectedQuote(ReviewRecord review, ReviewDtos.ActionRequest body) {
        return body.action() == ReviewAction.APPROVE_SUGGESTION
                ? review.getSuggestedQuoteId() : body.finalQuoteId();
    }

    private String normalizeKey(String value) {
        var key = value == null || value.isBlank() ? UUID.randomUUID().toString() : value.strip();
        if (key.length() < 8 || key.length() > 128) {
            throw new ApiException(HttpStatus.BAD_REQUEST, "invalid_idempotency_key", "幂等键长度必须为 8 至 128");
        }
        return key;
    }

    private List<String> stringList(Object value) {
        var result = new ArrayList<String>();
        if (value instanceof List<?> items) {
            for (var item : items) {
                var text = nullableText(item);
                if (text != null && !result.contains(text)) result.add(text);
            }
        }
        return result;
    }

    private int integer(Object value) {
        if (value instanceof Number number) return number.intValue();
        try {
            return Integer.parseInt(String.valueOf(value));
        } catch (RuntimeException error) {
            return 0;
        }
    }

    private String nullableText(Object value) {
        if (value == null) return null;
        var text = String.valueOf(value).strip();
        return text.isBlank() ? null : text;
    }

    private ApiException conflict(String code, String message) {
        return new ApiException(HttpStatus.CONFLICT, code, message);
    }
    private ApiException notFound(String code, String message) {
        return new ApiException(HttpStatus.NOT_FOUND, code, message);
    }
    private ApiException unprocessable(String code, String message) {
        return new ApiException(HttpStatus.UNPROCESSABLE_ENTITY, code, message);
    }
}
