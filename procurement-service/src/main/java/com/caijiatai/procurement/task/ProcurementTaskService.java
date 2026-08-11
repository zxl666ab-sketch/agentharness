package com.caijiatai.procurement.task;

import com.caijiatai.procurement.agent.AgentCommand;
import com.caijiatai.procurement.agent.AgentCommandRepository;
import com.caijiatai.procurement.agent.CanonicalJson;
import com.caijiatai.procurement.agent.IdempotencyRecord;
import com.caijiatai.procurement.agent.IdempotencyRecordRepository;
import com.caijiatai.procurement.api.ApiException;
import com.caijiatai.procurement.approval.PendingDecision;
import com.caijiatai.procurement.approval.PendingDecisionRepository;
import com.caijiatai.procurement.approval.ProcurementDecisionRepository;
import com.caijiatai.procurement.artifact.ArtifactStore;
import com.caijiatai.procurement.artifact.BusinessArtifactRepository;
import com.caijiatai.procurement.cache.TaskContextCache;
import com.caijiatai.procurement.artifact.ProcurementAttachment;
import com.caijiatai.procurement.artifact.ProcurementAttachmentRepository;
import com.caijiatai.procurement.comparison.ComparisonSnapshotRepository;
import com.caijiatai.procurement.config.AppProperties;
import com.caijiatai.procurement.quote.ProcurementQuoteRepository;
import com.caijiatai.procurement.quote.QuoteCorrection;
import com.caijiatai.procurement.quote.QuoteCorrectionRepository;
import com.caijiatai.procurement.report.AuditEvent;
import com.caijiatai.procurement.report.AuditEventRepository;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.nio.file.Files;
import java.math.BigDecimal;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import org.springframework.data.domain.PageRequest;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.multipart.MultipartFile;

@Service
public class ProcurementTaskService {
    private static final long MAX_FILE_BYTES = 5L * 1024 * 1024;
    private static final long MAX_TOTAL_BYTES = 20L * 1024 * 1024;
    private static final int MAX_QUOTES = 50;

    private final ProcurementTaskRepository tasks;
    private final ProcurementAttachmentRepository attachments;
    private final ProcurementQuoteRepository quotes;
    private final QuoteCorrectionRepository corrections;
    private final ComparisonSnapshotRepository snapshots;
    private final PendingDecisionRepository pendingDecisions;
    private final ProcurementDecisionRepository decisions;
    private final AgentCommandRepository commands;
    private final IdempotencyRecordRepository idempotency;
    private final AuditEventRepository audit;
    private final ArtifactStore artifactStore;
    private final BusinessArtifactRepository businessArtifacts;
    private final TaskViewMapper views;
    private final TaskContextCache contextCache;
    private final String operator;

    public ProcurementTaskService(
            ProcurementTaskRepository tasks,
            ProcurementAttachmentRepository attachments,
            ProcurementQuoteRepository quotes,
            QuoteCorrectionRepository corrections,
            ComparisonSnapshotRepository snapshots,
            PendingDecisionRepository pendingDecisions,
            ProcurementDecisionRepository decisions,
            AgentCommandRepository commands,
            IdempotencyRecordRepository idempotency,
            AuditEventRepository audit,
            ArtifactStore artifactStore,
            BusinessArtifactRepository businessArtifacts,
            TaskViewMapper views,
            TaskContextCache contextCache,
            AppProperties properties) {
        this.tasks = tasks;
        this.attachments = attachments;
        this.quotes = quotes;
        this.corrections = corrections;
        this.snapshots = snapshots;
        this.pendingDecisions = pendingDecisions;
        this.decisions = decisions;
        this.commands = commands;
        this.idempotency = idempotency;
        this.audit = audit;
        this.artifactStore = artifactStore;
        this.businessArtifacts = businessArtifacts;
        this.views = views;
        this.contextCache = contextCache;
        this.operator = properties.localOperator();
    }

