package com.caijiatai.procurement.contract;

import com.caijiatai.procurement.agent.AgentCommand;
import com.caijiatai.procurement.agent.AgentCommandRepository;
import com.caijiatai.procurement.agent.IdempotencyRecord;
import com.caijiatai.procurement.agent.IdempotencyRecordRepository;
import com.caijiatai.procurement.api.ApiException;
import com.caijiatai.procurement.cache.InsightsCache;
import com.caijiatai.procurement.config.AppProperties;
import com.caijiatai.procurement.order.OrderRepository;
import com.caijiatai.procurement.order.OrderService;
import com.caijiatai.procurement.order.PurchaseOrder;
import com.caijiatai.procurement.platform.statemachine.StateMachine;
import com.caijiatai.procurement.report.AuditEvent;
import com.caijiatai.procurement.report.AuditEventRepository;
import com.caijiatai.procurement.task.ProcurementDtos;
import com.caijiatai.procurement.task.ProcurementTask;
import com.caijiatai.procurement.task.ProcurementTaskRepository;
import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.dao.OptimisticLockingFailureException;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * 合同服务（P3-2）：定标 → 草拟（AI）→ 风险提示（AI）→ 人工审批 → 生效 → 关联订单 → 执行/关闭/变更。
 *
 * <p>纪律：合同金额/交期/供应商由定标结果注入（不来自 LLM）；草拟文本中的金额/日期必须与
 * 注入字段一致（Java 确定性校验，不一致拦截并要求人工确认）；必填条款（金额/交期）校验；
 * 变更需重新审批（allow-once），旧条款快照留痕。
 */
@Service
public class ContractService {
    private final ContractRepository contracts;
    private final ProcurementTaskRepository tasks;
    private final OrderRepository orders;
    private final OrderService orderService;
    private final AgentCommandRepository commands;
    private final IdempotencyRecordRepository idempotency;
    private final AuditEventRepository audit;
    private final InsightsCache insightsCache;
    private final StateMachine<ContractStatus, ContractEvent> contractMachine;
    private final String operator;

    public ContractService(
            ContractRepository contracts,
            ProcurementTaskRepository tasks,
            OrderRepository orders,
            OrderService orderService,
            AgentCommandRepository commands,
            IdempotencyRecordRepository idempotency,
            AuditEventRepository audit,
            InsightsCache insightsCache,
            StateMachine<ContractStatus, ContractEvent> contractMachine,
            AppProperties properties) {
        this.contracts = contracts;
        this.tasks = tasks;
        this.orders = orders;
        this.orderService = orderService;
        this.commands = commands;
        this.idempotency = idempotency;
        this.audit = audit;
        this.insightsCache = insightsCache;
        this.contractMachine = contractMachine;
        this.operator = properties.localOperator();
    }

    // ------------------------------------------------------------------
    // 定标 → 草拟
    // ------------------------------------------------------------------

    @Transactional
    public ProcurementDtos.OperationAccepted createDraft(String taskId, String idempotencyKey) {
        var requestSha = com.caijiatai.procurement.agent.CanonicalJson.sha256(Map.of("task_id", taskId));
        var key = normalizeIdempotencyKey(idempotencyKey, requestSha);
        var replay = idempotency.findById(new IdempotencyRecord.Key("contract_draft", key));
        if (replay.isPresent()) {
            var op = commands.findById(replay.get().getOperationId()).orElseThrow();
            var sameTask = taskId.equals(op.getAggregateId())
                    && taskId.equals(text(op.getPayload().get("task_id")));
            if (!replay.get().getPayloadSha256().equals(requestSha) && !sameTask) {
                throw conflict("idempotency_payload_conflict",
                        "同一幂等键已用于不同合同草拟请求");
            }
            return accepted(op, "已存在相同合同草拟请求");
        }
        var task = tasks.lockById(taskId)
                .orElseThrow(() -> notFound("task_not_found", "未找到采购任务"));
        if (!"approved".equals(task.getStatus())) {
            throw conflict("contract_requires_approved_task", "只有已批准（定标）任务可以生成合同");
        }
        if (!contracts.findByTaskId(taskId).isEmpty()) {
            throw conflict("contract_already_exists", "该任务已生成合同");
        }
        var order = orderService.ensureOrderForApprovedTask(task);
        if (order == null || order.getLandedTotal() == null) {
            throw conflict("contract_requires_cost", "订单缺少到货总价，无法生成合同（请先补录成本）");
        }
        var leadDays = leadDays(task);
        var contractNo = "CT-" + task.getReference();
        var contract = Contract.derive(
                taskId, contractNo, order.getSupplierName(), task.getItemName(),
                order.getLandedTotal(), leadDays);
        contracts.saveAndFlush(contract);
        var payload = draftPayload(contract, task, order.getLandedTotal(), leadDays);
        var command = commands.save(AgentCommand.accept(
                "draft_contract", taskId, task.getGeneration(), task.getVersion(), payload));
        idempotency.save(IdempotencyRecord.reserve("contract_draft", key, requestSha, command.getOperationId()));
        audit.save(AuditEvent.forBusiness(
                "contract", contract.getId(), "contract_draft_requested", operator,
                Map.of("contract_no", contractNo, "task_id", taskId)));
        return accepted(command, null);
    }

