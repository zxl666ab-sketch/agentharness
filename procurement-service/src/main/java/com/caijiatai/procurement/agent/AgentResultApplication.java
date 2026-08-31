package com.caijiatai.procurement.agent;

import com.caijiatai.procurement.api.ApiException;
import com.caijiatai.procurement.ai.AiTaskService;
import com.caijiatai.procurement.ai.AiErrorCategory;
import com.caijiatai.procurement.approval.ApprovalService;
import com.caijiatai.procurement.artifact.BusinessArtifactRepository;
import com.caijiatai.procurement.cache.TaskContextCache;
import com.caijiatai.procurement.comparison.ComparisonService;
import com.caijiatai.procurement.contract.ContractService;
import com.caijiatai.procurement.invoice.InvoiceService;
import com.caijiatai.procurement.interaction.HumanInteractionService;
import com.caijiatai.procurement.quote.ProcurementQuote;
import com.caijiatai.procurement.quote.ProcurementQuoteRepository;
import com.caijiatai.procurement.report.AuditEvent;
import com.caijiatai.procurement.report.AuditEventRepository;
import com.caijiatai.procurement.report.RuntimeReportProjection;
import com.caijiatai.procurement.report.RuntimeReportProjectionRepository;
import com.caijiatai.procurement.review.ReviewService;
import com.caijiatai.procurement.task.ProcurementTask;
import com.caijiatai.procurement.task.ProcurementTaskRepository;
import com.caijiatai.procurement.task.RequirementValidator;
import com.caijiatai.procurement.task.TaskStatus;
import java.math.BigDecimal;
import java.util.List;
import java.util.Map;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

@Service
public final class AgentResultApplication {
    private final ProcurementTaskRepository tasks;
    private final ProcurementQuoteRepository quotes;
    private final BusinessArtifactRepository artifacts;
    private final ComparisonService comparison;
    private final ApprovalService approvals;
    private final AuditEventRepository audit;
    private final RuntimeReportProjectionRepository runtimeReports;
    private final AiTaskService aiTasks;
    private final ReviewService reviews;
    private final TaskContextCache contextCache;
    private final com.caijiatai.procurement.cache.InsightsCache insightsCache;
    private final InvoiceService invoices;
    private final ContractService contracts;
    private final HumanInteractionService interactions;

    public AgentResultApplication(
            ProcurementTaskRepository tasks,
            ProcurementQuoteRepository quotes,
            BusinessArtifactRepository artifacts,
            ComparisonService comparison,
            ApprovalService approvals,
            AuditEventRepository audit,
            RuntimeReportProjectionRepository runtimeReports,
            AiTaskService aiTasks,
            ReviewService reviews,
            TaskContextCache contextCache,
            com.caijiatai.procurement.cache.InsightsCache insightsCache,
            InvoiceService invoices,
            ContractService contracts,
            HumanInteractionService interactions) {
        this.tasks = tasks;
        this.quotes = quotes;
        this.artifacts = artifacts;
        this.comparison = comparison;
        this.approvals = approvals;
        this.audit = audit;
        this.runtimeReports = runtimeReports;
        this.aiTasks = aiTasks;
        this.reviews = reviews;
        this.contextCache = contextCache;
        this.insightsCache = insightsCache;
        this.invoices = invoices;
        this.contracts = contracts;
        this.interactions = interactions;
    }

    public void apply(AgentCommand command, Map<String, Object> envelope) {
        try {
            var result = map(envelope.get("result"));
            if (result.isEmpty()) {
                result = envelope;
            }
            switch (command.getOperationType()) {
                case "start_conversation" -> startConversation(command, result);
                case "import_quote" -> importQuote(command, result);
                case "analyze" -> analyze(command, result);
                case "approve_decision" -> {
                    var decision = approvals.finalizeFromAgent(command, result);
                    reviews.finalizeDecision(decision);
                    cacheRuntimeReport(command, result);
                }
                case "resume_run" -> resume(command, result);
                case "human_interaction_answer" -> applyInteractionAnswer(command, result);
                case "create_structured", "reopen_task" -> bind(command, result);
                case "parse_invoice" -> invoices.applyParseResult(command, result);
                case "explain_invoice_diff" -> invoices.applyExplanation(command, result);
                case "draft_contract" -> contracts.applyDraftResult(command, result);
                default -> throw new ApiException(
                        HttpStatus.CONFLICT, "unknown_agent_operation", "Agent 命令类型不受支持");
            }
        } finally {
            evictContext(command.getAggregateId());
        }
    }

    public void recordTerminalFailure(AgentCommand command, String error) {
        recordTerminalFailure(command, error, AiErrorCategory.BUSINESS, false);
    }

