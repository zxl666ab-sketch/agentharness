package com.caijiatai.procurement.agent;

import com.caijiatai.procurement.api.ApiException;
import com.caijiatai.procurement.ai.AiErrorCategory;
import com.caijiatai.procurement.ai.AiTaskService;
import com.caijiatai.procurement.config.AppProperties;
import java.util.Map;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

@Component
@ConditionalOnProperty(prefix = "app", name = "agent-mode", havingValue = "kafka")
public class KafkaResultConsumer {
    private static final Logger log = LoggerFactory.getLogger(KafkaResultConsumer.class);

    private final AgentCommandRepository commands;
    private final AgentResultApplication resultApplication;
    private final String hmacKey;
    private final AiTaskService aiTasks;

    public KafkaResultConsumer(AgentCommandRepository commands, AgentResultApplication resultApplication,
            AppProperties properties, AiTaskService aiTasks) {
        this.commands = commands;
        this.resultApplication = resultApplication;
        this.hmacKey = properties.internalHmacKey();
        this.aiTasks = aiTasks;
    }

    @KafkaListener(topics = "caijiatai.results", groupId = "java-svc")
    @Transactional
    public void onResult(ConsumerRecord<String, byte[]> record) {
        var envelope = CanonicalJson.read(record.value());
        var operationId = text(envelope.get("operation_id"));
        var payloadSha = text(envelope.get("payload_sha256"));
        if (!MessageCodec.verifyEnvelope(hmacKey, envelope)) {
            log.warn("结果签名校验失败：{}", operationId);
            return;
        }
        var command = commands.findById(operationId).orElse(null);
        if (command == null) {
            log.warn("收到未知命令结果：{}", operationId);
            return;
        }
        if ("completed".equals(command.getStatus()) || "failed".equals(command.getStatus())
                || "cancelled".equals(command.getStatus())) {
            return; // 至少一次投递 + 幂等
        }
        if (!payloadSha.equals(command.getPayloadSha256())) {
            command.fail("Agent 结果 payload_sha256 与命令不一致");
            resultApplication.recordTerminalFailure(
                    command, "结果载荷指纹不一致", AiErrorCategory.VALIDATION, false);
            commands.save(command);
            return;
        }
        if ("failed".equals(envelope.get("status"))) {
            var error = text(envelope.getOrDefault("error", "Agent 操作失败"));
            command.fail(error);
            var category = category(envelope.get("error_category"));
            var retryable = Boolean.TRUE.equals(envelope.get("retryable"));
            resultApplication.recordTerminalFailure(command, error, category, retryable);
            if (retryable) aiTasks.retryAutomatically(command);
            commands.save(command);
            return;
        }
        try {
            resultApplication.apply(command, envelope);
            command.complete(envelope);
            commands.save(command);
        } catch (ApiException error) {
            command.fail(error.code() + ": " + error.getMessage());
            resultApplication.recordTerminalFailure(command, error.code() + ": " + error.getMessage());
            commands.save(command);
        }
    }

    private String text(Object value) {
        return value == null ? "" : String.valueOf(value);
    }

    private AiErrorCategory category(Object value) {
        try {
            return AiErrorCategory.valueOf(text(value));
        } catch (IllegalArgumentException error) {
            return AiErrorCategory.INTERNAL;
        }
    }
}
