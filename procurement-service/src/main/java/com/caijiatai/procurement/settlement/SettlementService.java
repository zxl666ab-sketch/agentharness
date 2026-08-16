package com.caijiatai.procurement.settlement;

import com.caijiatai.procurement.api.ApiException;
import com.caijiatai.procurement.invoice.InvoiceService;
import com.caijiatai.procurement.order.OrderRepository;
import com.caijiatai.procurement.platform.statemachine.StateMachine;
import com.caijiatai.procurement.report.AuditEvent;
import com.caijiatai.procurement.report.AuditEventRepository;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.dao.OptimisticLockingFailureException;
import org.springframework.data.domain.PageRequest;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * 对账付款服务（K8，冻结设计 4.3）：UNSETTLED --settle--> SETTLED --pay--> PAID。
 * 每次流转写审计事件 settlement_settled / settlement_paid。
 * P3-1：付款前校验订单发票三单匹配状态（未匹配/差异挂起 → 409）。
 */
@Service
public class SettlementService {
    private final SettlementRepository settlements;
    private final OrderRepository orders;
    private final AuditEventRepository audit;
    private final StateMachine<SettlementStatus, SettlementEvent> settlementMachine;
    private final InvoiceService invoices;

    public SettlementService(
            SettlementRepository settlements,
            OrderRepository orders,
            AuditEventRepository audit,
            StateMachine<SettlementStatus, SettlementEvent> settlementMachine,
            InvoiceService invoices) {
        this.settlements = settlements;
        this.orders = orders;
        this.audit = audit;
        this.settlementMachine = settlementMachine;
        this.invoices = invoices;
    }

    @Transactional(readOnly = true)
    public Map<String, Object> list(String status, int page, int size) {
        var pageable = PageRequest.of(Math.max(0, page), Math.min(100, Math.max(1, size)));
        var query = status == null || status.isBlank()
                ? settlements.findAllByOrderByCreatedAtDesc(pageable)
                : settlements.findByStatusOrderByCreatedAtDesc(status.strip().toUpperCase(java.util.Locale.ROOT), pageable);
        var items = query.getContent().stream().map(this::view).toList();
        var value = new LinkedHashMap<String, Object>();
        value.put("items", items);
        value.put("page", query.getNumber());
        value.put("size", query.getSize());
        value.put("total", query.getTotalElements());
        return value;
    }

    /**
     * 对账流转：settle / pay。
     * 非法流转 409 invalid_settlement_transition；版本冲突 409 settlement_concurrent_modification。
     */
    @Transactional
    public Map<String, Object> transition(
            String id, String action, Instant paidAt, String notes, String actor) {
        var settlement = settlements.lockById(id)
                .orElseThrow(() -> notFound("settlement_not_found", "未找到对账单"));
        SettlementEvent event;
        try {
            event = SettlementEvent.fromAction(action);
        } catch (RuntimeException error) {
            throw bad("invalid_settlement_action", "对账操作只能是 settle / pay");
        }
        var from = SettlementStatus.fromWire(settlement.getStatus());
        if (!settlementMachine.can(from, event)) {
            throw conflict("invalid_settlement_transition",
                    "对账单状态 " + from + " 不允许执行 " + event + " 操作");
        }
        if (event == SettlementEvent.PAY && paidAt == null) {
            throw bad("paid_at_required", "付款必须填写付款时间");
        }
        // P3-1 付款联动：订单存在未匹配/差异挂起发票时拒绝付款（409）
        if (event == SettlementEvent.PAY && invoices.hasUnresolvedInvoices(settlement.getOrderId())) {
            throw conflict("unmatched_invoice_blocks_payment",
                    "订单存在未匹配或差异挂起的发票，必须先完成三单匹配（或作废发票）才能付款");
        }
        var target = settlementMachine.transition(id, from, event, Map.of());
        try {
            if (event == SettlementEvent.PAY) {
                settlement.pay(paidAt, notes);
            } else {
                settlement.settle(notes);
            }
            settlements.saveAndFlush(settlement);
        } catch (OptimisticLockingFailureException error) {
            throw conflict("settlement_concurrent_modification", "对账单已被其他操作修改，请刷新后重试");
        }
        var order = orders.findById(settlement.getOrderId()).orElse(null);
        var payload = new LinkedHashMap<String, Object>();
        payload.put("settlement_id", settlement.getId());
        payload.put("settlement_no", settlement.getSettlementNo());
        payload.put("order_id", settlement.getOrderId());
        payload.put("action", event.name());
        payload.put("from", from.wireValue());
        payload.put("to", target.wireValue());
        payload.put("paid_at", paidAt == null ? null : paidAt.toString());
        payload.put("notes", notes == null ? "" : notes);
        audit.save(AuditEvent.create(
                order == null ? null : order.getTaskId(), null, null,
                SettlementEvent.PAY.equals(event) ? "settlement_paid" : "settlement_settled", actor,
                payload));
        return view(settlement);
    }

    private Map<String, Object> view(PurchaseSettlement settlement) {
        var value = new LinkedHashMap<String, Object>();
        value.put("id", settlement.getId());
        value.put("order_id", settlement.getOrderId());
        value.put("settlement_no", settlement.getSettlementNo());
        value.put("supplier_name", settlement.getSupplierName());
        value.put("total_amount", settlement.getTotalAmount().toPlainString());
        value.put("status", settlement.getStatus());
        value.put("paid_at", settlement.getPaidAt() == null ? null : settlement.getPaidAt().toString());
        value.put("notes", settlement.getNotes());
        value.put("version", settlement.getVersion());
        value.put("created_at", settlement.getCreatedAt().toString());
        value.put("updated_at", settlement.getUpdatedAt().toString());
        var order = orders.findById(settlement.getOrderId()).orElse(null);
        value.put("order_no", order == null ? null : order.getOrderNo());
        value.put("task_id", order == null ? null : order.getTaskId());
        return value;
    }

    private ApiException bad(String code, String message) {
        return new ApiException(HttpStatus.BAD_REQUEST, code, message);
    }

    private ApiException conflict(String code, String message) {
        return new ApiException(HttpStatus.CONFLICT, code, message);
    }

    private ApiException notFound(String code, String message) {
        return new ApiException(HttpStatus.NOT_FOUND, code, message);
    }
}
