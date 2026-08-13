package com.caijiatai.procurement.report;

import com.caijiatai.procurement.ai.AiTaskRepository;
import com.caijiatai.procurement.ai.AiTaskStatus;
import com.caijiatai.procurement.order.OrderRepository;
import com.caijiatai.procurement.order.OrderStatus;
import com.caijiatai.procurement.review.ReviewRecordRepository;
import com.caijiatai.procurement.review.ReviewStatus;
import com.caijiatai.procurement.settlement.SettlementRepository;
import com.caijiatai.procurement.settlement.SettlementStatus;
import com.caijiatai.procurement.supplier.SupplierRepository;
import com.caijiatai.procurement.task.ProcurementTask;
import com.caijiatai.procurement.task.ProcurementTaskRepository;
import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.Instant;
import java.time.ZoneOffset;
import java.time.ZonedDateTime;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.TreeMap;
import java.util.stream.Collectors;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * 统计报表聚合（K3，冻结口径 docs/platform-upgrade-design.md 4.8）。
 *
 * <p>成本节约率 = (Σ预算单价×数量 − Σ批准到货总价) / Σ预算单价×数量；
 * 预算取任务 constraints 的 max_landed_unit_cost（到货单价上限，保守口径），无预算任务不计入；
 * 批准到货总价取比价快照中批准报价的 landed_total_base（基准币种）；金额 BigDecimal，比例保留 4 位。
 */
@Service
public class InsightsService {
    private static final BigDecimal FOUR_DECIMALS_SCALE = new BigDecimal("1").setScale(4);

    private final ProcurementTaskRepository tasks;
    private final OrderRepository orders;
    private final SettlementRepository settlements;
    private final SupplierRepository suppliers;
    private final AiTaskRepository aiTasks;
    private final ReviewRecordRepository reviews;
    private final AuditEventRepository audit;

    public InsightsService(
            ProcurementTaskRepository tasks,
            OrderRepository orders,
            SettlementRepository settlements,
            SupplierRepository suppliers,
            AiTaskRepository aiTasks,
            ReviewRecordRepository reviews,
            AuditEventRepository audit) {
        this.tasks = tasks;
        this.orders = orders;
        this.settlements = settlements;
        this.suppliers = suppliers;
        this.aiTasks = aiTasks;
        this.reviews = reviews;
        this.audit = audit;
    }

    /** 驾驶舱概览：状态漏斗 + 成本节约率 + 全局计数（K9 待办中心数据源）。 */
    @Transactional(readOnly = true)
    public Map<String, Object> overview() {
        var value = new LinkedHashMap<String, Object>();
        value.put("status_funnel", statusFunnel());
        value.put("cost_savings", costSavings());
        value.put("counts", counts());
        return value;
    }

    private List<Map<String, Object>> statusFunnel() {
        var counts = new LinkedHashMap<String, Long>();
        for (var task : tasks.findAll()) {
            counts.merge(task.getStatus(), 1L, Long::sum);
        }
        var funnel = new ArrayList<Map<String, Object>>();
        counts.entrySet().stream()
                .sorted(Map.Entry.<String, Long>comparingByValue().reversed())
                .forEach(entry -> funnel.add(Map.of("status", entry.getKey(), "count", entry.getValue())));
        return funnel;
    }

    /** 成本节约率（冻结口径 4.8）：预算 = max_landed_unit_cost × 数量；批准到货 = 快照批准报价 landed_total_base。 */
    private Map<String, Object> costSavings() {
        var budgetTotal = BigDecimal.ZERO;
        var landedTotal = BigDecimal.ZERO;
        var included = 0;
        for (var task : tasks.findByStatusOrderByUpdatedAtDesc("approved")) {
            var budgetUnit = budgetUnit(task);
            if (budgetUnit == null) {
                continue; // 无预算的任务不计入
            }
            var landed = approvedLandedTotal(task);
            if (landed == null) {
                continue; // 缺批准到货总价的任务不计入（口径一致）
            }
            budgetTotal = budgetTotal.add(budgetUnit.multiply(task.getQuantity()));
            landedTotal = landedTotal.add(landed);
            included += 1;
        }
        var savings = budgetTotal.subtract(landedTotal);
        BigDecimal rate;
        if (budgetTotal.signum() == 0) {
            rate = null;
        } else {
            rate = savings.divide(budgetTotal, 4, RoundingMode.HALF_UP);
        }
        var value = new LinkedHashMap<String, Object>();
        value.put("budget_total", budgetTotal.setScale(2, RoundingMode.HALF_UP).toPlainString());
        value.put("landed_total", landedTotal.setScale(2, RoundingMode.HALF_UP).toPlainString());
        value.put("savings", savings.setScale(2, RoundingMode.HALF_UP).toPlainString());
        value.put("rate", rate == null ? null : rate.toPlainString());
        value.put("included_tasks", included);
        return value;
    }