    /** Agent 草拟结果应用：条款集 + 风险分级 + Java 一致性校验（权威）。 */
    @SuppressWarnings("unchecked")
    @Transactional
    public void applyDraftResult(AgentCommand command, Map<String, Object> result) {
        var contract = contracts.lockById(text(command.getPayload().get("contract_id")))
                .orElseThrow(() -> notFound("contract_not_found", "未找到合同"));
        var draftText = text(result.get("draft_text"));
        var rawClauses = result.get("clauses");
        if (draftText.isBlank() || !(rawClauses instanceof List<?> clauseList) || clauseList.isEmpty()) {
            throw new ApiException(HttpStatus.CONFLICT, "invalid_contract_draft", "Agent 未返回可验证的合同草拟与条款");
        }
        List<Map<String, Object>> clauses = new ArrayList<>();
        for (var raw : clauseList) {
            if (!(raw instanceof Map<?, ?> clause)) {
                continue;
            }
            var normalized = new LinkedHashMap<String, Object>();
            normalized.put("title", text(clause.get("title")));
            normalized.put("content", text(clause.get("content")));
            normalized.put("risk_level", text(clause.get("risk_level")));
            normalized.put("risk_reason", text(clause.get("risk_reason")));
            clauses.add(normalized);
        }
        var clauseValidation = ContractClausePolicy.validate(clauses);
        // Java 权威一致性：草拟文本金额/交期 vs 注入字段（变更审批中用待定修订值口径）
        var amount = contract.pendingAmount() != null ? contract.pendingAmount() : contract.getAmount();
        var leadDays = contract.pendingLeadDays() != null ? contract.pendingLeadDays() : contract.getLeadDays();
        var consistency = ContractConsistencyPolicy.check(draftText, amount, leadDays);
        var note = new StringBuilder();
        if (!Boolean.TRUE.equals(clauseValidation.get("valid"))) {
            note.append("必填条款缺失（金额/交期），审批被拦截。");
        }
        if (!Boolean.TRUE.equals(consistency.get("consistent"))) {
            note.append("草拟文本金额/交期与定标结果不一致，审批被拦截，需人工确认。");
        }
        contract.applyDraft(draftText, clauses, consistency, note.isEmpty() ? null : note.toString());
        contracts.save(contract);
        audit.save(AuditEvent.forBusiness(
                "contract", contract.getId(), "contract_drafted", "agent",
                Map.of("contract_no", contract.getContractNo(), "clause_count", clauses.size())));
        insightsCache.evictAll();
    }

    // ------------------------------------------------------------------
    // 查询
    // ------------------------------------------------------------------

    @Transactional(readOnly = true)
    public Map<String, Object> list(String status, String taskId, int page, int size) {
        var pageable = PageRequest.of(Math.max(0, page), Math.min(100, Math.max(1, size)));
        Page<Contract> query;
        if (taskId != null && !taskId.isBlank()) {
            query = contracts.findByTaskIdOrderByCreatedAtDesc(taskId, pageable);
        } else if (status == null || status.isBlank()) {
            query = contracts.findAllByOrderByCreatedAtDesc(pageable);
        } else {
            // 统一用小写 wire 值过滤（不依赖 DB 排序规则大小写不敏感）
            var wire = normalizeStatusFilter(status, "invalid_contract_status", "未知合同状态: ");
            query = contracts.findByStatusOrderByCreatedAtDesc(wire, pageable);
        }
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
        var contract = contracts.findById(id)
                .orElseThrow(() -> notFound("contract_not_found", "未找到合同"));
        return view(contract);
    }

