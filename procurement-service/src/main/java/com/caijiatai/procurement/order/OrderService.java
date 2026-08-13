package com.caijiatai.procurement.order;

import com.caijiatai.procurement.api.ApiException;
import com.caijiatai.procurement.artifact.BusinessArtifact;
import com.caijiatai.procurement.artifact.BusinessArtifactRepository;
import com.caijiatai.procurement.comparison.ComparisonSnapshotRepository;
import com.caijiatai.procurement.platform.statemachine.StateMachine;
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
import java.time.LocalDate;
import java.time.ZoneOffset;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.dao.OptimisticLockingFailureException;
import org.springframework.data.domain.PageRequest;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * 采购订单服务（K2+K8，冻结设计 4.3/4.4）。
 *
 * <p>惰性派生 tradeoff：订单派生独立于审批证据链，读接口带写副作用；
 * 用 purchase_order.UNIQUE(task_id) 保证并发幂等（DuplicateKeyException 按已存在吞掉）。
 */
@Service
public class OrderService {
    private static final Logger log = LoggerFactory.getLogger(OrderService.class);
    private static final List<String> ORDER_ARTIFACT_KINDS =
            List.of("purchase_order_draft", "supplier_confirmation_email");

    private final OrderRepository orders;
    private final SettlementRepository settlements;
    private final ProcurementTaskRepository tasks;
    private final ProcurementQuoteRepository quotes;
    private final ComparisonSnapshotRepository snapshots;
    private final BusinessArtifactRepository artifacts;
    private final AuditEventRepository audit;
    private final StateMachine<OrderStatus, OrderEvent> orderMachine;

    public OrderService(
            OrderRepository orders,
            SettlementRepository settlements,
            ProcurementTaskRepository tasks,
            ProcurementQuoteRepository quotes,
            ComparisonSnapshotRepository snapshots,
            BusinessArtifactRepository artifacts,
            AuditEventRepository audit,
            StateMachine<OrderStatus, OrderEvent> orderMachine) {
        this.orders = orders;
        this.settlements = settlements;
        this.tasks = tasks;
        this.quotes = quotes;
        this.snapshots = snapshots;
        this.artifacts = artifacts;
        this.audit = audit;
        this.orderMachine = orderMachine;
    }

    // ---------- 惰性派生（4.4） ----------

    /**
     * 已批准任务 → 订单（幂等）。任务 approved 且无对应订单时才派生；
     * 并发双写由 UNIQUE(task_id) 拦截，DuplicateKeyException 按已存在吞掉并返回既有订单。
     */
    @Transactional
    public PurchaseOrder ensureOrderForApprovedTask(ProcurementTask task) {
        if (!"approved".equals(task.getStatus())) {
            return null;
        }
        var existing = orders.findByTaskId(task.getId());
        if (existing.isPresent()) {
            return existing.get();
        }
        var quote = approvedQuote(task);
        if (quote == null) {
            log.warn("已批准任务缺少批准报价，跳过订单派生：{}", task.getReference());
            return null;
        }
        var landedTotal = landedTotal(task, quote.getId());
        var order = PurchaseOrder.derive(
                task.getId(),
                "PO-" + task.getReference(),
                quote.getSupplierName(),
                task.getItemName(),
                task.getQuantity(),
                task.getUnit(),
                landedTotal);
        try {
            var saved = orders.saveAndFlush(order);
            var payload = new LinkedHashMap<String, Object>();
            payload.put("order_id", saved.getId());
            payload.put("order_no", saved.getOrderNo());
            payload.put("landed_total", landedTotal == null ? null : landedTotal.toPlainString());
            audit.save(AuditEvent.create(
                    task.getId(), quote.getId(), task.getAnalysisRunId(), "order_created", "system",
                    payload));
            return saved;
        } catch (DataIntegrityViolationException error) {
            // UNIQUE(task_id)：并发惰性派生只允许一条，按已存在幂等吞掉
            return orders.findByTaskId(task.getId()).orElseThrow(() -> error);
        }
    }

    private ProcurementQuote approvedQuote(ProcurementTask task) {
        var quoteId = task.getApprovedQuoteId();
        if (quoteId == null || quoteId.isBlank()) {
            return null;
        }
        return quotes.findByIdAndTaskId(quoteId, task.getId()).orElse(null);
    }

