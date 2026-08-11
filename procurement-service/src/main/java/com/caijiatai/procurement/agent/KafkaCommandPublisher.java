package com.caijiatai.procurement.agent;

import com.caijiatai.procurement.config.AppProperties;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Component;

@Component
@ConditionalOnProperty(prefix = "app.agent", name = "mode", havingValue = "kafka")
public final class KafkaCommandPublisher implements AgentDispatcher {
    public static final String COMMANDS_TOPIC = "caijiatai.commands";

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
    public AgentClient.DispatchResult dispatch(AgentCommand command) {
        var envelope = new LinkedHashMap<String, Object>();
        envelope.put("operation_id", command.getOperationId());
        envelope.put("operation_type", command.getOperationType());
        envelope.put("aggregate_id", command.getAggregateId());
        envelope.put("generation", command.getGeneration());
        envelope.put("expected_task_version", command.getExpectedTaskVersion());
        envelope.put("payload_sha256", command.getPayloadSha256());
        envelope.put("payload", command.getPayload());
        envelope.put("published_at", Instant.now().toString());
        envelope.put("signature", MessageCodec.sign(
                hmacKey, command.getOperationId(), command.getPayloadSha256(), "command"));
        kafka.send(COMMANDS_TOPIC, command.getAggregateId(), CanonicalJson.bytes(envelope));
        return null;
    }
}
