package com.caijiatai.procurement.comparison;

import static org.assertj.core.api.Assertions.assertThat;

import com.caijiatai.procurement.quote.ProcurementQuote;
import com.caijiatai.procurement.task.ProcurementTask;
import java.math.BigDecimal;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

class ComparisonEngineTest {
    private final ComparisonEngine engine = new ComparisonEngine();

    @Test
    void normalizesTaxFreightPriceBasisAndRanksEligibleQuotes() {
        var task = packagingTask();
        var alpha = quote(
                "华东优包", "520", "1000", "0.13", true, "0", true,
                "5000", "7", true, "250", "350", "60", "PE", "white");
        var beta = quote(
                "沪上包装", "0.48", "1", "0.13", false, "600", false,
                "3000", "9", true, "250", "350", "60", "PE", "白色");

        var result = engine.compare(task, List.of(beta, alpha));
        @SuppressWarnings("unchecked")
        var rows = (List<Map<String, Object>>) result.result().get("quotes");
        @SuppressWarnings("unchecked")
        var alphaCost = (Map<String, Object>) rows.getFirst().get("cost");
        @SuppressWarnings("unchecked")
        var betaCost = (Map<String, Object>) rows.get(1).get("cost");

        assertThat(rows.getFirst().get("supplier_name")).isEqualTo("华东优包");
        assertThat(alphaCost.get("landed_total_base")).isEqualTo("5200.00");
        assertThat(alphaCost.get("landed_unit_base")).isEqualTo("0.5200");
        assertThat(alphaCost.get("tax_quote_currency")).isEqualTo("598.23");
        assertThat(betaCost.get("landed_total_base")).isEqualTo("6024.00");
        assertThat(result.result().get("eligible_count")).isEqualTo(2);
        assertThat(result.inputSha256()).matches("[0-9a-f]{64}");
    }

    @Test
    void rejectsMoqLeadTimeInvoiceBudgetAndWrongSpecifications() {
        var task = packagingTask();
        var invalid = quote(
                "错误供应商", "800", "1000", "0", true, "0", true,
                "20000", "20", false, "260", "350", "50", "PP", "black");
        var valid = quote(
                "有效供应商", "520", "1000", "0.13", true, "0", true,
                "5000", "7", true, "250", "350", "60", "PE", "white");

        var result = engine.compare(task, List.of(invalid, valid));
        @SuppressWarnings("unchecked")
        var rows = (List<Map<String, Object>>) result.result().get("quotes");
        var rejected = rows.stream()
                .filter(item -> item.get("supplier_name").equals("错误供应商"))
                .findFirst().orElseThrow();
        @SuppressWarnings("unchecked")
        var reasons = (List<Map<String, String>>) rejected.get("exclusion_reasons");

        assertThat(rejected.get("eligible")).isEqualTo(false);
        assertThat(reasons).extracting(item -> item.get("code"))
                .contains("moq", "lead_time", "invoice", "budget", "spec_material", "spec_color", "spec_width_mm", "spec_thickness_um");
        assertThat(result.result().get("recommended_quote_id")).isEqualTo(valid.getId());
    }

    @Test
    void enforcesDynamicHardSpecsAndKeepsPreferenceAsWarning() {
        var specs = new LinkedHashMap<String, Object>();
        specs.put("length", Map.of(
                "label", "长度", "type", "number", "value", "100", "unit", "mm",
                "match", "tolerance", "tolerance", "2", "priority", "hard"));
        specs.put("finish", Map.of(
                "label", "表面", "type", "text", "value", "哑光",
                "match", "exact", "priority", "preference"));
        var task = ProcurementTask.structured(
                2, "标签采购", "general", "标签", new BigDecimal("100.5"), "卷",
                specs,
                Map.of(
                        "base_currency", "CNY", "fx_rates", Map.of("CNY", "1"),
                        "max_lead_days", 30, "invoice_required", true));
        var first = dynamicQuote("A", "101", "亮光");
        var second = dynamicQuote("B", "110", "哑光");

        var result = engine.compare(task, List.of(first, second));
        @SuppressWarnings("unchecked")
        var rows = (List<Map<String, Object>>) result.result().get("quotes");
        var a = rows.stream().filter(item -> item.get("supplier_name").equals("A")).findFirst().orElseThrow();
        var b = rows.stream().filter(item -> item.get("supplier_name").equals("B")).findFirst().orElseThrow();

        assertThat(a.get("eligible")).isEqualTo(true);
        assertThat((List<?>) a.get("warnings")).isNotEmpty();
        assertThat(b.get("eligible")).isEqualTo(false);
    }