    /** 从比价快照取批准报价的到货总价（基准币种）；缺失时返回 null（收货派生对账时会被拒绝）。 */
    private BigDecimal landedTotal(ProcurementTask task, String quoteId) {
        if (task.getCurrentSnapshotId() == null) {
            return null;
        }
        var snapshot = snapshots.findByIdAndTaskId(task.getCurrentSnapshotId(), task.getId()).orElse(null);
        if (snapshot == null || !(snapshot.getResult().get("quotes") instanceof List<?> rawQuotes)) {
            return null;
        }
        for (var raw : rawQuotes) {
            if (!(raw instanceof Map<?, ?> quote)) {
                continue;
            }
            if (!quoteId.equals(quote.get("quote_id")) || !(quote.get("cost") instanceof Map<?, ?> cost)) {
                continue;
            }
            var value = cost.get("landed_total_base");
            if (value != null) {
                try {
                    return new BigDecimal(String.valueOf(value));
                } catch (NumberFormatException ignored) {
                    return null;
                }
            }
        }
        return null;
    }

    /** 查询前惰性派生全部已批准任务订单（冻结触发点：GET /api/procurement/orders）。 */
    @Transactional
    public void reconcileApprovedTasks() {
        for (var task : tasks.findByStatusOrderByUpdatedAtDesc("approved")) {
            ensureOrderForApprovedTask(task);
        }
    }

    // ---------- 查询 ----------

    @Transactional(readOnly = true)
    public Map<String, Object> list(String status, int page, int size) {
        var pageable = PageRequest.of(Math.max(0, page), Math.min(100, Math.max(1, size)));
        var query = status == null || status.isBlank()
                ? orders.findAllByOrderByCreatedAtDesc(pageable)
                : orders.findByStatusOrderByCreatedAtDesc(status.strip().toUpperCase(java.util.Locale.ROOT), pageable);
        var items = query.getContent().stream().map(this::view).toList();
        var value = new LinkedHashMap<String, Object>();
        value.put("items", items);
        value.put("page", query.getNumber());
        value.put("size", query.getSize());
        value.put("total", query.getTotalElements());
        return value;
    }

    @Transactional(readOnly = true)
    public Map<String, Object> detail(String id) {
        var order = orders.findById(id)
                .orElseThrow(() -> notFound("order_not_found", "未找到采购订单"));
        return view(order);
    }

    // ---------- 状态流转（4.3） ----------

    /**
     * 订单流转：ship / receive / close。
     * 非法流转 409 invalid_order_transition；版本冲突 409 order_concurrent_modification；
     * 超收 409 quantity_exceeded；收货派生对账时 landed_total 缺失 409 settlement_requires_cost。
     */
    @Transactional
    public Map<String, Object> transition(
            String id, String action, BigDecimal receivedQuantity, Instant arrivalDate,
            String notes, String actor) {
        var order = orders.lockById(id)
                .orElseThrow(() -> notFound("order_not_found", "未找到采购订单"));
        OrderEvent event;
        try {
            event = OrderEvent.fromAction(action);
        } catch (RuntimeException error) {
            throw bad("invalid_order_action", "订单操作只能是 ship / receive / close");
        }
        var from = OrderStatus.fromWire(order.getStatus());
        if (!orderMachine.can(from, event)) {
            throw conflict("invalid_order_transition",
                    "订单状态 " + from + " 不允许执行 " + event + " 操作");
        }
        if (event == OrderEvent.RECEIVE) {
            if (receivedQuantity == null || arrivalDate == null) {
                throw bad("receive_required_fields", "收货必须填写收货数量与到货日期");
            }
            if (receivedQuantity.signum() <= 0) {
                throw bad("invalid_received_quantity", "收货数量必须大于 0");
            }
            if (receivedQuantity.compareTo(order.getQuantity()) > 0) {
                throw conflict("quantity_exceeded",
                        "收货数量 " + receivedQuantity.toPlainString()
                                + " 超过订单数量 " + order.getQuantity().toPlainString());
            }
            if (order.getLandedTotal() == null) {
                throw conflict("settlement_requires_cost", "订单缺少到货总价，无法派生对账单，请先补录成本");
            }
        }
        if (event == OrderEvent.CLOSE && from == OrderStatus.RECEIVED) {
            // 已收货关闭=完成：对账单已派生，正常流转
        }
        var args = new LinkedHashMap<String, Object>();
        args.put("order_id", id);
        args.put("action", event.name());
        args.put("notes", notes == null ? "" : notes);
        var target = orderMachine.transition(id, from, event, args);
        try {
            if (event == OrderEvent.RECEIVE) {
                order.receive(receivedQuantity, arrivalDate, notes);
            } else if (event == OrderEvent.CLOSE) {
                order.close(notes);
            } else {
                order.ship();
            }
            orders.saveAndFlush(order);
        } catch (OptimisticLockingFailureException error) {
            throw conflict("order_concurrent_modification", "订单已被其他操作修改，请刷新后重试");
        }
        var transitionPayload = new LinkedHashMap<String, Object>();
        transitionPayload.put("order_id", id);
        transitionPayload.put("order_no", order.getOrderNo());
        transitionPayload.put("action", event.name());
        transitionPayload.put("from", from.wireValue());
        transitionPayload.put("to", target.wireValue());
        transitionPayload.put("received_quantity",
                receivedQuantity == null ? null : receivedQuantity.toPlainString());
        transitionPayload.put("arrival_date", arrivalDate == null ? null : arrivalDate.toString());
        transitionPayload.put("notes", notes == null ? "" : notes);
        audit.save(AuditEvent.create(
                order.getTaskId(), null, null, "order_transitioned", actor, transitionPayload));
        if (event == OrderEvent.RECEIVE) {
            deriveSettlement(order, actor);
        }
        return detail(id);
    }

