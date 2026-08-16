package com.caijiatai.procurement.invoice;

import com.caijiatai.procurement.agent.AgentCommand;
import com.caijiatai.procurement.agent.AgentCommandRepository;
import com.caijiatai.procurement.agent.IdempotencyRecord;
import com.caijiatai.procurement.agent.IdempotencyRecordRepository;
import com.caijiatai.procurement.api.ApiException;
import com.caijiatai.procurement.artifact.ArtifactStore;
import com.caijiatai.procurement.cache.InsightsCache;
import com.caijiatai.procurement.comparison.ComparisonSnapshotRepository;
import com.caijiatai.procurement.config.AppProperties;
import com.caijiatai.procurement.order.OrderRepository;
import com.caijiatai.procurement.order.PurchaseOrder;
import com.caijiatai.procurement.platform.statemachine.StateMachine;
import com.caijiatai.procurement.report.AuditEvent;
import com.caijiatai.procurement.report.AuditEventRepository;
import com.caijiatai.procurement.task.ProcurementDtos;
import com.caijiatai.procurement.task.ProcurementTask;
import com.caijiatai.procurement.task.ProcurementTaskRepository;
import java.io.ByteArrayInputStream;
import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.OptimisticLockingFailureException;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.multipart.MultipartFile;

/**
 * 发票服务（P3-1 旗舰）：上传 → Agent 解析 → Java 三单匹配 → 差异挂起/核销/作废。
 *
 * <p>纪律：匹配判断、金额计算、状态流转全部 Java 确定性（ThreeWayMatcher）；
 * Python 只解析字段与生成差异解释（模式 C）；付款前要求订单发票已匹配/核销。
 */
@Service
public class InvoiceService {
    private static final Logger log = LoggerFactory.getLogger(InvoiceService.class);
    private static final long MAX_FILE_BYTES = 5L * 1024 * 1024;

    private final InvoiceRepository invoices;
    private final OrderRepository orders;
    private final ProcurementTaskRepository tasks;
    private final ComparisonSnapshotRepository snapshots;
    private final AgentCommandRepository commands;
    private final IdempotencyRecordRepository idempotency;
    private final ArtifactStore artifactStore;
    private final AuditEventRepository audit;
    private final InsightsCache insightsCache;
    private final StateMachine<InvoiceStatus, InvoiceEvent> invoiceMachine;
    private final String operator;

    public InvoiceService(
            InvoiceRepository invoices,
            OrderRepository orders,
            ProcurementTaskRepository tasks,
            ComparisonSnapshotRepository snapshots,
            AgentCommandRepository commands,
            IdempotencyRecordRepository idempotency,
            ArtifactStore artifactStore,
            AuditEventRepository audit,
            InsightsCache insightsCache,
            StateMachine<InvoiceStatus, InvoiceEvent> invoiceMachine,
            AppProperties properties) {
        this.invoices = invoices;
        this.orders = orders;
        this.tasks = tasks;
        this.snapshots = snapshots;
        this.commands = commands;
        this.idempotency = idempotency;
        this.artifactStore = artifactStore;
        this.audit = audit;
        this.insightsCache = insightsCache;
        this.invoiceMachine = invoiceMachine;
        this.operator = properties.localOperator();
    }

    // ------------------------------------------------------------------
    // 上传 + 解析命令
    // ------------------------------------------------------------------

