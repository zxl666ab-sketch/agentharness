package com.caijiatai.procurement.agent;

import com.caijiatai.procurement.api.ApiException;
import com.caijiatai.procurement.artifact.ArtifactStore;
import com.caijiatai.procurement.artifact.BusinessArtifactRepository;
import com.caijiatai.procurement.approval.PendingDecision;
import com.caijiatai.procurement.approval.PendingDecisionRepository;
import com.caijiatai.procurement.config.AppProperties;
import com.caijiatai.procurement.interaction.HumanInteractionRepository;
import com.caijiatai.procurement.quote.ProcurementQuoteRepository;
import com.caijiatai.procurement.task.ProcurementTaskRepository;
import com.caijiatai.procurement.task.TaskViewMapper;
import java.nio.file.Files;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.data.domain.PageRequest;
import org.springframework.http.HttpStatus;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Component;

@Component
@ConditionalOnProperty(prefix = "app", name = "agent-mode", havingValue = "kafka")
public final class KafkaRpcServer {
    public static final String REQUESTS_TOPIC = "caijiatai.rpc.requests";
    public static final String RESPONSES_TOPIC = "caijiatai.rpc.responses";

    private static final Logger log = LoggerFactory.getLogger(KafkaRpcServer.class);

    private final KafkaTemplate<String, byte[]> kafka;
    private final String hmacKey;
    private final ProcurementTaskRepository tasks;
    private final ProcurementQuoteRepository quotes;
    private final BusinessArtifactRepository artifacts;
    private final ArtifactStore artifactStore;
    private final RuntimeEventRepository events;
    private final TaskViewMapper views;
    private final PendingDecisionRepository pendingDecisions;
    private final AgentCommandRepository commands;
    private final HumanInteractionRepository interactions;

    public KafkaRpcServer(KafkaTemplate<String, byte[]> kafka, AppProperties properties,
            ProcurementTaskRepository tasks, ProcurementQuoteRepository quotes,
            BusinessArtifactRepository artifacts, ArtifactStore artifactStore,
            RuntimeEventRepository events, TaskViewMapper views,
            PendingDecisionRepository pendingDecisions,
            AgentCommandRepository commands,
            HumanInteractionRepository interactions) {
        this.kafka = kafka;
        this.hmacKey = properties.internalHmacKey();
        this.tasks = tasks;
        this.quotes = quotes;
        this.artifacts = artifacts;
        this.artifactStore = artifactStore;
        this.events = events;
        this.views = views;
        this.pendingDecisions = pendingDecisions;
        this.commands = commands;
        this.interactions = interactions;
    }

    @KafkaListener(topics = REQUESTS_TOPIC, groupId = "java-svc-rpc")
    public void onRequest(ConsumerRecord<String, byte[]> record) {
        var envelope = CanonicalJson.read(record.value());
        var correlationId = text(envelope.get("correlation_id"));
        var kind = text(envelope.get("kind"));
        var requestSha = text(envelope.get("request_sha256"));
        if (!MessageCodec.verifyEnvelope(hmacKey, envelope)
                || !requestSha.equals(CanonicalJson.sha256(map(envelope.get("payload"))))) {
            log.warn("RPC 签名校验失败：{}", correlationId);
            // J-M1: answer with a signed error envelope instead of going silent,
            // otherwise the caller blocks until its own reply timeout.
            publishResponse(correlationId, Map.of(), "rpc_signature_invalid", requestSha);
            return;
        }
        Map<String, Object> result = Map.of();
        String error = null;
        try {
            result = switch (kind) {
                case "get_task_context" -> taskContext(envelope);
                case "get_artifact" -> artifact(envelope);
                case "list_events" -> listEvents(envelope);
                default -> throw new ApiException(HttpStatus.BAD_REQUEST, "unknown_rpc_kind", "未知 RPC 类型");
            };
        } catch (ApiException apiError) {
            error = apiError.code() + ": " + apiError.getMessage();
        } catch (Exception other) {
            error = "rpc_failed: " + other.getMessage();
            log.warn("RPC {} 处理失败", kind, other);
        }
        publishResponse(correlationId, result, error, requestSha);
    }

    private void publishResponse(
            String correlationId, Map<String, Object> result, String error, String requestSha) {
        var response = new LinkedHashMap<String, Object>();
        response.put("correlation_id", correlationId);
        response.put("status", error == null ? "ok" : "error");
        response.put("result", result);
        response.put("error", error);
        response.put("request_sha256", requestSha);
        response.put("processed_at", Instant.now().toString());
        response.put("signature", MessageCodec.signEnvelope(hmacKey, response));
        // J-H1: a producer-side failure (e.g. oversize artifact response beyond
        // max.request.size) used to vanish into the unobserved future.
        kafka.send(RESPONSES_TOPIC, correlationId, CanonicalJson.bytes(response))
                .whenComplete((sent, sendError) -> {
                    if (sendError != null) {
                        log.error("RPC 应答发送失败：correlation_id={}", correlationId, sendError);
                    }
                });
    }

