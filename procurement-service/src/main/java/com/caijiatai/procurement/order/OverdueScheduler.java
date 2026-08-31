package com.caijiatai.procurement.order;

import com.caijiatai.procurement.report.AuditEvent;
import com.caijiatai.procurement.report.AuditEventRepository;
import com.caijiatai.procurement.settlement.SettlementRepository;
import java.time.Clock;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

/**
 * 双超时调度（冻结设计 4.3）：
 * - 发货逾期：扫描 PENDING_SHIPMENT 且 updated_at < now()-7d，写 order_shipment_overdue（幂等去重）；
 * - 付款逾期：扫描 SETTLED 且 updated_at < now()-7d，写 settlement_payment_overdue（幂等去重）。
 * clock 注入可测。
 */
@Component
public class OverdueScheduler {
    private static final Logger log = LoggerFactory.getLogger(OverdueScheduler.class);
    private static final long OVERDUE_DAYS = 7;

    private final OrderRepository orders;
    private final SettlementRepository settlements;
    private final AuditEventRepository audit;
    private final Clock clock;

    public OverdueScheduler(
            OrderRepository orders,
            SettlementRepository settlements,
            AuditEventRepository audit,
            Clock clock) {
        this.orders = orders;
        this.settlements = settlements;
        this.audit = audit;
        this.clock = clock;
    }

    @Scheduled(fixedDelay = 60_000)
    @Transactional
    public void scanOrderShipmentOverdue() {
        var deadline = Instant.now(clock).minus(OVERDUE_DAYS, ChronoUnit.DAYS);
        int written = 0;
        for (var order : orders.findByStatusAndUpdatedAtBefore(OrderStatus.PENDING_SHIPMENT.wireValue(), deadline)) {
            // 幂等：同一订单只写一次（一任务一订单，task_id 去重即 order_id 去重）
            if (audit.existsByTaskIdAndEventType(order.getTaskId(), "order_shipment_overdue")) {
                continue;
            }
            audit.save(AuditEvent.create(
                    order.getTaskId(), null, null,
                    "order", order.getId(), "order_shipment_overdue", "system",
                    Map.of("order_id", order.getId(), "order_no", order.getOrderNo(),
                            "overdue_days", OVERDUE_DAYS,
                            "last_updated_at", order.getUpdatedAt().toString())));
            written += 1;
        }
        if (written > 0) {
            log.info("发货逾期调度：写入 {} 条 order_shipment_overdue", written);
        }
    }

    @Scheduled(fixedDelay = 60_000)
    @Transactional
    public void scanSettlementPaymentOverdue() {
        var deadline = Instant.now(clock).minus(OVERDUE_DAYS, ChronoUnit.DAYS);
        int written = 0;
        for (var settlement : settlements.findByStatusAndUpdatedAtBefore(
                com.caijiatai.procurement.settlement.SettlementStatus.SETTLED.wireValue(), deadline)) {
            var order = orders.findById(settlement.getOrderId()).orElse(null);
            var taskId = order == null ? null : order.getTaskId();
            if (taskId == null || audit.existsByTaskIdAndEventType(taskId, "settlement_payment_overdue")) {
                continue;
            }
            audit.save(AuditEvent.create(
                    taskId, null, null,
                    "settlement", settlement.getId(), "settlement_payment_overdue", "system",
                    Map.of("settlement_id", settlement.getId(),
                            "settlement_no", settlement.getSettlementNo(),
                            "order_id", settlement.getOrderId(),
                            "overdue_days", OVERDUE_DAYS,
                            "last_updated_at", settlement.getUpdatedAt().toString())));
            written += 1;
        }
        if (written > 0) {
            log.info("付款逾期调度：写入 {} 条 settlement_payment_overdue", written);
        }
    }
}