    @Transactional
    public ProcurementDtos.OperationAccepted upload(String orderId, MultipartFile file, String idempotencyKey) {
        var order = orders.findById(orderId)
                .orElseThrow(() -> notFound("order_not_found", "未找到采购订单"));
        if (file == null || file.isEmpty() || file.getSize() > MAX_FILE_BYTES) {
            throw bad("invalid_invoice_file", "发票文件不能为空且不超过 5MB");
        }
        try {
            var artifact = artifactStore.store(
                    "invoice_original",
                    orderId,
                    file.getOriginalFilename(),
                    file.getContentType(),
                    new ByteArrayInputStream(file.getBytes()),
                    Map.of("source", "invoice_upload"));
            var payload = new LinkedHashMap<String, Object>();
            payload.put("artifact_id", artifact.getId());
            payload.put("filename", file.getOriginalFilename());
            payload.put("sha256", artifact.getSha256());
            payload.put("order_id", orderId);
            payload.put("order_no", order.getOrderNo());
            payload.put("order_quantity", order.getQuantity() == null ? null : order.getQuantity().toPlainString());
            payload.put("order_landed_total",
                    order.getLandedTotal() == null ? null : order.getLandedTotal().toPlainString());
            payload.put("expected_tax_rate",
                    expectedTaxRate(order) == null ? null : expectedTaxRate(order).toPlainString());
            var payloadSha = com.caijiatai.procurement.agent.CanonicalJson.sha256(payload);
            var key = normalizeIdempotencyKey(idempotencyKey, payloadSha);
            var existing = idempotency.findById(new IdempotencyRecord.Key("invoice_upload", key));
            if (existing.isPresent()) {
                var op = commands.findById(existing.get().getOperationId()).orElseThrow();
                return accepted(op, "已存在相同发票上传操作");
            }
            var command = commands.save(AgentCommand.accept(
                    "parse_invoice", orderId, 1, 0, payload));
            idempotency.save(IdempotencyRecord.reserve("invoice_upload", key, payloadSha, command.getOperationId()));
            audit.save(AuditEvent.forBusiness(
                    "invoice", orderId, "invoice_upload_accepted", operator,
                    Map.of("operation_id", command.getOperationId(), "artifact_id", artifact.getId())));
            return accepted(command, null);
        } catch (java.io.IOException error) {
            throw new RuntimeException("发票文件读取失败", error);
        }
    }

    // ------------------------------------------------------------------
    // Agent 结果应用：登记 + 三单匹配
    // ------------------------------------------------------------------

    @SuppressWarnings("unchecked")
    @Transactional
    public void applyParseResult(AgentCommand command, Map<String, Object> result) {
        var orderId = command.getAggregateId();
        var order = orders.findById(orderId)
                .orElseThrow(() -> notFound("order_not_found", "未找到采购订单"));
        var rawInvoice = result.get("invoice");
        if (!(rawInvoice instanceof Map<?, ?> parsed)) {
            throw new ApiException(HttpStatus.CONFLICT, "invalid_invoice_parse", "Agent 未返回可验证的发票字段");
        }
        var map = (Map<String, Object>) parsed;
        var invoiceNo = text(map.get("invoice_no"));
        var totalAmount = decimal(map.get("total_amount"));
        if (invoiceNo.isBlank() || totalAmount == null) {
            throw new ApiException(HttpStatus.CONFLICT, "invalid_invoice_parse", "发票号码与价税合计为必填");
        }
        invoices.findByInvoiceNo(invoiceNo).ifPresent(existing -> {
            throw new ApiException(HttpStatus.CONFLICT, "invoice_already_registered",
                    "发票号码 " + invoiceNo + " 已登记");
        });
        var invoice = Invoice.register(
                orderId,
                invoiceNo,
                text(map.get("invoice_code")),
                date(map.get("issue_date")),
                decimal(map.get("quantity")),
                textOrNull(map.get("unit")),
                decimal(map.get("unit_price")),
                decimal(map.get("amount_excluding_tax")),
                decimal(map.get("tax_amount")),
                totalAmount,
                decimal(map.get("tax_rate")),
                text(map.get("supplier_name")).isBlank()
                        ? order.getSupplierName() : text(map.get("supplier_name")),
                text(command.getPayload().get("artifact_id")),
                text(command.getPayload().get("sha256")),
                text(map.get("parser_version")));
        invoices.saveAndFlush(invoice);
        match(invoice, order, Map.of("source", "parse", "actor", "agent"));
        audit.save(AuditEvent.forBusiness(
                "invoice", invoice.getId(), "invoice_registered", operator,
                Map.of("invoice_no", invoiceNo, "order_id", orderId, "parser_version", invoice.getParserVersion())));
        insightsCache.evictAll();
    }

