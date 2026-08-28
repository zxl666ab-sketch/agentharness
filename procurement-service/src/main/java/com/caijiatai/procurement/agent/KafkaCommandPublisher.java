package com.caijiatai.procurement.agent;

import com.caijiatai.procurement.config.AppProperties;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Component;

@Component
@ConditionalOnProperty(prefix = "app", name = "agent-mode", havingValue = "kafka")
public final class KafkaCommandPublisher implements AgentDispatcher {
    public static final String COMMANDS_TOPIC = "caijiatai.commands";

    private static final Logger log = LoggerFactory.getLogger(KafkaCommandPublisher.class);

    private final KafkaTemplate<String, byte[]> kafka;
    private final String hmacKey;

    public KafkaCommandPublisher(KafkaTemplate<String, byte[]> kafka, AppProperties properties) {
        this.kafka = kafka;
        this.hmacKey = properties.internalHmacKey();
    }

    @Override
    public boolean isAsync() {
        return true;
    }

    @Override
    public DispatchResult dispatch(AgentCommand command) {
        var envelope = new LinkedHashMap<String, Object>();
        var aiTaskId = text(command.getPayload().get("ai_task_id"));
        var isAiTask = "analyze".equals(command.getOperationType()) && !aiTaskId.isBlank();
        envelope.put("schema_version", 1);
        envelope.put("message_type", isAiTask ? "ai_task.command" : "agent.command");
        envelope.put("message_id", UUID.randomUUID().toString().replace("-", ""));
        envelope.put("operation_id", command.getOperationId());
        envelope.put("operation_type", command.getOperationType());
        envelope.put("aggregate_id", command.getAggregateId());
        if (isAiTask) {
            envelope.put("ai_task_id", aiTaskId);
            envelope.put("business_id", command.getAggregateId());
            envelope.put("trace_id", command.getPayload().get("trace_id"));
            envelope.put("task_type", command.getPayload().get("task_type"));
            envelope.put("file_ids", command.getPayload().getOrDefault("file_ids", java.util.List.of()));
        }
        envelope.put("generation", command.getGeneration());
        envelope.put("expected_task_version", command.getExpectedTaskVersion());
        envelope.put("payload_sha256", command.getPayloadSha256());
        envelope.put("payload", command.getPayload());
        envelope.put("published_at", Instant.now().toString());
        envelope.put("signature", MessageCodec.signEnvelope(hmacKey, envelope));
        // J-H1: fire-and-forget used to swallow producer failures (oversize payload,
        // broker down) until the outbox republish timeout misclassified them.
        kafka.send(COMMANDS_TOPIC, command.getAggregateId(), CanonicalJson.bytes(envelope))
                .whenComplete((sent, sendError) -> {
                    if (sendError != null) {
                        log.error("命令发送失败：operation_id={} aggregate_id={}",
                                command.getOperationId(), command.getAggregateId(), sendError);
                    }
                });
        return null;
    }

    private String text(Object value) {
        return value == null ? "" : String.valueOf(value);
    }
}