    // ---------- 对账派生（K8） ----------

    @Transactional
    public PurchaseSettlement deriveSettlement(PurchaseOrder order, String actor) {
        if (order.getLandedTotal() == null) {
            throw conflict("settlement_requires_cost", "订单缺少到货总价，无法派生对账单");
        }
        var existing = settlements.findByOrderId(order.getId());
        if (existing.isPresent()) {
            return existing.get();
        }
        var settlementNo = "ST-" + LocalDate.now(ZoneOffset.UTC).toString().replace("-", "")
                + "-" + order.getId().substring(0, 6).toUpperCase(java.util.Locale.ROOT);
        var settlement = settlements.saveAndFlush(PurchaseSettlement.derive(
                order.getId(), settlementNo, order.getSupplierName(), order.getLandedTotal()));
        audit.save(AuditEvent.create(
                order.getTaskId(), null, null, "settlement_created", actor,
                Map.of("settlement_id", settlement.getId(), "settlement_no", settlement.getSettlementNo(),
                        "order_id", order.getId(), "total_amount", settlement.getTotalAmount().toPlainString())));
        return settlement;
    }

    // ---------- 视图 ----------

    private Map<String, Object> view(PurchaseOrder order) {
        var value = new LinkedHashMap<String, Object>();
        value.put("id", order.getId());
        value.put("task_id", order.getTaskId());
        value.put("order_no", order.getOrderNo());
        value.put("supplier_name", order.getSupplierName());
        value.put("item_name", order.getItemName());
        value.put("quantity", order.getQuantity().toPlainString());
        value.put("unit", order.getUnit());
        value.put("landed_total", order.getLandedTotal() == null ? null : order.getLandedTotal().toPlainString());
        value.put("status", order.getStatus());
        value.put("received_quantity", order.getReceivedQuantity() == null
                ? null : order.getReceivedQuantity().toPlainString());
        value.put("arrival_date", order.getArrivalDate() == null ? null : order.getArrivalDate().toString());
        value.put("notes", order.getNotes());
        value.put("version", order.getVersion());
        value.put("created_at", order.getCreatedAt().toString());
        value.put("updated_at", order.getUpdatedAt().toString());
        var task = tasks.findById(order.getTaskId()).orElse(null);
        value.put("task_reference", task == null ? null : task.getReference());
        value.put("task_title", task == null ? null : task.getTitle());
        value.put("artifacts", artifacts.findByTaskIdInOrderByCreatedAtAsc(List.of(order.getTaskId())).stream()
                .filter(artifact -> ORDER_ARTIFACT_KINDS.contains(artifact.getKind()))
                .map(this::artifactView)
                .toList());
        value.put("settlement", settlementView(settlements.findByOrderId(order.getId()).orElse(null)));
        return value;
    }

    private Map<String, Object> artifactView(BusinessArtifact artifact) {
        var value = new LinkedHashMap<String, Object>();
        value.put("id", artifact.getId());
        value.put("kind", artifact.getKind());
        value.put("filename", artifact.getFilename());
        value.put("content_type", artifact.getContentType());
        value.put("size_bytes", artifact.getSizeBytes());
        value.put("sha256", artifact.getSha256());
        value.put("created_at", artifact.getCreatedAt().toString());
        return value;
    }

    private Map<String, Object> settlementView(PurchaseSettlement settlement) {
        if (settlement == null) {
            return null;
        }
        var value = new LinkedHashMap<String, Object>();
        value.put("id", settlement.getId());
        value.put("settlement_no", settlement.getSettlementNo());
        value.put("total_amount", settlement.getTotalAmount().toPlainString());
        value.put("status", settlement.getStatus());
        value.put("paid_at", settlement.getPaidAt() == null ? null : settlement.getPaidAt().toString());
        value.put("notes", settlement.getNotes());
        value.put("version", settlement.getVersion());
        value.put("created_at", settlement.getCreatedAt().toString());
        value.put("updated_at", settlement.getUpdatedAt().toString());
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
