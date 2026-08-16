package com.caijiatai.procurement.agent;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Primary;
import org.springframework.stereotype.Component;

/**
 * Demo-mode agent dispatcher: completes commands deterministically on the Java side
 * so the procurement closed loop runs without the Python agent (decision gate 1).
 * Synthetic results never carry business truth beyond what the Java engine computes.
 */
@Component
@Primary
@ConditionalOnProperty(prefix = "app", name = "agent-mode", havingValue = "demo")
public final class SyntheticAgentClient implements AgentDispatcher {

    @Override
    public AgentDispatcher.DispatchResult dispatch(AgentCommand command) {
        var result = switch (command.getOperationType()) {
            case "create_structured", "start_conversation" -> Map.of(
                    "session_id", sha256hex(command.getOperationId() + ":session"),
                    "run_id", sha256hex(command.getOperationId() + ":run"));
            case "analyze" -> Map.of(
                    "run_id", sha256hex(command.getOperationId() + ":run"),
                    "input_sha256", text(command.getPayload().get("input_sha256")),
                    "structured_result", Map.of(
                            "summary", "离线确定性分析已完成",
                            "risk_flags", java.util.List.of()),
                    "sources", java.util.List.of(),
                    "provider", "procurement_fake",
                    "model", "deterministic",
                    "prompt_version", "quote-analysis-v1");
            case "approve_decision" -> approval(command);
            case "import_quote", "reopen_task", "resume_run" -> Map.of(
                    "run_id", sha256hex(command.getOperationId() + ":run"));
            case "parse_invoice" -> parseInvoice(command);
            case "explain_invoice_diff" -> explainInvoiceDiff(command);
            case "draft_contract" -> draftContract(command);
            default -> Map.of();
        };
        return new AgentDispatcher.DispatchResult(200, Map.of("status", "completed", "result", result));
    }

    /** 演示模式：合同草拟由 Java 侧模板合成（真实模式由 Python 确定性模板 + 条款库软提示）。 */
    private Map<String, Object> draftContract(AgentCommand command) {
        var payload = command.getPayload();
        var amount = text(payload.get("amount"));
        var leadDays = String.valueOf(payload.get("lead_days"));
        var supplier = text(payload.get("supplier_name"));
        var item = text(payload.get("item_name"));
        return Map.of(
                "draft_text", "采购合同\n\n甲方（采购方）：采购工作台演示企业\n乙方（供应商）：" + supplier
                        + "\n\n一、合同金额为人民币 " + amount + " 元（价税合计）。\n"
                        + "二、乙方应于合同生效后 " + leadDays + " 天内交货。\n"
                        + "三、标的物：" + item + "。质量标准以双方确认的样品为准。\n"
                        + "四、付款条款：验收合格后 30 日内付款。\n",
                "clauses", java.util.List.of(
                        Map.of("title", "金额条款", "content", "合同金额为人民币 " + amount + " 元（价税合计）。",
                                "risk_level", "提示", "risk_reason", "演示模式草拟"),
                        Map.of("title", "交期条款", "content", "乙方应于合同生效后 " + leadDays + " 天内交货。",
                                "risk_level", "低", "risk_reason", "演示模式草拟"),
                        Map.of("title", "质量标准条款", "content", "质量标准以双方确认的样品为准。",
                                "risk_level", "提示", "risk_reason", "建议补充书面质量标准附件"),
                        Map.of("title", "付款条款", "content", "验收合格后 30 日内付款。",
                                "risk_level", "低", "risk_reason", "演示模式草拟")));
    }

    /** 演示模式：发票字段由 Java 侧合成（真实模式由 Python 确定性解析）。 */
    private Map<String, Object> parseInvoice(AgentCommand command) {
        var payload = command.getPayload();
        var invoice = new LinkedHashMap<String, Object>();
        invoice.put("invoice_code", "INV-CODE-" + command.getOperationId().substring(0, 8));
        invoice.put("invoice_no", "INV-" + command.getOperationId().substring(0, 12));
        invoice.put("issue_date", java.time.LocalDate.now().toString());
        invoice.put("supplier_name", "演示供应商");
        // 数值字段仅在注入值存在时才输出，缺失时省略（避免把"缺成本数据"误报为字段非法）
        if (payload.get("order_landed_total") != null) {
            invoice.put("total_amount", String.valueOf(payload.get("order_landed_total")));
        }
        if (payload.get("order_quantity") != null) {
            invoice.put("quantity", String.valueOf(payload.get("order_quantity")));
        }
        invoice.put("parser_version", "invoice-v1");
        return Map.of("invoice", invoice);
    }

    private Map<String, Object> explainInvoiceDiff(AgentCommand command) {
        var diffs = command.getPayload().get("diffs");
        return Map.of("explanation", Map.of(
                "reason", "三单匹配存在差异，请核对发票与订单/收货数据（演示模式）。",
                "suggestions", java.util.List.of("请核对金额合计与订单到货总价"),
                "source", "synthetic_agent"));
    }

    private Map<String, Object> approval(AgentCommand command) {
        var payload = command.getPayload();
        var binding = new LinkedHashMap<String, Object>();
        binding.put("pending_decision_id", text(payload.get("pending_decision_id")));
        binding.put("run_id", text(payload.get("run_id")));
        binding.put("tool_name", "procurement_approve_supplier");
        binding.put("task_version", payload.get("task_version"));
        binding.put("snapshot_id", text(payload.get("snapshot_id")));
        binding.put("input_sha256", text(payload.get("input_sha256")));
        binding.put("business_decision", text(payload.get("business_decision")));
        binding.put("quote_id", text(payload.get("quote_id")));
        binding.put("note_hash", text(payload.get("note_hash")));
        var approval = new LinkedHashMap<String, Object>(binding);
        approval.put("id", sha256hex(command.getOperationId() + ":approval"));
        approval.put("decision", "formal_java_confirmation");
        approval.put("confirmation_source", "java_control_plane");
        approval.put("arguments_sha256", CanonicalJson.sha256(binding));
        approval.put("created_at", Instant.now().toString());
        return Map.of("approval", approval);
    }

    private String text(Object value) {
        return value == null ? "" : String.valueOf(value);
    }

    private String sha256hex(String value) {
        try {
            var digest = MessageDigest.getInstance("SHA-256")
                    .digest(value.getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(digest).substring(0, 32);
        } catch (NoSuchAlgorithmException error) {
            throw new IllegalStateException(error);
        }
    }
}
