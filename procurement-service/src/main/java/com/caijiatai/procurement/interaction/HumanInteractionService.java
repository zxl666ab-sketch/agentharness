package com.caijiatai.procurement.interaction;

import com.caijiatai.procurement.agent.AgentCommand;
import com.caijiatai.procurement.agent.AgentCommandRepository;
import com.caijiatai.procurement.agent.CanonicalJson;
import com.caijiatai.procurement.agent.IdempotencyRecord;
import com.caijiatai.procurement.agent.IdempotencyRecordRepository;
import com.caijiatai.procurement.api.ApiException;
import com.caijiatai.procurement.artifact.BusinessArtifactRepository;
import com.caijiatai.procurement.artifact.ArtifactStore;
import com.caijiatai.procurement.config.AppProperties;
import com.caijiatai.procurement.report.AuditEvent;
import com.caijiatai.procurement.report.AuditEventRepository;
import com.caijiatai.procurement.task.ProcurementDtos;
import com.caijiatai.procurement.task.ProcurementTaskRepository;
import com.caijiatai.procurement.task.TaskStatus;
import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.UUID;
import java.io.IOException;
import java.util.Locale;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.multipart.MultipartFile;

@Service
public class HumanInteractionService {
    private static final int MAX_KEY_LENGTH = 200;
    private static final int MAX_TEXT_LENGTH = 20_000;

    private final HumanInteractionRepository interactions;
    private final ProcurementTaskRepository tasks;
    private final AgentCommandRepository commands;
    private final IdempotencyRecordRepository idempotency;
    private final BusinessArtifactRepository artifacts;
    private final ArtifactStore artifactStore;
    private final AuditEventRepository audit;
    private final String operator;

    public HumanInteractionService(
            HumanInteractionRepository interactions,
            ProcurementTaskRepository tasks,
            AgentCommandRepository commands,
            IdempotencyRecordRepository idempotency,
            BusinessArtifactRepository artifacts,
            ArtifactStore artifactStore,
            AuditEventRepository audit,
            AppProperties properties) {
        this.interactions = interactions;
        this.tasks = tasks;
        this.commands = commands;
        this.idempotency = idempotency;
        this.artifacts = artifacts;
        this.artifactStore = artifactStore;
        this.audit = audit;
        this.operator = properties.localOperator();
    }

    @Transactional(readOnly = true)
    public List<HumanInteractionDtos.View> list(String taskId) {
        if (!tasks.existsById(taskId)) throw notFound("task_not_found", "未找到采购任务");
        return interactions.findByTaskIdOrderByCreatedAtDesc(taskId).stream().map(this::view).toList();
    }

    @Transactional(readOnly = true)
    public HumanInteractionDtos.View detail(String interactionId) {
        return view(interactions.findById(interactionId)
                .orElseThrow(() -> notFound("interaction_not_found", "未找到待回答问题")));
    }

    @Transactional
    public HumanInteractionDtos.ArtifactView upload(String interactionId, MultipartFile file) {
        var interaction = interactions.lockById(interactionId)
                .orElseThrow(() -> notFound("interaction_not_found", "未找到待回答问题"));
        if (!HumanInteractionStatus.WAITING.name().equals(interaction.getStatus())) {
            throw conflict("interaction_not_waiting", "该问题已经被回答或已失效");
        }
        var filename = file.getOriginalFilename();
        if (filename == null || filename.isBlank() || filename.contains("/") || filename.contains("\\")) {
            throw bad("invalid_filename", "补充文件名无效");
        }
        var lower = filename.toLowerCase(Locale.ROOT);
        if (!lower.endsWith(".xlsx") && !lower.endsWith(".pdf")) {
            throw bad("unsupported_interaction_artifact", "补充材料仅支持 XLSX 和 PDF");
        }
        if (file.isEmpty() || file.getSize() > 5L * 1024 * 1024) {
            throw bad("invalid_interaction_artifact_size", "补充文件必须非空且不得超过 5 MB");
        }
        try {
            var artifact = artifactStore.store(
                    "human_interaction_attachment", interaction.getTaskId(), filename,
                    file.getContentType(), file.getInputStream(),
                    Map.of("interaction_id", interaction.getId()));
            audit.save(AuditEvent.create(
                    interaction.getTaskId(), null, interaction.getRunId(),
                    "human_interaction_artifact_uploaded", operator,
                    Map.of("interaction_id", interaction.getId(), "artifact_id", artifact.getId())));
            return new HumanInteractionDtos.ArtifactView(
                    artifact.getId(), artifact.getFilename(), artifact.getContentType(),
                    artifact.getSizeBytes(), artifact.getSha256());
        } catch (IOException error) {
            throw bad("interaction_artifact_read_failed", "无法读取补充文件");
        }
    }