    /** 差异解释结果（explain_invoice_diff 命令）落库为可审计解释记录。 */
    @SuppressWarnings("unchecked")
    @Transactional
    public void applyExplanation(AgentCommand command, Map<String, Object> result) {
        var invoice = invoices.findById(text(command.getPayload().get("invoice_id")))
                .orElseThrow(() -> notFound("invoice_not_found", "未找到发票"));
        var raw = result.get("explanation");
        if (!(raw instanceof Map<?, ?> explanation)) {
            return; // 解释缺失不阻断（非阻塞参与点）
        }
        invoice.applyExplanation((Map<String, Object>) explanation);
        invoices.save(invoice);
        audit.save(AuditEvent.forBusiness(
                "invoice", invoice.getId(), "invoice_explained", "agent",
                Map.of("invoice_no", invoice.getInvoiceNo())));
    }

    // ------------------------------------------------------------------
    // 三单匹配（Java 权威）
    // ------------------------------------------------------------------

    @Transactional
    public void match(Invoice invoice, PurchaseOrder order, Map<String, Object> context) {
        var purchase = new ThreeWayMatcher.PurchaseSide(
                order.getQuantity(), order.getLandedTotal(), expectedTaxRate(order));
        var result = ThreeWayMatcher.match(purchase, invoice);
        invoice.applyMatchResult(result.matched(), result.toMap(), null);
        if (result.matched()) {
            audit.save(AuditEvent.forBusiness(
                    "invoice", invoice.getId(), "invoice_matched", "system",
                    Map.of("invoice_no", invoice.getInvoiceNo(), "order_no", order.getOrderNo())));
        } else {
            audit.save(AuditEvent.forBusiness(
                    "invoice", invoice.getId(), "invoice_diff_hold", "system",
                    Map.of("invoice_no", invoice.getInvoiceNo(), "diff_count", result.diffs().size())));
            requestExplanation(invoice, result, order);
        }
        invoices.save(invoice);
    }

    /** 模式 C：Java 产出结构化差异 → Python 生成自然语言原因与建议（异步非阻塞）。 */
    private void requestExplanation(Invoice invoice, ThreeWayMatcher.MatchResult result, PurchaseOrder order) {
        var payload = new LinkedHashMap<String, Object>();
        payload.put("invoice_id", invoice.getId());
        payload.put("invoice_no", invoice.getInvoiceNo());
        payload.put("order_no", order.getOrderNo());
        payload.put("supplier_name", invoice.getSupplierName());
        payload.put("diffs", result.diffs().stream().map(diff -> Map.of(
                "field", diff.field(),
                "expected", diff.expected(),
                "actual", diff.actual(),
                "diff", diff.diff())).toList());
        commands.save(AgentCommand.accept(
                "explain_invoice_diff", invoice.getOrderId(), 1, 0, payload));
    }

    private BigDecimal expectedTaxRate(PurchaseOrder order) {
        var task = tasks.findById(order.getTaskId()).orElse(null);
        if (task == null || task.getCurrentSnapshotId() == null) {
            return null;
        }
        var snapshot = snapshots.findByIdAndTaskId(task.getCurrentSnapshotId(), task.getId()).orElse(null);
        if (snapshot == null || !(snapshot.getResult().get("quotes") instanceof List<?> rawQuotes)) {
            return null;
        }
        var quoteId = task.getApprovedQuoteId();
        if (quoteId == null) {
            return null;
        }
        for (var raw : rawQuotes) {
            if (!(raw instanceof Map<?, ?> quote)) {
                continue;
            }
            if (!quoteId.equals(quote.get("quote_id"))
                    || !(quote.get("commercial") instanceof Map<?, ?> commercial)) {
                continue;
            }
            var rate = commercial.get("tax_rate");
            if (rate == null) {
                return null;
            }
            try {
                return new BigDecimal(String.valueOf(rate));
            } catch (NumberFormatException ignored) {
                return null;
            }
        }
        return null;
    }