    @Test
    void treatsChineseAndEnglishDynamicColorValuesAsEquivalent() {
        var specs = Map.<String, Object>of("color", Map.of(
                "label", "颜色", "type", "text", "value", "白色",
                "match", "exact", "priority", "hard"));
        var task = ProcurementTask.structured(
                2, "快递袋采购", "general", "标签", new BigDecimal("100"), "卷", specs,
                Map.of(
                        "base_currency", "CNY", "fx_rates", Map.of("CNY", "1"),
                        "max_lead_days", 30, "invoice_required", true));

        var result = engine.compare(task, List.of(dynamicQuote("A", "100", "哑光"), dynamicQuote("B", "100", "亮光")));
        @SuppressWarnings("unchecked")
        var rows = (List<Map<String, Object>>) result.result().get("quotes");

        assertThat(rows).allSatisfy(item -> assertThat(item.get("eligible")).isEqualTo(true));
    }

    @Test
    void mapsDynamicLabelsToStandardFieldsAndConvertsUnits() {
        var specs = new LinkedHashMap<String, Object>();
        specs.put("width", Map.of(
                "label", "宽度", "type", "number", "value", "25", "unit", "cm",
                "match", "exact", "priority", "hard"));
        specs.put("thickness", Map.of(
                "label", "厚度", "type", "number", "value", "60", "unit", "微米",
                "match", "exact", "priority", "hard"));
        var task = ProcurementTask.structured(
                2, "标签采购", "general", "标签", new BigDecimal("100"), "卷", specs,
                Map.of(
                        "base_currency", "CNY", "fx_rates", Map.of("CNY", "1"),
                        "max_lead_days", 30, "invoice_required", true));
        var quote = quote(
                "A", "10", "1", "0", true, "0", true,
                "1", "5", true, "250", "350", "60", "PE", "white", "热敏标签 25cm 60um");
        var other = quote(
                "B", "11", "1", "0", true, "0", true,
                "1", "5", true, "250", "350", "60", "PE", "white", "热敏标签 25cm 60um");

        var result = engine.compare(task, List.of(quote, other));
        @SuppressWarnings("unchecked")
        var rows = (List<Map<String, Object>>) result.result().get("quotes");
        assertThat(rows).allSatisfy(item -> assertThat(item.get("eligible")).isEqualTo(true));
    }

    @Test
    void mapsCanonicalPrintColorsAndLayersToStandardQuoteFields() {
        var specs = new LinkedHashMap<String, Object>();
        specs.put("print_colors", Map.of(
                "label", "印刷色数", "type", "number", "value", "1",
                "match", "exact", "priority", "hard"));
        specs.put("layers", Map.of(
                "label", "瓦楞层数", "type", "number", "value", "5",
                "match", "exact", "priority", "hard"));
        var task = ProcurementTask.structured(
                2, "纸箱采购", "general", "五层瓦楞纸箱", new BigDecimal("5000"), "个",
                specs,
                Map.of(
                        "base_currency", "CNY", "fx_rates", Map.of("CNY", "1"),
                        "max_lead_days", 20, "invoice_required", true));

        var result = engine.compare(task, List.of(cartonQuote("甲", "3.2"), cartonQuote("乙", "3.3")));
        @SuppressWarnings("unchecked")
        var rows = (List<Map<String, Object>>) result.result().get("quotes");

        assertThat(rows).allSatisfy(item -> assertThat(item.get("eligible")).isEqualTo(true));
    }

