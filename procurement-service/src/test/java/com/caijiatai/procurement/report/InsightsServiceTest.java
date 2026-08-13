package com.caijiatai.procurement.report;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import com.caijiatai.procurement.ai.AiTaskRepository;
import com.caijiatai.procurement.order.OrderRepository;
import com.caijiatai.procurement.order.PurchaseOrder;
import com.caijiatai.procurement.review.ReviewRecordRepository;
import com.caijiatai.procurement.settlement.SettlementRepository;
import com.caijiatai.procurement.supplier.SupplierRepository;
import com.caijiatai.procurement.task.ProcurementTask;
import com.caijiatai.procurement.task.ProcurementTaskRepository;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import org.junit.jupiter.api.Test;

class InsightsServiceTest {
    private final ProcurementTaskRepository tasks = mock(ProcurementTaskRepository.class);
    private final OrderRepository orders = mock(OrderRepository.class);
    private final SettlementRepository settlements = mock(SettlementRepository.class);
    private final SupplierRepository suppliers = mock(SupplierRepository.class);
    private final AiTaskRepository aiTasks = mock(AiTaskRepository.class);
    private final ReviewRecordRepository reviews = mock(ReviewRecordRepository.class);
    private final AuditEventRepository audit = mock(AuditEventRepository.class);
    private final InsightsService service =
            new InsightsService(tasks, orders, settlements, suppliers, aiTasks, reviews, audit);

    private ProcurementTask approvedTask(String item, String quantity, String budgetUnit, String landed) {
        var constraints = new LinkedHashMap<String, Object>();
        constraints.put("base_currency", "CNY");
        constraints.put("fx_rates", Map.of("CNY", "1"));
        constraints.put("max_landed_unit_cost", budgetUnit);
        var task = ProcurementTask.structured(
                1, item + "采购", "ecommerce_packaging", item,
                new BigDecimal(quantity), "piece", Map.of(), constraints);
        task.finalizeDecision("quote-" + item, false);
        if (landed != null) {
            var order = PurchaseOrder.derive(task.getId(), "PO-" + task.getReference(),
                    "供应商", item, new BigDecimal(quantity), "piece", new BigDecimal(landed));
            when(orders.findByTaskId(task.getId())).thenReturn(Optional.of(order));
        }
        return task;
    }

    @Test
    void costSavingsFollowsFrozenFormulaWithFourDecimalRate() {
        // 任务A：预算 0.70×15000=10500，到货 7500；任务B：预算 3.0×5000=15000，到货 11000
        var taskA = approvedTask("快递袋", "15000", "0.70", "7500.00");
        var taskB = approvedTask("封箱胶带", "5000", "3.00", "11000.00");
        when(tasks.findByStatusOrderByUpdatedAtDesc("approved")).thenReturn(List.of(taskA, taskB));

        @SuppressWarnings("unchecked")
        var savings = (Map<String, Object>) service.overview().get("cost_savings");

        assertThat(savings.get("budget_total")).isEqualTo("25500.00");
        assertThat(savings.get("landed_total")).isEqualTo("18500.00");
        assertThat(savings.get("savings")).isEqualTo("7000.00");
        assertThat(savings.get("rate")).isEqualTo("0.2745");
        assertThat(savings.get("included_tasks")).isEqualTo(2);
    }

    @Test
    void costSavingsSkipsTasksWithoutBudgetAndWithoutLandedTotal() {
        var withBudget = approvedTask("快递袋", "15000", "0.70", "7500.00");
        // 无预算任务：不计入
        var noBudget = approvedTask("垃圾袋", "1000", null, "900.00");
        // 有预算但缺批准到货总价：不计入
        var noLanded = approvedTask("气泡膜", "300", "45.0", null);
        when(tasks.findByStatusOrderByUpdatedAtDesc("approved"))
                .thenReturn(List.of(withBudget, noBudget, noLanded));

        @SuppressWarnings("unchecked")
        var savings = (Map<String, Object>) service.overview().get("cost_savings");

        assertThat(savings.get("budget_total")).isEqualTo("10500.00");
        assertThat(savings.get("included_tasks")).isEqualTo(1);
        assertThat(savings.get("rate")).isEqualTo("0.2857");
    }