    // ------------------------------------------------------------------
    // 查询
    // ------------------------------------------------------------------

    @Transactional(readOnly = true)
    public Map<String, Object> list(String status, String orderId, int page, int size) {
        var pageable = PageRequest.of(Math.max(0, page), Math.min(100, Math.max(1, size)));
        Page<Invoice> query;
        if (orderId != null && !orderId.isBlank()) {
            query = invoices.findByOrderIdOrderByCreatedAtDesc(orderId, pageable);
        } else if (status == null || status.isBlank()) {
            query = invoices.findAllByOrderByCreatedAtDesc(pageable);
        } else {
            query = invoices.findByStatusOrderByCreatedAtDesc(
                    status.strip().toUpperCase(java.util.Locale.ROOT), pageable);
        }
        var items = query.getContent().stream().map(invoice -> view(invoice, false)).toList();
        var value = new LinkedHashMap<String, Object>();
        value.put("items", items);
        value.put("page", query.getNumber());
        value.put("size", query.getSize());
        value.put("total", query.getTotalElements());
        return value;
    }

    @Transactional(readOnly = true)
    public Map<String, Object> detail(String id) {
        var invoice = invoices.findById(id)
                .orElseThrow(() -> notFound("invoice_not_found", "未找到发票"));
        return view(invoice, true);
    }

    // ------------------------------------------------------------------
    // 差异挂起处理：作废 / 手工改单 / 强制通过（allow-once）/ 核销
    // ------------------------------------------------------------------

    @Transactional
    public Map<String, Object> action(
            String id, String action, InvoiceDtos.InvoiceAction body, String actor) {
        var invoice = invoices.lockById(id)
                .orElseThrow(() -> notFound("invoice_not_found", "未找到发票"));
        var from = InvoiceStatus.fromWire(invoice.getStatus());
        try {
            switch (action) {
                case "void" -> {
                    if (!invoiceMachine.can(from, InvoiceEvent.VOID)) {
                        throw conflict("invalid_invoice_transition",
                                "发票状态 " + from + " 不允许作废");
                    }
                    if (body.notes() == null || body.notes().isBlank()) {
                        throw bad("void_requires_notes", "作废发票必须填写原因");
                    }
                    invoiceMachine.transition(id, from, InvoiceEvent.VOID, Map.of("notes", body.notes()));
                    invoice.voidInvoice(body.notes());
                    audit.save(AuditEvent.forBusiness(
                            "invoice", invoice.getId(), "invoice_voided", actor,
                            Map.of("invoice_no", invoice.getInvoiceNo(), "reason", body.notes())));
                }
                case "correct" -> {
                    if (!invoiceMachine.can(from, InvoiceEvent.MATCH)
                            && !invoiceMachine.can(from, InvoiceEvent.HOLD)) {
                        throw conflict("invalid_invoice_transition",
                                "发票状态 " + from + " 不允许手工改单");
                    }
                    invoice.applyHumanCorrection(
                            body.quantity(), body.unitPrice(), body.amountExcludingTax(),
                            body.taxAmount(), body.totalAmount(), body.taxRate(), body.notes());
                    audit.save(AuditEvent.forBusiness(
                            "invoice", invoice.getId(), "invoice_manual_corrected", actor,
                            Map.of("invoice_no", invoice.getInvoiceNo())));
                    var order = orders.findById(invoice.getOrderId()).orElseThrow();
                    match(invoice, order, Map.of("source", "correction", "actor", actor));
                }
                case "force_match" -> {
                    if (!invoiceMachine.can(from, InvoiceEvent.FORCE_MATCH)) {
                        throw conflict("invalid_invoice_transition",
                                "只有差异挂起（DIFF_HOLD）的发票允许强制通过");
                    }
                    if (!Boolean.TRUE.equals(body.confirmed()) || body.notes() == null || body.notes().isBlank()) {
                        throw bad("force_match_requires_confirmation", "强制通过必须勾选确认并填写人工备注");
                    }
                    // allow-once 语义：一次确认后即 MATCHED，不可重复触发
                    invoiceMachine.transition(id, from, InvoiceEvent.FORCE_MATCH, Map.of("notes", body.notes()));
                    invoice.forceMatch(body.notes());
                    audit.save(AuditEvent.forBusiness(
                            "invoice", invoice.getId(), "invoice_force_matched", actor,
                            Map.of("invoice_no", invoice.getInvoiceNo(), "notes", body.notes())));
                }
                case "reconcile" -> {
                    if (!invoiceMachine.can(from, InvoiceEvent.RECONCILE)) {
                        throw conflict("invalid_invoice_transition",
                                "只有已匹配（MATCHED）的发票允许核销");
                    }
                    invoiceMachine.transition(id, from, InvoiceEvent.RECONCILE, Map.of());
                    invoice.reconcile();
                    audit.save(AuditEvent.forBusiness(
                            "invoice", invoice.getId(), "invoice_reconciled", actor,
                            Map.of("invoice_no", invoice.getInvoiceNo())));
                }
                default -> throw bad("invalid_invoice_action", "发票操作只能是 void / correct / force_match / reconcile");
            }
        } catch (OptimisticLockingFailureException error) {
            throw conflict("invoice_concurrent_modification", "发票已被其他操作修改，请刷新后重试");
        }
        var saved = invoices.saveAndFlush(invoice);
        insightsCache.evictAll();
        return view(saved, true);
    }