    @Test
    void acceptsCartonWithCanonicalMaterialColorAndHeight() {
        var specs = new LinkedHashMap<String, Object>();
        specs.put("width_mm", "400");
        specs.put("length_mm", "300");
        specs.put("height_mm", "250");
        specs.put("thickness_um", "5000");
        specs.put("material", "瓦楞纸");
        specs.put("color", "牛皮色");
        specs.put("print_colors", 1);
        var task = ProcurementTask.structured(
                1, "苏州工厂出口瓦楞纸箱采购", "ecommerce_packaging", "五层瓦楞纸箱",
                new BigDecimal("5000"), "piece", specs,
                Map.of(
                        "base_currency", "CNY", "fx_rates", Map.of("CNY", "1"),
                        "max_lead_days", 20, "invoice_required", true,
                        "size_tolerance_mm", "5", "thickness_tolerance_um", "500"));

        var result = engine.compare(task,
                List.of(cartonV1Quote("浙江箱业", "3.2"), cartonV1Quote("华南纸业", "3.3")));
        @SuppressWarnings("unchecked")
        var rows = (List<Map<String, Object>>) result.result().get("quotes");

        assertThat(rows).allSatisfy(item -> assertThat(item.get("eligible")).isEqualTo(true));
    }

    @Test
    void rejectsCartonWhenHeightIsOutOfTolerance() {
        var specs = new LinkedHashMap<String, Object>();
        specs.put("width_mm", "400");
        specs.put("length_mm", "300");
        specs.put("height_mm", "250");
        specs.put("thickness_um", "5000");
        specs.put("material", "瓦楞纸");
        specs.put("color", "牛皮色");
        specs.put("print_colors", 1);
        var task = ProcurementTask.structured(
                1, "苏州工厂出口瓦楞纸箱采购", "ecommerce_packaging", "五层瓦楞纸箱",
                new BigDecimal("5000"), "piece", specs,
                Map.of(
                        "base_currency", "CNY", "fx_rates", Map.of("CNY", "1"),
                        "max_lead_days", 20, "invoice_required", true,
                        "size_tolerance_mm", "5", "thickness_tolerance_um", "500"));
        var wrong = cartonV1Quote("浙江箱业", "3.2");
        var fields = new LinkedHashMap<String, Object>();
        mapFieldValues(wrong).forEach(fields::put);
        fields.put("height_mm", "260");
        var wrongQuote = createQuote("浙江箱业", fields, Map.of());

        var result = engine.compare(task, List.of(wrongQuote, cartonV1Quote("华南纸业", "3.3")));
        @SuppressWarnings("unchecked")
        var rows = (List<Map<String, Object>>) result.result().get("quotes");
        var rejected = rows.stream().filter(item -> item.get("supplier_name").equals("浙江箱业"))
                .findFirst().orElseThrow();
        @SuppressWarnings("unchecked")
        var reasons = (List<Map<String, String>>) rejected.get("exclusion_reasons");

        assertThat(rejected.get("eligible")).isEqualTo(false);
        assertThat(reasons).extracting(item -> item.get("code")).contains("spec_height_mm");
    }

    @Test
    void failsClosedWhenRequirementMaterialIsNotRecognized() {
        var specs = new LinkedHashMap<String, Object>();
        specs.put("width_mm", "250");
        specs.put("length_mm", "350");
        specs.put("thickness_um", "60");
        specs.put("material", "HDPE");
        specs.put("color", "白色");
        specs.put("print_colors", 1);
        var task = ProcurementTask.structured(
                1, "测试采购", "ecommerce_packaging", "快递袋",
                new BigDecimal("10000"), "piece", specs,
                Map.of(
                        "base_currency", "CNY", "fx_rates", Map.of("CNY", "1"),
                        "max_lead_days", 15, "invoice_required", true,
                        "size_tolerance_mm", "2", "thickness_tolerance_um", "3"));
        var quote = quote(
                "华东优包", "520", "1000", "0.13", true, "0", true,
                "5000", "7", true, "250", "350", "60", "PE", "white");

        var result = engine.compare(task, List.of(quote, quote(
                "沪上包装", "0.48", "1", "0.13", false, "600", false,
                "3000", "9", true, "250", "350", "60", "PE", "白色")));
        @SuppressWarnings("unchecked")
        var rows = (List<Map<String, Object>>) result.result().get("quotes");

        assertThat(rows).allSatisfy(item -> {
            @SuppressWarnings("unchecked")
            var reasons = (List<Map<String, String>>) item.get("exclusion_reasons");
            assertThat(reasons).extracting(map -> map.get("code")).contains("spec_material");
            assertThat(reasons).anySatisfy(map -> assertThat(map.get("message")).contains("无法复核材质一致性"));
        });
    }

