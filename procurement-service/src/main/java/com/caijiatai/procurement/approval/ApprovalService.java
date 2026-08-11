package com.caijiatai.procurement.approval;

import com.caijiatai.procurement.agent.AgentCommand;
import com.caijiatai.procurement.agent.AgentCommandRepository;
import com.caijiatai.procurement.agent.CanonicalJson;
import com.caijiatai.procurement.agent.IdempotencyRecord;
import com.caijiatai.procurement.agent.IdempotencyRecordRepository;
import com.caijiatai.procurement.api.ApiException;
import com.caijiatai.procurement.artifact.ArtifactStore;
import com.caijiatai.procurement.comparison.ComparisonEngine;
import com.caijiatai.procurement.comparison.ComparisonSnapshotRepository;
import com.caijiatai.procurement.config.AppProperties;
import com.caijiatai.procurement.quote.ProcurementQuoteRepository;
import com.caijiatai.procurement.report.AuditEvent;
import com.caijiatai.procurement.report.AuditEventRepository;
import com.caijiatai.procurement.task.ProcurementDtos;
import com.caijiatai.procurement.task.ProcurementTaskRepository;
import com.caijiatai.procurement.task.TaskStatus;
import java.io.ByteArrayInputStream;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Objects;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class ApprovalService {
    private final ProcurementTaskRepository tasks;
    private final ProcurementQuoteRepository quotes;
    private final ComparisonSnapshotRepository snapshots;
    private final PendingDecisionRepository pendingDecisions;
    private final ProcurementDecisionRepository decisions;
    private final AgentCommandRepository commands;
    private final IdempotencyRecordRepository idempotency;
    private final ComparisonEngine engine;
    private final ArtifactStore artifacts;
    private final AuditEventRepository audit;
    private final String operator;

    public ApprovalService(
            ProcurementTaskRepository tasks,
            ProcurementQuoteRepository quotes,
            ComparisonSnapshotRepository snapshots,
            PendingDecisionRepository pendingDecisions,
            ProcurementDecisionRepository decisions,
            AgentCommandRepository commands,
            IdempotencyRecordRepository idempotency,
            ComparisonEngine engine,
            ArtifactStore artifacts,
            AuditEventRepository audit,
            AppProperties properties) {
        this.tasks = tasks;
        this.quotes = quotes;
        this.snapshots = snapshots;
        this.pendingDecisions = pendingDecisions;
        this.decisions = decisions;
        this.commands = commands;
        this.idempotency = idempotency;
        this.engine = engine;
        this.artifacts = artifacts;
        this.audit = audit;
        this.operator = properties.localOperator();
    }

    @Transactional
    public RequestResult request(String taskId, ProcurementDtos.Decision body, String idempotencyKey) {
        if (!body.confirmed()) {
            throw bad("decision_not_confirmed", "必须确认已核对报价原件与到货成本");
        }
        if (!"approved".equals(body.decision()) && !"no_award".equals(body.decision())) {
            throw bad("invalid_decision", "决定只能是 approved 或 no_award");
        }
        if ("approved".equals(body.decision()) && (body.quoteId() == null || body.quoteId().isBlank())) {
            throw bad("quote_required", "批准供应商必须选择报价");
        }
        if ("no_award".equals(body.decision()) && (body.note() == null || body.note().isBlank())) {
            throw bad("note_required", "流标必须填写原因");
        }
        var requestFingerprint = new LinkedHashMap<String, Object>();
        requestFingerprint.put("task_id", taskId);
        requestFingerprint.put("decision", body.decision());
        requestFingerprint.put("snapshot_id", body.snapshotId());
        requestFingerprint.put("input_sha256", body.inputSha256());
        requestFingerprint.put("quote_id", body.quoteId());
        requestFingerprint.put("confirmed", body.confirmed());
        requestFingerprint.put("note", body.note() == null ? "" : body.note().strip());
        var payloadSha = CanonicalJson.sha256(requestFingerprint);
        var key = idempotencyKey == null || idempotencyKey.isBlank() ? "sha256:" + payloadSha : idempotencyKey.strip();
        if (key.length() > 200) {
            throw bad("invalid_idempotency_key", "幂等键不得超过 200 个字符");
        }
        var existingRecord = idempotency.findById(new IdempotencyRecord.Key("decision", key));
        if (existingRecord.isPresent()) {
            if (!existingRecord.get().getPayloadSha256().equals(payloadSha)) {
                throw new ApiException(HttpStatus.CONFLICT, "idempotency_payload_conflict", "同一幂等键已用于不同请求内容");
            }
            var existingCommand = commands.findById(existingRecord.get().getOperationId()).orElse(null);
            return new RequestResult(
                    existingCommand,
                    existingCommand == null ? null : pendingDecisions.findByOperationId(existingCommand.getOperationId()).orElse(null),
                    decisions.findByTaskId(taskId).orElse(null));
        }
        var task = tasks.lockById(taskId).orElseThrow(() -> notFound("task_not_found", "未找到采购任务"));
        var existing = decisions.findByTaskId(taskId);
        if (existing.isPresent()) {
            return new RequestResult(null, null, existing.get());
        }
        if (!Objects.equals(task.getCurrentSnapshotId(), body.snapshotId())) {
            throw stale();
        }
        var snapshot = snapshots.findByIdAndTaskId(body.snapshotId(), taskId).orElseThrow(this::stale);
        if (!snapshot.getInputSha256().equals(body.inputSha256())) {
            throw stale();
        }
        validateSelection(snapshot.getResult(), body.decision(), body.quoteId());
        var pendingId = UUID.randomUUID().toString().replace("-", "");
        var operationId = UUID.randomUUID().toString();
        var note = body.note() == null ? "" : body.note().strip();
        var noteHash = CanonicalJson.sha256(Map.of("note", note));
        task.setStatus(TaskStatus.APPROVAL_PENDING);
        var expectedVersion = task.getVersion() + 1;
        var binding = binding(
                pendingId,
                task.getAnalysisRunId(),
                expectedVersion,
                snapshot.getId(),
                snapshot.getInputSha256(),
                body.decision(),
                body.quoteId(),
                noteHash);
        var pending = pendingDecisions.save(PendingDecision.create(
                pendingId,
                operationId,
                taskId,
                task.getAnalysisRunId(),
                expectedVersion,
                snapshot.getId(),
                snapshot.getInputSha256(),
                body.decision(),
                body.quoteId(),
                noteHash));
        var payload = new LinkedHashMap<String, Object>(binding);
        payload.put("note", note);
        var command = commands.save(AgentCommand.accept(
                operationId,
                "approve_decision",
                taskId,
                task.getGeneration(),
                expectedVersion,
                payload));
        commands.alignTimestampsToDbClock(command.getOperationId());
        idempotency.save(IdempotencyRecord.reserve("decision", key, payloadSha, operationId));
        audit.save(AuditEvent.create(
                taskId, body.quoteId(), task.getAnalysisRunId(), "supplier_approval_requested", operator,
                Map.of(
                        "pending_decision_id", pending.getId(),
                        "operation_id", operationId,
                        "binding_sha256", CanonicalJson.sha256(binding))));
        return new RequestResult(command, pending, null);
    }

    @Transactional
    public ProcurementDecision finalizeFromAgent(AgentCommand command, Map<String, Object> result) {
        var pending = pendingDecisions.findByOperationId(command.getOperationId())
                .orElseThrow(() -> invalidApproval("待决审批不存在"));
        var existing = decisions.findByPendingDecisionId(pending.getId());
        if (existing.isPresent()) {
            return existing.get();
        }
        var approval = map(result.getOrDefault("approval", result));
        var approvalAt = instant(approval.get("created_at"));
        var expectedBinding = binding(
                pending.getId(), pending.getRunId(), pending.getTaskVersion(), pending.getSnapshotId(),
                pending.getInputSha256(), pending.getDecision(), pending.getQuoteId(), pending.getNoteHash());
        requireEquals("pending_decision_id", pending.getId(), approval.get("pending_decision_id"));
        requireEquals("run_id", pending.getRunId(), approval.get("run_id"));
        requireEquals("tool_name", "procurement_approve_supplier", approval.get("tool_name"));
        requireEquals("task_version", String.valueOf(pending.getTaskVersion()), approval.get("task_version"));
        requireEquals("snapshot_id", pending.getSnapshotId(), approval.get("snapshot_id"));
        requireEquals("input_sha256", pending.getInputSha256(), approval.get("input_sha256"));
        requireEquals("decision", pending.getDecision(), approval.get("business_decision"));
        requireEquals("quote_id", pending.getQuoteId(), approval.get("quote_id"));
        requireEquals("note_hash", pending.getNoteHash(), approval.get("note_hash"));
        // The explicit buyer confirmation is created by this Java control plane
        // before the Agent command exists.  The Agent only records that exact
        // fact; it must not auto-resolve a second Harness approval.
        requireEquals("approval_decision", "formal_java_confirmation", approval.get("decision"));
        requireEquals("confirmation_source", "java_control_plane", approval.get("confirmation_source"));
        requireEquals("arguments_sha256", CanonicalJson.sha256(expectedBinding), approval.get("arguments_sha256"));
        var task = tasks.lockById(pending.getTaskId()).orElseThrow(() -> stale());
        if (task.getVersion() != pending.getTaskVersion()
                || task.getGeneration() != command.getGeneration()
                || !Objects.equals(task.getCurrentSnapshotId(), pending.getSnapshotId())) {
            pending.stale();
            throw stale();
        }
        var snapshot = snapshots.findByIdAndTaskId(pending.getSnapshotId(), task.getId()).orElseThrow(this::stale);
        var recalculated = engine.compare(task, quotes.findByTaskIdOrderByCreatedAtAsc(task.getId()));
        if (!recalculated.inputSha256().equals(snapshot.getInputSha256())) {
            pending.stale();
            throw stale();
        }
        validateSelection(recalculated.result(), pending.getDecision(), pending.getQuoteId());
        pending.approve(
                text(approval.get("id")), text(approval.get("arguments_sha256")),
                text(approval.get("decision")), approvalAt);
        var note = text(command.getPayload().get("note"));
        if (!CanonicalJson.sha256(Map.of("note", note)).equals(pending.getNoteHash())) {
            throw invalidApproval("审批备注摘要不匹配");
        }
        var decision = decisions.save(ProcurementDecision.create(pending, note.isBlank() ? null : note, operator));
        task.finalizeDecision(pending.getQuoteId(), "no_award".equals(pending.getDecision()));
        pending.complete();
        createExecutionArtifacts(task.getReference(), task.getId(), decision, snapshot);
        audit.save(AuditEvent.create(
                task.getId(), pending.getQuoteId(), pending.getRunId(), "procurement_decision_finalized", operator,
                Map.of(
                        "decision_id", decision.getId(),
                        "pending_decision_id", pending.getId(),
                        "approval_id", decision.getApprovalId())));
        return decision;
    }

    private void createExecutionArtifacts(
            String reference,
            String taskId,
            ProcurementDecision decision,
            com.caijiatai.procurement.comparison.ComparisonSnapshot snapshot) {
        if ("no_award".equals(decision.getDecision())) {
            return;
        }
        var quote = quotes.findByIdAndTaskId(decision.getQuoteId(), taskId).orElseThrow(this::stale);
        var order = "采购订单草稿\n采购编号：" + reference + "\n供应商：" + quote.getSupplierName()
                + "\n报价证据：" + quote.getSourceSha256() + "\n比价输入：" + snapshot.getInputSha256() + "\n";
        var mail = "主题：" + reference + " 供应商确认\n\n" + quote.getSupplierName()
                + "：\n采购决定已完成，请人工核对订单条款后回复确认。\n";
        artifacts.store(
                "purchase_order_draft", taskId, reference + "-采购订单草稿.txt", "text/plain; charset=utf-8",
                new ByteArrayInputStream(order.getBytes(StandardCharsets.UTF_8)),
                Map.of("decision_id", decision.getId(), "run_id", decision.getRunId()));
        artifacts.store(
                "supplier_confirmation_email", taskId, reference + "-供应商确认邮件.txt", "text/plain; charset=utf-8",
                new ByteArrayInputStream(mail.getBytes(StandardCharsets.UTF_8)),
                Map.of("decision_id", decision.getId(), "run_id", decision.getRunId()));
    }

    @SuppressWarnings("unchecked")
    private void validateSelection(Map<String, Object> result, String decision, String quoteId) {
        var eligible = ((java.util.List<Object>) result.getOrDefault("quotes", java.util.List.of())).stream()
                .map(this::map)
                .filter(item -> Boolean.TRUE.equals(item.get("eligible")))
                .map(item -> text(item.get("quote_id")))
                .toList();
        if ("approved".equals(decision) && !eligible.contains(quoteId)) {
            throw new ApiException(HttpStatus.CONFLICT, "quote_not_eligible", "只能批准当前快照中的合格报价");
        }
        if ("no_award".equals(decision) && !eligible.isEmpty()) {
            throw new ApiException(HttpStatus.CONFLICT, "eligible_quotes_exist", "存在合格报价时不能流标");
        }
    }

    private Map<String, Object> binding(
            String pendingId,
            String runId,
            long taskVersion,
            String snapshotId,
            String inputSha256,
            String decision,
            String quoteId,
            String noteHash) {
        var binding = new LinkedHashMap<String, Object>();
        binding.put("pending_decision_id", pendingId);
        binding.put("run_id", runId);
        binding.put("tool_name", "procurement_approve_supplier");
        binding.put("task_version", taskVersion);
        binding.put("snapshot_id", snapshotId);
        binding.put("input_sha256", inputSha256);
        binding.put("business_decision", decision);
        binding.put("quote_id", quoteId);
        binding.put("note_hash", noteHash);
        return binding;
    }

    private void requireEquals(String field, Object expected, Object actual) {
        if (!Objects.equals(expected == null ? null : String.valueOf(expected), actual == null ? null : String.valueOf(actual))) {
            throw invalidApproval("审批绑定字段不匹配：" + field);
        }
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> map(Object value) {
        return value instanceof Map<?, ?> raw ? (Map<String, Object>) raw : Map.of();
    }

    private String text(Object value) { return value == null ? "" : String.valueOf(value); }

    private Instant instant(Object value) {
        try {
            return Instant.parse(text(value));
        } catch (RuntimeException error) {
            throw invalidApproval("审批时间格式无效");
        }
    }

    private ApiException bad(String code, String message) {
        return new ApiException(HttpStatus.BAD_REQUEST, code, message);
    }
    private ApiException notFound(String code, String message) {
        return new ApiException(HttpStatus.NOT_FOUND, code, message);
    }
    private ApiException stale() {
        return new ApiException(HttpStatus.CONFLICT, "stale_approval", "审批证据已因任务或报价变化失效");
    }
    private ApiException invalidApproval(String message) {
        return new ApiException(HttpStatus.CONFLICT, "invalid_agent_approval", message);
    }

    public record RequestResult(AgentCommand command, PendingDecision pending, ProcurementDecision decision) {}
}