    // ------------------------------------------------------------------
    // 状态流转：提交 / 审批（allow-once）/ 驳回 / 执行 / 关闭 / 变更
    // ------------------------------------------------------------------

    @Transactional
    public Map<String, Object> action(String id, String action, ContractDtos.ContractAction body) {
        var contract = contracts.lockById(id)
                .orElseThrow(() -> notFound("contract_not_found", "未找到合同"));
        var from = ContractStatus.fromWire(contract.getStatus());
        try {
            switch (action) {
                case "submit" -> {
                    if (!contractMachine.can(from, ContractEvent.SUBMIT)) {
                        throw conflict("invalid_contract_transition", "合同状态 " + from + " 不允许提交审批");
                    }
                    if (contract.getDraftText() == null || contract.getClauses().isEmpty()) {
                        throw conflict("contract_not_drafted", "合同尚未完成 AI 草拟，请稍候或重新生成");
                    }
                    contractMachine.transition(id, from, ContractEvent.SUBMIT, Map.of());
                    contract.submit(body.notes());
                    audit.save(AuditEvent.forBusiness(
                            "contract", contract.getId(), "contract_submitted", operator,
                            Map.of("contract_no", contract.getContractNo())));
                }
                case "approve" -> {
                    if (!contractMachine.can(from, ContractEvent.APPROVE)) {
                        throw conflict("invalid_contract_transition",
                                "只有待审批（PENDING_APPROVAL）或变更审批（CHANGE_REQUEST）允许批准");
                    }
                    if (!Boolean.TRUE.equals(body.confirmed()) || body.notes() == null || body.notes().isBlank()) {
                        throw bad("approve_requires_confirmation", "批准合同必须勾选确认并填写人工备注");
                    }
                    if (from == ContractStatus.PENDING_APPROVAL || from == ContractStatus.CHANGE_REQUEST) {
                        // 变更批准前按待定修订值重新校验条款与一致性（未重新草拟会在此被拦下）
                        var clauseValidation = ContractClausePolicy.validate(contract.getClauses());
                        var amount = contract.pendingAmount() != null ? contract.pendingAmount() : contract.getAmount();
                        var leadDays = contract.pendingLeadDays() != null
                                ? contract.pendingLeadDays() : contract.getLeadDays();
                        var consistency = ContractConsistencyPolicy.check(contract.getDraftText(), amount, leadDays);
                        if (!Boolean.TRUE.equals(clauseValidation.get("valid"))) {
                            throw conflict("contract_missing_required_clauses", "必填条款（金额/交期）缺失，不能批准");
                        }
                        if (!Boolean.TRUE.equals(consistency.get("consistent"))) {
                            throw conflict("contract_consistency_required",
                                    "草拟文本金额/交期与（修订后）定标口径不一致，请重新草拟或人工确认");
                        }
                    }
                    contractMachine.transition(id, from, ContractEvent.APPROVE, Map.of("notes", body.notes()));
                    if (from == ContractStatus.CHANGE_REQUEST) {
                        contract.applyPendingChange(); // 变更批准：落定修订后的金额/交期
                    }
                    contract.approve(body.notes());
                    var order = orders.findByTaskId(contract.getTaskId()).orElse(null);
                    if (order != null) {
                        contract.linkOrder(order.getId());
                    }
                    audit.save(AuditEvent.forBusiness(
                            "contract", contract.getId(),
                            from == ContractStatus.CHANGE_REQUEST ? "contract_change_approved" : "contract_approved",
                            operator, Map.of("contract_no", contract.getContractNo(), "notes", body.notes())));
                }
                case "reject" -> {
                    if (!contractMachine.can(from, ContractEvent.REJECT)) {
                        throw conflict("invalid_contract_transition", "合同状态 " + from + " 不允许驳回");
                    }
                    contractMachine.transition(id, from, ContractEvent.REJECT, Map.of());
                    // 驳回目标按来源分流（PENDING_APPROVAL→DRAFT；CHANGE_REQUEST→恢复变更前状态）
                    contract.reject(body.notes());
                    audit.save(AuditEvent.forBusiness(
                            "contract", contract.getId(), "contract_rejected", operator,
                            Map.of("contract_no", contract.getContractNo(), "notes", body.notes() == null ? "" : body.notes())));
                }
                case "execute" -> {
                    if (!contractMachine.can(from, ContractEvent.EXECUTE)) {
                        throw conflict("invalid_contract_transition", "只有已生效（EFFECTIVE）合同可以开始执行");
                    }
                    contractMachine.transition(id, from, ContractEvent.EXECUTE, Map.of());
                    contract.execute();
                    audit.save(AuditEvent.forBusiness(
                            "contract", contract.getId(), "contract_executing", operator,
                            Map.of("contract_no", contract.getContractNo())));
                }
                case "close" -> {
                    if (!contractMachine.can(from, ContractEvent.CLOSE)) {
                        throw conflict("invalid_contract_transition", "只有执行中（EXECUTING）合同可以关闭");
                    }
                    contractMachine.transition(id, from, ContractEvent.CLOSE, Map.of());
                    contract.close(body.notes());
                    audit.save(AuditEvent.forBusiness(
                            "contract", contract.getId(), "contract_closed", operator,
                            Map.of("contract_no", contract.getContractNo())));
                }
                case "request_change" -> {
                    if (!contractMachine.can(from, ContractEvent.REQUEST_CHANGE)) {
                        throw conflict("invalid_contract_transition", "只有生效/执行中合同可以发起变更");
                    }
                    if (body.notes() == null || body.notes().isBlank()) {
                        throw bad("change_requires_notes", "合同变更必须填写变更原因");
                    }
                    if (body.newAmount() == null || body.newLeadDays() == null) {
                        throw bad("change_requires_values", "合同变更必须提供修订后的金额与交期（new_amount / new_lead_days）");
                    }
                    if (body.newAmount().signum() <= 0 || body.newLeadDays() <= 0) {
                        throw bad("invalid_change_values", "修订后的金额必须大于 0、交期必须大于 0 天");
                    }
                    contractMachine.transition(id, from, ContractEvent.REQUEST_CHANGE, Map.of());
                    contract.requestChange(body.notes(), body.newAmount(), body.newLeadDays());
                    audit.save(AuditEvent.forBusiness(
                            "contract", contract.getId(), "contract_change_requested", operator,
                            Map.of("contract_no", contract.getContractNo(), "notes", body.notes(),
                                    "new_amount", plain(body.newAmount()), "new_lead_days", body.newLeadDays())));
                }
                default -> throw bad("invalid_contract_action",
                        "合同操作只能是 submit / approve / reject / execute / close / request_change");
            }
            var saved = contracts.saveAndFlush(contract);
            insightsCache.evictAll();
            return view(saved);
        } catch (OptimisticLockingFailureException error) {
            throw conflict("contract_concurrent_modification", "合同已被其他操作修改，请刷新后重试");
        }
    }