    @Transactional
    public ProcurementDtos.OperationAccepted startConversation(
            String message, List<MultipartFile> files, String idempotencyKey) {
        if (message == null || message.isBlank() || message.length() > 20_000) {
            throw bad("invalid_message", "采购目标不能为空且不得超过 20000 个字符");
        }
        if (files == null || files.size() < 2 || files.size() > MAX_QUOTES) {
            throw bad("invalid_attachment_count", "首次提交必须包含 2 至 50 份报价");
        }
        var loaded = loadFiles(files);
        var fingerprint = new LinkedHashMap<String, Object>();
        fingerprint.put("message", message.strip());
        fingerprint.put("files", loaded.stream().map(item -> Map.of(
                "filename", item.filename(), "sha256", item.sha256(), "size", item.bytes().length)).toList());
        var payloadSha = CanonicalJson.sha256(fingerprint);
        var scope = "conversation";
        var key = normalizeIdempotencyKey(idempotencyKey, payloadSha);
        var existing = idempotency.findById(new IdempotencyRecord.Key(scope, key));
        if (existing.isPresent()) {
            return existingAccepted(existing.get(), payloadSha);
        }

        var task = tasks.saveAndFlush(ProcurementTask.draft(message));
        var artifactPayload = new ArrayList<Map<String, Object>>();
        for (var file : loaded) {
            var artifact = artifactStore.store(
                    "procurement_original",
                    task.getId(),
                    file.filename(),
                    file.contentType(),
                    new ByteArrayInputStream(file.bytes()),
                    Map.of("source", "conversation"));
            attachments.save(ProcurementAttachment.from(task.getId(), artifact));
            artifactPayload.add(Map.of(
                    "artifact_id", artifact.getId(),
                    "filename", artifact.getFilename(),
                    "sha256", artifact.getSha256(),
                    "content_type", artifact.getContentType(),
                    "size_bytes", artifact.getSizeBytes()));
        }
        var payload = new LinkedHashMap<String, Object>();
        payload.put("message", message.strip());
        payload.put("attachments", artifactPayload);
        var command = commands.save(AgentCommand.accept(
                "start_conversation", task.getId(), task.getGeneration(), task.getVersion(), payload));
            commands.alignTimestampsToDbClock(command.getOperationId());
        idempotency.save(IdempotencyRecord.reserve(scope, key, payloadSha, command.getOperationId()));
        audit.save(AuditEvent.create(
                task.getId(), null, null, "procurement_conversation_accepted", operator,
                Map.of("operation_id", command.getOperationId(), "attachment_count", files.size())));
        return accepted(command, task);
    }

    @Transactional
    public Map<String, Object> createStructured(
            ProcurementDtos.Requirement body, String idempotencyKey) {
        validateRequirement(body);
        var fingerprint = Map.<String, Object>of(
                "schema_version", body.schemaVersion(),
                "title", body.title(),
                "category", body.category(),
                "item_name", body.itemName(),
                "quantity", CanonicalJson.decimal(body.quantity()),
                "unit", body.unit(),
                "specifications", body.specifications(),
                "constraints", constraints(body.constraints()));
        var payloadSha = CanonicalJson.sha256(fingerprint);
        var key = normalizeIdempotencyKey(idempotencyKey, payloadSha);
        var existing = idempotency.findById(new IdempotencyRecord.Key("structured_request", key));
        if (existing.isPresent()) {
            if (!existing.get().getPayloadSha256().equals(payloadSha)) {
                throw conflict("idempotency_payload_conflict", "同一幂等键已用于不同请求内容");
            }
            var existingCommand = commands.findById(existing.get().getOperationId()).orElseThrow();
            return detail(existingCommand.getAggregateId());
        }
        var task = tasks.saveAndFlush(ProcurementTask.structured(
                body.schemaVersion(), body.title().strip(), body.category().strip(), body.itemName().strip(),
                body.quantity(), body.unit().strip(), body.specifications(), constraints(body.constraints())));
        var command = commands.save(AgentCommand.accept(
                "create_structured", task.getId(), task.getGeneration(), task.getVersion(),
                Map.of("task_id", task.getId(), "requirement", fingerprint)));
            commands.alignTimestampsToDbClock(command.getOperationId());
        idempotency.save(IdempotencyRecord.reserve("structured_request", key, payloadSha, command.getOperationId()));
        audit.save(AuditEvent.create(
                task.getId(), null, null, "structured_request_created", operator,
                Map.of("operation_id", command.getOperationId())));
        contextCache.evict(task.getId());
        return detail(task.getId());
    }

