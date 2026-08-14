package com.caijiatai.procurement.agent;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import com.caijiatai.procurement.order.OrderRepository;
import com.caijiatai.procurement.order.PurchaseOrder;
import com.caijiatai.procurement.task.ProcurementTask;
import com.caijiatai.procurement.task.ProcurementTaskRepository;
import java.math.BigDecimal;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import org.junit.jupiter.api.Test;

class ReferencePriceServiceTest {
    private final ProcurementTaskRepository tasks = mock(ProcurementTaskRepository.class);
    private final OrderRepository orders = mock(OrderRepository.class);
    private final ReferencePriceService service = new ReferencePriceService(tasks, orders);

    private ProcurementTask task(String item, String category) {
        return ProcurementTask.structured(1, item + "采购", category, item,
                BigDecimal.TEN, "piece", Map.of(), Map.of());
    }

    private PurchaseOrder order(String taskId, String supplier, String landed) {
        return PurchaseOrder.derive(taskId, "PO-X-" + taskId, supplier, "物料", BigDecimal.TEN, "piece",
                new BigDecimal(landed));
    }

    @Test
    void returnsNullIntervalWhenFewerThanThreeRecords() {
        var taskA = task("快递袋", "ecommerce_packaging");
        var taskB = task("气泡膜", "ecommerce_packaging");
        when(tasks.findById(taskA.getId())).thenReturn(Optional.of(taskA));
        when(tasks.findById(taskB.getId())).thenReturn(Optional.of(taskB));
        when(orders.findAllByLandedTotalNotNullOrderByCreatedAtDesc()).thenReturn(List.of(
                order(taskA.getId(), "华东优包", "7500.00"),
                order(taskB.getId(), "华南气泡包装", "9600.00")));

        var result = service.referencePrices("t-ref", "快递袋", "ecommerce_packaging");

        assertThat(result.get("records")).asList().hasSize(1);
        assertThat(result.get("interval")).isNull();
    }

    @Test
    void computesP25P75FromApprovedLandingTotals() {
        var taskA = task("快递袋", "ecommerce_packaging");
        var taskB = task("快递袋", "ecommerce_packaging");
        var taskC = task("快递袋", "ecommerce_packaging");
        when(tasks.findById(taskA.getId())).thenReturn(Optional.of(taskA));
        when(tasks.findById(taskB.getId())).thenReturn(Optional.of(taskB));
        when(tasks.findById(taskC.getId())).thenReturn(Optional.of(taskC));
        when(orders.findAllByLandedTotalNotNullOrderByCreatedAtDesc()).thenReturn(List.of(
                order(taskA.getId(), "华东优包", "7500.00"),
                order(taskB.getId(), "沪上包装", "9400.00"),
                order(taskC.getId(), "江南优品", "9540.00")));

        var result = service.referencePrices("t-ref", "快递袋", "ecommerce_packaging");

        @SuppressWarnings("unchecked")
        var interval = (Map<String, Object>) result.get("interval");
        assertThat(interval.get("p25")).isEqualTo("7500.00");
        assertThat(interval.get("p75")).isEqualTo("9400.00");
        assertThat(interval.get("count")).isEqualTo(3);
        assertThat(interval.get("basis")).isEqualTo("landed_total_base");
    }

    @Test
    void matchesByNormalizedItemNameIgnoringCaseAndSpaces() {
        var taskA = task("快递袋", "ecommerce_packaging");
        var taskB = task("气泡膜", "ecommerce_packaging");
        when(tasks.findById(taskA.getId())).thenReturn(Optional.of(taskA));
        when(tasks.findById(taskB.getId())).thenReturn(Optional.of(taskB));
        when(orders.findAllByLandedTotalNotNullOrderByCreatedAtDesc()).thenReturn(List.of(
                order(taskA.getId(), "华东优包", "7500.00"),
                order(taskB.getId(), "华南气泡包装", "9600.00")));

        var result = service.referencePrices("t-ref", " 快 递 袋 ", "ecommerce_packaging");

        assertThat(result.get("records")).asList().hasSize(1);
        assertThat(result.get("interval")).isNull();
    }

