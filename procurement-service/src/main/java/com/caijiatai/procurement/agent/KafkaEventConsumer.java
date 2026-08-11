package com.caijiatai.procurement.agent;

import com.caijiatai.procurement.config.AppProperties;
import java.time.Instant;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

@Component
@ConditionalOnProperty(prefix = "app.agent", name = "mode", havingValue = "kafka")
public class KafkaEventConsumer {
    private static final Logger log = LoggerFactory.getLogger(KafkaEventConsumer.class);

    private final RuntimeEventRepository events;
    private final String hmacKey;

    public KafkaEventConsumer(RuntimeEventRepository events, AppProperties properties) {
        this.events = events;
        this.hmacKey = properties.internalHmacKey();
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
            var signature = text(envelope.get("signature"));
            if (!MessageCodec.verify(hmacKey, taskId + ":" + runId + ":" + eventType,
                    payloadSha == null ? "" : payloadSha, "event", signature)) {
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
        }
    }

    private String text(Object value) {
        return value == null ? "" : String.valueOf(value);
    }

    private long longValue(Object value) {
        return value == null ? 0L : Long.parseLong(String.valueOf(value));
    }
}