    @Transactional
    public ProcurementDtos.OperationAccepted uploadQuote(
            String taskId, MultipartFile file, String idempotencyKey) {
        var task = lockTask(taskId);
        if (decisions.findByTaskId(taskId).isPresent()) {
            throw conflict("task_terminal", "终态采购任务不能新增报价");
        }
        if (quotes.countByTaskId(taskId) >= MAX_QUOTES) {
            throw conflict("quote_limit_reached", "每个采购任务最多 50 份报价");
        }
        var loaded = loadFiles(List.of(file)).getFirst();
        var payloadSha = CanonicalJson.sha256(Map.of(
                "task_id", taskId, "filename", loaded.filename(), "sha256", loaded.sha256()));
        var key = normalizeIdempotencyKey(idempotencyKey, payloadSha);
        var existing = idempotency.findById(new IdempotencyRecord.Key("quote_import", key));
        if (existing.isPresent()) {
            return existingAccepted(existing.get(), payloadSha);
        }
        invalidate(task);
        var artifact = artifactStore.store(
                "procurement_original", taskId, loaded.filename(), loaded.contentType(),
                new ByteArrayInputStream(loaded.bytes()), Map.of("source", "quote_import"));
        attachments.save(ProcurementAttachment.from(taskId, artifact));
        contextCache.evict(taskId);
        var payload = Map.<String, Object>of(
                "artifact_id", artifact.getId(), "filename", artifact.getFilename(),
                "sha256", artifact.getSha256(), "content_type", artifact.getContentType(),
                "size_bytes", artifact.getSizeBytes());
        var command = commands.save(AgentCommand.accept(
                "import_quote", taskId, task.getGeneration(), task.getVersion(), payload));
            commands.alignTimestampsToDbClock(command.getOperationId());
        idempotency.save(IdempotencyRecord.reserve("quote_import", key, payloadSha, command.getOperationId()));
        audit.save(AuditEvent.create(
                taskId, null, task.getAnalysisRunId(), "quote_import_accepted", operator,
                Map.of("operation_id", command.getOperationId(), "artifact_id", artifact.getId())));
        return accepted(command, task);
    }

    @Transactional
    public Map<String, Object> correctQuote(
            String taskId, String quoteId, ProcurementDtos.QuoteCorrection body) {
        var task = lockTask(taskId);
        contextCache.evict(taskId);
        var quote = quotes.findByIdAndTaskId(quoteId, taskId)
                .orElseThrow(() -> notFound("quote_not_found", "未找到报价"));
        var fields = map(quote.getExtracted().get("fields"));
        var entry = map(fields.get(body.field()));
        if (entry.isEmpty()) {
            throw bad("unknown_quote_field", "报价字段不存在");
        }
        var oldValue = entry.get("value");
        quote.correct(body.field(), body.value());
        corrections.save(QuoteCorrection.create(taskId, quoteId, body.field(), oldValue, body.value(), operator));
        invalidate(task);
        refreshReviewStatus(task);
        audit.save(AuditEvent.create(
                taskId, quoteId, task.getAnalysisRunId(), "quote_field_corrected", operator,
                Map.of("field", body.field())));
        return views.quote(quote);
    }

    @Transactional
    public Map<String, Object> correctRequirement(String taskId, ProcurementDtos.Requirement body) {
        var task = lockTask(taskId);
        contextCache.evict(taskId);
        validateRequirement(body);
        task.applyRequirement(
                body.schemaVersion(), body.title().strip(), body.category().strip(), body.itemName().strip(),
                body.quantity(), body.unit().strip(), body.specifications(), constraints(body.constraints()));
        task.confirmRequirement();
        invalidate(task);
        refreshReviewStatus(task);
        audit.save(AuditEvent.create(
                taskId, null, task.getAnalysisRunId(), "requirement_corrected", operator,
                Map.of("schema_version", body.schemaVersion())));
        return detail(taskId);
    }

