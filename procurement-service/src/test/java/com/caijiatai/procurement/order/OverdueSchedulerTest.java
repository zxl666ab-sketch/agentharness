package com.caijiatai.procurement.order;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.caijiatai.procurement.report.AuditEvent;
import com.caijiatai.procurement.report.AuditEventRepository;
import com.caijiatai.procurement.settlement.PurchaseSettlement;
import com.caijiatai.procurement.settlement.SettlementRepository;
import com.caijiatai.procurement.settlement.SettlementStatus;
import java.math.BigDecimal;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Optional;
import org.junit.jupiter.api.Test;

class OverdueSchedulerTest {
    private final OrderRepository orders = mock(OrderRepository.class);
    private final SettlementRepository settlements = mock(SettlementRepository.class);
    private final AuditEventRepository audit = mock(AuditEventRepository.class);

    private Clock clockAt(Instant instant) {
        return Clock.fixed(instant, ZoneOffset.UTC);
    }

    @Test
    void writesShipmentOverdueForOrdersOlderThanSevenDays() {
        var now = Instant.parse("2026-08-14T00:00:00Z");
        var staleOrder = PurchaseOrder.derive("t1", "PO-RFQ-STALE", "华东优包", "快递袋",
                BigDecimal.TEN, "piece", BigDecimal.ONE);
        var freshOrder = PurchaseOrder.derive("t2", "PO-RFQ-FRESH", "华东优包", "快递袋",
                BigDecimal.TEN, "piece", BigDecimal.ONE);
        when(orders.findByStatusAndUpdatedAtBefore(OrderStatus.PENDING_SHIPMENT.wireValue(),
                now.minusSeconds(7L * 24 * 3600)))
                .thenReturn(List.of(staleOrder, freshOrder));
        when(audit.existsByTaskIdAndEventType(any(), any())).thenReturn(false);
        var scheduler = new OverdueScheduler(orders, settlements, audit, clockAt(now));

        scheduler.scanOrderShipmentOverdue();

        verify(audit, times(2)).save(any(AuditEvent.class));
    }

    @Test
    void shipmentOverdueIsIdempotentPerOrder() {
        var now = Instant.parse("2026-08-14T00:00:00Z");
        var staleOrder = PurchaseOrder.derive("t1", "PO-RFQ-STALE", "华东优包", "快递袋",
                BigDecimal.TEN, "piece", BigDecimal.ONE);
        when(orders.findByStatusAndUpdatedAtBefore(any(), any())).thenReturn(List.of(staleOrder));
        when(audit.existsByTaskIdAndEventType("t1", "order_shipment_overdue")).thenReturn(true);
        var scheduler = new OverdueScheduler(orders, settlements, audit, clockAt(now));

        scheduler.scanOrderShipmentOverdue();

        verify(audit, never()).save(any(AuditEvent.class));
    }

    @Test
    void skipsOrdersNotYetOverdue() {
        var now = Instant.parse("2026-08-14T00:00:00Z");
        when(orders.findByStatusAndUpdatedAtBefore(any(), any())).thenReturn(List.of());
        var scheduler = new OverdueScheduler(orders, settlements, audit, clockAt(now));

        scheduler.scanOrderShipmentOverdue();

        verify(audit, never()).save(any(AuditEvent.class));
    }

    @Test
    void writesPaymentOverdueForSettledSettlementsOlderThanSevenDays() {
        var now = Instant.parse("2026-08-14T00:00:00Z");
        var settled = PurchaseSettlement.derive("order-1", "ST-20260801-ABC", "华东优包",
                new BigDecimal("7500.00"));
        settled.settle(null);
        var order = PurchaseOrder.derive("t1", "PO-RFQ-1", "华东优包", "快递袋",
                BigDecimal.TEN, "piece", BigDecimal.ONE);
        when(settlements.findByStatusAndUpdatedAtBefore(SettlementStatus.SETTLED.wireValue(),
                now.minusSeconds(7L * 24 * 3600)))
                .thenReturn(List.of(settled));
        when(orders.findById("order-1")).thenReturn(Optional.of(order));
        when(audit.existsByTaskIdAndEventType(any(), any())).thenReturn(false);
        var scheduler = new OverdueScheduler(orders, settlements, audit, clockAt(now));

        scheduler.scanSettlementPaymentOverdue();

        verify(audit).save(any(AuditEvent.class));
    }

    @Test
    void paymentOverdueSkipsAlreadyFlagged() {
        var now = Instant.parse("2026-08-14T00:00:00Z");
        var settled = PurchaseSettlement.derive("order-1", "ST-20260801-ABC", "华东优包",
                new BigDecimal("7500.00"));
        settled.settle(null);
        var order = PurchaseOrder.derive("t1", "PO-RFQ-1", "华东优包", "快递袋",
                BigDecimal.TEN, "piece", BigDecimal.ONE);
        when(settlements.findByStatusAndUpdatedAtBefore(any(), any())).thenReturn(List.of(settled));
        when(orders.findById("order-1")).thenReturn(Optional.of(order));
        when(audit.existsByTaskIdAndEventType("t1", "settlement_payment_overdue")).thenReturn(true);
        var scheduler = new OverdueScheduler(orders, settlements, audit, clockAt(now));

        scheduler.scanSettlementPaymentOverdue();

        verify(audit, never()).save(any(AuditEvent.class));
    }

    @Test
    void paymentOverdueSkipsPaidSettlements() {
        var now = Instant.parse("2026-08-14T00:00:00Z");
        when(settlements.findByStatusAndUpdatedAtBefore(eq(SettlementStatus.SETTLED.wireValue()), any()))
                .thenReturn(List.of());
        var scheduler = new OverdueScheduler(orders, settlements, audit, clockAt(now));

        scheduler.scanSettlementPaymentOverdue();

        verify(audit, never()).save(any(AuditEvent.class));
    }

    @Test
    void clockDrivesTheDeadline() {
        var scheduler = new OverdueScheduler(orders, settlements, audit,
                clockAt(Instant.parse("2026-08-14T00:00:00Z")));
        scheduler.scanOrderShipmentOverdue();
        verify(orders).findByStatusAndUpdatedAtBefore(
                OrderStatus.PENDING_SHIPMENT.wireValue(), Instant.parse("2026-08-07T00:00:00Z"));
    }
}
