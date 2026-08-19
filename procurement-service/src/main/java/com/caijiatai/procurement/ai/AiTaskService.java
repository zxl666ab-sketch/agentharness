package com.caijiatai.procurement.ai;

import com.caijiatai.procurement.agent.AgentCommand;
import com.caijiatai.procurement.agent.AgentCommandRepository;
import com.caijiatai.procurement.agent.CanonicalJson;
import com.caijiatai.procurement.agent.IdempotencyRecord;
import com.caijiatai.procurement.agent.IdempotencyRecordRepository;
import com.caijiatai.procurement.api.ApiException;
import com.caijiatai.procurement.config.AppProperties;
import com.caijiatai.procurement.quote.ProcurementQuoteRepository;
import com.caijiatai.procurement.report.AuditEvent;
import com.caijiatai.procurement.report.AuditEventRepository;
import com.caijiatai.procurement.task.ProcurementTask;
import com.caijiatai.procurement.task.ProcurementTaskRepository;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Sort;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class AiTaskService {
    private final ProcurementTaskRepository businessTasks;
    private final ProcurementQuoteRepository quotes;
    private final AgentCommandRepository commands;
    private final IdempotencyRecordRepository idempotency;
    private final AiTaskRepository tasks;
    private final AiTaskRecordRepository records;
    private final AiResultRepository results;
    private final AuditEventRepository audit;
    private final AiTaskViewMapper views;
    private final String operator;

    public AiTaskService(
            ProcurementTaskRepository businessTasks,
            ProcurementQuoteRepository quotes,
            AgentCommandRepository commands,
            IdempotencyRecordRepository idempotency,
            AiTaskRepository tasks,
            AiTaskRecordRepository records,
            AiResultRepository results,
            AuditEventRepository audit,
            AiTaskViewMapper views,
            AppProperties properties) {
        this.businessTasks = businessTasks;
        this.quotes = quotes;
        this.commands = commands;
        this.idempotency = idempotency;
        this.tasks = tasks;
        this.records = records;
        this.results = results;
        this.audit = audit;
        this.views = views;
        this.operator = properties.localOperator();
    }

    @Transactional
    public Launch create(
            String businessId,
            AiTaskType taskType,
            String idempotencyKey) {
        var type = taskType == null ? AiTaskType.QUOTE_ANALYSIS : taskType;
        var business = businessTasks.lockById(businessId)
                .orElseThrow(() -> notFound("task_not_found", "未找到采购任务"));
        validateAnalysisInput(business);
        var taskQuotes = quotes.findByTaskIdOrderByCreatedAtAsc(businessId);
        var fileIds = taskQuotes.stream().map(item -> item.getSourceArtifactId()).distinct().sorted().toList();
        var semanticInput = new LinkedHashMap<String, Object>();
        semanticInput.put("business_id", businessId);
        semanticInput.put("generation", business.getGeneration());
        semanticInput.put("task_version", business.getVersion());
        semanticInput.put("task_type", type.name());
        semanticInput.put("file_ids", fileIds);
        semanticInput.put("quote_sha256", taskQuotes.stream()
                .map(item -> item.getSourceSha256()).sorted().toList());
        var inputSha = CanonicalJson.sha256(semanticInput);
        var key = normalizeKey(idempotencyKey, inputSha);
        var requestSha = CanonicalJson.sha256(Map.of(
                "business_id", businessId,
                "generation", business.getGeneration(),
                "task_type", type.name(),
                "input_sha256", inputSha));
        var existing = idempotency.findById(new IdempotencyRecord.Key("ai_task", key));
        if (existing.isPresent()) {
            return replay(existing.get(), requestSha);
        }

        var aiTask = AiTask.create(
                businessId,
                type,
                business.getGeneration(),
                business.getVersion(),
                inputSha,
                key,
                operator,
                3);
        var payload = new LinkedHashMap<String, Object>(semanticInput);
        payload.put("task_id", businessId);
        payload.put("ai_task_id", aiTask.getId());
        payload.put("trace_id", aiTask.getTraceId());
        payload.put("input_sha256", inputSha);
        var command = commands.save(AgentCommand.accept(
                "analyze",
                businessId,
                business.getGeneration(),
                business.getVersion(),
                payload));
        commands.alignTimestampsToDbClock(command.getOperationId());
        aiTask.bindOperation(command.getOperationId());
        tasks.save(aiTask);
        records.save(AiTaskRecord.pending(aiTask.getId(), command.getOperationId(), 1));
        idempotency.save(IdempotencyRecord.reserve("ai_task", key, requestSha, command.getOperationId()));
        audit.save(AuditEvent.create(
                businessId,
                null,
                business.getAnalysisRunId(),
                "ai_task_created",
                operator,
                Map.of(
                        "ai_task_id", aiTask.getId(),
                        "operation_id", command.getOperationId(),
                        "generation", aiTask.getGeneration(),
                        "input_sha256", inputSha)));
        return new Launch(aiTask, command, business);
    }

    @Transactional(readOnly = true)
    public Map<String, Object> detail(String id) {
        var task = tasks.findById(id).orElseThrow(() -> notFound("ai_task_not_found", "未找到 AI 任务"));
        return views.detail(
                task,
                records.findByAiTaskIdOrderByAttemptAscSequenceAscCreatedAtAsc(id),
                results.findByAiTaskId(id).orElse(null));
    }

    @Transactional(readOnly = true)
    public Map<String, Object> operationDetail(String operationId) {
        var task = tasks.findByOperationId(operationId)
                .orElseThrow(() -> notFound("ai_task_not_found", "该操作没有关联 AI 任务"));
        return detail(task.getId());
    }

    @Transactional(readOnly = true)
    public Map<String, Object> list(
            AiTaskStatus status,
            AiTaskType taskType,
            String businessId,
            int page,
            int size) {
        var pageable = PageRequest.of(
                Math.max(0, page),
                Math.min(100, Math.max(1, size)),
                Sort.by(Sort.Order.desc("updatedAt"), Sort.Order.desc("id")));
        Specification<AiTask> spec = (root, query, cb) -> cb.conjunction();
        if (status != null) spec = spec.and((root, query, cb) -> cb.equal(root.get("status"), status));
        if (taskType != null) spec = spec.and((root, query, cb) -> cb.equal(root.get("taskType"), taskType));
        if (businessId != null && !businessId.isBlank()) {
            spec = spec.and((root, query, cb) -> cb.equal(root.get("businessId"), businessId));
        }
        Page<AiTask> result = tasks.findAll(spec, pageable);
        return Map.of(
                "items", result.getContent().stream().map(views::summary).toList(),
                "page", result.getNumber(),
                "size", result.getSize(),
                "total", result.getTotalElements());
    }

    @Transactional
    public void markDispatching(AgentCommand command) {
        tasks.lockByOperationId(command.getOperationId()).ifPresent(AiTask::dispatching);
    }

    @Transactional
    public void deliveryDeferred(AgentCommand command, String error) {
        tasks.lockByOperationId(command.getOperationId())
                .ifPresent(task -> task.deliveryDeferred(error));
    }

    @Transactional
    public void applyStepEvent(Map<String, Object> envelope) {
        var aiTaskId = text(envelope.get("ai_task_id"));
        var operationId = text(envelope.get("operation_id"));
        if (aiTaskId.isBlank() || operationId.isBlank()) return;
        var task = tasks.lockById(aiTaskId).orElse(null);
        if (task == null || !operationId.equals(task.getOperationId())) return;
        var attempt = integer(envelope.getOrDefault("attempt", task.getRetryCount() + 1));
        var sequence = integer(envelope.getOrDefault("sequence", 0));
        if (records.existsByAiTaskIdAndAttemptAndSequence(aiTaskId, attempt, sequence)) return;
        var step = AiTaskStep.valueOf(text(envelope.get("step")));
        var stepStatus = AiStepStatus.valueOf(text(envelope.get("step_status")));
        var occurredAt = instant(envelope.get("occurred_at"));
        var startedAt = stepStatus == AiStepStatus.RUNNING ? occurredAt : null;
        var finishedAt = switch (stepStatus) {
            case SUCCEEDED, FAILED, SKIPPED -> occurredAt;
            default -> null;
        };
        var errorCategory = nullableEnum(AiErrorCategory.class, envelope.get("error_category"));
        records.save(AiTaskRecord.create(
                aiTaskId,
                operationId,
                attempt,
                sequence,
                step,
                stepStatus,
                nullableText(envelope.get("summary")),
                errorCategory,
                nullableText(envelope.get("error_code")),
                nullableText(envelope.get("error_message")),
                startedAt,
                finishedAt));
        if (task.getStatus() == AiTaskStatus.SUCCEEDED
                || task.getStatus() == AiTaskStatus.CANCELLED) {
            return; // Kafka topics are independently ordered; never regress a terminal result.
        }
        var progress = decimal(envelope.getOrDefault("progress", task.getProgress()));
        if (stepStatus == AiStepStatus.RUNNING || stepStatus == AiStepStatus.SUCCEEDED) {
            task.running(step, progress);
        } else if (stepStatus == AiStepStatus.FAILED) {
            task.failed(
                    errorCategory == null ? AiErrorCategory.INTERNAL : errorCategory,
                    text(envelope.getOrDefault("error_code", "AI_STEP_FAILED")),
                    text(envelope.getOrDefault("error_message", "AI 步骤失败")),
                    Boolean.TRUE.equals(envelope.get("retryable")));
        }
    }

    @Transactional
    public AiResult succeed(AgentCommand command, Map<String, Object> value) {
        var task = tasks.lockByOperationId(command.getOperationId())
                .orElseThrow(() -> conflict("ai_task_state_missing", "AI 操作缺少任务状态"));
        if (task.getGeneration() != command.getGeneration()) {
            task.markStale("INPUT_GENERATION_CHANGED");
            throw conflict("stale_ai_result", "AI 结果已因采购输入变化失效");
        }
        var existing = results.findByAiTaskId(task.getId());
        if (existing.isPresent()) return existing.get();
        var inputSha = text(value.getOrDefault("input_sha256", task.getInputSha256()));
        if (!task.getInputSha256().equals(inputSha)) {
            throw conflict("ai_result_input_mismatch", "AI 结果输入指纹与任务不一致");
        }
        var structured = map(value.get("structured_result"));
        if (structured.isEmpty()) {
            structured = Map.of("summary", "确定性比价输入已完成校验");
        }
        var result = results.save(AiResult.create(
                task.getId(),
                task.getBusinessId(),
                task.getGeneration(),
                task.getInputSha256(),
                mapOrNull(value.get("raw_result")),
                structured,
                mapList(value.get("sources")),
                nullableText(value.get("provider")),
                nullableText(value.get("model")),
                text(value.getOrDefault("prompt_version", "quote-analysis-v1")),
                nullableText(value.get("parser_version"))));
        task.succeeded(result.getId());
        audit.save(AuditEvent.create(
                task.getBusinessId(),
                null,
                null,
                "ai_task_succeeded",
                "agent",
                Map.of(
                        "ai_task_id", task.getId(),
                        "ai_result_id", result.getId(),
                        "input_sha256", task.getInputSha256(),
                        "result_sha256", result.getResultSha256())));
        return result;
    }

    @Transactional
    public AiTask fail(AgentCommand command, String error, AiErrorCategory category, boolean retryable) {
        var task = tasks.lockByOperationId(command.getOperationId()).orElse(null);
        if (task != null) {
            if (task.getStatus() == AiTaskStatus.SUCCEEDED || task.getStatus() == AiTaskStatus.CANCELLED) {
                return task;
            }
            if (task.getStatus() == AiTaskStatus.FAILED) return task;
            task.failed(category, errorCode(error), error, retryable);
            var attempt = task.getRetryCount() + 1;
            var sequence = records.findFirstByAiTaskIdAndAttemptOrderBySequenceDesc(task.getId(), attempt)
                    .map(record -> record.getSequence() + 1)
                    .orElse(1);
            records.save(AiTaskRecord.create(
                    task.getId(), command.getOperationId(), attempt, sequence,
                    task.getCurrentStep() == null ? AiTaskStep.INPUT_VALIDATE : task.getCurrentStep(),
                    AiStepStatus.FAILED, "AI 任务失败", category, errorCode(error), error,
                    null, Instant.now()));
            audit.save(AuditEvent.create(
                    task.getBusinessId(),
                    null,
                    null,
                    "ai_task_failed",
                    "agent",
                    Map.of(
                            "ai_task_id", task.getId(),
                            "operation_id", command.getOperationId(),
                            "error_code", errorCode(error))));
        }
        return task;
    }

    @Transactional
    public AiTask retry(String aiTaskId, String idempotencyKey, boolean automatic) {
        var task = tasks.lockById(aiTaskId)
                .orElseThrow(() -> notFound("ai_task_not_found", "未找到 AI 任务"));
        var key = normalizeKey(idempotencyKey, "retry-" + UUID.randomUUID());
        var scope = "ai_task_retry:" + aiTaskId;
        var requestSha = CanonicalJson.sha256(Map.of(
                "ai_task_id", aiTaskId,
                "action", "retry"));
        var replay = idempotency.findById(new IdempotencyRecord.Key(scope, key));
        if (replay.isPresent()) {
            if (!replay.get().getPayloadSha256().equals(requestSha)) {
                throw conflict("idempotency_payload_conflict", "同一幂等键已用于其他 AI 重试");
            }
            return tasks.findById(aiTaskId)
                    .orElseThrow(() -> conflict("ai_task_state_missing", "AI 重试状态不存在"));
        }
        if (task.isStale()) {
            throw conflict("stale_ai_task", "采购输入已变化，请启动新的 AI 任务");
        }
        if (task.getStatus() != AiTaskStatus.FAILED || !task.isRetryable()) {
            throw conflict("ai_task_not_retryable", "该 AI 任务当前不可重试");
        }
        var business = businessTasks.lockById(task.getBusinessId())
                .orElseThrow(() -> notFound("task_not_found", "未找到采购任务"));
        if (business.getGeneration() != task.getGeneration()) {
            markTaskStale(task, "INPUT_GENERATION_CHANGED");
            throw conflict("stale_ai_task", "采购输入已变化，请启动新的 AI 任务");
        }
        var previous = commands.findById(task.getOperationId())
                .orElseThrow(() -> conflict("ai_task_operation_missing", "AI 任务缺少上一操作"));
        var nextAttempt = task.getRetryCount() + 2;
        var payload = new LinkedHashMap<String, Object>(previous.getPayload());
        payload.put("attempt", nextAttempt);
        var command = commands.save(AgentCommand.accept(
                "analyze",
                task.getBusinessId(),
                task.getGeneration(),
                business.getVersion(),
                payload));
        commands.alignTimestampsToDbClock(command.getOperationId());
        if (automatic) {
            command.defer(Math.min(30, 1L << Math.min(task.getRetryCount() + 1, 5)));
        }
        task.retrying(command.getOperationId());
        records.save(AiTaskRecord.pending(task.getId(), command.getOperationId(), nextAttempt));
        idempotency.save(IdempotencyRecord.reserve(scope, key, requestSha, command.getOperationId()));
        audit.save(AuditEvent.create(
                task.getBusinessId(), null, null,
                automatic ? "ai_task_auto_retry_scheduled" : "ai_task_retry_scheduled",
                automatic ? "system" : operator,
                Map.of(
                        "ai_task_id", task.getId(),
                        "operation_id", command.getOperationId(),
                        "attempt", nextAttempt)));
        return task;
    }

    @Transactional
    public AiTask retryAutomatically(AgentCommand failedCommand) {
        var task = tasks.findByOperationId(failedCommand.getOperationId()).orElse(null);
        if (task == null || !task.isRetryable()) return task;
        return retry(
                task.getId(),
                "auto-" + task.getRetryCount() + "-" + failedCommand.getOperationId(),
                true);
    }

    @Transactional
    public AiTask cancel(String aiTaskId, String idempotencyKey) {
        var key = normalizeKey(idempotencyKey, "cancel-" + UUID.randomUUID());
        var scope = "ai_task_cancel:" + aiTaskId;
        var requestSha = CanonicalJson.sha256(Map.of("ai_task_id", aiTaskId, "action", "cancel"));
        var replay = idempotency.findById(new IdempotencyRecord.Key(scope, key));
        if (replay.isPresent()) {
            if (!replay.get().getPayloadSha256().equals(requestSha)) {
                throw conflict("idempotency_payload_conflict", "同一幂等键已用于其他取消请求");
            }
            return tasks.findById(aiTaskId)
                    .orElseThrow(() -> notFound("ai_task_not_found", "未找到 AI 任务"));
        }
        var task = tasks.lockById(aiTaskId)
                .orElseThrow(() -> notFound("ai_task_not_found", "未找到 AI 任务"));
        if (task.getStatus() == AiTaskStatus.SUCCEEDED || task.getStatus() == AiTaskStatus.CANCELLED) {
            throw conflict("ai_task_not_cancellable", "该 AI 任务当前不可取消");
        }
        var operation = commands.findById(task.getOperationId()).orElse(null);
        if (operation != null && !"completed".equals(operation.getStatus())
                && !"failed".equals(operation.getStatus())
                && !"cancelled".equals(operation.getStatus())) {
            operation.cancel();
        }
        task.cancelled();
        idempotency.save(IdempotencyRecord.reserve(
                scope, key, requestSha, operation == null ? null : operation.getOperationId()));
        audit.save(AuditEvent.create(
                task.getBusinessId(), null, null, "ai_task_cancelled", operator,
                Map.of("ai_task_id", task.getId())));
        return task;
    }

    @Transactional
    public void markBusinessStale(String businessId, String reason) {
        for (var task : tasks.findByBusinessIdAndStaleFalse(businessId)) {
            markTaskStale(task, reason);
        }
    }

    private void markTaskStale(AiTask task, String reason) {
        task.markStale(reason);
        results.findByAiTaskId(task.getId()).ifPresent(result -> result.markStale(reason));
        if (task.getOperationId() != null) {
            commands.findById(task.getOperationId()).ifPresent(command -> {
                if (!"completed".equals(command.getStatus()) && !"failed".equals(command.getStatus())
                        && !"cancelled".equals(command.getStatus())) {
                    command.cancel();
                }
            });
        }
    }

    public Map<String, Object> summary(AiTask task) {
        return views.summary(task);
    }

    private Launch replay(IdempotencyRecord record, String requestSha) {
        if (!record.getPayloadSha256().equals(requestSha)) {
            throw conflict("idempotency_payload_conflict", "同一幂等键已用于不同 AI 任务输入");
        }
        var command = commands.findById(record.getOperationId())
                .orElseThrow(() -> conflict("idempotency_state_missing", "AI 幂等操作状态不存在"));
        var aiTask = tasks.findByOperationId(command.getOperationId())
                .orElseThrow(() -> conflict("ai_task_state_missing", "AI 幂等任务状态不存在"));
        var business = businessTasks.findById(aiTask.getBusinessId())
                .orElseThrow(() -> notFound("task_not_found", "未找到采购任务"));
        return new Launch(aiTask, command, business);
    }

    private void validateAnalysisInput(ProcurementTask business) {
        if (!business.isRequirementConfirmed()
                || business.getQuantity() == null
                || business.getQuantity().signum() <= 0
                || business.getUnit() == null
                || business.getUnit().isBlank()) {
            throw conflict("requirement_review_required", "采购需求必须先由采购员保存确认");
        }
        var taskQuotes = quotes.findByTaskIdOrderByCreatedAtAsc(business.getId());
        if (taskQuotes.size() < 2) {
            throw conflict("insufficient_quotes", "至少需要两份报价才能比价");
        }
        if (taskQuotes.stream().anyMatch(item -> !item.reviewFields().isEmpty())) {
            throw conflict("quote_review_required", "报价字段尚未全部复核");
        }
    }

    private String normalizeKey(String value, String fallback) {
        if (value == null || value.isBlank()) return fallback;
        var stripped = value.strip();
        if (stripped.length() < 8 || stripped.length() > 128) {
            throw new ApiException(HttpStatus.BAD_REQUEST, "invalid_idempotency_key", "幂等键长度必须为 8 至 128");
        }
        return stripped;
    }

    private String errorCode(String error) {
        if (error == null || error.isBlank()) return "AI_TASK_FAILED";
        var separator = error.indexOf(':');
        var value = separator > 0 ? error.substring(0, separator) : error;
        value = value.replaceAll("[^A-Za-z0-9_]+", "_").toUpperCase(java.util.Locale.ROOT);
        return value.substring(0, Math.min(value.length(), 100));
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> map(Object value) {
        return value instanceof Map<?, ?> raw ? (Map<String, Object>) raw : Map.of();
    }

    private Map<String, Object> mapOrNull(Object value) {
        if (value == null) return null;
        return map(value);
    }

    private List<Map<String, Object>> mapList(Object value) {
        if (!(value instanceof List<?> items)) return List.of();
        var result = new ArrayList<Map<String, Object>>();
        for (var item : items) {
            var mapped = map(item);
            if (!mapped.isEmpty()) result.add(mapped);
        }
        return result;
    }

    private String text(Object value) { return value == null ? "" : String.valueOf(value); }
    private String nullableText(Object value) {
        var result = text(value);
        return result.isBlank() ? null : result;
    }
    private int integer(Object value) { return Integer.parseInt(text(value)); }
    private BigDecimal decimal(Object value) { return new BigDecimal(text(value)); }
    private Instant instant(Object value) {
        var text = text(value);
        return text.isBlank() ? Instant.now() : Instant.parse(text);
    }
    private <T extends Enum<T>> T nullableEnum(Class<T> type, Object value) {
        var text = text(value);
        return text.isBlank() ? null : Enum.valueOf(type, text);
    }
    private ApiException conflict(String code, String message) {
        return new ApiException(HttpStatus.CONFLICT, code, message);
    }
    private ApiException notFound(String code, String message) {
        return new ApiException(HttpStatus.NOT_FOUND, code, message);
    }

    public record Launch(AiTask task, AgentCommand command, ProcurementTask businessTask) {}
}