    public void recordTerminalFailure(
            AgentCommand command,
            String error,
            AiErrorCategory category,
            boolean retryable) {
        try {
            if ("analyze".equals(command.getOperationType())) {
                aiTasks.fail(command, error, category, retryable);
                var task = tasks.lockById(command.getAggregateId()).orElse(null);
                if (task == null || task.getGeneration() != command.getGeneration()) {
                    return;
                }
                task.restoreReadyAfterFailedAnalysis();
                audit.save(AuditEvent.create(
                        task.getId(), null, task.getAnalysisRunId(), "analysis_failed", "agent",
                        Map.of("operation_id", command.getOperationId(), "error", error)));
                return;
            }
            if ("approve_decision".equals(command.getOperationType())) {
                reviews.failPendingDecision(command.getOperationId(), error);
                return;
            }
            if ("start_conversation".equals(command.getOperationType())) {
                var task = tasks.lockById(command.getAggregateId()).orElse(null);
                if (task == null || task.getGeneration() != command.getGeneration()) {
                    return;
                }
                // 需求与报价未成功落库的草稿无法从 UI 恢复，取消它避免“Agent 读取中”死任务。
                if ((TaskStatus.DRAFT.wireValue().equals(task.getStatus())
                        || TaskStatus.COLLECTING.wireValue().equals(task.getStatus()))
                        && quotes.countByTaskId(task.getId()) == 0) {
                    task.setStatus(TaskStatus.CANCELLED);
                }
                audit.save(AuditEvent.create(
                        task.getId(), null, task.getAnalysisRunId(), "conversation_failed", "agent",
                        Map.of("operation_id", command.getOperationId(), "error", error)));
            }
        } finally {
            evictContext(command.getAggregateId());
        }
    }

