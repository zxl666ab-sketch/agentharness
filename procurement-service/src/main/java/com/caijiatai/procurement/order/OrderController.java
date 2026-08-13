package com.caijiatai.procurement.order;

import com.caijiatai.procurement.config.AppProperties;
import com.caijiatai.procurement.settlement.SettlementService;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.Map;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** 采购订单/对账接口（K2/K8，路径见冻结设计 4.11）。 */
@RestController
@RequestMapping("/api/procurement")
public final class OrderController {
    private final OrderService orders;
    private final SettlementService settlements;
    private final String operator;

    public OrderController(
            OrderService orders,
            SettlementService settlements,
            AppProperties properties) {
        this.orders = orders;
        this.settlements = settlements;
        this.operator = properties.localOperator();
    }

    @GetMapping("/orders")
    public Map<String, Object> listOrders(
            @RequestParam(required = false) String status,
            @RequestParam(defaultValue = "0") int page,
            @RequestParam(defaultValue = "20") int size) {
        // 冻结触发点：查询时惰性派生已批准任务订单（幂等）
        orders.reconcileApprovedTasks();
        return orders.list(status, page, size);
    }

    @GetMapping("/orders/{id}")
    public Map<String, Object> orderDetail(@PathVariable String id) {
        return orders.detail(id);
    }

    public record OrderTransitionRequest(
            String action,
            BigDecimal received_quantity,
            Instant arrival_date,
            String notes) {}

    @PostMapping("/orders/{id}/transition")
    public Map<String, Object> transitionOrder(
            @PathVariable String id, @RequestBody OrderTransitionRequest body) {
        return orders.transition(
                id,
                body.action(),
                body.received_quantity(),
                body.arrival_date(),
                body.notes(),
                operator);
    }

    @GetMapping("/settlements")
    public Map<String, Object> listSettlements(
            @RequestParam(required = false) String status,
            @RequestParam(defaultValue = "0") int page,
            @RequestParam(defaultValue = "20") int size) {
        return settlements.list(status, page, size);
    }

    public record SettlementTransitionRequest(
            String action,
            Instant paid_at,
            String notes) {}

    @PostMapping("/settlements/{id}/transition")
    public Map<String, Object> transitionSettlement(
            @PathVariable String id, @RequestBody SettlementTransitionRequest body) {
        return settlements.transition(id, body.action(), body.paid_at(), body.notes(), operator);
    }
}
