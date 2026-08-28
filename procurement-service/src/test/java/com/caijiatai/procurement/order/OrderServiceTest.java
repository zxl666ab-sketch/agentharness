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
import com.caijiatai.procurement.agent.IdempotencyRecordRepository;
import com.caijiatai.procurement.agent.IdempotencyRecord;
import com.caijiatai.procurement.artifact.BusinessArtifactRepository;
import com.caijiatai.procurement.comparison.ComparisonSnapshotRepository;
import com.caijiatai.procurement.invoice.InvoiceRepository;
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
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.dao.OptimisticLockingFailureException;
import org.springframework.data.domain.PageImpl;
import org.springframework.data.domain.Pageable;

class OrderServiceTest {
    private final OrderRepository orders = mock(OrderRepository.class);
    private final SettlementRepository settlements = mock(SettlementRepository.class);
    private final ProcurementTaskRepository tasks = mock(ProcurementTaskRepository.class);
    private final ProcurementQuoteRepository quotes = mock(ProcurementQuoteRepository.class);
    private final ComparisonSnapshotRepository snapshots = mock(ComparisonSnapshotRepository.class);
    private final BusinessArtifactRepository artifacts = mock(BusinessArtifactRepository.class);
    private final AuditEventRepository audit = mock(AuditEventRepository.class);
    private final IdempotencyRecordRepository idempotency = mock(IdempotencyRecordRepository.class);
    private final InvoiceRepository invoices = mock(InvoiceRepository.class);
    private final OrderService service = new OrderService(
            orders, settlements, tasks, quotes, snapshots, artifacts, audit, idempotency, invoices,
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
        when(idempotency.findById(any())).thenReturn(Optional.empty());
        when(invoices.findByOrderIdOrderByCreatedAtAsc(any())).thenReturn(List.of());
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
    void partialReceiptAccumulatesBatchesAndDerivesSettlementOnlyWhenComplete() {
        var order = PurchaseOrder.derive(task.getId(), "PO-" + task.getReference(),
                "华东优包", "快递袋", new BigDecimal("100"), "piece", new BigDecimal("50.00"));
        order.ship();
        when(orders.lockById("o1")).thenReturn(Optional.of(order));
        when(orders.findById("o1")).thenReturn(Optional.of(order));

        var partial = service.transition(
                "o1", "receive", new BigDecimal("30"),
                Instant.parse("2026-08-14T00:00:00Z"), "第一批", "采购员");

        assertThat(partial.get("status")).isEqualTo("PARTIALLY_RECEIVED");
        assertThat(partial.get("received_quantity")).isEqualTo("30");
        verify(settlements, never()).saveAndFlush(any());

        var completed = service.transition(
                "o1", "receive", new BigDecimal("70"),
                Instant.parse("2026-08-15T00:00:00Z"), "第二批", "采购员");

        assertThat(completed.get("status")).isEqualTo("RECEIVED");
        assertThat(completed.get("received_quantity")).isEqualTo("100");
        verify(settlements).saveAndFlush(any(PurchaseSettlement.class));
        verify(audit, times(3)).save(any(AuditEvent.class));
    }

    @Test
    void sameReceiptPayloadReplayDoesNotAccumulateOrAuditTwice() {
        var order = PurchaseOrder.derive(task.getId(), "PO-" + task.getReference(),
                "华东优包", "快递袋", new BigDecimal("100"), "piece", new BigDecimal("50.00"));
        order.ship();
        when(orders.lockById("o1")).thenReturn(Optional.of(order));
        when(orders.findById("o1")).thenReturn(Optional.of(order));
        var stored = new AtomicReference<IdempotencyRecord>();
        var idempotencyId = new IdempotencyRecord.Key("order_transition:o1", "receipt-key-0001");
        when(idempotency.findById(idempotencyId)).thenAnswer(ignored -> Optional.ofNullable(stored.get()));
        when(idempotency.save(any())).thenAnswer(invocation -> {
            stored.set(invocation.getArgument(0));
            return invocation.getArgument(0);
        });

        var first = service.transition(
                "o1", "receive", new BigDecimal("30.0"),
                Instant.parse("2026-08-20T00:00:00Z"), "第一批", "采购员", "receipt-key-0001");
        var replay = service.transition(
                "o1", "receive", new BigDecimal("30.00"),
                Instant.parse("2026-08-20T00:00:00Z"), "第一批", "采购员", "receipt-key-0001");

        assertThat(first).isEqualTo(replay);
        assertThat(order.getReceivedQuantity()).isEqualByComparingTo("30");
        verify(audit, times(1)).save(any(AuditEvent.class));
        verify(idempotency, times(1)).save(any(IdempotencyRecord.class));
    }

    @Test
    void sameReceiptKeyWithDifferentPayloadIsRejected() {
        var order = PurchaseOrder.derive(task.getId(), "PO-" + task.getReference(),
                "华东优包", "快递袋", new BigDecimal("100"), "piece", new BigDecimal("50.00"));
        order.ship();
        when(orders.lockById("o1")).thenReturn(Optional.of(order));
        when(orders.findById("o1")).thenReturn(Optional.of(order));
        var stored = new AtomicReference<IdempotencyRecord>();
        var idempotencyId = new IdempotencyRecord.Key("order_transition:o1", "receipt-key-0002");
        when(idempotency.findById(idempotencyId)).thenAnswer(ignored -> Optional.ofNullable(stored.get()));
        when(idempotency.save(any())).thenAnswer(invocation -> {
            stored.set(invocation.getArgument(0));
            return invocation.getArgument(0);
        });
        service.transition(
                "o1", "receive", new BigDecimal("30"),
                Instant.parse("2026-08-20T00:00:00Z"), null, "采购员", "receipt-key-0002");

        assertThatThrownBy(() -> service.transition(
                "o1", "receive", new BigDecimal("40"),
                Instant.parse("2026-08-20T00:00:00Z"), null, "采购员", "receipt-key-0002"))
                .isInstanceOf(ApiException.class)
                .satisfies(error -> assertThat(((ApiException) error).code())
                        .isEqualTo("idempotency_payload_conflict"));
        assertThat(order.getReceivedQuantity()).isEqualByComparingTo("30");
        verify(audit, times(1)).save(any(AuditEvent.class));
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
    void receivedOrderCannotCloseBeforePayment() {
        var order = pendingOrder();
        order.ship();
        order.receive(order.getQuantity(), Instant.parse("2026-08-20T00:00:00Z"), null);
        when(orders.lockById("o1")).thenReturn(Optional.of(order));

        assertThatThrownBy(() -> service.transition(
                "o1", "close", null, null, "提前关闭", "采购员", "close-key-0001"))
                .isInstanceOf(ApiException.class)
                .satisfies(error -> assertThat(((ApiException) error).code())
                        .isEqualTo("payment_required_before_close"));
        verify(audit, never()).save(any(AuditEvent.class));
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
    void approvedTaskWithoutLandedCostCannotCreateFormalOrder() {
        when(orders.findByTaskId(task.getId())).thenReturn(Optional.empty());

        assertThatThrownBy(() -> service.ensureOrderForApprovedTask(task))
                .isInstanceOf(ApiException.class)
                .satisfies(error -> assertThat(((ApiException) error).code())
                        .isEqualTo("order_requires_landed_cost"));
        verify(orders, never()).saveAndFlush(any());
    }

    @Test
    void listNarrowsToTaskScopedFinderWhenTaskIdProvided() {
        var order = pendingOrder();
        when(orders.findAllByTaskIdOrderByCreatedAtDesc(eq(task.getId()), any(Pageable.class)))
                .thenReturn(new PageImpl<>(List.of(order)));

        var result = service.list(null, task.getId(), 0, 20);

        assertThat(result).containsKeys("items", "page", "size", "total");
        assertThat((List<?>) result.get("items")).singleElement().satisfies(raw -> {
            @SuppressWarnings("unchecked")
            var item = (Map<String, Object>) raw;
            assertThat(item).containsEntry("id", order.getId());
            assertThat(item).containsEntry("task_id", task.getId());
        });
        assertThat(result.get("total")).isEqualTo(1L);
        verify(orders).findAllByTaskIdOrderByCreatedAtDesc(eq(task.getId()), any(Pageable.class));
        verify(orders, never()).findAllByOrderByCreatedAtDesc(any());
        verify(orders, never()).findByStatusOrderByCreatedAtDesc(any(), any());
    }

    @Test
    void listCombinesTaskIdAndStatusFilters() {
        when(orders.findByTaskIdAndStatusOrderByCreatedAtDesc(
                eq(task.getId()), eq("SHIPPED"), any(Pageable.class)))
                .thenReturn(new PageImpl<>(List.of()));

        var result = service.list("shipped", task.getId(), 0, 20);

        assertThat((List<?>) result.get("items")).isEmpty();
        verify(orders).findByTaskIdAndStatusOrderByCreatedAtDesc(
                eq(task.getId()), eq("SHIPPED"), any(Pageable.class));
        verify(orders, never()).findAllByTaskIdOrderByCreatedAtDesc(any(), any());
    }

    @Test
    void listWithoutTaskIdKeepsTheGlobalPagingPath() {
        when(orders.findAllByOrderByCreatedAtDesc(any(Pageable.class)))
                .thenReturn(new PageImpl<>(List.of()));

        service.list(null, null, 0, 20);

        verify(orders).findAllByOrderByCreatedAtDesc(any(Pageable.class));
        verify(orders, never()).findAllByTaskIdOrderByCreatedAtDesc(any(), any());
    }
}
