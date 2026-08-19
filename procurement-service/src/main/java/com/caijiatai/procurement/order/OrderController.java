package com.caijiatai.procurement.order;

import com.caijiatai.procurement.cache.InsightsCache;
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
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RestController;

/** 采购订单/对账接口（K2/K8，路径见冻结设计 4.11）。 */
@RestController
@RequestMapping("/api/procurement")
public final class OrderController {
    private final OrderService orders;
    private final SettlementService settlements;
    private final InsightsCache insightsCache;
    private final String operator;

    public OrderController(
            OrderService orders,
            SettlementService settlements,
            InsightsCache insightsCache,
            AppProperties properties) {
        this.orders = orders;
        this.settlements = settlements;
        this.insightsCache = insightsCache;
        this.operator = properties.localOperator();
    }

    @GetMapping("/orders")
    public Map<String, Object> listOrders(
            @RequestParam(required = false) String status,
            @RequestParam(defaultValue = "0") int page,
            @RequestParam(defaultValue = "20") int size) {
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
            @PathVariable String id,
            @RequestHeader(name = "Idempotency-Key") String idempotencyKey,
            @RequestBody OrderTransitionRequest body) {
        var value = orders.transition(
                id,
                body.action(),
                body.received_quantity(),
                body.arrival_date(),
                body.notes(),
                operator,
                idempotencyKey);
        insightsCache.evictAll();
        return value;
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
            @PathVariable String id,
            @RequestHeader(name = "Idempotency-Key") String idempotencyKey,
            @RequestBody SettlementTransitionRequest body) {
        var value = settlements.transition(
                id, body.action(), body.paid_at(), body.notes(), operator, idempotencyKey);
        insightsCache.evictAll();
        return value;
    }
}
