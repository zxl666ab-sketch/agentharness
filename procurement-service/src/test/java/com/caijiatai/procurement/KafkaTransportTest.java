package com.caijiatai.procurement;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import com.caijiatai.procurement.agent.AgentCommand;
import com.caijiatai.procurement.agent.CanonicalJson;
import com.caijiatai.procurement.agent.KafkaCommandPublisher;
import com.caijiatai.procurement.agent.KafkaRpcServer;
import com.caijiatai.procurement.agent.MessageCodec;
import com.caijiatai.procurement.agent.RuntimeEvent;
import com.caijiatai.procurement.agent.RuntimeEventRepository;
import com.caijiatai.procurement.artifact.ArtifactStore;
import com.caijiatai.procurement.artifact.BusinessArtifactRepository;
import com.caijiatai.procurement.config.AppProperties;
import com.caijiatai.procurement.quote.ProcurementQuoteRepository;
import com.caijiatai.procurement.task.ProcurementTaskRepository;
import com.caijiatai.procurement.task.TaskViewMapper;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentLinkedQueue;
import org.apache.kafka.clients.consumer.ConsumerConfig;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.apache.kafka.clients.consumer.KafkaConsumer;
import org.apache.kafka.common.serialization.ByteArrayDeserializer;
import org.apache.kafka.common.serialization.ByteArraySerializer;
import org.apache.kafka.common.serialization.StringDeserializer;
import org.apache.kafka.common.serialization.StringSerializer;
import org.junit.jupiter.api.Test;
import org.springframework.data.domain.Pageable;
import org.springframework.kafka.core.DefaultKafkaProducerFactory;
import org.springframework.kafka.core.KafkaTemplate;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.kafka.ConfluentKafkaContainer;

@Testcontainers
class KafkaTransportTest {
    private static final String HMAC = "test-hmac-key-for-kafka-transport-0123456789abcdef";

    @Container
    static final ConfluentKafkaContainer KAFKA = new ConfluentKafkaContainer("confluentinc/cp-kafka:7.8.0");

    private KafkaTemplate<String, byte[]> template() {
        var props = new java.util.HashMap<String, Object>();
        props.put(ConsumerConfig.BOOTSTRAP_SERVERS_CONFIG, KAFKA.getBootstrapServers());
        props.put(org.apache.kafka.clients.producer.ProducerConfig.KEY_SERIALIZER_CLASS_CONFIG,
                StringSerializer.class);
        props.put(org.apache.kafka.clients.producer.ProducerConfig.VALUE_SERIALIZER_CLASS_CONFIG,
                ByteArraySerializer.class);
        props.put(org.apache.kafka.clients.producer.ProducerConfig.ACKS_CONFIG, "all");
        var factory = new DefaultKafkaProducerFactory<String, byte[]>(props);
        return new KafkaTemplate<>(factory);
    }

    private AppProperties properties() {
        return new AppProperties("采购员", java.nio.file.Path.of("target", "test-artifacts-kafka"),
                java.net.URI.create("http://127.0.0.1:5173"), false,
                new AppProperties.Outbox(500), "kafka", new AppProperties.DemoSeed(false, java.nio.file.Path.of(".")),
                HMAC);
    }