    /** Agent 只能提出候选问题；Java 保存并对外发布唯一的可回答交互。 */
    @Transactional
    public HumanInteraction createFromAgent(
            String taskId, String runId, int generation, Map<String, Object> candidate) {
        var kind = text(candidate.getOrDefault("kind", "clarification"), 40, "interaction_kind_invalid");
        var question = text(candidate.get("question"), MAX_TEXT_LENGTH, "interaction_question_invalid");
        var reason = text(candidate.get("reason"), MAX_TEXT_LENGTH, "interaction_reason_invalid");
        var businessStep = text(candidate.getOrDefault("business_step", "字段复核"), 80, "interaction_step_invalid");
        var schema = map(candidate.get("answer_schema"));
        validateSchemaDefinition(schema);
        var relatedFields = stringList(candidate.get("related_fields"), 100, 100);
        var relatedArtifacts = stringList(candidate.get("related_artifact_ids"), 20, 34);
        validateArtifacts(taskId, relatedArtifacts);
        var checkpointId = optionalText(candidate.get("checkpoint_id"), 64);
        var fingerprintPayload = new LinkedHashMap<String, Object>();
        fingerprintPayload.put("kind", kind);
        fingerprintPayload.put("question", question);
        fingerprintPayload.put("reason", reason);
        fingerprintPayload.put("business_step", businessStep);
        fingerprintPayload.put("related_fields", relatedFields);
        fingerprintPayload.put("related_artifact_ids", relatedArtifacts);
        fingerprintPayload.put("answer_schema", schema);
        var fingerprint = CanonicalJson.sha256(fingerprintPayload);
        var existing = interactions.findByTaskIdAndGenerationAndQuestionFingerprint(
                taskId, generation, fingerprint);
        if (existing.isPresent()) return existing.get();
        var expiresAt = parseInstant(candidate.get("expires_at"));
        var interaction = interactions.save(HumanInteraction.waiting(
                taskId, runId, checkpointId, generation, fingerprint, kind, question, reason,
                businessStep, relatedFields, relatedArtifacts, schema, expiresAt));
        audit.save(AuditEvent.create(
                taskId, null, runId, "human_interaction_requested", "agent",
                Map.of(
                        "interaction_id", interaction.getId(),
                        "kind", kind,
                        "business_step", businessStep,
                        "question_fingerprint", fingerprint)));
        return interaction;
    }