    @Transactional
    public ProcurementDtos.OperationAccepted analyze(String taskId, String idempotencyKey) {
        var task = lockTask(taskId);
        contextCache.evict(taskId);
        if (!task.isRequirementConfirmed()) {
            throw conflict("requirement_review_required", "采购需求必须先由采购员保存确认");
        }
        var taskQuotes = quotes.findByTaskIdOrderByCreatedAtAsc(taskId);
        if (taskQuotes.size() < 2) {
            throw conflict("insufficient_quotes", "至少需要两份报价才能比价");
        }
        if (taskQuotes.stream().anyMatch(item -> !item.reviewFields().isEmpty())) {
            throw conflict("quote_review_required", "报价字段尚未全部复核");
        }
        // Quote source hashes and the accepted task version bind the durable command.
        var payload = Map.<String, Object>of(
                "task_id", taskId,
                "task_version", task.getVersion(),
                "quote_sha256", taskQuotes.stream().map(item -> item.getSourceSha256()).sorted().toList());
        var explicitKey = idempotencyKey != null && !idempotencyKey.isBlank();
        var requestSha = CanonicalJson.sha256(explicitKey ? Map.of("task_id", taskId) : payload);
        var key = normalizeIdempotencyKey(idempotencyKey, requestSha);
        var existing = idempotency.findById(new IdempotencyRecord.Key("analyze", key));
        if (existing.isPresent()) {
            return existingAccepted(existing.get(), requestSha);
        }
        task.setStatus(TaskStatus.ANALYZING);
        var command = commands.save(AgentCommand.accept(
                "analyze", taskId, task.getGeneration(), task.getVersion(), payload));
            commands.alignTimestampsToDbClock(command.getOperationId());
        idempotency.save(IdempotencyRecord.reserve("analyze", key, requestSha, command.getOperationId()));
        audit.save(AuditEvent.create(
                taskId, null, task.getAnalysisRunId(), "analysis_accepted", operator,
                Map.of("operation_id", command.getOperationId())));
        return accepted(command, task);
    }

    @Transactional
    public ProcurementDtos.OperationAccepted resume(
            String taskId, ProcurementDtos.Resume body, String idempotencyKey) {
        var task = lockTask(taskId);
        if (task.getAnalysisRunId() == null) {
            throw conflict("run_not_bound", "采购任务尚未绑定 Agent Run");
        }
        var payload = Map.<String, Object>of(
                "task_id", taskId,
                "run_id", task.getAnalysisRunId(),
                "message", body.message().strip());
        var payloadSha = CanonicalJson.sha256(payload);
        var key = normalizeIdempotencyKey(idempotencyKey, payloadSha);
        var existing = idempotency.findById(new IdempotencyRecord.Key("resume_run", key));
        if (existing.isPresent()) {
            return existingAccepted(existing.get(), payloadSha);
        }
        var command = commands.save(AgentCommand.accept(
                "resume_run", taskId, task.getGeneration(), task.getVersion(), payload));
            commands.alignTimestampsToDbClock(command.getOperationId());
        idempotency.save(IdempotencyRecord.reserve("resume_run", key, payloadSha, command.getOperationId()));
        audit.save(AuditEvent.create(
                taskId, null, task.getAnalysisRunId(), "agent_resume_accepted", operator,
                Map.of("operation_id", command.getOperationId())));
        return accepted(command, task);
    }

    @Transactional
    public Map<String, Object> reopen(
            String taskId, ProcurementDtos.Reopen body, String idempotencyKey) {
        var source = lockTask(taskId);
        if (!List.of(TaskStatus.APPROVED.wireValue(), TaskStatus.NO_AWARD.wireValue())
                .contains(source.getStatus())) {
            throw conflict("task_not_terminal", "只有终态采购任务可以复制重开");
        }
        var fingerprint = Map.<String, Object>of(
                "source_task_id", taskId, "copy_quotes", body.copyQuotes());
        var payloadSha = CanonicalJson.sha256(fingerprint);
        var key = normalizeIdempotencyKey(idempotencyKey, payloadSha);
        var existing = idempotency.findById(new IdempotencyRecord.Key("reopen", key));
        if (existing.isPresent()) {
            if (!existing.get().getPayloadSha256().equals(payloadSha)) {
                throw conflict("idempotency_payload_conflict", "同一幂等键已用于不同请求内容");
            }
            var command = commands.findById(existing.get().getOperationId()).orElseThrow();
            return detail(command.getAggregateId());
        }
        var reopened = tasks.saveAndFlush(ProcurementTask.reopenFrom(source));
        if (body.copyQuotes()) {
            for (var sourceQuote : quotes.findByTaskIdOrderByCreatedAtAsc(taskId)) {
                var sourceArtifact = businessArtifacts.findById(sourceQuote.getSourceArtifactId())
                        .orElseThrow(() -> notFound("artifact_not_found", "报价原件不存在"));
                try (var input = Files.newInputStream(artifactStore.path(sourceArtifact))) {
                    var copiedArtifact = artifactStore.store(
                            "procurement_original",
                            reopened.getId(),
                            sourceArtifact.getFilename(),
                            sourceArtifact.getContentType(),
                            input,
                            Map.of("copied_from_task_id", taskId, "copied_from_artifact_id", sourceArtifact.getId()));
                    attachments.save(ProcurementAttachment.from(reopened.getId(), copiedArtifact));
                    quotes.save(sourceQuote.copyTo(reopened.getId(), copiedArtifact.getId()));
                } catch (IOException error) {
                    throw new ApiException(HttpStatus.INTERNAL_SERVER_ERROR, "artifact_copy_failed", "复制报价原件失败");
                }
            }
            var unresolved = quotes.findByTaskIdOrderByCreatedAtAsc(reopened.getId()).stream()
                    .mapToInt(item -> item.reviewFields().size()).sum();
            reopened.setStatus(unresolved == 0 ? TaskStatus.READY : TaskStatus.REVIEW);
        }
        var command = commands.save(AgentCommand.accept(
                "reopen_task", reopened.getId(), reopened.getGeneration(), reopened.getVersion(),
                Map.of("source_task_id", taskId, "copy_quotes", body.copyQuotes())));
            commands.alignTimestampsToDbClock(command.getOperationId());
        idempotency.save(IdempotencyRecord.reserve("reopen", key, payloadSha, command.getOperationId()));
        audit.save(AuditEvent.create(
                reopened.getId(), null, null, "task_reopened", operator,
                Map.of("source_task_id", taskId, "copy_quotes", body.copyQuotes(), "operation_id", command.getOperationId())));
        return detail(reopened.getId());
    }