    @Test
    void commandPublisherSignsAndSendsKafkaMessage() throws Exception {
        var topic = "caijiatai.commands";
        var template = template();
        var publisher = new KafkaCommandPublisher(template, properties());
        var command = AgentCommand.accept("analyze", "a".repeat(32), 1, 0,
                Map.ofEntries(
                        Map.entry("task_id", "a".repeat(32)),
                        Map.entry("ai_task_id", "b".repeat(32)),
                        Map.entry("trace_id", "c".repeat(32)),
                        Map.entry("task_type", "QUOTE_ANALYSIS"),
                        Map.entry("file_ids", List.of("jb" + "d".repeat(32))),
                        Map.entry("input_sha256", "e".repeat(64))));

        publisher.dispatch(command);

        var records = new ConcurrentLinkedQueue<ConsumerRecord<String, byte[]>>();
        var consumerProps = new java.util.HashMap<String, Object>();
        consumerProps.put(ConsumerConfig.BOOTSTRAP_SERVERS_CONFIG, KAFKA.getBootstrapServers());
        consumerProps.put(ConsumerConfig.GROUP_ID_CONFIG, "test-" + UUID.randomUUID());
        consumerProps.put(ConsumerConfig.AUTO_OFFSET_RESET_CONFIG, "earliest");
        consumerProps.put(ConsumerConfig.KEY_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class);
        consumerProps.put(ConsumerConfig.VALUE_DESERIALIZER_CLASS_CONFIG, ByteArrayDeserializer.class);
        try (var consumer = new KafkaConsumer<String, byte[]>(consumerProps)) {
            consumer.subscribe(List.of(topic));
            long deadline = System.currentTimeMillis() + 15_000;
            while (records.isEmpty() && System.currentTimeMillis() < deadline) {
                consumer.poll(Duration.ofMillis(300)).forEach(records::add);
            }
        }
        assertThat(records).isNotEmpty();
        var envelope = CanonicalJson.read(records.peek().value());
        assertThat(envelope.get("operation_id")).isEqualTo(command.getOperationId());
        assertThat(envelope).containsEntry("message_type", "ai_task.command");
        assertThat(envelope).containsEntry("ai_task_id", "b".repeat(32));
        assertThat(envelope).containsEntry("business_id", "a".repeat(32));
        assertThat(envelope).containsEntry("trace_id", "c".repeat(32));
        assertThat(envelope).containsEntry("task_type", "QUOTE_ANALYSIS");
        assertThat(envelope.get("file_ids")).isEqualTo(List.of("jb" + "d".repeat(32)));
        assertThat(envelope.get("payload_sha256")).isEqualTo(command.getPayloadSha256());
        assertThat(envelope.get("signature")).isEqualTo(MessageCodec.signEnvelope(HMAC, envelope));
    }

    @Test
    void rpcServerListEventsReturnsSignedResponse() {
        var topic = "caijiatai.rpc.requests";
        var responses = "caijiatai.rpc.responses";
        var template = template();

        var events = mock(RuntimeEventRepository.class);
        when(events.findByGlobalSeqGreaterThanOrderByGlobalSeqAsc(anyLong(), any(Pageable.class)))
                .thenReturn(List.of(RuntimeEvent.create(1L, "t".repeat(32), "r".repeat(32),
                        "run_started", Map.of("k", "v"), Instant.parse("2026-08-11T00:00:00Z"))));
        var server = new KafkaRpcServer(
                template, properties(),
                mock(ProcurementTaskRepository.class),
                mock(ProcurementQuoteRepository.class),
                mock(BusinessArtifactRepository.class),
                mock(ArtifactStore.class),
                events,
                mock(TaskViewMapper.class));

        var correlationId = UUID.randomUUID().toString().replace("-", "");
        var payload = Map.<String, Object>of("after_seq", 0L, "limit", 10);
        var requestSha = CanonicalJson.sha256(payload);
        var envelope = new java.util.LinkedHashMap<String, Object>(Map.of(
                "correlation_id", correlationId,
                "kind", "list_events",
                "payload", payload,
                "reply_to", responses,
                "request_sha256", requestSha));
        envelope.put("signature", MessageCodec.signEnvelope(HMAC, envelope));
        server.onRequest(new ConsumerRecord<>(topic, 0, 0L, correlationId, CanonicalJson.bytes(envelope)));

        var records = new ConcurrentLinkedQueue<ConsumerRecord<String, byte[]>>();
        var consumerProps = new java.util.HashMap<String, Object>();
        consumerProps.put(ConsumerConfig.BOOTSTRAP_SERVERS_CONFIG, KAFKA.getBootstrapServers());
        consumerProps.put(ConsumerConfig.GROUP_ID_CONFIG, "test-rpc-" + UUID.randomUUID());
        consumerProps.put(ConsumerConfig.AUTO_OFFSET_RESET_CONFIG, "earliest");
        consumerProps.put(ConsumerConfig.KEY_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class);
        consumerProps.put(ConsumerConfig.VALUE_DESERIALIZER_CLASS_CONFIG, ByteArrayDeserializer.class);
        try (var consumer = new KafkaConsumer<String, byte[]>(consumerProps)) {
            consumer.subscribe(List.of(responses));
            long deadline = System.currentTimeMillis() + 15_000;
            while (records.isEmpty() && System.currentTimeMillis() < deadline) {
                consumer.poll(Duration.ofMillis(300)).forEach(records::add);
            }
        }
        assertThat(records).isNotEmpty();
        var response = CanonicalJson.read(records.peek().value());
        assertThat(response.get("correlation_id")).isEqualTo(correlationId);
        assertThat(response.get("status")).isEqualTo("ok");
        assertThat(response.get("signature")).isEqualTo(MessageCodec.signEnvelope(HMAC, response));
        var result = (Map<?, ?>) response.get("result");
        assertThat((List<?>) result.get("events")).hasSize(1);
    }
}
