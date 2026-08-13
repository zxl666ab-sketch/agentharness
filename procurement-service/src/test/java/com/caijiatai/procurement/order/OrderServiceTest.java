package com.caijiatai.procurement.order;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.caijiatai.procurement.api.ApiException;
import com.caijiatai.procurement.artifact.BusinessArtifactRepository;
import com.caijiatai.procurement.comparison.ComparisonSnapshotRepository;
import com.caijiatai.procurement.quote.ProcurementQuote;
import com.caijiatai.procurement.quote.ProcurementQuoteRepository;
import com.caijiatai.procurement.report.AuditEvent;
import com.caijiatai.procurement.report.AuditEventRepository;
import com.caijiatai.procurement.settlement.PurchaseSettlement;
import com.caijiatai.procurement.settlement.SettlementRepository;
import com.caijiatai.procurement.task.ProcurementTask;
import com.caijiatai.procurement.task.ProcurementTaskRepository;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.dao.OptimisticLockingFailureException;

class OrderServiceTest {
    private final OrderRepository orders = mock(OrderRepository.class);
    private final SettlementRepository settlements = mock(SettlementRepository.class);
    private final ProcurementTaskRepository tasks = mock(ProcurementTaskRepository.class);
    private final ProcurementQuoteRepository quotes = mock(ProcurementQuoteRepository.class);
    private final ComparisonSnapshotRepository snapshots = mock(ComparisonSnapshotRepository.class);
    private final BusinessArtifactRepository artifacts = mock(BusinessArtifactRepository.class);
    private final AuditEventRepository audit = mock(AuditEventRepository.class);
    private final OrderService service = new OrderService(
            orders, settlements, tasks, quotes, snapshots, artifacts, audit,
            new OrderStateMachineConfig().orderStateMachine());

    private ProcurementTask task;
    private ProcurementQuote quote;

    @BeforeEach
    void setUp() {
        task = ProcurementTask.structured(1, "快递袋采购", "ecommerce_packaging", "快递袋",
                new BigDecimal("15000"), "piece", Map.of(), Map.of());
        task.finalizeDecision("quote-1", false);
        quote = quote("quote-1", "华东优包");
        when(tasks.findById(task.getId())).thenReturn(Optional.of(task));
        when(quotes.findByIdAndTaskId("quote-1", task.getId())).thenReturn(Optional.of(quote));
        when(artifacts.findByTaskIdInOrderByCreatedAtAsc(any())).thenReturn(List.of());
        when(settlements.findByOrderId(any())).thenReturn(Optional.empty());
        when(orders.saveAndFlush(any())).thenAnswer(invocation -> invocation.getArgument(0));
        when(settlements.saveAndFlush(any())).thenAnswer(invocation -> invocation.getArgument(0));
    }

    private ProcurementQuote quote(String id, String supplier) {
        var extracted = new LinkedHashMap<String, Object>();
        extracted.put("fields", Map.of());
        extracted.put("review_fields", List.of());
        return ProcurementQuote.create(
                task.getId(), "artifact-" + id, supplier, id + ".xlsx", "xlsx",
                "sha256-" + id, extracted, "ready", "v1", BigDecimal.ZERO);
    }

    private com.caijiatai.procurement.comparison.ComparisonSnapshot snapshotWithCost(String quoteId, String landed) {
        var cost = new LinkedHashMap<String, Object>();
        cost.put("landed_total_base", landed);
        var row = new LinkedHashMap<String, Object>();
        row.put("quote_id", quoteId);
        row.put("cost", cost);
        var result = new LinkedHashMap<String, Object>();
        result.put("quotes", List.of(row));
        return com.caijiatai.procurement.comparison.ComparisonSnapshot.create(
                task.getId(), "run1", 1, 1, "sha1", result, "artifact-snapshot");
    }

    private PurchaseOrder pendingOrder() {
        return PurchaseOrder.derive(task.getId(), "PO-" + task.getReference(),
                "华东优包", "快递袋", new BigDecimal("15000"), "piece", new BigDecimal("7500.00"));
    }

    @Test
    void derivesOrderFromApprovedTaskWithLandedCostFromSnapshot() {
        when(orders.findByTaskId(task.getId())).thenReturn(Optional.empty());
        when(orders.findById(any())).thenAnswer(invocation -> Optional.empty());
        when(snapshots.findByIdAndTaskId("snap1", task.getId()))
                .thenReturn(Optional.of(snapshotWithCost(quote.getId(), "7500.00")));
        task.useSnapshot("snap1");
        task.finalizeDecision("quote-1", false);

        var order = service.ensureOrderForApprovedTask(task);

        assertThat(order).isNotNull();
        assertThat(order.getOrderNo()).isEqualTo("PO-" + task.getReference());
        assertThat(order.getSupplierName()).isEqualTo("华东优包");
        assertThat(order.getLandedTotal()).isEqualByComparingTo("7500.00");
        assertThat(order.getStatus()).isEqualTo("PENDING_SHIPMENT");
        verify(orders).saveAndFlush(order);
        verify(audit).save(any(AuditEvent.class));
    }

    @Test
    void derivationIsIdempotentWhenOrderAlreadyExists() {
        var existing = pendingOrder();
        when(orders.findByTaskId(task.getId())).thenReturn(Optional.of(existing));

        var order = service.ensureOrderForApprovedTask(task);

        assertThat(order).isSameAs(existing);
        verify(orders, never()).saveAndFlush(any());
    }

    @Test
    void derivationSkipsNonApprovedTasks() {
        task = ProcurementTask.structured(1, "未批准任务", "ecommerce_packaging", "快递袋",
                BigDecimal.ONE, "piece", Map.of(), Map.of());

        assertThat(service.ensureOrderForApprovedTask(task)).isNull();
        verify(orders, never()).saveAndFlush(any());
    }