    /** 付款联动：订单存在未匹配/未核销发票时拒绝付款（409）。 */
    public boolean hasUnresolvedInvoices(String orderId) {
        return invoices.countByOrderIdAndStatusIn(orderId,
                List.of(InvoiceStatus.REGISTERED.wireValue(), InvoiceStatus.DIFF_HOLD.wireValue())) > 0;
    }

    // ------------------------------------------------------------------
    // 视图
    // ------------------------------------------------------------------

    private Map<String, Object> view(Invoice invoice, boolean withComparison) {
        var value = new LinkedHashMap<String, Object>();
        value.put("id", invoice.getId());
        value.put("order_id", invoice.getOrderId());
        value.put("invoice_code", invoice.getInvoiceCode());
        value.put("invoice_no", invoice.getInvoiceNo());
        value.put("issue_date", invoice.getIssueDate() == null ? null : invoice.getIssueDate().toString());
        value.put("quantity", plain(invoice.getQuantity()));
        value.put("unit", invoice.getUnit());
        value.put("unit_price", plain(invoice.getUnitPrice()));
        value.put("amount_excluding_tax", plain(invoice.getAmountExcludingTax()));
        value.put("tax_amount", plain(invoice.getTaxAmount()));
        value.put("total_amount", plain(invoice.getTotalAmount()));
        value.put("tax_rate", plain(invoice.getTaxRate()));
        value.put("supplier_name", invoice.getSupplierName());
        value.put("parser_version", invoice.getParserVersion());
        value.put("status", InvoiceStatus.fromWire(invoice.getStatus()).name());
        value.put("match_result", invoice.getMatchResult());
        value.put("match_explanation", invoice.getMatchExplanation());
        value.put("notes", invoice.getNotes());
        value.put("version", invoice.getVersion());
        value.put("created_at", invoice.getCreatedAt().toString());
        value.put("updated_at", invoice.getUpdatedAt().toString());
        value.put("matched_at", invoice.getMatchedAt() == null ? null : invoice.getMatchedAt().toString());
        value.put("reconciled_at", invoice.getReconciledAt() == null ? null : invoice.getReconciledAt().toString());
        var order = orders.findById(invoice.getOrderId()).orElse(null);
        value.put("order_no", order == null ? null : order.getOrderNo());
        value.put("task_reference", order == null ? null : taskReference(order));
        value.put("order_quantity", order == null ? null : plain(order.getQuantity()));
        value.put("order_received_quantity", order == null ? null : plain(order.getReceivedQuantity()));
        value.put("order_landed_total", order == null ? null : plain(order.getLandedTotal()));
        value.put("expected_tax_rate", order == null ? null : plain(expectedTaxRate(order)));
        if (withComparison && invoice.getMatchResult() != null) {
            var comparison = new LinkedHashMap<String, Object>();
            var po = new LinkedHashMap<String, Object>();
            po.put("quantity", order == null ? null : plain(order.getQuantity()));
            po.put("received_quantity", order == null ? null : plain(order.getReceivedQuantity()));
            po.put("landed_total", order == null ? null : plain(order.getLandedTotal()));
            comparison.put("po", po);
            var grn = new LinkedHashMap<String, Object>();
            grn.put("received_quantity", order == null ? null : plain(order.getReceivedQuantity()));
            grn.put("received_at", order == null ? null : String.valueOf(order.getArrivalDate()));
            comparison.put("grn", grn);
            var invoiceSide = new LinkedHashMap<String, Object>();
            invoiceSide.put("quantity", plain(invoice.getQuantity()));
            invoiceSide.put("unit_price", plain(invoice.getUnitPrice()));
            invoiceSide.put("total_amount", plain(invoice.getTotalAmount()));
            invoiceSide.put("tax_rate", plain(invoice.getTaxRate()));
            comparison.put("invoice", invoiceSide);
            value.put("three_way", comparison);
        }
        return value;
    }

