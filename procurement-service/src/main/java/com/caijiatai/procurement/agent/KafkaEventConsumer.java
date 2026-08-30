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
        if (!(payload instanceof java.util.Map<?, ?> raw)) {
            // J-M7: previously dropped without a trace (e.g. heartbeat.ping with null
            // payload left /api/runtime permanently 503 with no diagnostic).
            log.warn("事件 payload 非对象，已丢弃：type={}", eventType);
            return;
        }
        @SuppressWarnings("unchecked")
        var payloadMap = (java.util.Map<String, Object>) raw;
        var payloadSha = text(envelope.get("payload_sha256"));
        if (!MessageCodec.verifyEnvelope(hmacKey, envelope)
                || !payloadSha.equals(CanonicalJson.sha256(payloadMap))) {
            log.warn("事件签名校验失败：{}", eventType);
            return;
        }
        // LIVE-1: parse occurred_at first and make it part of the dedup key, so a
        // regressed global_seq (topic pruning + counter re-seeding) cannot silently
        // discard genuinely new events that collide with stale sequence numbers.
        var occurredAt = Instant.parse(
                text(envelope.getOrDefault("occurred_at", Instant.now().toString())));
        if (events.existsByGlobalSeqAndOccurredAt(globalSeq, occurredAt)
                || (!runId.isBlank() && events.existsByRunIdAndTypeAndOccurredAt(runId, eventType, occurredAt))) {
            log.warn("重复事件已跳过：global_seq={} type={}", globalSeq, eventType);
            return;
        }
        // The agent-side counter can restart below Java's high-water mark (retention
        // trims the topic and the durable watermark predates a stale island of old
        // rows). Re-key such events to the tail instead of poisoning the consumer
        // with a unique-constraint violation on uq_runtime_event_global_seq.
        var storedSeq = globalSeq;
        if (events.existsByGlobalSeq(storedSeq)) {
            storedSeq = events.maxGlobalSeq() + 1;
            log.warn("事件序号冲突：global_seq={} 已被历史事件占用，改挂到 {} 入库（type={}）",
                    globalSeq, storedSeq, eventType);
        }
        events.save(RuntimeEvent.create(
                storedSeq,
                taskId.isBlank() ? null : taskId,
                runId.isBlank() ? null : runId,
                eventType,
                payloadMap,
                occurredAt));
        if ("ai_task.step".equals(eventType)) {
            aiTasks.applyStepEvent(envelope);
        }
    }

    private String text(Object value) {
        return value == null ? "" : String.valueOf(value);
    }

    private long longValue(Object value) {
        return value == null ? 0L : Long.parseLong(String.valueOf(value));
    }
}
