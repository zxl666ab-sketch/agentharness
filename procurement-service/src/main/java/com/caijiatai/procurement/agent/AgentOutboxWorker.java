package com.caijiatai.procurement.agent;

import com.caijiatai.procurement.task.ProcurementTaskRepository;
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
    private static final Logger log = LoggerFactory.getLogger(AgentOutboxWorker.class);
    private final AgentCommandRepository commands;
    private final ProcurementTaskRepository tasks;
    private final AgentDispatcher client;
    private final AgentResultApplication resultApplication;

    public AgentOutboxWorker(
            AgentCommandRepository commands,
            ProcurementTaskRepository tasks,
            AgentDispatcher client,
            AgentResultApplication resultApplication) {
        this.commands = commands;
        this.tasks = tasks;
        this.client = client;
        this.resultApplication = resultApplication;
    }

    @Scheduled(fixedDelayString = "${app.outbox.poll-delay-ms:500}")
    @Transactional
    public void dispatch() {
        for (var command : commands.lockDispatchable(PageRequest.of(0, 10))) {
            command.dispatching();
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
                    command.accepted(response.body());
                } else if (response.status() == 409) {
                    command.fail("Agent rejected operation id with a different payload");
                } else {
                    command.retry("Agent returned HTTP " + response.status());
                }
            } catch (AgentClient.AgentUnavailableException error) {
                command.retry(error.getMessage());
                tasks.markRetryable(command.getAggregateId(), "Agent 暂时不可用，命令将自动重试");
            } catch (CannotAcquireLockException error) {
                command.retry("数据库锁冲突，命令将自动重试");
            } catch (com.caijiatai.procurement.api.ApiException error) {
                terminalFailure(command, error.code() + ": " + error.getMessage());
            }
        }
        for (var command : commands.lockPublishedStale(Instant.now().minusSeconds(120), PageRequest.of(0, 10))) {
            try {
                client.dispatch(command); // 结果丢失自愈：重新发布，等待结果幂等回传
            } catch (RuntimeException error) {
                log.warn("重发 published 命令失败：{}", command.getOperationId(), error);
            }
        }
    }

    private void terminalFailure(AgentCommand command, String error) {
        command.fail(error);
        resultApplication.recordTerminalFailure(command, error);
    }
}
