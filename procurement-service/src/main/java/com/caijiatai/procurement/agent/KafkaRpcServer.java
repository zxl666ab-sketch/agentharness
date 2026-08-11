package com.caijiatai.procurement.agent;

import com.caijiatai.procurement.api.ApiException;
import com.caijiatai.procurement.artifact.ArtifactStore;
import com.caijiatai.procurement.artifact.BusinessArtifactRepository;
import com.caijiatai.procurement.config.AppProperties;
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
@ConditionalOnProperty(prefix = "app.agent", name = "mode", havingValue = "kafka")
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

    public KafkaRpcServer(KafkaTemplate<String, byte[]> kafka, AppProperties properties,
            ProcurementTaskRepository tasks, ProcurementQuoteRepository quotes,
            BusinessArtifactRepository artifacts, ArtifactStore artifactStore,
            RuntimeEventRepository events, TaskViewMapper views) {
        this.kafka = kafka;
        this.hmacKey = properties.internalHmacKey();
        this.tasks = tasks;
        this.quotes = quotes;
        this.artifacts = artifacts;
        this.artifactStore = artifactStore;
        this.events = events;
        this.views = views;
    }

    @KafkaListener(topics = REQUESTS_TOPIC, groupId = "java-svc-rpc")
    public void onRequest(ConsumerRecord<String, byte[]> record) {
        var envelope = CanonicalJson.read(record.value());
        var correlationId = text(envelope.get("correlation_id"));
        var kind = text(envelope.get("kind"));
        var requestSha = text(envelope.get("request_sha256"));
        var signature = text(envelope.get("signature"));
        if (!MessageCodec.verify(hmacKey, correlationId, requestSha, "rpc", signature)) {
            log.warn("RPC 签名校验失败：{}", correlationId);
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
        var response = new LinkedHashMap<String, Object>();
        response.put("correlation_id", correlationId);
        response.put("status", error == null ? "ok" : "error");
        response.put("result", result);
        response.put("error", error);
        response.put("request_sha256", requestSha);
        response.put("processed_at", Instant.now().toString());
        response.put("signature", MessageCodec.sign(hmacKey, correlationId, requestSha, "rpc"));
        kafka.send(RESPONSES_TOPIC, correlationId, CanonicalJson.bytes(response));
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
        value.remove("attachments");
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
        var limit = payload.containsKey("limit") ? integer(payload.get("limit")) : 100;
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
