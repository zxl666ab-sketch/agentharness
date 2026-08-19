package com.caijiatai.procurement.invoice;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.caijiatai.procurement.agent.AgentCommand;
import com.caijiatai.procurement.agent.AgentCommandRepository;
import com.caijiatai.procurement.agent.IdempotencyRecord;
import com.caijiatai.procurement.agent.IdempotencyRecordRepository;
import com.caijiatai.procurement.api.ApiException;
import com.caijiatai.procurement.artifact.ArtifactStore;
import com.caijiatai.procurement.artifact.BusinessArtifact;
import com.caijiatai.procurement.cache.InsightsCache;
import com.caijiatai.procurement.comparison.ComparisonSnapshotRepository;
import com.caijiatai.procurement.config.AppProperties;
import com.caijiatai.procurement.order.OrderRepository;
import com.caijiatai.procurement.order.PurchaseOrder;
import com.caijiatai.procurement.report.AuditEventRepository;
import com.caijiatai.procurement.settlement.PurchaseSettlement;
import com.caijiatai.procurement.settlement.SettlementRepository;
import com.caijiatai.procurement.task.ProcurementTaskRepository;
import java.math.BigDecimal;
import java.time.Instant;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.Map;
import java.util.Optional;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockMultipartFile;

class InvoiceServiceIdempotencyTest {
    private final InvoiceRepository invoices = mock(InvoiceRepository.class);
    private final OrderRepository orders = mock(OrderRepository.class);
    private final ProcurementTaskRepository tasks = mock(ProcurementTaskRepository.class);
    private final ComparisonSnapshotRepository snapshots = mock(ComparisonSnapshotRepository.class);
    private final AgentCommandRepository commands = mock(AgentCommandRepository.class);
    private final IdempotencyRecordRepository idempotency = mock(IdempotencyRecordRepository.class);
    private final ArtifactStore artifactStore = mock(ArtifactStore.class);
    private final AuditEventRepository audit = mock(AuditEventRepository.class);
    private final SettlementRepository settlements = mock(SettlementRepository.class);
    private final InsightsCache insightsCache = mock(InsightsCache.class);
    private final AppProperties properties = mock(AppProperties.class);
    private final InvoiceService service;

    InvoiceServiceIdempotencyTest() {
        when(properties.localOperator()).thenReturn("采购员");
        service = new InvoiceService(
                invoices, orders, tasks, snapshots, commands, idempotency, artifactStore, audit, settlements, insightsCache,
                new InvoiceStateMachineConfig().invoiceStateMachine(), properties);
    }

