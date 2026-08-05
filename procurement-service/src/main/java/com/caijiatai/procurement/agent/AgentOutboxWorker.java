package com.caijiatai.procurement.agent;

import com.caijiatai.procurement.task.ProcurementTaskRepository;
import java.time.Instant;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.data.domain.PageRequest;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

@Component
@ConditionalOnProperty(prefix = "app.outbox", name = "enabled", havingValue = "true", matchIfMissing = true)
public class AgentOutboxWorker {
    private final AgentCommandRepository commands;
    private final ProcurementTaskRepository tasks;
    private final AgentClient client;
    private final AgentResultApplication resultApplication;

    public AgentOutboxWorker(
            AgentCommandRepository commands,
            ProcurementTaskRepository tasks,
            AgentClient client,
            AgentResultApplication resultApplication) {
        this.commands = commands;
        this.tasks = tasks;
        this.client = client;
        this.resultApplication = resultApplication;
    }

    @Scheduled(fixedDelayString = "${app.outbox.poll-delay-ms:500}")
    @Transactional
    public void dispatch() {
        for (var command : commands.lockDispatchable(Instant.now(), PageRequest.of(0, 10))) {
            command.dispatching();
            try {
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
            } catch (com.caijiatai.procurement.api.ApiException error) {
                terminalFailure(command, error.code() + ": " + error.getMessage());
            }
        }
    }

    private void terminalFailure(AgentCommand command, String error) {
        command.fail(error);
        resultApplication.recordTerminalFailure(command, error);
    }
}