    @Transactional(readOnly = true)
    public List<Map<String, Object>> list() {
        return tasks.findAllByOrderByUpdatedAtDesc(PageRequest.of(0, 200)).stream()
                .map(task -> views.summary(
                        task,
                        quotes.countByTaskId(task.getId()),
                        quotes.findByTaskIdOrderByCreatedAtAsc(task.getId()).stream()
                                .mapToInt(item -> item.reviewFields().size()).sum(),
                        decisions.findByTaskId(task.getId()).orElse(null)))
                .toList();
    }

    @Transactional(readOnly = true)
    public Map<String, Object> detail(String taskId) {
        var task = tasks.findById(taskId).orElseThrow(() -> notFound("task_not_found", "未找到采购任务"));
        var cached = contextCache.get(taskId, task.getGeneration());
        if (cached.isPresent()) {
            return cached.get();
        }
        var current = task.getCurrentSnapshotId() == null ? null
                : snapshots.findByIdAndTaskId(task.getCurrentSnapshotId(), taskId).orElse(null);
        var value = views.detail(
                task,
                attachments.findByTaskIdOrderByCreatedAtAsc(taskId),
                quotes.findByTaskIdOrderByCreatedAtAsc(taskId),
                current,
                decisions.findByTaskId(taskId).orElse(null));
        contextCache.put(taskId, task.getGeneration(), value);
        return value;
    }

    @Transactional
    public void delete(String taskId) {
        var task = lockTask(taskId);
        contextCache.evict(taskId);
        var reference = task.getReference();
        tasks.delete(task);
    }

    private void invalidate(ProcurementTask task) {
        task.invalidateAnalysis();
        pendingDecisions.findByTaskIdAndStatusIn(task.getId(), List.of("pending", "approved"))
                .forEach(PendingDecision::stale);
    }

    private void refreshReviewStatus(ProcurementTask task) {
        var unresolved = quotes.findByTaskIdOrderByCreatedAtAsc(task.getId()).stream()
                .anyMatch(item -> !item.reviewFields().isEmpty());
        task.setStatus(task.isRequirementConfirmed() && !unresolved
                ? TaskStatus.READY : TaskStatus.REVIEW);
    }

    private ProcurementDtos.OperationAccepted accepted(AgentCommand command, ProcurementTask task) {
        return new ProcurementDtos.OperationAccepted(
                command.getOperationId(), task.getId(), task.getSessionId(), task.getAnalysisRunId(),
                "accepted", "/api/procurement/operations/" + command.getOperationId());
    }

    private ProcurementDtos.OperationAccepted existingAccepted(IdempotencyRecord record, String payloadSha) {
        if (!record.getPayloadSha256().equals(payloadSha)) {
            throw conflict("idempotency_payload_conflict", "同一幂等键已用于不同请求内容");
        }
        var command = commands.findById(record.getOperationId())
                .orElseThrow(() -> conflict("idempotency_state_missing", "幂等操作状态不存在"));
        var task = tasks.findById(command.getAggregateId())
                .orElseThrow(() -> notFound("task_not_found", "未找到采购任务"));
        return accepted(command, task);
    }