    private String taskReference(PurchaseOrder order) {
        return tasks.findById(order.getTaskId()).map(ProcurementTask::getReference).orElse(null);
    }

    // ------------------------------------------------------------------
    // 工具
    // ------------------------------------------------------------------

    private String plain(BigDecimal value) {
        return value == null ? null : value.stripTrailingZeros().toPlainString();
    }

    private static String text(Object value) {
        return value == null ? "" : String.valueOf(value).trim();
    }

    private static String textOrNull(Object value) {
        var text = text(value);
        return text.isEmpty() ? null : text;
    }

    private static BigDecimal decimal(Object value) {
        if (value == null) {
            return null;
        }
        try {
            return new BigDecimal(String.valueOf(value));
        } catch (NumberFormatException error) {
            throw new ApiException(HttpStatus.CONFLICT, "invalid_invoice_field",
                    "发票字段不是有效数值: " + value);
        }
    }

    private static LocalDate date(Object value) {
        var text = text(value);
        if (text.isEmpty()) {
            return null;
        }
        try {
            return LocalDate.parse(text);
        } catch (java.time.format.DateTimeParseException error) {
            return null;
        }
    }

    private static String normalizeIdempotencyKey(String key, String payloadSha) {
        return key == null || key.isBlank() ? payloadSha : key;
    }

    private static ProcurementDtos.OperationAccepted accepted(AgentCommand command, String message) {
        var value = new LinkedHashMap<String, Object>();
        value.put("operation_id", command.getOperationId());
        value.put("purchase_request_id", command.getAggregateId());
        value.put("session_id", null);
        value.put("run_id", null);
        if (message != null) {
            value.put("message", message);
        }
        return new ProcurementDtos.OperationAccepted(
                command.getOperationId(), command.getAggregateId(), null, null,
                "accepted", "/api/procurement/operations/" + command.getOperationId());
    }

    private static com.caijiatai.procurement.api.ApiException bad(String code, String message) {
        return new com.caijiatai.procurement.api.ApiException(HttpStatus.BAD_REQUEST, code, message);
    }

    private static com.caijiatai.procurement.api.ApiException conflict(String code, String message) {
        return new com.caijiatai.procurement.api.ApiException(HttpStatus.CONFLICT, code, message);
    }

    private static com.caijiatai.procurement.api.ApiException notFound(String code, String message) {
        return new com.caijiatai.procurement.api.ApiException(HttpStatus.NOT_FOUND, code, message);
    }
}