    @Transactional(noRollbackFor = ApiException.class)
    public ProcurementDtos.OperationAccepted answer(
            String interactionId, HumanInteractionDtos.Answer body, String idempotencyKey) {
        var taskId = interactions.findTaskIdById(interactionId)
                .orElseThrow(() -> notFound("interaction_not_found", "未找到待回答问题"));
        var task = tasks.lockById(taskId)
                .orElseThrow(() -> notFound("task_not_found", "未找到采购任务"));
        var interaction = interactions.lockById(interactionId)
                .orElseThrow(() -> notFound("interaction_not_found", "未找到待回答问题"));
        var normalizedArtifacts = body.artifactIds() == null
                ? List.<String>of() : body.artifactIds().stream().distinct().toList();
        var fingerprint = new LinkedHashMap<String, Object>();
        fingerprint.put("interaction_id", interactionId);
        fingerprint.put("answer", body.answer());
        fingerprint.put("note", body.note() == null ? "" : body.note().strip());
        fingerprint.put("artifact_ids", normalizedArtifacts);
        var payloadSha = CanonicalJson.sha256(fingerprint);
        var key = requireIdempotencyKey(idempotencyKey);
        var scope = "human_interaction:" + interactionId;
        var replay = idempotency.findById(new IdempotencyRecord.Key(scope, key));
        if (replay.isPresent()) {
            if (!replay.get().getPayloadSha256().equals(payloadSha)) {
                throw conflict("idempotency_payload_conflict", "同一幂等键已用于不同回答");
            }
            var command = commands.findById(replay.get().getOperationId())
                    .orElseThrow(() -> conflict("interaction_operation_missing", "回答已保存，但恢复任务状态暂不可用"));
            return accepted(command);
        }
        if (!HumanInteractionStatus.WAITING.name().equals(interaction.getStatus())) {
            throw conflict("interaction_not_waiting", "该问题已经被回答或已失效");
        }
        if (interaction.getExpiresAt() != null && !interaction.getExpiresAt().isAfter(Instant.now())) {
            interaction.expire();
            throw conflict("interaction_expired", "该问题已过期，请重新生成问题");
        }
        if (task.getGeneration() != interaction.getGeneration()) {
            interaction.stale();
            throw conflict("interaction_stale", "采购输入已变化，该问题已失效");
        }
        validateAnswer(interaction.getAnswerSchema(), body.answer());
        validateArtifacts(task.getId(), normalizedArtifacts);
        if ("file_upload".equals(String.valueOf(interaction.getAnswerSchema().get("type")))) {
            var answeredArtifacts = collection(body.answer()).stream()
                    .map(String::valueOf).distinct().toList();
            if (!answeredArtifacts.equals(normalizedArtifacts)) {
                throw bad("interaction_answer_invalid", "补充文件回答与已授权文件不一致");
            }
        }
        var operationId = UUID.randomUUID().toString();
        interaction.answer(
                body.answer(), body.note() == null ? null : body.note().strip(),
                normalizedArtifacts, operator, operationId);
        var commandPayload = new LinkedHashMap<String, Object>();
        commandPayload.put("interaction_id", interaction.getId());
        commandPayload.put("answer", body.answer());
        commandPayload.put("note", body.note() == null ? "" : body.note().strip());
        commandPayload.put("artifact_ids", normalizedArtifacts);
        commandPayload.put("run_id", interaction.getRunId());
        commandPayload.put("checkpoint_id", interaction.getCheckpointId());
        var command = commands.save(AgentCommand.accept(
                operationId, "human_interaction_answer", task.getId(), task.getGeneration(),
                task.getVersion(), commandPayload));
        commands.alignTimestampsToDbClock(command.getOperationId());
        idempotency.save(IdempotencyRecord.reserve(scope, key, payloadSha, operationId));
        audit.save(AuditEvent.create(
                task.getId(), null, interaction.getRunId(), "human_interaction_answered", operator,
                Map.of(
                        "interaction_id", interaction.getId(),
                        "operation_id", operationId,
                        "artifact_count", normalizedArtifacts.size())));
        return accepted(command);
    }

    @Transactional
    public ProcurementDtos.OperationAccepted retry(String interactionId) {
        var interaction = interactions.lockById(interactionId)
                .orElseThrow(() -> notFound("interaction_not_found", "未找到待回答问题"));
        if (!HumanInteractionStatus.ANSWERED.name().equals(interaction.getStatus())
                || interaction.getOperationId() == null) {
            throw conflict("interaction_not_answered", "该问题没有可恢复的已保存回答");
        }
        var command = commands.lockById(interaction.getOperationId())
                .orElseThrow(() -> conflict("interaction_operation_missing", "回答已保存，但恢复任务状态暂不可用"));
        command.requeue();
        commands.saveAndFlush(command);
        commands.alignTimestampsToDbClock(command.getOperationId());
        audit.save(AuditEvent.create(
                interaction.getTaskId(), null, interaction.getRunId(),
                "human_interaction_resume_requeued", operator,
                Map.of("interaction_id", interactionId, "operation_id", command.getOperationId())));
        return accepted(command);
    }