    private BigDecimal budgetUnit(ProcurementTask task) {
        var raw = task.getConstraints().get("max_landed_unit_cost");
        if (raw == null || String.valueOf(raw).isBlank()) {
            return null;
        }
        try {
            var value = new BigDecimal(String.valueOf(raw));
            return value.signum() > 0 ? value : null;
        } catch (NumberFormatException error) {
            return null;
        }
    }

    private BigDecimal approvedLandedTotal(ProcurementTask task) {
        var quoteId = task.getApprovedQuoteId();
        if (quoteId == null || quoteId.isBlank()) {
            return null;
        }
        var order = orders.findByTaskId(task.getId()).orElse(null);
        if (order != null && order.getLandedTotal() != null) {
            return order.getLandedTotal();
        }
        return null;
    }

    private Map<String, Object> counts() {
        var value = new LinkedHashMap<String, Object>();
        value.put("tasks", tasks.count());
        value.put("approved_tasks", tasks.countByStatus("approved"));
        value.put("orders", orders.count());
        value.put("orders_pending_shipment", orders.countByStatus(OrderStatus.PENDING_SHIPMENT.wireValue()));
        value.put("orders_shipped", orders.countByStatus(OrderStatus.SHIPPED.wireValue()));
        value.put("orders_received", orders.countByStatus(OrderStatus.RECEIVED.wireValue()));
        value.put("orders_closed", orders.countByStatus(OrderStatus.CLOSED.wireValue()));
        value.put("settlements_unsettled", settlements.countByStatus(SettlementStatus.UNSETTLED.wireValue()));
        value.put("settlements_settled", settlements.countByStatus(SettlementStatus.SETTLED.wireValue()));
        value.put("settlements_paid", settlements.countByStatus(SettlementStatus.PAID.wireValue()));
        value.put("suppliers", suppliers.count());
        value.put("suppliers_blacklisted", suppliers.countByStatus("BLACKLISTED"));
        value.put("reviews_pending", reviews.countByStatus(ReviewStatus.PENDING));
        value.put("ai_tasks_failed", aiTasks.countByStatus(AiTaskStatus.FAILED) + aiTasks.countByStaleTrue());
        value.put("overdue_orders", audit.countByEventType("order_shipment_overdue"));
        value.put("overdue_payments", audit.countByEventType("settlement_payment_overdue"));
        return value;
    }

    /** 月度趋势（近 N 个月）：任务数 + 批准金额（BigDecimal）。 */
    @Transactional(readOnly = true)
    public List<Map<String, Object>> trend(int months) {
        var monthsToInclude = Math.min(24, Math.max(1, months));
        var cutoff = ZonedDateTime.now(ZoneOffset.UTC)
                .minusMonths(monthsToInclude - 1L).withDayOfMonth(1).toInstant();
        var byMonth = new TreeMap<String, Map<String, Object>>();
        var approvedLanded = new LinkedHashMap<String, BigDecimal>();
        for (var task : tasks.findByCreatedAtGreaterThanEqualOrderByCreatedAtAsc(cutoff)) {
            var month = monthOf(task.getCreatedAt());
            var entry = byMonth.computeIfAbsent(month, ignored -> {
                var value = new LinkedHashMap<String, Object>();
                value.put("month", month);
                value.put("task_count", 0L);
                value.put("approved_amount", "0.00");
                return value;
            });
            entry.put("task_count", (long) entry.get("task_count") + 1);
            if ("approved".equals(task.getStatus())) {
                var landed = approvedLandedTotal(task);
                if (landed != null) {
                    approvedLanded.merge(month, landed, BigDecimal::add);
                }
            }
        }
        approvedLanded.forEach((month, total) -> {
            var entry = byMonth.get(month);
            if (entry != null) {
                entry.put("approved_amount", total.setScale(2, RoundingMode.HALF_UP).toPlainString());
            }
        });
        return new ArrayList<>(byMonth.values());
    }

    /** 品类分布：按 task.category 分组计数。 */
    @Transactional(readOnly = true)
    public List<Map<String, Object>> categories() {
        var counts = new LinkedHashMap<String, Long>();
        for (var task : tasks.findAll()) {
            counts.merge(task.getCategory(), 1L, Long::sum);
        }
        var result = new ArrayList<Map<String, Object>>();
        counts.entrySet().stream()
                .sorted(Map.Entry.<String, Long>comparingByValue().reversed())
                .forEach(entry -> result.add(Map.of("category", entry.getKey(), "count", entry.getValue())));
        return result;
    }

    private String monthOf(Instant instant) {
        return ZonedDateTime.ofInstant(instant, ZoneOffset.UTC).toString().substring(0, 7);
    }
}
