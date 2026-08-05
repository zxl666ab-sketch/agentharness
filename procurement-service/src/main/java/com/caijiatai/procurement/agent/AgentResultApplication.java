package com.caijiatai.procurement.agent;

import com.caijiatai.procurement.api.ApiException;
import com.caijiatai.procurement.approval.ApprovalService;
import com.caijiatai.procurement.artifact.BusinessArtifactRepository;
import com.caijiatai.procurement.comparison.ComparisonService;
import com.caijiatai.procurement.quote.ProcurementQuote;
import com.caijiatai.procurement.quote.ProcurementQuoteRepository;
import com.caijiatai.procurement.report.AuditEvent;
import com.caijiatai.procurement.report.AuditEventRepository;
import com.caijiatai.procurement.report.RuntimeReportProjection;
import com.caijiatai.procurement.report.RuntimeReportProjectionRepository;
import com.caijiatai.procurement.task.ProcurementTask;
import com.caijiatai.procurement.task.ProcurementTaskRepository;
import com.caijiatai.procurement.task.TaskStatus;
import java.math.BigDecimal;
import java.util.List;
import java.util.Map;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;

@Service
public final class AgentResultApplication {
    private final ProcurementTaskRepository tasks;
    private final ProcurementQuoteRepository quotes;
    private final BusinessArtifactRepository artifacts;
    private final ComparisonService comparison;
    private final ApprovalService approvals;
    private final AuditEventRepository audit;
    private final RuntimeReportProjectionRepository runtimeReports;

    public AgentResultApplication(
            ProcurementTaskRepository tasks,
            ProcurementQuoteRepository quotes,
            BusinessArtifactRepository artifacts,
            ComparisonService comparison,
            ApprovalService approvals,
            AuditEventRepository audit,
            RuntimeReportProjectionRepository runtimeReports) {
        this.tasks = tasks;
        this.quotes = quotes;
        this.artifacts = artifacts;
        this.comparison = comparison;
        this.approvals = approvals;
        this.audit = audit;
        this.runtimeReports = runtimeReports;
    }

    public void apply(AgentCommand command, Map<String, Object> envelope) {
        var result = map(envelope.get("result"));
        if (result.isEmpty()) {
            result = envelope;
        }
        switch (command.getOperationType()) {
            case "start_conversation" -> startConversation(command, result);
            case "import_quote" -> importQuote(command, result);
            case "analyze" -> analyze(command, result);
            case "approve_decision" -> {
                approvals.finalizeFromAgent(command, result);
                cacheRuntimeReport(command, result);
            }
            case "resume_run" -> resume(command, result);
            case "create_structured", "reopen_task" -> bind(command, result);
            default -> throw new ApiException(
                    HttpStatus.CONFLICT, "unknown_agent_operation", "Agent 命令类型不受支持");
        }
    }

    public void recordTerminalFailure(AgentCommand command, String error) {
        if (!"analyze".equals(command.getOperationType())) {
            return;
        }
        var task = tasks.lockById(command.getAggregateId()).orElse(null);
        if (task == null || task.getGeneration() != command.getGeneration()) {
            return;
        }
        task.restoreReadyAfterFailedAnalysis();
        audit.save(AuditEvent.create(
                task.getId(), null, task.getAnalysisRunId(), "analysis_failed", "agent",
                Map.of("operation_id", command.getOperationId(), "error", error)));
    }

    private void startConversation(AgentCommand command, Map<String, Object> result) {
        var task = lock(command);
        var requirement = map(result.get("requirement"));
        if (requirement.isEmpty()) {
            throw invalidResult("Agent 未返回结构化采购需求");
        }
        task.applyRequirement(
                integer(requirement.getOrDefault("schema_version", 1)),
                text(requirement.get("title")),
                text(requirement.getOrDefault("category", "ecommerce_packaging")),
                text(requirement.get("item_name")),
                number(requirement.get("quantity")),
                text(requirement.getOrDefault("unit", "piece")),
                map(requirement.get("specifications")),
                map(requirement.get("constraints")));
        var sessionId = text(result.get("session_id"));
        var runId = text(result.get("run_id"));
        if (sessionId.matches("[0-9a-f]{32}") && runId.matches("[0-9a-f]{32}")) {
            task.bindAgent(sessionId, runId);
        }
        for (var raw : list(result.get("quotes"))) {
            persistQuote(task, map(raw));
        }
        // Model extraction is only a draft.  A buyer must persist a review
        // through the Java API before deterministic comparison is permitted.
        task.requireRequirementReview();
        var unresolved = quotes.findByTaskIdOrderByCreatedAtAsc(task.getId()).stream()
                .mapToInt(item -> item.reviewFields().size()).sum();
        task.setStatus(TaskStatus.REVIEW);
        audit.save(AuditEvent.create(
                task.getId(), null, task.getAnalysisRunId(), "agent_parse_completed", "agent",
                Map.of("operation_id", command.getOperationId(), "unresolved_fields", unresolved)));
    }

