package com.caijiatai.procurement.agent;

import com.caijiatai.procurement.task.ProcurementTaskRepository;
import com.caijiatai.procurement.ai.AiTaskService;
import com.caijiatai.procurement.ai.AiErrorCategory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.data.domain.PageRequest;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import java.time.Instant;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.CannotAcquireLockException;
import org.springframework.transaction.annotation.Transactional;

@Component
@ConditionalOnProperty(prefix = "app.outbox", name = "enabled", havingValue = "true", matchIfMissing = true)
public class AgentOutboxWorker {
    private static final int MAX_DELIVERY_ATTEMPTS = 4;
    private static final Logger log = LoggerFactory.getLogger(AgentOutboxWorker.class);
    private final AgentCommandRepository commands;
    private final ProcurementTaskRepository tasks;
    private final AgentDispatcher client;
    private final AgentResultApplication resultApplication;
    private final AiTaskService aiTasks;

    public AgentOutboxWorker(
            AgentCommandRepository commands,
            ProcurementTaskRepository tasks,
            AgentDispatcher client,
            AgentResultApplication resultApplication,
            AiTaskService aiTasks) {
        this.commands = commands;
        this.tasks = tasks;
        this.client = client;
        this.resultApplication = resultApplication;
        this.aiTasks = aiTasks;
    }

    @Scheduled(fixedDelayString = "${app.outbox.poll-delay-ms:500}")
    @Transactional
    public void dispatch() {
        for (var command : commands.lockDispatchable(PageRequest.of(0, 10))) {
            command.dispatching();
            if ("analyze".equals(command.getOperationType())) {
                aiTasks.markDispatching(command);
            }
            try {
                if (client.isAsync()) {
                    client.dispatch(command);
                    command.published();
                    continue;
                }
                var response = client.dispatch(command);
                if (response.status() == 200) {
                    if ("failed".equals(response.body().get("status"))) {
                        terminalFailure(command, String.valueOf(
                                response.body().getOrDefault("error", "Agent operation failed")));
                    } else {
                        resultApplication.apply(command, response.body());
                        tasks.flush();
                        command.complete(response.body());
                        tasks.clearRetryable(command.getAggregateId());
                    }
                } else if (response.status() == 202) {
                    if (command.getAttemptCount() >= MAX_DELIVERY_ATTEMPTS) {
                        terminalFailure(command, "Agent 操作超时", AiErrorCategory.TRANSPORT, true);
                    } else {
                        command.accepted(response.body());
                    }
                } else if (response.status() == 409) {
                    terminalFailure(
                            command,
                            "operation_payload_conflict: Agent 拒绝了相同操作 ID 的不同载荷",
                            AiErrorCategory.VALIDATION,
                            false);
                } else {
                    deferOrExhaust(command, "Agent returned HTTP " + response.status());
                }
            } catch (AgentDispatcher.AgentUnavailableException error) {
                deferOrExhaust(command, error.getMessage());
                tasks.markRetryable(command.getAggregateId(), "Agent 暂时不可用，命令将自动重试");
            } catch (CannotAcquireLockException error) {
                deferOrExhaust(command, "数据库锁冲突，命令将自动重试");
            } catch (com.caijiatai.procurement.api.ApiException error) {
                terminalFailure(
                        command,
                        error.code() + ": " + error.getMessage(),
                        classify(error.code()),
                        false);
            }
        }
        for (var command : commands.lockPublishedStale(Instant.now().minusSeconds(120), PageRequest.of(0, 10))) {
            try {
                if (command.getAttemptCount() >= MAX_DELIVERY_ATTEMPTS) {
                    terminalFailure(command, "Agent 结果等待超时", AiErrorCategory.TRANSPORT, true);
                    continue;
                }
                client.dispatch(command); // 结果丢失自愈：以同 operation 重发，Agent 幂等回传
                command.republished();
            } catch (RuntimeException error) {
                if (!command.retry(error.getMessage(), MAX_DELIVERY_ATTEMPTS)) {
                    terminalFailure(command, error.getMessage(), AiErrorCategory.TRANSPORT, true);
                } else {
                    aiTasks.deliveryDeferred(command, error.getMessage());
                    log.warn("重发 published 命令失败：{}", command.getOperationId(), error);
                }
            }
        }
    }

    private void terminalFailure(AgentCommand command, String error) {
        terminalFailure(command, error, AiErrorCategory.BUSINESS, false);
    }

    private void terminalFailure(
            AgentCommand command,
            String error,
            AiErrorCategory category,
            boolean retryable) {
        command.fail(error);
        resultApplication.recordTerminalFailure(command, error, category, retryable);
    }

    private void deferOrExhaust(AgentCommand command, String error) {
        if (command.retry(error, MAX_DELIVERY_ATTEMPTS)) {
            aiTasks.deliveryDeferred(command, error);
            return;
        }
        terminalFailure(command, error, AiErrorCategory.TRANSPORT, true);
    }

    private AiErrorCategory classify(String code) {
        if (code == null) return AiErrorCategory.INTERNAL;
        if (code.startsWith("invalid_") || code.contains("mismatch")) {
            return AiErrorCategory.VALIDATION;
        }
        if (code.contains("provider") || code.contains("model")) {
            return AiErrorCategory.PROVIDER;
        }
        return AiErrorCategory.BUSINESS;
    }
}