    @Test
    void costSavingsReturnsNullRateWhenNoBudgetIncluded() {
        var noBudget = approvedTask("垃圾袋", "1000", null, "900.00");
        when(tasks.findByStatusOrderByUpdatedAtDesc("approved")).thenReturn(List.of(noBudget));

        @SuppressWarnings("unchecked")
        var savings = (Map<String, Object>) service.overview().get("cost_savings");

        assertThat(savings.get("budget_total")).isEqualTo("0.00");
        assertThat(savings.get("rate")).isNull();
    }

    @Test
    void statusFunnelGroupsByTaskStatus() {
        var approved = approvedTask("快递袋", "15000", "0.70", "7500.00");
        var approved2 = approvedTask("封箱胶带", "5000", "3.00", "11000.00");
        var collecting = ProcurementTask.structured(1, "待比价", "ecommerce_packaging", "胶带",
                BigDecimal.ONE, "piece", Map.of(), Map.of());
        when(tasks.findAll()).thenReturn(List.of(approved, approved2, collecting));

        @SuppressWarnings("unchecked")
        var funnel = (List<Map<String, Object>>) service.overview().get("status_funnel");

        assertThat(funnel).anySatisfy(entry -> {
            assertThat(entry.get("status")).isEqualTo("approved");
            assertThat(entry.get("count")).isEqualTo(2L);
        });
        assertThat(funnel).anySatisfy(entry -> {
            assertThat(entry.get("status")).isEqualTo("collecting");
            assertThat(entry.get("count")).isEqualTo(1L);
        });
    }

    @Test
    void countsAggregateEveryDomain() {
        when(tasks.count()).thenReturn(5L);
        when(tasks.countByStatus("approved")).thenReturn(3L);
        when(orders.count()).thenReturn(3L);
        when(orders.countByStatus("RECEIVED")).thenReturn(2L);
        when(settlements.countByStatus("PAID")).thenReturn(1L);
        when(suppliers.count()).thenReturn(4L);
        when(suppliers.countByStatus("BLACKLISTED")).thenReturn(1L);
        when(reviews.countByStatus(any())).thenReturn(2L);
        when(aiTasks.countByStatus(any())).thenReturn(0L);
        when(aiTasks.countByStaleTrue()).thenReturn(1L);
        when(audit.countByEventType("order_shipment_overdue")).thenReturn(0L);
        when(audit.countByEventType("settlement_payment_overdue")).thenReturn(0L);

        @SuppressWarnings("unchecked")
        var counts = (Map<String, Object>) service.overview().get("counts");

        assertThat(counts.get("tasks")).isEqualTo(5L);
        assertThat(counts.get("approved_tasks")).isEqualTo(3L);
        assertThat(counts.get("orders_received")).isEqualTo(2L);
        assertThat(counts.get("settlements_paid")).isEqualTo(1L);
        assertThat(counts.get("suppliers_blacklisted")).isEqualTo(1L);
        assertThat(counts.get("ai_tasks_failed")).isEqualTo(1L);
        assertThat(counts.get("reviews_pending")).isEqualTo(2L);
    }

    @Test
    void trendGroupsByMonthWithApprovedAmounts() {
        var task = approvedTask("快递袋", "15000", "0.70", "7500.00");
        when(tasks.findByCreatedAtGreaterThanEqualOrderByCreatedAtAsc(any())).thenReturn(List.of(task));

        var trend = service.trend(6);

        assertThat(trend).hasSize(1);
        var entry = trend.getFirst();
        assertThat(entry.get("task_count")).isEqualTo(1L);
        assertThat(entry.get("approved_amount")).isEqualTo("7500.00");
        assertThat(String.valueOf(entry.get("month"))).matches("[0-9]{4}-[0-9]{2}");
    }

    @Test
    void categoriesGroupByTaskCategory() {
        var taskA = approvedTask("快递袋", "15000", "0.70", "7500.00");
        var taskB = approvedTask("封箱胶带", "5000", "3.00", "11000.00");
        var other = ProcurementTask.structured(1, "非包装", "office_supplies", "纸",
                BigDecimal.ONE, "piece", Map.of(), Map.of());
        when(tasks.findAll()).thenReturn(List.of(taskA, taskB, other));

        var categories = service.categories();

        assertThat(categories).hasSize(2);
        assertThat(categories).anySatisfy(entry -> {
            assertThat(entry.get("category")).isEqualTo("ecommerce_packaging");
            assertThat(entry.get("count")).isEqualTo(2L);
        });
    }
}