    private void importQuote(AgentCommand command, Map<String, Object> result) {
        var task = lock(command);
        persistQuote(task, map(result.getOrDefault("quote", result)));
        var unresolved = quotes.findByTaskIdOrderByCreatedAtAsc(task.getId()).stream()
                .mapToInt(item -> item.reviewFields().size()).sum();
        task.setStatus(unresolved == 0 && task.isRequirementConfirmed()
                ? TaskStatus.READY : TaskStatus.REVIEW);
        audit.save(AuditEvent.create(
                task.getId(), null, task.getAnalysisRunId(), "agent_quote_parse_completed", "agent",
                Map.of("operation_id", command.getOperationId())));
    }

    private void analyze(AgentCommand command, Map<String, Object> result) {
        var task = lock(command);
        var runId = text(result.getOrDefault("run_id", task.getAnalysisRunId()));
        if (!runId.matches("[0-9a-f]{32}")) {
            throw invalidResult("Agent 分析结果缺少 run_id");
        }
        comparison.analyze(task, runId);
    }

    private void resume(AgentCommand command, Map<String, Object> result) {
        var task = lock(command);
        var runId = text(result.getOrDefault("run_id", task.getAnalysisRunId()));
        audit.save(AuditEvent.create(
                task.getId(), null, runId, "agent_run_resumed", "agent",
                Map.of("operation_id", command.getOperationId())));
    }

    private void bind(AgentCommand command, Map<String, Object> result) {
        var task = lock(command);
        var sessionId = text(result.get("session_id"));
        var runId = text(result.get("run_id"));
        if (!sessionId.matches("[0-9a-f]{32}") || !runId.matches("[0-9a-f]{32}")) {
            throw invalidResult("Agent 绑定结果缺少 Session 或 Run");
        }
        task.bindAgent(sessionId, runId);
        audit.save(AuditEvent.create(
                task.getId(), null, runId, "agent_binding_created", "agent",
                Map.of("operation_id", command.getOperationId())));
    }

    private void cacheRuntimeReport(AgentCommand command, Map<String, Object> result) {
        var report = map(result.get("runtime_report"));
        var evidenceSha256 = text(result.get("runtime_evidence_sha256"));
        if (!report.isEmpty() && evidenceSha256.matches("[0-9a-f]{64}")) {
            runtimeReports.save(RuntimeReportProjection.create(
                    command.getAggregateId(), text(result.get("run_id")), evidenceSha256, report));
        }
    }

    private void persistQuote(ProcurementTask task, Map<String, Object> value) {
        var artifactId = text(value.get("artifact_id"));
        var artifact = artifacts.findById(artifactId)
                .orElseThrow(() -> invalidResult("Agent 报价结果引用了未知业务 Artifact"));
        if (!task.getId().equals(artifact.getTaskId())) {
            throw invalidResult("Agent 报价结果引用了其他任务的 Artifact");
        }
        var extracted = map(value.get("extracted"));
        if (extracted.isEmpty()) {
            throw invalidResult("Agent 报价结果缺少抽取字段");
        }
        var supplier = text(value.get("supplier_name"));
        if (supplier.isBlank()) {
            supplier = text(map(map(extracted.get("fields")).get("supplier_name")).get("value"));
        }
        quotes.save(ProcurementQuote.create(
                task.getId(),
                artifactId,
                supplier,
                artifact.getFilename(),
                artifact.getFilename().toLowerCase().endsWith(".pdf") ? "pdf" : "xlsx",
                artifact.getSha256(),
                extracted,
                text(value.getOrDefault("status", "needs_review")),
                text(value.getOrDefault("parser_version", "packaging-quote-v3")),
                number(value.getOrDefault("processing_ms", "0"))));
    }

    private ProcurementTask lock(AgentCommand command) {
        var task = tasks.lockById(command.getAggregateId())
                .orElseThrow(() -> invalidResult("Agent 命令对应任务不存在"));
        if (task.getGeneration() != command.getGeneration()) {
            throw new ApiException(HttpStatus.CONFLICT, "stale_agent_result", "Agent 结果已因任务修正失效");
        }
        return task;
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> map(Object value) {
        return value instanceof Map<?, ?> raw ? (Map<String, Object>) raw : Map.of();
    }

    @SuppressWarnings("unchecked")
    private List<Object> list(Object value) {
        return value instanceof List<?> raw ? (List<Object>) raw : List.of();
    }

    private String text(Object value) { return value == null ? "" : String.valueOf(value); }
    private int integer(Object value) { return Integer.parseInt(text(value)); }
    private BigDecimal number(Object value) { return new BigDecimal(text(value)); }

    private ApiException invalidResult(String message) {
        return new ApiException(HttpStatus.CONFLICT, "invalid_agent_result", message);
    }
}
