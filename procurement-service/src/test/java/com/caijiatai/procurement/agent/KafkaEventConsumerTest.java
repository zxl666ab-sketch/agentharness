package com.caijiatai.procurement.agent;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

import com.caijiatai.procurement.ai.AiTaskService;
import com.caijiatai.procurement.config.AppProperties;
import java.net.URI;
import java.nio.file.Path;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

/** LIVE-1：去重键为 (global_seq, occurred_at)，非对象 payload 有日志丢弃（J-M7）。 */
class KafkaEventConsumerTest {
    private static final String HMAC = "test-hmac-key-for-event-consumer-0123456789abcdef";

    private final RuntimeEventRepository events = mock(RuntimeEventRepository.class);
    private final AiTaskService aiTasks = mock(AiTaskService.class);
    private final KafkaEventConsumer consumer = new KafkaEventConsumer(
            events,
            new AppProperties("采购员", Path.of("target", "test-artifacts-events"),
                    URI.create("http://127.0.0.1:5173"), false,
                    new AppProperties.Outbox(500), "kafka",
                    new AppProperties.DemoSeed(false, Path.of(".")), HMAC),
            aiTasks);

    private ConsumerRecord<String, byte[]> event(long globalSeq, String type, Object payload,
            String occurredAt) {
        var envelope = new LinkedHashMap<String, Object>();
        envelope.put("type", type);
        envelope.put("global_seq", globalSeq);
        envelope.put("task_id", "");
        envelope.put("run_id", "");
        envelope.put("payload", payload);
        envelope.put("occurred_at", occurredAt);
        if (payload instanceof Map<?, ?> map) {
            envelope.put("payload_sha256", CanonicalJson.sha256(map));
        }
        envelope.put("signature", MessageCodec.signEnvelope(HMAC, envelope));
        return new ConsumerRecord<>("caijiatai.events", 0, 0L, type, CanonicalJson.bytes(envelope));
    }

    @Test
    void storesFreshEventWithParsedOccurredAt() {
        var record = event(101L, "heartbeat.ping", Map.of("seq", 1), "2026-09-03T00:00:00Z");
        when(events.existsByGlobalSeqAndOccurredAt(101L, Instant.parse("2026-09-03T00:00:00Z")))
                .thenReturn(false);

        consumer.onEvent(record);

        var saved = ArgumentCaptor.forClass(RuntimeEvent.class);
        verify(events).save(saved.capture());
        assertThat(saved.getValue().getGlobalSeq()).isEqualTo(101L);
        assertThat(saved.getValue().getOccurredAt()).isEqualTo(Instant.parse("2026-09-03T00:00:00Z"));
    }

    @Test
    void exactDuplicateIncludingOccurredAtIsSkipped() {
        var occurredAt = Instant.parse("2026-09-03T00:00:05Z");
        when(events.existsByGlobalSeqAndOccurredAt(eq(102L), eq(occurredAt))).thenReturn(true);

        consumer.onEvent(event(102L, "heartbeat.ping", Map.of("seq", 1), occurredAt.toString()));

        verify(events, never()).save(any());
    }

    @Test
    void regressedGlobalSeqWithNewerOccurredAtIsStillPersisted() {
        // LIVE-1 scenario: the agent re-seeds its counter after topic pruning, so an
        // old global_seq row exists with a different timestamp — the new event must
        // not be dropped the way seq-only dedup dropped it.
        var occurredAt = Instant.parse("2026-09-03T12:00:00Z");
        when(events.existsByGlobalSeqAndOccurredAt(eq(103L), eq(occurredAt))).thenReturn(false);

        consumer.onEvent(event(103L, "run_completed", Map.of("status", "ok"), occurredAt.toString()));

        verify(events).save(any(RuntimeEvent.class));
    }

    @Test
    void nonObjectPayloadIsDroppedWithoutTouchingTheProjection() {
        consumer.onEvent(event(104L, "heartbeat.ping", null, "2026-09-03T00:00:00Z"));

        verifyNoInteractions(events);
        verifyNoInteractions(aiTasks);
    }
}
