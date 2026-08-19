package com.caijiatai.procurement.contract;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.caijiatai.procurement.agent.AgentCommand;
import com.caijiatai.procurement.agent.AgentCommandRepository;
import com.caijiatai.procurement.agent.CanonicalJson;
import com.caijiatai.procurement.agent.IdempotencyRecord;
import com.caijiatai.procurement.agent.IdempotencyRecordRepository;
import com.caijiatai.procurement.cache.InsightsCache;
import com.caijiatai.procurement.config.AppProperties;
import com.caijiatai.procurement.order.OrderRepository;
import com.caijiatai.procurement.order.OrderService;
import com.caijiatai.procurement.report.AuditEventRepository;
import com.caijiatai.procurement.task.ProcurementTaskRepository;
import java.util.Map;
import java.util.Optional;
import org.junit.jupiter.api.Test;

class ContractServiceIdempotencyTest {
    private final ContractRepository contracts = mock(ContractRepository.class);
    private final ProcurementTaskRepository tasks = mock(ProcurementTaskRepository.class);
    private final OrderRepository orders = mock(OrderRepository.class);
    private final OrderService orderService = mock(OrderService.class);
    private final AgentCommandRepository commands = mock(AgentCommandRepository.class);
    private final IdempotencyRecordRepository idempotency = mock(IdempotencyRecordRepository.class);
    private final AuditEventRepository audit = mock(AuditEventRepository.class);
    private final InsightsCache insightsCache = mock(InsightsCache.class);
    private final AppProperties properties = mock(AppProperties.class);
    private final ContractService service;

    ContractServiceIdempotencyTest() {
        when(properties.localOperator()).thenReturn("采购员");
        service = new ContractService(
                contracts, tasks, orders, orderService, commands, idempotency, audit, insightsCache,
                new ContractStateMachineConfig().contractStateMachine(), properties);
    }

    @Test
    void replaysTheOriginalOperationBeforeCheckingWhetherAContractNowExists() {
        var taskId = "a".repeat(32);
        var requestSha = CanonicalJson.sha256(Map.of("task_id", taskId));
        var command = AgentCommand.accept("operation-1", "draft_contract", taskId, 1, 0, Map.of());
        when(idempotency.findById(new IdempotencyRecord.Key("contract_draft", "retry-key")))
                .thenReturn(Optional.of(IdempotencyRecord.reserve(
                        "contract_draft", "retry-key", requestSha, command.getOperationId())));
        when(commands.findById(command.getOperationId())).thenReturn(Optional.of(command));

        var accepted = service.createDraft(taskId, "retry-key");

        assertThat(accepted.operationId()).isEqualTo("operation-1");
        verify(tasks, never()).lockById(taskId);
        verify(contracts, never()).findByTaskId(taskId);
    }
}