    // ------------------------------------------------------------------
    // 重新草拟（M6：一致性校验失败/变更后的 DRAFT 与 CHANGE_REQUEST 出口）
    // ------------------------------------------------------------------

    /** 重新草拟：仅 DRAFT（初次失败重试）与 CHANGE_REQUEST（变更修订后重新起草）。 */
    @Transactional
    public ProcurementDtos.OperationAccepted regenDraft(String id) {
        var contract = contracts.lockById(id)
                .orElseThrow(() -> notFound("contract_not_found", "未找到合同"));
        var from = ContractStatus.fromWire(contract.getStatus());
        if (from != ContractStatus.DRAFT && from != ContractStatus.CHANGE_REQUEST) {
            throw conflict("invalid_contract_regen",
                    "只有草稿（DRAFT）或变更审批（CHANGE_REQUEST）状态允许重新草拟");
        }
        var task = tasks.findById(contract.getTaskId()).orElse(null);
        var amount = contract.pendingAmount() != null ? contract.pendingAmount() : contract.getAmount();
        var leadDays = contract.pendingLeadDays() != null ? contract.pendingLeadDays() : contract.getLeadDays();
        var payload = draftPayload(contract, task, amount, leadDays);
        var payloadSha = com.caijiatai.procurement.agent.CanonicalJson.sha256(payload);
        var key = normalizeIdempotencyKey(null, payloadSha);
        var existing = idempotency.findById(new IdempotencyRecord.Key("contract_regenerate", key));
        if (existing.isPresent()) {
            var op = commands.findById(existing.get().getOperationId()).orElseThrow();
            return accepted(op, "已存在相同合同草拟请求");
        }
        var command = commands.save(AgentCommand.accept(
                "draft_contract", contract.getTaskId(), 1, 0, payload));
        idempotency.save(IdempotencyRecord.reserve("contract_regenerate", key, payloadSha, command.getOperationId()));
        audit.save(AuditEvent.forBusiness(
                "contract", contract.getId(), "contract_regen_requested", operator,
                Map.of("contract_no", contract.getContractNo(),
                        "amount", plain(amount), "lead_days", leadDays)));
        return accepted(command, null);
    }