    @Test
    void derivationSwallowsDuplicateKeyRaceAndReturnsExistingOrder() {
        var existing = pendingOrder();
        when(orders.findByTaskId(task.getId())).thenReturn(Optional.empty(), Optional.of(existing));
        when(orders.saveAndFlush(any()))
                .thenThrow(new DataIntegrityViolationException("Duplicate entry for uq_purchase_order_task"));

        var order = service.ensureOrderForApprovedTask(task);

        assertThat(order).isSameAs(existing);
    }

    @Test
    void shipMovesPendingOrderToShipped() {
        var order = pendingOrder();
        when(orders.lockById("o1")).thenReturn(Optional.of(order));
        when(orders.findById("o1")).thenReturn(Optional.of(order));

        var result = service.transition("o1", "ship", null, null, null, "采购员");

        assertThat(result.get("status")).isEqualTo("SHIPPED");
        verify(audit).save(any(AuditEvent.class));
    }

    @Test
    void illegalTransitionFromShippedShipIsRejected() {
        var order = pendingOrder();
        order.ship();
        when(orders.lockById("o1")).thenReturn(Optional.of(order));

        assertThatThrownBy(() -> service.transition("o1", "ship", null, null, null, "采购员"))
                .isInstanceOf(ApiException.class)
                .satisfies(error -> assertThat(((ApiException) error).code()).isEqualTo("invalid_order_transition"));
        verify(audit, never()).save(any(AuditEvent.class));
    }

    @Test
    void receiveRejectsQuantityExceedingOrder() {
        var order = pendingOrder();
        order.ship();
        when(orders.lockById("o1")).thenReturn(Optional.of(order));

        assertThatThrownBy(() -> service.transition(
                "o1", "receive", new BigDecimal("20000"), Instant.now(), null, "采购员"))
                .isInstanceOf(ApiException.class)
                .satisfies(error -> assertThat(((ApiException) error).code()).isEqualTo("quantity_exceeded"));
    }

    @Test
    void receiveWithoutLandedTotalIsRejectedBeforeSettlementDerivation() {
        var order = PurchaseOrder.derive(task.getId(), "PO-" + task.getReference(),
                "华东优包", "快递袋", new BigDecimal("15000"), "piece", null);
        order.ship();
        when(orders.lockById("o1")).thenReturn(Optional.of(order));

        assertThatThrownBy(() -> service.transition(
                "o1", "receive", new BigDecimal("15000"), Instant.now(), null, "采购员"))
                .isInstanceOf(ApiException.class)
                .satisfies(error -> assertThat(((ApiException) error).code()).isEqualTo("settlement_requires_cost"));
        verify(settlements, never()).saveAndFlush(any());
    }

    @Test
    void receiveDerivesSettlementAutomatically() {
        var order = pendingOrder();
        order.ship();
        when(orders.lockById("o1")).thenReturn(Optional.of(order));
        when(orders.findById("o1")).thenReturn(Optional.of(order));

        var result = service.transition(
                "o1", "receive", new BigDecimal("15000"), Instant.parse("2026-08-14T00:00:00Z"), null, "采购员");

        assertThat(result.get("status")).isEqualTo("RECEIVED");
        assertThat(result.get("received_quantity")).isEqualTo("15000");
        verify(settlements).saveAndFlush(any(PurchaseSettlement.class));
        // order_transitioned + settlement_created 两条审计
        verify(audit, times(2)).save(any(AuditEvent.class));
    }

    @Test
    void closeFromPendingCancelsWithoutSettlement() {
        var order = pendingOrder();
        when(orders.lockById("o1")).thenReturn(Optional.of(order));
        when(orders.findById("o1")).thenReturn(Optional.of(order));

        var result = service.transition("o1", "close", null, null, "需求取消", "采购员");

        assertThat(result.get("status")).isEqualTo("CLOSED");
        verify(settlements, never()).saveAndFlush(any());
    }

    @Test
    void versionConflictMapsToOrderConcurrentModification() {
        var order = pendingOrder();
        when(orders.lockById("o1")).thenReturn(Optional.of(order));
        when(orders.saveAndFlush(any())).thenThrow(new OptimisticLockingFailureException("stale"));

        assertThatThrownBy(() -> service.transition("o1", "ship", null, null, null, "采购员"))
                .isInstanceOf(ApiException.class)
                .satisfies(error -> {
                    var api = (ApiException) error;
                    assertThat(api.code()).isEqualTo("order_concurrent_modification");
                    assertThat(api.status().value()).isEqualTo(409);
                });
    }

    @Test
    void reconcileApprovedTasksDerivesForEveryApprovedTask() {
        var other = ProcurementTask.structured(1, "气泡膜采购", "ecommerce_packaging", "气泡膜",
                new BigDecimal("300"), "piece", Map.of(), Map.of());
        other.finalizeDecision("quote-2", false);
        when(tasks.findByStatusOrderByUpdatedAtDesc("approved")).thenReturn(List.of(task, other));
        when(orders.findByTaskId(task.getId())).thenReturn(Optional.of(pendingOrder()));
        when(orders.findByTaskId(other.getId())).thenReturn(Optional.empty());
        var otherQuote = ProcurementQuote.create(
                other.getId(), "artifact-quote-2", "华南气泡包装", "quote-2.xlsx", "xlsx",
                "sha256-quote-2", Map.of("fields", Map.of(), "review_fields", List.of()),
                "ready", "v1", BigDecimal.ZERO);
        when(quotes.findByIdAndTaskId("quote-2", other.getId())).thenReturn(Optional.of(otherQuote));

        service.reconcileApprovedTasks();

        verify(orders, times(1)).saveAndFlush(any());
    }
}
