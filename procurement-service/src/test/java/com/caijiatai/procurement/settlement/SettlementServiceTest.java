package com.caijiatai.procurement.settlement;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.caijiatai.procurement.api.ApiException;
import com.caijiatai.procurement.order.OrderRepository;
import com.caijiatai.procurement.order.OrderStateMachineConfig;
import com.caijiatai.procurement.report.AuditEvent;
import com.caijiatai.procurement.report.AuditEventRepository;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.Map;
import java.util.Optional;
import org.junit.jupiter.api.Test;
import org.springframework.dao.OptimisticLockingFailureException;

class SettlementServiceTest {
    private final SettlementRepository settlements = mock(SettlementRepository.class);
    private final OrderRepository orders = mock(OrderRepository.class);
    private final AuditEventRepository audit = mock(AuditEventRepository.class);
    private final SettlementService service = new SettlementService(
            settlements, orders, audit, new OrderStateMachineConfig().settlementStateMachine());

    private PurchaseSettlement unsettled() {
        return PurchaseSettlement.derive("order-1", "ST-20260814-ABC123", "华东优包",
                new BigDecimal("7500.00"));
    }

    @Test
    void settleMovesUnsettledToSettledWithAudit() {
        var settlement = unsettled();
        when(settlements.lockById("s1")).thenReturn(Optional.of(settlement));
        when(settlements.saveAndFlush(any())).thenAnswer(invocation -> invocation.getArgument(0));

        var result = service.transition("s1", "settle", null, null, "采购员");

        assertThat(result.get("status")).isEqualTo("SETTLED");
        verify(audit).save(any(AuditEvent.class));
    }

    @Test
    void payRequiresPaidAt() {
        var settlement = unsettled();
        settlement.settle(null);
        when(settlements.lockById("s1")).thenReturn(Optional.of(settlement));

        assertThatThrownBy(() -> service.transition("s1", "pay", null, null, "采购员"))
                .isInstanceOf(ApiException.class)
                .satisfies(error -> assertThat(((ApiException) error).code()).isEqualTo("paid_at_required"));
    }

    @Test
    void payMovesSettledToPaidWithPaidAt() {
        var settlement = unsettled();
        settlement.settle(null);
        when(settlements.lockById("s1")).thenReturn(Optional.of(settlement));
        when(settlements.saveAndFlush(any())).thenAnswer(invocation -> invocation.getArgument(0));

        var result = service.transition(
                "s1", "pay", Instant.parse("2026-08-14T00:00:00Z"), "银行转账", "采购员");

        assertThat(result.get("status")).isEqualTo("PAID");
        assertThat(result.get("paid_at")).isEqualTo("2026-08-14T00:00:00Z");
    }

    @Test
    void illegalTransitionFromPaidIsRejected() {
        var settlement = unsettled();
        settlement.settle(null);
        settlement.pay(Instant.now(), null);
        when(settlements.lockById("s1")).thenReturn(Optional.of(settlement));

        assertThatThrownBy(() -> service.transition("s1", "pay", Instant.now(), null, "采购员"))
                .isInstanceOf(ApiException.class)
                .satisfies(error -> assertThat(((ApiException) error).code()).isEqualTo("invalid_settlement_transition"));
        verify(audit, never()).save(any(AuditEvent.class));
    }

    @Test
    void versionConflictMapsToSettlementConcurrentModification() {
        var settlement = unsettled();
        when(settlements.lockById("s1")).thenReturn(Optional.of(settlement));
        when(settlements.saveAndFlush(any())).thenThrow(new OptimisticLockingFailureException("stale"));

        assertThatThrownBy(() -> service.transition("s1", "settle", null, null, "采购员"))
                .isInstanceOf(ApiException.class)
                .satisfies(error -> {
                    var api = (ApiException) error;
                    assertThat(api.code()).isEqualTo("settlement_concurrent_modification");
                    assertThat(api.status().value()).isEqualTo(409);
                });
    }

    @Test
    void unknownActionIsRejected() {
        var settlement = unsettled();
        when(settlements.lockById("s1")).thenReturn(Optional.of(settlement));

        assertThatThrownBy(() -> service.transition("s1", "explode", null, null, "采购员"))
                .isInstanceOf(ApiException.class)
                .satisfies(error -> assertThat(((ApiException) error).code()).isEqualTo("invalid_settlement_action"));
    }
}
