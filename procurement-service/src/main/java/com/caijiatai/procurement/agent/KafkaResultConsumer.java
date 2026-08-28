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
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

@Component
@ConditionalOnProperty(prefix = "app", name = "agent-mode", havingValue = "kafka")
public class KafkaResultConsumer {
    private static final Logger log = LoggerFactory.getLogger(KafkaResultConsumer.class);

    private final AgentCommandRepository commands;
    private final AgentResultApplication resultApplication;
    private final String hmacKey;
    private final AiTaskService aiTasks;
    private final TransactionTemplate transactions;

    public KafkaResultConsumer(AgentCommandRepository commands, AgentResultApplication resultApplication,
            AppProperties properties, AiTaskService aiTasks, PlatformTransactionManager transactionManager) {
        this.commands = commands;
        this.resultApplication = resultApplication;
        this.hmacKey = properties.internalHmacKey();
        this.aiTasks = aiTasks;
        // J-H2: no @Transactional on the listener. A business 409 (ApiException) thrown
        // inside apply() marks the surrounding transaction rollback-only, which used to
        // also discard the failure bookkeeping in the catch block → poison retries ×5 →
        // DLQ + outbox re-publish ×4, misclassifying a 409 as a transport timeout.
        // Success and terminal-failure writes therefore run in separate transactions.
        this.transactions = new TransactionTemplate(transactionManager);
    }

    @KafkaListener(topics = "caijiatai.results", groupId = "java-svc")
    public void onResult(ConsumerRecord<String, byte[]> record) {
        var envelope = CanonicalJson.read(record.value());
        var operationId = text(envelope.get("operation_id"));
        var payloadSha = text(envelope.get("payload_sha256"));
        // Early returns below touch no state, so they stay outside any transaction.
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
            transactions.executeWithoutResult(ignored -> {
                command.fail("Agent 结果 payload_sha256 与命令不一致");
                resultApplication.recordTerminalFailure(
                        command, "结果载荷指纹不一致", AiErrorCategory.VALIDATION, false);
                commands.save(command);
            });
            return;
        }
        if ("failed".equals(envelope.get("status"))) {
            var error = text(envelope.getOrDefault("error", "Agent 操作失败"));
            var category = category(envelope.get("error_category"));
            var retryable = Boolean.TRUE.equals(envelope.get("retryable"));
            transactions.executeWithoutResult(ignored -> {
                command.fail(error);
                resultApplication.recordTerminalFailure(command, error, category, retryable);
                if (retryable) aiTasks.retryAutomatically(command);
                commands.save(command);
            });
            return;
        }
        try {
            transactions.executeWithoutResult(ignored -> {
                resultApplication.apply(command, envelope);
                command.complete(envelope);
                commands.save(command);
            });
        } catch (ApiException error) {
            // Runs in its own new transaction: the success transaction above has already
            // rolled back, so this terminal-failure record survives.
            var message = error.code() + ": " + error.getMessage();
            transactions.executeWithoutResult(ignored -> {
                command.fail(message);
                resultApplication.recordTerminalFailure(command, message);
                commands.save(command);
            });
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