    @Test
    void failsClosedWhenRequirementItemIsNotRecognized() {
        var task = ProcurementTask.structured(
                1, "测试采购", "ecommerce_packaging", "定制包装",
                new BigDecimal("10000"), "piece",
                Map.of(
                        "width_mm", "250", "length_mm", "350", "thickness_um", "60",
                        "material", "PE", "color", "白色", "print_colors", 1),
                Map.of(
                        "base_currency", "CNY", "fx_rates", Map.of("CNY", "1"),
                        "max_lead_days", 15, "invoice_required", true,
                        "size_tolerance_mm", "2", "thickness_tolerance_um", "3"));
        var quote = quote(
                "华东优包", "520", "1000", "0.13", true, "0", true,
                "5000", "7", true, "250", "350", "60", "PE", "white");

        var result = engine.compare(task, List.of(quote, quote(
                "沪上包装", "0.48", "1", "0.13", false, "600", false,
                "3000", "9", true, "250", "350", "60", "PE", "白色")));
        @SuppressWarnings("unchecked")
        var rows = (List<Map<String, Object>>) result.result().get("quotes");

        assertThat(rows).allSatisfy(item -> {
            @SuppressWarnings("unchecked")
            var reasons = (List<Map<String, String>>) item.get("exclusion_reasons");
            assertThat(reasons).extracting(map -> map.get("code")).contains("item_identity");
            assertThat(reasons).anySatisfy(map -> assertThat(map.get("message")).contains("无法复核物料一致性"));
        });
    }

    private ProcurementQuote cartonV1Quote(String supplier, String price) {
        var values = new LinkedHashMap<String, Object>();
        values.put("supplier_name", supplier);
        values.put("item_description", "五层瓦楞纸箱 400x300x250 mm");
        values.put("currency", "CNY");
        values.put("unit_price", price);
        values.put("price_basis", "1");
        values.put("tax_rate", "0");
        values.put("tax_included", true);
        values.put("shipping_fee", "0");
        values.put("shipping_included", true);
        values.put("moq", "1");
        values.put("lead_time_days", "10");
        values.put("supports_invoice", true);
        values.put("width_mm", "400");
        values.put("length_mm", "300");
        values.put("height_mm", "250");
        values.put("thickness_um", "5000");
        values.put("material", "瓦楞纸");
        values.put("color", "牛皮色");
        values.put("print_colors", 1);
        return createQuote(supplier, values, Map.of());
    }

    private Map<String, Object> mapFieldValues(ProcurementQuote quote) {
        var values = new LinkedHashMap<String, Object>();
        @SuppressWarnings("unchecked")
        var fields = (Map<String, Object>) quote.getExtracted().get("fields");
        fields.forEach((key, raw) -> {
            @SuppressWarnings("unchecked")
            var entry = (Map<String, Object>) raw;
            values.put(key, entry.get("value"));
        });
        return values;
    }

    private ProcurementTask packagingTask() {
        return ProcurementTask.structured(
                1,
                "快递袋采购",
                "ecommerce_packaging",
                "快递袋",
                new BigDecimal("10000"),
                "piece",
                Map.of(
                        "width_mm", "250", "length_mm", "350", "thickness_um", "60",
                        "material", "PE", "color", "白色", "print_colors", 1),
                Map.of(
                        "base_currency", "CNY", "fx_rates", Map.of("CNY", "1"),
                        "max_lead_days", 15, "invoice_required", true,
                        "size_tolerance_mm", "2", "thickness_tolerance_um", "3",
                        "max_landed_unit_cost", "0.70"));
    }

