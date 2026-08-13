package com.caijiatai.procurement.agent;

import com.caijiatai.procurement.config.AppProperties;
import com.caijiatai.procurement.ai.AiTaskService;
import java.time.Instant;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

@Component
@ConditionalOnProperty(prefix = "app", name = "agent-mode", havingValue = "kafka")
public class KafkaEventConsumer {
    private static final Logger log = LoggerFactory.getLogger(KafkaEventConsumer.class);

    private final RuntimeEventRepository events;
    private final String hmacKey;
    private final AiTaskService aiTasks;

    public KafkaEventConsumer(
            RuntimeEventRepository events,
            AppProperties properties,
            AiTaskService aiTasks) {
        this.events = events;
        this.hmacKey = properties.internalHmacKey();
        this.aiTasks = aiTasks;
    }

    @KafkaListener(topics = "caijiatai.events", groupId = "java-svc-events")
    @Transactional
    public void onEvent(ConsumerRecord<String, byte[]> record) {
        var envelope = CanonicalJson.read(record.value());
        var eventType = text(envelope.get("type"));
        var globalSeq = longValue(envelope.get("global_seq"));
        var taskId = text(envelope.get("task_id"));
        var runId = text(envelope.get("run_id"));
        var payload = envelope.get("payload");
        if (payload instanceof java.util.Map<?, ?> raw) {
            @SuppressWarnings("unchecked")
            var payloadMap = (java.util.Map<String, Object>) raw;
            var payloadSha = text(envelope.get("payload_sha256"));
            if (!MessageCodec.verifyEnvelope(hmacKey, envelope)
                    || !payloadSha.equals(CanonicalJson.sha256(payloadMap))) {
                log.warn("事件签名校验失败：{}", eventType);
                return;
            }
            if (events.existsByGlobalSeq(globalSeq)) {
                return;
            }
            events.save(RuntimeEvent.create(
                    globalSeq,
                    taskId.isBlank() ? null : taskId,
                    runId.isBlank() ? null : runId,
                    eventType,
                    payloadMap,
                    Instant.parse(text(envelope.getOrDefault("occurred_at", Instant.now().toString())))));
            if ("ai_task.step".equals(eventType)) {
                aiTasks.applyStepEvent(envelope);
            }
        }
    }

    private String text(Object value) {
        return value == null ? "" : String.valueOf(value);
    }

    private long longValue(Object value) {
        return value == null ? 0L : Long.parseLong(String.valueOf(value));
    }
}