    @Transactional(noRollbackFor = ApiException.class)
    public HumanInteractionDtos.View cancel(String interactionId, HumanInteractionDtos.Cancel body) {
        var taskId = interactions.findTaskIdById(interactionId)
                .orElseThrow(() -> notFound("interaction_not_found", "未找到待回答问题"));
        var task = tasks.lockById(taskId)
                .orElseThrow(() -> notFound("task_not_found", "未找到采购任务"));
        var interaction = interactions.lockById(interactionId)
                .orElseThrow(() -> notFound("interaction_not_found", "未找到待回答问题"));
        if (!HumanInteractionStatus.WAITING.name().equals(interaction.getStatus())) {
            throw conflict("interaction_not_waiting", "该问题已经被回答或已失效");
        }
        var reason = body.reason() == null || body.reason().isBlank() ? "用户取消任务" : body.reason().strip();
        interaction.cancel(reason);
        task.setStatus(TaskStatus.CANCELLED);
        staleWaiting(task.getId(), interaction.getId());
        audit.save(AuditEvent.create(
                task.getId(), null, interaction.getRunId(), "human_interaction_cancelled", operator,
                Map.of("interaction_id", interaction.getId(), "reason", reason)));
        return view(interaction);
    }

    @Transactional
    public void applied(String interactionId) {
        if (interactionId == null || interactionId.isBlank()) return;
        var interaction = interactions.lockById(interactionId)
                .orElseThrow(() -> conflict("interaction_not_found", "Agent 返回了未知交互"));
        interaction.applied();
        audit.save(AuditEvent.create(
                interaction.getTaskId(), null, interaction.getRunId(),
                "human_interaction_applied", "agent", Map.of("interaction_id", interactionId)));
    }

    @Transactional
    public void staleWaiting(String taskId) {
        staleWaiting(taskId, null);
    }

    private void staleWaiting(String taskId, String exceptId) {
        interactions.lockWaitingByTaskId(taskId).stream()
                .filter(item -> !Objects.equals(item.getId(), exceptId))
                .forEach(HumanInteraction::stale);
    }

    private void validateArtifacts(String taskId, List<String> artifactIds) {
        for (var artifactId : artifactIds) {
            var artifact = artifacts.findById(artifactId)
                    .orElseThrow(() -> notFound("artifact_not_found", "补充文件不存在"));
            if (!taskId.equals(artifact.getTaskId())) {
                throw conflict("artifact_task_mismatch", "补充文件不属于当前采购任务");
            }
        }
    }

    private void validateSchemaDefinition(Map<String, Object> schema) {
        var type = String.valueOf(schema.getOrDefault("type", "string"));
        if (!List.of("string", "number", "boolean", "date", "single_choice",
                "multiple_choice", "field_review", "file_upload").contains(type)) {
            throw bad("answer_schema_invalid", "问题回答类型不受支持");
        }
        if ((type.equals("single_choice") || type.equals("multiple_choice"))
                && collection(schema.get("options")).isEmpty()) {
            throw bad("answer_schema_invalid", "选择题必须提供候选项");
        }
        if (type.equals("field_review")) {
            var fields = collection(schema.get("fields"));
            if (fields.isEmpty()) {
                throw bad("answer_schema_invalid", "字段复核必须提供字段定义");
            }
            var names = new java.util.HashSet<String>();
            for (var raw : fields) {
                if (!(raw instanceof Map<?, ?> field)) {
                    throw bad("answer_schema_invalid", "字段复核定义无效");
                }
                var name = String.valueOf(field.containsKey("name") ? field.get("name") : "").strip();
                var fieldType = String.valueOf(
                        field.containsKey("type") ? field.get("type") : "string");
                if (name.isBlank() || name.length() > 100 || !names.add(name)) {
                    throw bad("answer_schema_invalid", "字段复核名称无效或重复");
                }
                if (!Set.of("string", "number", "boolean", "date",
                        "single_choice", "multiple_choice").contains(fieldType)) {
                    throw bad("answer_schema_invalid", "字段复核类型不受支持");
                }
                if ((fieldType.equals("single_choice") || fieldType.equals("multiple_choice"))
                        && collection(field.get("options")).isEmpty()) {
                    throw bad("answer_schema_invalid", "字段选择项不能为空");
                }
            }
        }
    }