    private void evictContext(String taskId) {
        contextCache.evict(taskId);
        // 看板纪律：分析完成/正式决定/报价落库/失败都会改变状态漏斗、订单数、
        // ai_tasks_failed 与成本节约率，异步结果应用后必须主动失效看板缓存。
        // apply() 运行在消费者事务内：立即清一次 + afterCommit 再清一次，
        // 防止「清缓存后、提交前」的并发读把旧数据回填进缓存（TTL 内一直旧）。
        insightsCache.evictAll();
        if (TransactionSynchronizationManager.isSynchronizationActive()) {
            TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
                @Override
                public void afterCommit() {
                    contextCache.evict(taskId);
                    insightsCache.evictAll();
                }
            });
        }
    }

    private void startConversation(AgentCommand command, Map<String, Object> result) {
        var task = lock(command);
        var sessionId = text(result.get("session_id"));
        var runId = text(result.get("run_id"));
        if (sessionId.matches("[0-9a-f]{32}") && runId.matches("[0-9a-f]{32}")) {
            task.bindAgent(sessionId, runId);
        }
        var interaction = map(result.get("interaction"));
        if (!interaction.isEmpty()) {
            interactions.createFromAgent(task.getId(), runId, task.getGeneration(), interaction);
            task.setStatus(TaskStatus.WAITING_HUMAN);
            audit.save(AuditEvent.create(
                    task.getId(), null, runId, "conversation_waiting_human", "agent",
                    Map.of("operation_id", command.getOperationId())));
            return;
        }
        var requirement = map(result.get("requirement"));
        if (requirement.isEmpty()) {
            throw invalidResult("Agent 未返回结构化采购需求");
        }
        var schemaVersion = integer(requirement.getOrDefault("schema_version", 1));
        var category = text(requirement.getOrDefault("category", "ecommerce_packaging"));
        var itemName = text(requirement.get("item_name")).strip();
        var title = text(requirement.get("title")).strip();
        if (title.isBlank() || title.length() > 200) {
            throw invalidResult("Agent 未返回有效采购标题");
        }
        var quantity = number(requirement.get("quantity"));
        var unit = text(requirement.getOrDefault("unit", "piece"));
        var specifications = normalizeSpecifications(map(requirement.get("specifications")));
        var constraints = map(requirement.get("constraints"));
        RequirementValidator.validate(schemaVersion, category, itemName, quantity, unit, specifications,
                decimal(constraints.get("size_tolerance_mm")),
                decimal(constraints.get("thickness_tolerance_um")),
                decimal(constraints.get("max_landed_unit_cost")));
        task.applyRequirement(schemaVersion, title, category, itemName, quantity, unit,
                specifications, constraints);
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
        var parsedQuotes = list(result.get("quotes"));
        if (parsedQuotes.isEmpty()) {
            parsedQuotes = List.of(result.getOrDefault("quote", result));
        }
        for (var raw : parsedQuotes) {
            var quote = map(raw);
            // J-M4: at-least-once result delivery can replay an import; skip quotes
            // whose source artifact was already imported for this task (same guard as
            // applyInteractionAnswer) so retries cannot create duplicate quote rows.
            var artifactId = text(quote.get("artifact_id"));
            if (!artifactId.isBlank() && quotes.existsByTaskIdAndSourceArtifactId(task.getId(), artifactId)) {
                continue;
            }
            persistQuote(task, quote);
        }
        var unresolved = quotes.findByTaskIdOrderByCreatedAtAsc(task.getId()).stream()
                .mapToInt(item -> item.reviewFields().size()).sum();
        task.setStatus(unresolved == 0 && task.isRequirementConfirmed()
                ? TaskStatus.READY : TaskStatus.REVIEW);
        audit.save(AuditEvent.create(
                task.getId(), null, task.getAnalysisRunId(), "agent_quote_parse_completed", "agent",
                Map.of("operation_id", command.getOperationId(), "quote_count", parsedQuotes.size())));
    }

    private void analyze(AgentCommand command, Map<String, Object> result) {
        var task = lock(command);
        var runId = text(result.getOrDefault("run_id", task.getAnalysisRunId()));
        if (!runId.matches("[0-9a-f]{32}")) {
            throw invalidResult("Agent 分析结果缺少 run_id");
        }
        task.bindAnalysisRun(runId);
        var snapshot = comparison.analyze(task, runId);
        var aiResult = aiTasks.succeed(command, result);
        tasks.flush();
        reviews.createPending(task, command.getOperationId(), aiResult, snapshot);
    }

    private void resume(AgentCommand command, Map<String, Object> result) {
        var task = lock(command);
        var runId = text(result.getOrDefault("run_id", task.getAnalysisRunId()));
        audit.save(AuditEvent.create(
                task.getId(), null, runId, "agent_run_resumed", "agent",
                Map.of("operation_id", command.getOperationId())));
    }

    private void applyInteractionAnswer(AgentCommand command, Map<String, Object> result) {
        var task = lock(command);
        var interactionId = text(result.getOrDefault("interaction_id", command.getPayload().get("interaction_id")));
        interactions.applied(interactionId);
        var sessionId = text(result.get("session_id"));
        var runId = text(result.getOrDefault("run_id", task.getAnalysisRunId()));
        if (sessionId.matches("[0-9a-f]{32}") && runId.matches("[0-9a-f]{32}")) {
            task.bindAgent(sessionId, runId);
        } else if (runId.matches("[0-9a-f]{32}")) {
            task.bindAnalysisRun(runId);
        }
        var requirement = map(result.get("requirement"));
        if (!requirement.isEmpty()) {
            var schemaVersion = integer(requirement.getOrDefault("schema_version", 1));
            var category = text(requirement.getOrDefault("category", "ecommerce_packaging"));
            var itemName = text(requirement.get("item_name")).strip();
            var title = text(requirement.get("title")).strip();
            if (title.isBlank() || title.length() > 200) {
                throw invalidResult("Agent 恢复后未返回有效采购标题");
            }
            var quantity = number(requirement.get("quantity"));
            var unit = text(requirement.get("unit")).strip();
            var specifications = normalizeSpecifications(map(requirement.get("specifications")));
            var constraints = map(requirement.get("constraints"));
            RequirementValidator.validate(schemaVersion, category, itemName, quantity, unit, specifications,
                    decimal(constraints.get("size_tolerance_mm")),
                    decimal(constraints.get("thickness_tolerance_um")),
                    decimal(constraints.get("max_landed_unit_cost")));
            task.applyRequirement(schemaVersion, title, category, itemName, quantity, unit,
                    specifications, constraints);
            task.requireRequirementReview();
            for (var raw : list(result.get("quotes"))) {
                var quote = map(raw);
                var artifactId = text(quote.get("artifact_id"));
                if (!quotes.existsByTaskIdAndSourceArtifactId(task.getId(), artifactId)) {
                    persistQuote(task, quote);
                }
            }
        }
        var nextQuestion = map(result.get("interaction"));
        if (!nextQuestion.isEmpty()) {
            interactions.createFromAgent(task.getId(), runId, task.getGeneration(), nextQuestion);
            task.setStatus(TaskStatus.WAITING_HUMAN);
        } else if (!requirement.isEmpty() || TaskStatus.WAITING_HUMAN.wireValue().equals(task.getStatus())) {
            task.setStatus(TaskStatus.REVIEW);
        }
        audit.save(AuditEvent.create(
                task.getId(), null, runId, "human_interaction_result_applied", "agent",
                Map.of(
                        "interaction_id", interactionId,
                        "operation_id", command.getOperationId(),
                        "rebuilt", Boolean.TRUE.equals(result.get("rebuilt")))));
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

    private BigDecimal decimal(Object value) {
        if (value == null || text(value).isBlank()) return null;
        try {
            return new BigDecimal(text(value));
        } catch (NumberFormatException error) {
            throw invalidResult("Agent 需求约束不是有效数值");
        }
    }

    private Map<String, Object> normalizeSpecifications(Map<String, Object> input) {
        var result = new java.util.LinkedHashMap<>(input);
        var printColors = text(result.get("print_colors")).trim();
        if (!printColors.isBlank()) {
            if (printColors.matches("[0-9]+")) result.put("print_colors", printColors);
            else if (printColors.matches("[0-9]+\\s*色")) {
                result.put("print_colors", printColors.replaceAll("[^0-9].*", ""));
            } else if (printColors.matches("单色|一色|单色印刷|一色印刷|one[- ]?colou?r")) {
                result.put("print_colors", "1");
            } else if (printColors.matches("双色|二色|双色印刷|二色印刷|two[- ]?colou?rs?")) {
                result.put("print_colors", "2");
            }
        }
        return result;
    }

    private ApiException invalidResult(String message) {
        return new ApiException(HttpStatus.CONFLICT, "invalid_agent_result", message);
    }
}