    private ProcurementQuote quote(
            String supplier,
            String price,
            String basis,
            String taxRate,
            boolean taxIncluded,
            String shipping,
            boolean shippingIncluded,
            String moq,
            String lead,
            boolean invoice,
            String width,
            String length,
            String thickness,
            String material,
            String color) {
        return quote(supplier, price, basis, taxRate, taxIncluded, shipping, shippingIncluded,
                moq, lead, invoice, width, length, thickness, material, color, "PE mailer 250x350mm 60um");
    }

    private ProcurementQuote quote(
            String supplier,
            String price,
            String basis,
            String taxRate,
            boolean taxIncluded,
            String shipping,
            boolean shippingIncluded,
            String moq,
            String lead,
            boolean invoice,
            String width,
            String length,
            String thickness,
            String material,
            String color,
            String description) {
        var values = new LinkedHashMap<String, Object>();
        values.put("supplier_name", supplier);
        values.put("item_description", description);
        values.put("currency", "CNY");
        values.put("unit_price", price);
        values.put("price_basis", basis);
        values.put("tax_rate", taxRate);
        values.put("tax_included", taxIncluded);
        values.put("shipping_fee", shipping);
        values.put("shipping_included", shippingIncluded);
        values.put("moq", moq);
        values.put("lead_time_days", lead);
        values.put("supports_invoice", invoice);
        values.put("width_mm", width);
        values.put("length_mm", length);
        values.put("thickness_um", thickness);
        values.put("material", material);
        values.put("color", color);
        values.put("print_colors", 1);
        return createQuote(supplier, values, Map.of());
    }

    private ProcurementQuote dynamicQuote(String supplier, String length, String finish) {
        var values = new LinkedHashMap<String, Object>();
        values.put("supplier_name", supplier);
        values.put("item_description", "标签");
        values.put("currency", "CNY");
        values.put("unit_price", "10");
        values.put("price_basis", "1");
        values.put("tax_rate", "0");
        values.put("tax_included", true);
        values.put("shipping_fee", "0");
        values.put("shipping_included", true);
        values.put("moq", "1");
        values.put("lead_time_days", "5");
        values.put("supports_invoice", true);
        values.put("color", "white");
        return createQuote(supplier, values, Map.of(
                "length", field(length),
                "finish", field(finish)));
    }

    private ProcurementQuote cartonQuote(String supplier, String price) {
        var values = new LinkedHashMap<String, Object>();
        values.put("supplier_name", supplier);
        values.put("item_description", "五层瓦楞纸箱 400x300 mm");
        values.put("currency", "CNY");
        values.put("unit_price", price);
        values.put("price_basis", "1");
        values.put("tax_rate", "0");
        values.put("tax_included", true);
        values.put("shipping_fee", "0");
        values.put("shipping_included", true);
        values.put("moq", "1");
        values.put("lead_time_days", "10");
        values.put("supports_invoice", true);
        values.put("color", "牛皮色");
        values.put("print_colors", 1);
        values.put("layers", 5);
        return createQuote(supplier, values, Map.of());
    }

    private ProcurementQuote createQuote(
            String supplier, Map<String, Object> values, Map<String, Object> specifications) {
        var fields = new LinkedHashMap<String, Object>();
        values.forEach((key, value) -> fields.put(key, field(value)));
        var extracted = new LinkedHashMap<String, Object>();
        extracted.put("fields", fields);
        extracted.put("specifications", specifications);
        extracted.put("review_fields", List.of());
        return ProcurementQuote.create(
                "task", "jb" + "a".repeat(32), supplier, supplier + ".xlsx", "xlsx",
                "b".repeat(64), extracted, "ready", "test-parser", BigDecimal.ONE);
    }

    private Map<String, Object> field(Object value) {
        return new LinkedHashMap<>(Map.of("value", value, "confidence", 1, "status", "accepted"));
    }
}