    private void validateAnswer(Map<String, Object> schema, Object answer) {
        var type = String.valueOf(schema.containsKey("type") ? schema.get("type") : "string");
        switch (type) {
            case "string" -> {
                if (!(answer instanceof String value) || value.isBlank() || value.length() > MAX_TEXT_LENGTH)
                    throw bad("interaction_answer_invalid", "请填写有效回答");
            }
            case "number" -> {
                if (!(answer instanceof Number)) {
                    throw bad("interaction_answer_invalid", "回答必须是有效数字");
                }
                try { new BigDecimal(String.valueOf(answer)); }
                catch (NumberFormatException error) {
                    throw bad("interaction_answer_invalid", "回答必须是有效数字");
                }
            }
            case "boolean" -> {
                if (!(answer instanceof Boolean))
                    throw bad("interaction_answer_invalid", "回答必须是是或否");
            }
            case "date" -> {
                try { LocalDate.parse(String.valueOf(answer)); }
                catch (DateTimeParseException error) {
                    throw bad("interaction_answer_invalid", "日期格式必须为 YYYY-MM-DD");
                }
            }
            case "single_choice" -> {
                if (!optionValues(schema).contains(String.valueOf(answer)))
                    throw bad("interaction_answer_invalid", "请选择有效候选项");
            }
            case "multiple_choice" -> {
                if (!(answer instanceof Collection<?> selected) || selected.isEmpty()
                        || !optionValues(schema).containsAll(selected.stream().map(String::valueOf).toList()))
                    throw bad("interaction_answer_invalid", "请选择有效候选项");
            }
            case "field_review" -> {
                if (!(answer instanceof Map<?, ?> value) || value.isEmpty()) {
                    throw bad("interaction_answer_invalid", "请提交待复核字段");
                }
                validateFieldReview(schema, value);
            }
            case "file_upload" -> {
                if (!(answer instanceof Collection<?> value) || value.isEmpty())
                    throw bad("interaction_answer_invalid", "请先上传补充文件");
            }
            default -> throw bad("answer_schema_invalid", "问题回答类型不受支持");
        }
    }

    private void validateFieldReview(Map<String, Object> schema, Map<?, ?> answer) {
        var known = new java.util.HashSet<String>();
        for (var raw : collection(schema.get("fields"))) {
            if (!(raw instanceof Map<?, ?> field)) {
                throw bad("answer_schema_invalid", "字段复核定义无效");
            }
            var name = String.valueOf(field.get("name"));
            known.add(name);
            var required = Boolean.TRUE.equals(field.get("required"));
            if (!answer.containsKey(name) || answer.get(name) == null
                    || answer.get(name) instanceof String text && text.isBlank()) {
                if (required) {
                    throw bad("interaction_answer_invalid", "请填写" + fieldLabel(field, name));
                }
                continue;
            }
            validateFieldValue(field, answer.get(name), fieldLabel(field, name));
        }
        for (var key : answer.keySet()) {
            if (!known.contains(String.valueOf(key))) {
                throw bad("interaction_answer_invalid", "回答包含未定义字段");
            }
        }
    }

    private void validateFieldValue(Map<?, ?> schema, Object value, String label) {
        var type = String.valueOf(schema.containsKey("type") ? schema.get("type") : "string");
        switch (type) {
            case "string" -> {
                if (!(value instanceof String text) || text.isBlank() || text.length() > MAX_TEXT_LENGTH) {
                    throw bad("interaction_answer_invalid", label + "必须是有效文本");
                }
            }
            case "number" -> {
                if (!(value instanceof Number)) {
                    throw bad("interaction_answer_invalid", label + "必须是有效数字");
                }
                try { new BigDecimal(String.valueOf(value)); }
                catch (NumberFormatException error) {
                    throw bad("interaction_answer_invalid", label + "必须是有效数字");
                }
            }
            case "boolean" -> {
                if (!(value instanceof Boolean)) {
                    throw bad("interaction_answer_invalid", label + "必须是是或否");
                }
            }
            case "date" -> {
                if (!(value instanceof String text)) {
                    throw bad("interaction_answer_invalid", label + "日期格式必须为 YYYY-MM-DD");
                }
                try { LocalDate.parse(text); }
                catch (DateTimeParseException error) {
                    throw bad("interaction_answer_invalid", label + "日期格式必须为 YYYY-MM-DD");
                }
            }
            case "single_choice" -> {
                if (!fieldOptionValues(schema).contains(String.valueOf(value))) {
                    throw bad("interaction_answer_invalid", "请选择有效的" + label);
                }
            }
            case "multiple_choice" -> {
                if (!(value instanceof Collection<?> selected) || selected.isEmpty()
                        || !fieldOptionValues(schema).containsAll(
                                selected.stream().map(String::valueOf).toList())) {
                    throw bad("interaction_answer_invalid", "请选择有效的" + label);
                }
            }
            default -> throw bad("answer_schema_invalid", "字段复核类型不受支持");
        }
    }

