package com.caijiatai.procurement.task;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/procurement")
public final class ProcurementMetaController {
    @GetMapping("/meta")
    public Map<String, Object> meta() {
        var fields = new LinkedHashMap<String, Object>();
        field(fields, "supplier_name", "供应商", "text", true);
        field(fields, "item_description", "品名/描述", "text", true);
        field(fields, "material", "材质", "text", true);
        field(fields, "color", "颜色", "text", true);
        field(fields, "print_colors", "印刷色数", "integer", true);
        field(fields, "currency", "币种", "text", true);
        field(fields, "unit_price", "报价", "currency", true);
        field(fields, "price_basis", "计价数量", "integer", true);
        field(fields, "tax_rate", "税率", "rate", true);
        field(fields, "tax_included", "是否含税", "boolean", true);
        field(fields, "shipping_fee", "运费", "currency", false);
        field(fields, "shipping_included", "是否含运费", "boolean", true);
        field(fields, "moq", "起订量（MOQ）", "decimal", true);
        field(fields, "lead_time_days", "交期（天）", "integer", true);
        field(fields, "supports_invoice", "是否可开票", "boolean", true);
        field(fields, "width_mm", "宽度（mm）", "decimal", true);
        field(fields, "length_mm", "长度（mm）", "decimal", true);
        field(fields, "height_mm", "高度（mm）", "decimal", true);
        field(fields, "thickness_um", "厚度（µm）", "decimal", true);
        field(fields, "payment_terms", "付款条件", "text", false);
        field(fields, "valid_until", "报价有效期", "date", false);
        return Map.ofEntries(
                Map.entry("category", "ecommerce_packaging"),
                Map.entry("categories", List.of("ecommerce_packaging", "general")),
                Map.entry("requirement_schema_versions", List.of(1, 2)),
                Map.entry("parser_version", "packaging-quote-v3"),
                Map.entry("ruleset_version", "landed-cost-v1"),
                Map.entry("ruleset_versions", List.of("landed-cost-v1", "dynamic-spec-v2")),
                Map.entry("max_file_bytes", 5 * 1024 * 1024),
                Map.entry("max_conversation_upload_bytes", 20 * 1024 * 1024),
                Map.entry("max_quotes_per_request", 50),
                Map.entry("allowed_extensions", List.of(".xlsx", ".pdf")),
                Map.entry("field_meta", fields));
    }

    private void field(
            Map<String, Object> fields, String name, String label, String kind, boolean required) {
        fields.put(name, Map.of("label", label, "kind", kind, "required", required));
    }
}