    @Test
    void rejectsAnExplicitKeyReusedForDifferentInvoiceContent() {
        var order = PurchaseOrder.derive(
                "task-1", "PO-1", "供应商A", "物料A", BigDecimal.TEN, "件", BigDecimal.TEN);
        when(orders.lockById("order-1")).thenReturn(Optional.of(order));
        when(idempotency.findById(new IdempotencyRecord.Key("invoice_upload", "shared-key")))
                .thenReturn(Optional.of(IdempotencyRecord.reserve(
                        "invoice_upload", "shared-key", "different-request-sha", "old-operation")));
        var file = new MockMultipartFile(
                "file", "invoice.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "new invoice bytes".getBytes(java.nio.charset.StandardCharsets.UTF_8));

        assertThatThrownBy(() -> service.upload("order-1", file, "shared-key"))
                .isInstanceOf(ApiException.class)
                .satisfies(error -> {
                    var api = (ApiException) error;
                    assertThat(api.status().value()).isEqualTo(409);
                    assertThat(api.code()).isEqualTo("idempotency_payload_conflict");
                });
        verify(artifactStore, never()).store(
                org.mockito.ArgumentMatchers.any(), org.mockito.ArgumentMatchers.any(),
                org.mockito.ArgumentMatchers.any(), org.mockito.ArgumentMatchers.any(),
                org.mockito.ArgumentMatchers.any(), org.mockito.ArgumentMatchers.any());
    }

    @Test
    void rejectsALegacyKeyReusedWithADifferentFilename() throws Exception {
        var order = PurchaseOrder.derive(
                "task-1", "PO-1", "供应商A", "物料A", BigDecimal.TEN, "件", BigDecimal.TEN);
        when(orders.lockById("order-1")).thenReturn(Optional.of(order));
        var bytes = "same invoice bytes".getBytes(java.nio.charset.StandardCharsets.UTF_8);
        var contentSha = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
        var command = AgentCommand.accept(
                "old-operation", "parse_invoice", "order-1", 1, 0,
                Map.of("sha256", contentSha, "filename", "original.xlsx"));
        when(idempotency.findById(new IdempotencyRecord.Key("invoice_upload", "legacy-key")))
                .thenReturn(Optional.of(IdempotencyRecord.reserve(
                        "invoice_upload", "legacy-key", contentSha, command.getOperationId())));
        when(commands.findById(command.getOperationId())).thenReturn(Optional.of(command));
        var renamedFile = new MockMultipartFile(
                "file", "renamed.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", bytes);

        assertThatThrownBy(() -> service.upload("order-1", renamedFile, "legacy-key"))
                .isInstanceOf(ApiException.class)
                .satisfies(error -> assertThat(((ApiException) error).code())
                        .isEqualTo("idempotency_payload_conflict"));
        verify(artifactStore, never()).store(
                org.mockito.ArgumentMatchers.any(), org.mockito.ArgumentMatchers.any(),
                org.mockito.ArgumentMatchers.any(), org.mockito.ArgumentMatchers.any(),
                org.mockito.ArgumentMatchers.any(), org.mockito.ArgumentMatchers.any());
    }

    @Test
    void rejectsInvoiceUploadAfterOrderIsClosed() {
        var order = PurchaseOrder.derive(
                "task-1", "PO-1", "供应商A", "物料A", BigDecimal.TEN, "件", BigDecimal.TEN);
        order.close("履约已完成");
        when(orders.lockById("order-1")).thenReturn(Optional.of(order));
        var file = new MockMultipartFile(
                "file", "invoice.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "late invoice".getBytes(java.nio.charset.StandardCharsets.UTF_8));

        assertThatThrownBy(() -> service.upload("order-1", file, "closed-order-key"))
                .isInstanceOf(ApiException.class)
                .satisfies(error -> {
                    var api = (ApiException) error;
                    assertThat(api.status().value()).isEqualTo(409);
                    assertThat(api.code()).isEqualTo("invoice_upload_not_allowed");
                });
        verify(artifactStore, never()).store(
                org.mockito.ArgumentMatchers.any(), org.mockito.ArgumentMatchers.any(),
                org.mockito.ArgumentMatchers.any(), org.mockito.ArgumentMatchers.any(),
                org.mockito.ArgumentMatchers.any(), org.mockito.ArgumentMatchers.any());
    }

    @Test
    void rejectsInvoiceUploadAfterSettlementIsPaid() {
        var order = PurchaseOrder.derive(
                "task-1", "PO-1", "供应商A", "物料A", BigDecimal.TEN, "件", BigDecimal.TEN);
        var settlement = PurchaseSettlement.derive(
                "order-1", "ST-1", "供应商A", BigDecimal.TEN);
        settlement.settle("已对账");
        settlement.pay(Instant.parse("2026-08-18T08:00:00Z"), "已付款");
        when(orders.lockById("order-1")).thenReturn(Optional.of(order));
        when(settlements.lockByOrderId("order-1")).thenReturn(Optional.of(settlement));
        var file = new MockMultipartFile(
                "file", "invoice.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "late invoice".getBytes(java.nio.charset.StandardCharsets.UTF_8));

        assertThatThrownBy(() -> service.upload("order-1", file, "paid-order-key"))
                .isInstanceOf(ApiException.class)
                .satisfies(error -> {
                    var api = (ApiException) error;
                    assertThat(api.status().value()).isEqualTo(409);
                    assertThat(api.code()).isEqualTo("invoice_upload_not_allowed");
                });
        verify(artifactStore, never()).store(
                org.mockito.ArgumentMatchers.any(), org.mockito.ArgumentMatchers.any(),
                org.mockito.ArgumentMatchers.any(), org.mockito.ArgumentMatchers.any(),
                org.mockito.ArgumentMatchers.any(), org.mockito.ArgumentMatchers.any());
    }

    @Test
    void acceptsInvoiceUploadAfterSettlementIsSettledButNotPaid() {
        var order = PurchaseOrder.derive(
                "task-1", "PO-1", "供应商A", "物料A", BigDecimal.TEN, "件", BigDecimal.TEN);
        var settlement = PurchaseSettlement.derive(
                "order-1", "ST-1", "供应商A", BigDecimal.TEN);
        settlement.settle("已对账");
        when(orders.lockById("order-1")).thenReturn(Optional.of(order));
        when(settlements.lockByOrderId("order-1")).thenReturn(Optional.of(settlement));
        when(idempotency.findById(org.mockito.ArgumentMatchers.any())).thenReturn(Optional.empty());
        var artifact = mock(BusinessArtifact.class);
        when(artifact.getId()).thenReturn("artifact-1");
        when(artifact.getSha256()).thenReturn("a".repeat(64));
        when(artifactStore.store(
                org.mockito.ArgumentMatchers.any(), org.mockito.ArgumentMatchers.any(),
                org.mockito.ArgumentMatchers.any(), org.mockito.ArgumentMatchers.any(),
                org.mockito.ArgumentMatchers.any(), org.mockito.ArgumentMatchers.any()))
                .thenReturn(artifact);
        when(commands.save(org.mockito.ArgumentMatchers.any(AgentCommand.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));
        var file = new MockMultipartFile(
                "file", "invoice.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "new invoice".getBytes(java.nio.charset.StandardCharsets.UTF_8));

        var accepted = service.upload("order-1", file, "settled-order-key");

        assertThat(accepted.status()).isEqualTo("accepted");
        assertThat(accepted.purchaseRequestId()).isEqualTo("order-1");
        verify(commands).save(org.mockito.ArgumentMatchers.any(AgentCommand.class));
    }
}