    @Test
    void fallsBackToCategoryWhenItemNameMissing() {
        var taskA = task("封箱胶带", "ecommerce_packaging");
        var taskB = task("气泡膜", "ecommerce_packaging");
        var taskC = task("快递袋", "ecommerce_packaging");
        when(tasks.findById(taskA.getId())).thenReturn(Optional.of(taskA));
        when(tasks.findById(taskB.getId())).thenReturn(Optional.of(taskB));
        when(tasks.findById(taskC.getId())).thenReturn(Optional.of(taskC));
        when(orders.findAllByLandedTotalNotNullOrderByCreatedAtDesc()).thenReturn(List.of(
                order(taskA.getId(), "嘉兴胶粘", "11000.00"),
                order(taskB.getId(), "华南气泡包装", "9600.00"),
                order(taskC.getId(), "华东优包", "7500.00")));

        var result = service.referencePrices("t-ref", "", "ecommerce_packaging");

        assertThat(result.get("records")).asList().hasSize(3);
        @SuppressWarnings("unchecked")
        var interval = (Map<String, Object>) result.get("interval");
        assertThat(interval.get("p25")).isEqualTo("7500.00");
        assertThat(interval.get("p75")).isEqualTo("9600.00");
    }

    @Test
    void normalizesNullToEmpty() {
        assertThat(ReferencePriceService.normalize(null)).isEmpty();
        assertThat(ReferencePriceService.normalize("  PE 快递袋 ")).isEqualTo("pe快递袋");
    }

    @Test
    void similarityMatchesNormalizedEqualityAndContainment() {
        assertThat(ReferencePriceService.isSimilarItem("快递袋", "快递袋")).isTrue();
        assertThat(ReferencePriceService.isSimilarItem("pe快递袋", "快递袋")).isTrue();
        assertThat(ReferencePriceService.isSimilarItem("快递袋", "pe快递袋")).isTrue();
        assertThat(ReferencePriceService.isSimilarItem("快递袋", "垃圾袋")).isFalse();
        assertThat(ReferencePriceService.isSimilarItem("快递袋", "")).isFalse();
        assertThat(ReferencePriceService.isSimilarItem("a", "ab")).isFalse();
    }

    @Test
    void prefixVariantItemMatchesHistoryForReferencePrices() {
        var taskA = task("快递袋", "ecommerce_packaging");
        var taskB = task("气泡膜", "ecommerce_packaging");
        when(tasks.findById(taskA.getId())).thenReturn(Optional.of(taskA));
        when(tasks.findById(taskB.getId())).thenReturn(Optional.of(taskB));
        when(orders.findAllByLandedTotalNotNullOrderByCreatedAtDesc()).thenReturn(List.of(
                order(taskA.getId(), "华东优包", "7500.00"),
                order(taskB.getId(), "华南气泡包装", "9600.00")));

        // 对话流程抽取的物料名可能是 "PE快递袋"，历史成交记为 "快递袋"（相似度匹配）
        var result = service.referencePrices("t-ref", "PE快递袋", "ecommerce_packaging");

        assertThat(result.get("records")).asList().hasSize(1);
        assertThat(result.get("interval")).isNull(); // 仍不足 3 条 → 不下结论
    }

    @Test
    void recordsCarrySupplierAndReference() {
        var taskA = task("快递袋", "ecommerce_packaging");
        when(tasks.findById(taskA.getId())).thenReturn(Optional.of(taskA));
        when(orders.findAllByLandedTotalNotNullOrderByCreatedAtDesc())
                .thenReturn(List.of(order(taskA.getId(), "华东优包", "7500.00")));

        var result = service.referencePrices("t-ref", "快递袋", "ecommerce_packaging");

        @SuppressWarnings("unchecked")
        var records = (List<Map<String, Object>>) result.get("records");
        assertThat(records).hasSize(1);
        assertThat(records.getFirst().get("supplier_name")).isEqualTo("华东优包");
        assertThat(String.valueOf(records.getFirst().get("reference"))).startsWith("RFQ-");
        assertThat(records.getFirst().get("landed_total")).isEqualTo("7500.00");
    }
}