    private Map<String, Object> taskContext(Map<String, Object> envelope) {
        var payload = map(envelope.get("payload"));
        var taskId = text(payload.get("task_id"));
        var task = tasks.findById(taskId)
                .orElseThrow(() -> new ApiException(HttpStatus.NOT_FOUND, "task_not_found", "任务不存在"));
        var value = views.detail(
                task,
                List.of(),
                quotes.findByTaskIdOrderByCreatedAtAsc(taskId),
                null,
                null);
        value.put("attachments", artifacts.findByTaskIdOrderByCreatedAtAsc(taskId).stream()
                .filter(item -> "procurement_original".equals(item.getKind()))
                .map(item -> Map.<String, Object>of(
                        "artifact_id", item.getId(),
                        "filename", item.getFilename(),
                        "sha256", item.getSha256(),
                        "content_type", item.getContentType(),
                        "size_bytes", item.getSizeBytes()))
                .toList());
        value.put("authorized_artifacts", artifacts.findByTaskIdOrderByCreatedAtAsc(taskId).stream()
                .filter(item -> "procurement_original".equals(item.getKind())
                        || "human_interaction_attachment".equals(item.getKind()))
                .map(item -> Map.<String, Object>of(
                        "artifact_id", item.getId(),
                        "filename", item.getFilename(),
                        "sha256", item.getSha256(),
                        "content_type", item.getContentType(),
                        "size_bytes", item.getSizeBytes()))
                .toList());
        value.put("source_message", commands
                .findFirstByAggregateIdAndOperationTypeOrderByAcceptedAtAsc(taskId, "start_conversation")
                .map(command -> text(command.getPayload().get("message")))
                .orElse(""));
        value.put("interactions", interactions.findByTaskIdOrderByCreatedAtDesc(taskId).stream()
                .map(item -> {
                    var entry = new LinkedHashMap<String, Object>();
                    entry.put("interaction_id", item.getId());
                    entry.put("run_id", item.getRunId());
                    entry.put("checkpoint_id", item.getCheckpointId());
                    entry.put("generation", item.getGeneration());
                    entry.put("status", item.getStatus());
                    entry.put("answer_schema", item.getAnswerSchema());
                    entry.put("answer", item.getAnswer());
                    entry.put("note", item.getAnswerNote());
                    entry.put("artifact_ids", item.getAnswerArtifactIds());
                    return entry;
                })
                .toList());
        value.put("pending_decisions", pendingDecisions
                .findByTaskIdAndStatusIn(taskId, List.of("pending", "approved", "stale"))
                .stream()
                .map(this::pendingDecision)
                .toList());
        return value;
    }

    private Map<String, Object> pendingDecision(PendingDecision pending) {
        var value = new LinkedHashMap<String, Object>();
        value.put("pending_decision_id", pending.getId());
        value.put("operation_id", pending.getOperationId());
        value.put("run_id", pending.getRunId());
        value.put("tool_name", pending.getToolName());
        value.put("task_version", pending.getTaskVersion());
        value.put("snapshot_id", pending.getSnapshotId());
        value.put("input_sha256", pending.getInputSha256());
        value.put("business_decision", pending.getDecision());
        value.put("quote_id", pending.getQuoteId());
        value.put("note_hash", pending.getNoteHash());
        value.put("status", pending.getStatus());
        return value;
    }

    private Map<String, Object> artifact(Map<String, Object> envelope) throws Exception {
        var payload = map(envelope.get("payload"));
        var artifactId = text(payload.get("artifact_id"));
        var artifact = artifacts.findById(artifactId)
                .orElseThrow(() -> new ApiException(HttpStatus.NOT_FOUND, "artifact_not_found", "业务文件不存在"));
        var bytes = Files.readAllBytes(artifactStore.path(artifact));
        if (bytes.length > 2 * 1024 * 1024) {
            throw new ApiException(HttpStatus.PAYLOAD_TOO_LARGE, "artifact_too_large",
                    "业务文件超过 2MB，需要分块传输（二期实现）");
        }
        var value = new LinkedHashMap<String, Object>();
        value.put("artifact_id", artifact.getId());
        value.put("task_id", artifact.getTaskId());
        value.put("filename", artifact.getFilename());
        value.put("content_type", artifact.getContentType());
        value.put("sha256", artifact.getSha256());
        value.put("size_bytes", artifact.getSizeBytes());
        value.put("base64", Base64.getEncoder().encodeToString(bytes));
        return value;
    }

    private Map<String, Object> listEvents(Map<String, Object> envelope) {
        var payload = map(envelope.get("payload"));
        long after = longValue(payload.get("after_seq"));
        // J-M2: clamp caller-supplied limit like OrderService.list does. An unclamped
        // limit let one list_events RPC pull the whole projection into a single reply.
        // Other RPC kinds (get_task_context / get_artifact) take no limit parameter.
        var requestedLimit = payload.containsKey("limit") ? integer(payload.get("limit")) : 100;
        var limit = Math.min(Math.max(1, requestedLimit), 500);
        var rows = events.findByGlobalSeqGreaterThanOrderByGlobalSeqAsc(after, PageRequest.of(0, limit));
        var items = new ArrayList<Map<String, Object>>();
        for (var row : rows) {
            var item = new LinkedHashMap<String, Object>();
            item.put("global_seq", row.getGlobalSeq());
            item.put("task_id", row.getTaskId());
            item.put("run_id", row.getRunId());
            item.put("type", row.getType());
            item.put("payload", row.getPayload());
            item.put("occurred_at", row.getOccurredAt().toString());
            items.add(item);
        }
        return Map.of("events", items, "next_after_seq", rows.isEmpty() ? after : rows.getLast().getGlobalSeq());
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> map(Object value) {
        return value instanceof Map<?, ?> raw ? (Map<String, Object>) raw : Map.of();
    }

    private String text(Object value) {
        return value == null ? "" : String.valueOf(value);
    }

    private long longValue(Object value) {
        return value == null ? 0L : Long.parseLong(String.valueOf(value));
    }

    private int integer(Object value) {
        return Integer.parseInt(String.valueOf(value));
    }
}