    /** 合同草拟命令载荷（金额/交期由调用方给定：定标口径或变更待定修订值）。 */
    private LinkedHashMap<String, Object> draftPayload(
            Contract contract, ProcurementTask task, BigDecimal amount, int leadDays) {
        var payload = new LinkedHashMap<String, Object>();
        payload.put("contract_id", contract.getId());
        payload.put("contract_no", contract.getContractNo());
        payload.put("task_id", contract.getTaskId());
        payload.put("task_reference", task == null ? "" : task.getReference());
        payload.put("supplier_name", contract.getSupplierName());
        payload.put("item_name", contract.getItemName());
        payload.put("amount", amount.stripTrailingZeros().toPlainString());
        payload.put("lead_days", leadDays);
        return payload;
    }

    // ------------------------------------------------------------------
    // 视图
    // ------------------------------------------------------------------

    private Map<String, Object> view(Contract contract) {
        var task = tasks.findById(contract.getTaskId()).orElse(null);
        var order = contract.getOrderId() == null ? null : orders.findById(contract.getOrderId()).orElse(null);
        var value = new LinkedHashMap<String, Object>();
        value.put("id", contract.getId());
        value.put("contract_no", contract.getContractNo());
        value.put("task_id", contract.getTaskId());
        value.put("task_reference", task == null ? null : task.getReference());
        value.put("order_id", contract.getOrderId());
        value.put("order_no", order == null ? null : order.getOrderNo());
        value.put("supplier_name", contract.getSupplierName());
        value.put("item_name", contract.getItemName());
        value.put("amount", plain(contract.getAmount()));
        value.put("lead_days", contract.getLeadDays());
        value.put("status", ContractStatus.fromWire(contract.getStatus()).name());
        value.put("draft_text", contract.getDraftText());
        value.put("clauses", contract.getClauses());
        value.put("consistency", contract.getConsistency());
        if (contract.getClauses() != null) {
            value.put("clause_validation", ContractClausePolicy.validate(contract.getClauses()));
        }
        value.put("change_history", contract.getChangeHistory());
        value.put("notes", contract.getNotes());
        value.put("version", contract.getVersion());
        value.put("created_at", contract.getCreatedAt().toString());
        value.put("updated_at", contract.getUpdatedAt().toString());
        value.put("approved_at", contract.getApprovedAt() == null ? null : contract.getApprovedAt().toString());
        return value;
    }

    // ------------------------------------------------------------------
    // 工具
    // ------------------------------------------------------------------

    private static int leadDays(ProcurementTask task) {
        var constraints = task.getConstraints();
        if (constraints == null || !(constraints.get("max_lead_days") instanceof Number number)) {
            return 0;
        }
        return Math.max(0, number.intValue());
    }

    private static String plain(BigDecimal value) {
        return value == null ? null : value.stripTrailingZeros().toPlainString();
    }

    private static String text(Object value) {
        return value == null ? "" : String.valueOf(value).trim();
    }

    private static String normalizeIdempotencyKey(String key, String payloadSha) {
        return key == null || key.isBlank() ? payloadSha : key;
    }

    /** 状态筛选：入参（大写枚举名或小写 wire 值均可）统一映射回落库的小写 wire 值，非法值 400。 */
    private static String normalizeStatusFilter(String status, String errorCode, String prefix) {
        var raw = status.strip();
        for (var value : ContractStatus.values()) {
            if (value.name().equalsIgnoreCase(raw) || value.wireValue().equalsIgnoreCase(raw)) {
                return value.wireValue();
            }
        }
        throw bad(errorCode, prefix + status);
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

    private static ApiException bad(String code, String message) {
        return new ApiException(HttpStatus.BAD_REQUEST, code, message);
    }

    private static ApiException conflict(String code, String message) {
        return new ApiException(HttpStatus.CONFLICT, code, message);
    }

    private static ApiException notFound(String code, String message) {
        return new ApiException(HttpStatus.NOT_FOUND, code, message);
    }
}