    private ProcurementTask lockTask(String taskId) {
        return tasks.lockById(taskId).orElseThrow(() -> notFound("task_not_found", "未找到采购任务"));
    }

    private List<LoadedFile> loadFiles(List<MultipartFile> files) {
        long total = 0;
        var result = new ArrayList<LoadedFile>();
        for (var file : files) {
            var filename = file.getOriginalFilename();
            if (filename == null || filename.isBlank() || filename.contains("/") || filename.contains("\\")) {
                throw bad("invalid_filename", "文件名无效");
            }
            var lower = filename.toLowerCase(Locale.ROOT);
            if (!lower.endsWith(".xlsx") && !lower.endsWith(".pdf")) {
                throw bad("unsupported_quote_type", "仅支持 .xlsx 和文本型 .pdf 报价");
            }
            if (file.isEmpty() || file.getSize() > MAX_FILE_BYTES) {
                throw bad("invalid_quote_size", "报价文件必须非空且不得超过 5 MB");
            }
            total += file.getSize();
            if (total > MAX_TOTAL_BYTES) {
                throw bad("upload_too_large", "单次上传报价总计不得超过 20 MB");
            }
            try {
                var bytes = file.getBytes();
                result.add(new LoadedFile(filename, file.getContentType(), bytes, sha256(bytes)));
            } catch (IOException error) {
                throw bad("quote_read_failed", "无法读取报价文件");
            }
        }
        return result;
    }

