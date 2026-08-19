package com.caijiatai.procurement.invoice;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import com.caijiatai.procurement.agent.AgentCommandRepository;
import com.caijiatai.procurement.agent.IdempotencyRecordRepository;
import com.caijiatai.procurement.artifact.ArtifactStore;
import com.caijiatai.procurement.cache.InsightsCache;
import com.caijiatai.procurement.comparison.ComparisonSnapshotRepository;
import com.caijiatai.procurement.config.AppProperties;
import com.caijiatai.procurement.order.OrderRepository;
import com.caijiatai.procurement.report.AuditEventRepository;
import com.caijiatai.procurement.settlement.SettlementRepository;
import com.caijiatai.procurement.task.ProcurementTaskRepository;
import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class InvoiceSettlementGateTest {
    private final InvoiceRepository invoices = mock(InvoiceRepository.class);
    private final AgentCommandRepository commands = mock(AgentCommandRepository.class);
    private final InvoiceService service;

    InvoiceSettlementGateTest() {
        var properties = mock(AppProperties.class);
        when(properties.localOperator()).thenReturn("采购员");
        service = new InvoiceService(
                invoices,
                mock(OrderRepository.class),
                mock(ProcurementTaskRepository.class),
                mock(ComparisonSnapshotRepository.class),
                commands,
                mock(IdempotencyRecordRepository.class),
                mock(ArtifactStore.class),
                mock(AuditEventRepository.class),
                mock(SettlementRepository.class),
                mock(InsightsCache.class),
                new InvoiceStateMachineConfig().invoiceStateMachine(),
                properties);
    }

    @BeforeEach
    void emptyByDefault() {
        when(invoices.findByOrderIdOrderByCreatedAtAsc("order-1")).thenReturn(List.of());
    }

    @Test
    void noInvoiceDoesNotSatisfySettlementGate() {
        assertThat(service.isReconciledForSettlement("order-1")).isFalse();
    }

    @Test
    void registeredMatchedAndDiffHoldInvoicesDoNotSatisfySettlementGate() {
        for (var invoice : List.of(registered(), matched(), diffHold())) {
            when(invoices.findByOrderIdOrderByCreatedAtAsc("order-1")).thenReturn(List.of(invoice));
            assertThat(service.isReconciledForSettlement("order-1"))
                    .as(invoice.getStatus())
                    .isFalse();
        }
    }

    @Test
    void onlyVoidedInvoicesDoNotSatisfySettlementGate() {
        var invoice = registered();
        invoice.voidInvoice("作废");
        when(invoices.findByOrderIdOrderByCreatedAtAsc("order-1")).thenReturn(List.of(invoice));

        assertThat(service.isReconciledForSettlement("order-1")).isFalse();
    }

    @Test
    void everyEffectiveInvoiceMustBeReconciled() {
        var reconciled = matched();
        reconciled.reconcile();
        when(invoices.findByOrderIdOrderByCreatedAtAsc("order-1"))
                .thenReturn(List.of(reconciled, matched()));
        assertThat(service.isReconciledForSettlement("order-1")).isFalse();

        var second = matched();
        second.reconcile();
        when(invoices.findByOrderIdOrderByCreatedAtAsc("order-1"))
                .thenReturn(List.of(reconciled, second));
        assertThat(service.isReconciledForSettlement("order-1")).isTrue();
    }

    @Test
    void voidedInvoiceDoesNotBlockAReconciledEffectiveInvoice() {
        var reconciled = matched();
        reconciled.reconcile();
        var voided = registered();
        voided.voidInvoice("重复发票");
        when(invoices.findByOrderIdOrderByCreatedAtAsc("order-1"))
                .thenReturn(List.of(reconciled, voided));

        assertThat(service.isReconciledForSettlement("order-1")).isTrue();
    }

    @Test
    void pendingInvoiceParseKeepsPaymentBlockedAfterEarlierInvoicesWereReconciled() {
        var reconciled = matched();
        reconciled.reconcile();
        when(invoices.findByOrderIdOrderByCreatedAtAsc("order-1"))
                .thenReturn(List.of(reconciled));
        when(commands.existsByAggregateIdAndOperationTypeAndStatusIn(
                "order-1",
                "parse_invoice",
                List.of("pending", "dispatching", "accepted", "published")))
                .thenReturn(true);

        assertThat(service.isReconciledForSettlement("order-1")).isFalse();
    }

    private Invoice registered() {
        return Invoice.register(
                "order-1", "INV-001", null, LocalDate.parse("2026-08-20"),
                BigDecimal.TEN, "piece", BigDecimal.ONE, BigDecimal.TEN,
                BigDecimal.ZERO, BigDecimal.TEN, BigDecimal.ZERO, "供应商",
                "artifact-1", "a".repeat(64), "test");
    }

    private Invoice matched() {
        var invoice = registered();
        invoice.applyMatchResult(true, java.util.Map.of("matched", true), null);
        return invoice;
    }

    private Invoice diffHold() {
        var invoice = registered();
        invoice.applyMatchResult(false, java.util.Map.of("matched", false), null);
        return invoice;
    }
}