    private List<String> fieldOptionValues(Map<?, ?> schema) {
        return collection(schema.get("options")).stream().map(item -> {
            if (item instanceof Map<?, ?> option) return String.valueOf(option.get("value"));
            return String.valueOf(item);
        }).toList();
    }

    private String fieldLabel(Map<?, ?> field, String fallback) {
        var label = String.valueOf(field.containsKey("label") ? field.get("label") : fallback).strip();
        return label.isBlank() ? fallback : label;
    }

    private List<String> optionValues(Map<String, Object> schema) {
        return collection(schema.get("options")).stream().map(item -> {
            if (item instanceof Map<?, ?> option) return String.valueOf(option.get("value"));
            return String.valueOf(item);
        }).toList();
    }

    private HumanInteractionDtos.View view(HumanInteraction value) {
        return new HumanInteractionDtos.View(
                value.getId(), value.getTaskId(), value.getRunId(), value.getCheckpointId(), value.getGeneration(),
                value.getKind(), value.getQuestion(), value.getReason(), value.getBusinessStep(),
                value.getRelatedFields(), value.getRelatedArtifactIds(), value.getAnswerSchema(),
                value.getStatus(), value.getAnswer(), value.getAnswerNote(), value.getAnswerArtifactIds(),
                value.getAnsweredBy(), value.getAnsweredAt(), value.getAppliedAt(), value.getExpiresAt(),
                value.getCancelReason(), value.getOperationId(), value.getCreatedAt(), value.getUpdatedAt());
    }

    private ProcurementDtos.OperationAccepted accepted(AgentCommand command) {
        return new ProcurementDtos.OperationAccepted(
                command.getOperationId(), command.getAggregateId(), null, null, command.getStatus(),
                "/api/procurement/operations/" + command.getOperationId());
    }

    private String requireIdempotencyKey(String value) {
        if (value == null || value.isBlank())
            throw bad("idempotency_key_required", "回答问题必须提供 Idempotency-Key");
        var key = value.strip();
        if (key.length() > MAX_KEY_LENGTH)
            throw bad("invalid_idempotency_key", "幂等键不得超过 200 个字符");
        return key;
    }

    private String text(Object value, int max, String code) {
        var result = value == null ? "" : String.valueOf(value).strip();
        if (result.isBlank() || result.length() > max) throw bad(code, "Agent 问题内容无效");
        return result;
    }

    private String optionalText(Object value, int max) {
        var result = value == null ? null : String.valueOf(value).strip();
        return result == null || result.isBlank() ? null : result.substring(0, Math.min(max, result.length()));
    }

    private List<String> stringList(Object value, int maxItems, int maxLength) {
        var result = new ArrayList<String>();
        for (var item : collection(value)) {
            var text = String.valueOf(item).strip();
            if (!text.isBlank() && text.length() <= maxLength && !result.contains(text)) result.add(text);
            if (result.size() >= maxItems) break;
        }
        return result;
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> map(Object value) {
        if (value instanceof Map<?, ?> map) return new LinkedHashMap<>((Map<String, Object>) map);
        return new LinkedHashMap<>();
    }

    private Collection<?> collection(Object value) {
        return value instanceof Collection<?> collection ? collection : List.of();
    }

    private Instant parseInstant(Object value) {
        if (value == null || String.valueOf(value).isBlank()) return null;
        try { return Instant.parse(String.valueOf(value)); }
        catch (RuntimeException error) { throw bad("interaction_expiry_invalid", "问题过期时间无效"); }
    }

    private ApiException bad(String code, String message) {
        return new ApiException(HttpStatus.UNPROCESSABLE_ENTITY, code, message);
    }

    private ApiException conflict(String code, String message) {
        return new ApiException(HttpStatus.CONFLICT, code, message);
    }

    private ApiException notFound(String code, String message) {
        return new ApiException(HttpStatus.NOT_FOUND, code, message);
    }
}