    private String sha256(byte[] bytes) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
        } catch (NoSuchAlgorithmException error) {
            throw new IllegalStateException(error);
        }
    }

    private Map<String, Object> constraints(ProcurementDtos.Constraints value) {
        var rates = new LinkedHashMap<String, String>();
        value.fxRates().forEach((key, rate) -> rates.put(key.toUpperCase(Locale.ROOT), CanonicalJson.decimal(rate)));
        var base = value.baseCurrency().toUpperCase(Locale.ROOT);
        if (!"1".equals(rates.get(base))) {
            throw bad("invalid_base_currency_rate", "本位币汇率必须等于 1");
        }
        var result = new LinkedHashMap<String, Object>();
        result.put("base_currency", base);
        result.put("fx_rates", rates);
        result.put("max_lead_days", value.maxLeadDays());
        result.put("invoice_required", value.invoiceRequired());
        putDecimal(result, "size_tolerance_mm", value.sizeToleranceMm());
        putDecimal(result, "thickness_tolerance_um", value.thicknessToleranceUm());
        putDecimal(result, "max_landed_unit_cost", value.maxLandedUnitCost());
        result.put("destination", value.destination() == null ? "" : value.destination().strip());
        if (value.requiredDeliveryDate() != null && !value.requiredDeliveryDate().isBlank()) {
            result.put("required_delivery_date", LocalDate.parse(value.requiredDeliveryDate()).toString());
        }
        return result;
    }

    private void validateRequirement(ProcurementDtos.Requirement value) {
        if (value.schemaVersion() == 1) {
            var required = List.of("width_mm", "length_mm", "thickness_um", "material", "color", "print_colors");
            if (!value.specifications().keySet().containsAll(required)) {
                throw bad("invalid_v1_specifications", "V1 包装需求缺少固定规格字段");
            }
        }
        if (value.schemaVersion() == 2) {
            value.specifications().forEach((key, raw) -> {
                var spec = map(raw);
                if (!List.of("number", "text", "boolean").contains(String.valueOf(spec.get("type")))) {
                    throw bad("invalid_dynamic_spec", "动态规格类型无效：" + key);
                }
                if (!List.of("exact", "tolerance", "range", "gte", "lte")
                        .contains(String.valueOf(spec.get("match")))) {
                    throw bad("invalid_dynamic_spec", "动态规格匹配方式无效：" + key);
                }
            });
        }
        if (!"ecommerce_packaging".equals(value.category())) {
            throw bad("invalid_category", "当前采购域仅支持 ecommerce_packaging");
        }
        if (!"piece".equals(value.unit())) {
            throw bad("invalid_unit", "当前采购域仅支持 piece 计价单位");
        }
        if (value.quantity().compareTo(new BigDecimal("100000000")) > 0) {
            throw bad("invalid_quantity", "采购数量不得超过 1 亿");
        }
        var specs = value.specifications();
        positiveDecimal(specs.get("width_mm"), "宽度", new BigDecimal("10000"));
        positiveDecimal(specs.get("length_mm"), "长度", new BigDecimal("10000000"));
        positiveDecimal(specs.get("thickness_um"), "厚度", new BigDecimal("5000"));
        if (specs.get("print_colors") != null) {
            int printColors = integerValue(specs.get("print_colors"), "印刷色数");
            if (printColors < 0 || printColors > 12) {
                throw bad("invalid_print_colors", "印刷色数必须在 0 到 12 之间");
            }
        }
        if (specs.containsKey("height_mm")) {
            positiveDecimal(specs.get("height_mm"), "高度", new BigDecimal("10000"));
        } else if (requiresHeight(value.itemName())) {
            throw bad("invalid_specifications", "纸箱采购规格必须提供高度 height_mm");
        }
        var constraints = value.constraints();
        if (constraints.sizeToleranceMm() != null) {
            rangeDecimal(constraints.sizeToleranceMm(), "尺寸公差", BigDecimal.ZERO, new BigDecimal("100"));
        }
        if (constraints.thicknessToleranceUm() != null) {
            rangeDecimal(constraints.thicknessToleranceUm(), "厚度公差", BigDecimal.ZERO, new BigDecimal("5000"));
        }
        if (constraints.maxLandedUnitCost() != null && constraints.maxLandedUnitCost().signum() <= 0) {
            throw bad("invalid_constraints", "到货单价上限必须大于 0");
        }
    }

    private void positiveDecimal(Object value, String label, BigDecimal max) {
        if (value == null || String.valueOf(value).isBlank()) {
            throw bad("invalid_specifications", "缺少规格字段：" + label);
        }
        try {
            var number = new BigDecimal(String.valueOf(value));
            if (number.signum() <= 0) {
                throw bad("invalid_specifications", label + "必须大于 0");
            }
            if (number.compareTo(max) > 0) {
                throw bad("invalid_specifications", label + "超过上限 " + max.toPlainString());
            }
        } catch (NumberFormatException error) {
            throw bad("invalid_specifications", label + "不是有效数值");
        }
    }

    private void rangeDecimal(BigDecimal value, String label, BigDecimal min, BigDecimal max) {
        if (value.compareTo(min) < 0 || value.compareTo(max) > 0) {
            throw bad("invalid_constraints", label + "必须在 " + min.toPlainString() + " 到 " + max.toPlainString() + " 之间");
        }
    }

    private int integerValue(Object value, String label) {
        try {
            return new BigDecimal(String.valueOf(value)).intValueExact();
        } catch (RuntimeException error) {
            throw bad("invalid_" + label, label + "必须是整数");
        }
    }

    private boolean requiresHeight(String itemName) {
        var text = itemName == null ? "" : itemName.toLowerCase(Locale.ROOT);
        if (text.contains("胶带") || text.matches(".*\\btape\\b.*")) {
            return false;
        }
        if (List.of("纸箱", "包装箱", "carton", "corrugated").stream().anyMatch(text::contains)) {
            return true;
        }
        return text.matches(".*\\bbox(?:es)?\\b.*") && !text.contains("tape");
    }

    private void putDecimal(Map<String, Object> result, String key, BigDecimal value) {
        if (value != null) {
            result.put(key, CanonicalJson.decimal(value));
        }
    }

    private String normalizeIdempotencyKey(String key, String fallback) {
        var value = key == null || key.isBlank() ? "sha256:" + fallback : key.strip();
        if (value.length() > 200) {
            throw bad("invalid_idempotency_key", "幂等键不得超过 200 个字符");
        }
        return value;
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> map(Object value) {
        return value instanceof Map<?, ?> raw ? (Map<String, Object>) raw : Map.of();
    }

    private ApiException bad(String code, String message) {
        return new ApiException(HttpStatus.BAD_REQUEST, code, message);
    }

    private ApiException conflict(String code, String message) {
        return new ApiException(HttpStatus.CONFLICT, code, message);
    }

    private ApiException notFound(String code, String message) {
        return new ApiException(HttpStatus.NOT_FOUND, code, message);
    }

    private record LoadedFile(String filename, String contentType, byte[] bytes, String sha256) {}
}
