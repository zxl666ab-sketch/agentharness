package com.caijiatai.procurement.comparison;

import com.caijiatai.procurement.agent.CanonicalJson;
import com.caijiatai.procurement.api.ApiException;
import com.caijiatai.procurement.quote.ProcurementQuote;
import com.caijiatai.procurement.task.ProcurementTask;
import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.regex.Pattern;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;

@Component
public final class ComparisonEngine {
    private static final BigDecimal MONEY = new BigDecimal("0.01");
    private static final BigDecimal UNIT_MONEY = new BigDecimal("0.0001");

    public Calculation compare(ProcurementTask task, List<ProcurementQuote> quotes) {
        return compare(task, quotes, LocalDate.now(ZoneOffset.UTC));
    }

    public Calculation compare(
            ProcurementTask task, List<ProcurementQuote> quotes, LocalDate asOf) {
        if (quotes.size() < 2) {
            throw new ApiException(HttpStatus.CONFLICT, "insufficient_quotes", "至少需要两份报价才能比价");
        }
        var canonicalInput = canonicalInput(task, quotes, asOf);
        var inputSha256 = CanonicalJson.sha256(canonicalInput);
        var rows = quotes.stream()
                .map(quote -> normalize(task, quote, asOf))
                .sorted(Comparator
                        .comparing((QuoteResult item) -> !item.eligible())
                        .thenComparing(QuoteResult::landedTotal)
                        .thenComparing(QuoteResult::leadDays)
                        .thenComparing(QuoteResult::supplierName)
                        .thenComparing(QuoteResult::quoteId))
                .toList();
        var eligible = rows.stream().filter(QuoteResult::eligible).toList();
        BigDecimal best = eligible.isEmpty() ? null : eligible.getFirst().landedTotal();
        var resultRows = new ArrayList<Map<String, Object>>();
        int rank = 0;
        for (var row : rows) {
            Integer rowRank = null;
            String score = null;
            if (row.eligible()) {
                rowRank = ++rank;
                score = decimal(best.signum() == 0
                        ? new BigDecimal("100.0")
                        : best.multiply(new BigDecimal("100")).divide(row.landedTotal(), 1, RoundingMode.HALF_UP));
            }
            resultRows.add(row.toMap(rowRank, score));
        }
        String recommended = eligible.isEmpty() ? null : eligible.getFirst().quoteId();
        var explanation = new ArrayList<String>();
        if (!eligible.isEmpty()) {
            explanation.add(eligible.getFirst().supplierName() + " 在满足全部硬性条件的报价中到货总成本最低");
            if (eligible.size() > 1) {
                explanation.add("较第二名节省 " + money(eligible.get(1).landedTotal().subtract(best))
                        + " " + baseCurrency(task));
            }
        }
        var result = new LinkedHashMap<String, Object>();
        result.put("schema_version", 1);
        result.put("ruleset_version", task.getSchemaVersion() == 2 ? "dynamic-spec-v2" : "landed-cost-v1");
        result.put("request_id", task.getId());
        result.put("base_currency", baseCurrency(task));
        result.put("quantity", decimal(task.getQuantity()));
        result.put("quotes", resultRows);
        result.put("eligible_count", eligible.size());
        result.put("excluded_count", rows.size() - eligible.size());
        result.put("recommended_quote_id", recommended);
        result.put("recommendation_explanation", explanation);
        return new Calculation(inputSha256, canonicalInput, result);
    }

    private Map<String, Object> canonicalInput(
            ProcurementTask task, List<ProcurementQuote> quotes, LocalDate asOf) {
        var request = new LinkedHashMap<String, Object>();
        request.put("id", task.getId());
        request.put("schema_version", task.getSchemaVersion());
        request.put("category", task.getCategory());
        request.put("item_name", task.getItemName());
        request.put("quantity", decimal(task.getQuantity()));
        request.put("unit", task.getUnit());
        request.put("specifications", task.getSpecifications());
        request.put("constraints", task.getConstraints());
        request.put("created_at", task.getCreatedAt().toString());
        var quoteInputs = quotes.stream()
                .sorted(Comparator.comparing(ProcurementQuote::getId))
                .map(quote -> {
                    var value = new LinkedHashMap<String, Object>();
                    value.put("id", quote.getId());
                    value.put("source_sha256", quote.getSourceSha256());
                    value.put("fields", fieldValues(quote));
                    if (task.getSchemaVersion() == 2) {
                        value.put("specifications", quote.getExtracted().getOrDefault("specifications", Map.of()));
                    }
                    return value;
                })
                .toList();
        var input = new LinkedHashMap<String, Object>();
        input.put("ruleset_version", task.getSchemaVersion() == 2 ? "dynamic-spec-v2" : "landed-cost-v1");
        input.put("analysis_as_of", asOf.toString());
        input.put("request", request);
        input.put("quotes", quoteInputs);
        return input;
    }

    private QuoteResult normalize(ProcurementTask task, ProcurementQuote quote, LocalDate asOf) {
        var fields = fieldValues(quote);
        var constraints = task.getConstraints();
        var quantity = task.getQuantity();
        var currency = text(fields.get("currency")).toUpperCase(Locale.ROOT);
        var fxRates = map(constraints.get("fx_rates"));
        var fxRate = number(fxRates.get(currency), "缺少 " + currency + " 汇率");
        var unitPrice = positive(fields.get("unit_price"), "报价");
        var priceBasis = positive(fields.get("price_basis"), "计价数量");
        var taxRate = number(fields.get("tax_rate"), "税率");
        var shippingFee = number(fields.getOrDefault("shipping_fee", "0"), "运费");
        var moq = positive(fields.get("moq"), "MOQ");
        int leadDays = positive(fields.get("lead_time_days"), "交期").intValueExact();
        if (taxRate.signum() < 0 || taxRate.compareTo(BigDecimal.ONE) > 0 || shippingFee.signum() < 0) {
            throw invalidQuote("税率或运费超出允许范围");
        }
        var quotedUnit = unitPrice.divide(priceBasis, 24, RoundingMode.HALF_UP);
        var quotedGoods = quotedUnit.multiply(quantity);
        BigDecimal tax;
        BigDecimal goodsWithTax;
        if (Boolean.TRUE.equals(fields.get("tax_included"))) {
            goodsWithTax = quotedGoods;
            tax = taxRate.signum() == 0 ? BigDecimal.ZERO
                    : quotedGoods.subtract(quotedGoods.divide(BigDecimal.ONE.add(taxRate), 24, RoundingMode.HALF_UP));
        } else {
            tax = quotedGoods.multiply(taxRate);
            goodsWithTax = quotedGoods.add(tax);
        }
        var freight = Boolean.TRUE.equals(fields.get("shipping_included")) ? BigDecimal.ZERO : shippingFee;
        var landedQuote = goodsWithTax.add(freight);
        var landedBase = landedQuote.multiply(fxRate);
        var landedUnit = landedBase.divide(quantity, 24, RoundingMode.HALF_UP);
        var exclusions = new ArrayList<Map<String, String>>();
        var warnings = new ArrayList<String>();
        if (quantity.compareTo(moq) < 0) {
            exclude(exclusions, "moq", "起订量（MOQ）" + decimal(moq) + " 高于采购量 " + decimal(quantity));
        }
        int maxLead = integer(constraints.getOrDefault("max_lead_days", 0));
        if (maxLead > 0 && leadDays > maxLead) {
            exclude(exclusions, "lead_time", "交期 " + leadDays + " 天超过上限 " + maxLead + " 天");
        }
        if (Boolean.TRUE.equals(constraints.get("invoice_required"))
                && !Boolean.TRUE.equals(fields.get("supports_invoice"))) {
            exclude(exclusions, "invoice", "不能提供要求的发票");
        }
        var specChecks = task.getSchemaVersion() == 2
                ? dynamicChecks(task, quote, exclusions, warnings)
                : legacyChecks(task, quote, fields, exclusions);
        itemIdentityCheck(task, fields, exclusions, specChecks);
        var budget = constraints.get("max_landed_unit_cost");
        if (budget != null && landedUnit.compareTo(number(budget, "到货单价上限")) > 0) {
            exclude(exclusions, "budget", "到货单价 " + unitMoney(landedUnit) + " 超过上限 " + budget);
        }
        var validUntil = text(fields.get("valid_until"));
        if (!validUntil.isBlank()) {
            try {
                if (LocalDate.parse(validUntil).isBefore(asOf)) {
                    exclude(exclusions, "expired", "报价已于 " + validUntil + " 失效");
                }
            } catch (RuntimeException error) {
                warnings.add("报价有效期格式无法复核");
            }
        }
        var deadline = text(constraints.get("required_delivery_date"));
        if (!deadline.isBlank() && asOf.plusDays(leadDays).isAfter(LocalDate.parse(deadline))) {
            exclude(exclusions, "required_delivery_date", "预计到货日期晚于要求日期 " + deadline);
        }
        return new QuoteResult(
                quote.getId(),
                text(fields.getOrDefault("supplier_name", quote.getSupplierName())),
                exclusions.isEmpty(), exclusions, warnings, specChecks,
                currency, baseCurrency(task), fxRate, unitPrice, priceBasis,
                quotedUnit, quotedGoods, tax, freight, landedQuote, landedBase, landedUnit,
                moq, leadDays, taxRate,
                Boolean.TRUE.equals(fields.get("tax_included")),
                Boolean.TRUE.equals(fields.get("shipping_included")),
                Boolean.TRUE.equals(fields.get("supports_invoice")),
                text(fields.get("payment_terms")), validUntil,
                text(fields.get("item_description")));
    }

    private List<Map<String, Object>> legacyChecks(
            ProcurementTask task,
            ProcurementQuote quote,
            Map<String, Object> fields,
            List<Map<String, String>> exclusions) {
        var checks = new ArrayList<Map<String, Object>>();
        var specs = task.getSpecifications();
        exactCanonicalCheck(checks, exclusions, "material", "材质", specs.get("material"), fields.get("material"));
        exactCanonicalCheck(checks, exclusions, "color", "颜色", specs.get("color"), fields.get("color"));
        exactCheck(checks, exclusions, "print_colors", specs.get("print_colors"), fields.get("print_colors"));
        var tolerance = number(task.getConstraints().getOrDefault("size_tolerance_mm", "0"), "尺寸公差");
        var expectedWidth = number(specs.get("width_mm"), "需求宽度");
        var expectedLength = number(specs.get("length_mm"), "需求长度");
        var actualWidth = number(fields.get("width_mm"), "报价宽度");
        var actualLength = number(fields.get("length_mm"), "报价长度");
        boolean direct = within(actualWidth, expectedWidth, tolerance) && within(actualLength, expectedLength, tolerance);
        boolean swapped = within(actualWidth, expectedLength, tolerance) && within(actualLength, expectedWidth, tolerance);
        boolean dimensions = direct || swapped;
        checks.add(Map.of(
                "field", "dimensions_mm",
                "expected", decimal(expectedWidth) + " × " + decimal(expectedLength),
                "actual", decimal(actualWidth) + " × " + decimal(actualLength),
                "tolerance", decimal(tolerance),
                "orientation", direct ? "direct" : swapped ? "swapped" : "unmatched",
                "passed", dimensions));
        if (!dimensions) {
            if (!within(actualWidth, expectedWidth, tolerance)) {
                exclude(exclusions, "spec_width_mm", "报价宽度超出需求公差");
            }
            if (!within(actualLength, expectedLength, tolerance)) {
                exclude(exclusions, "spec_length_mm", "报价长度超出需求公差");
            }
        }
        var thicknessTolerance = number(task.getConstraints().getOrDefault("thickness_tolerance_um", "0"), "厚度公差");
        var expectedThickness = number(specs.get("thickness_um"), "需求厚度");
        var actualThickness = number(fields.get("thickness_um"), "报价厚度");
        boolean thickness = within(actualThickness, expectedThickness, thicknessTolerance);
        checks.add(Map.of(
                "field", "thickness_um", "expected", decimal(expectedThickness),
                "actual", decimal(actualThickness), "tolerance", decimal(thicknessTolerance), "passed", thickness));
        if (!thickness) {
            exclude(exclusions, "spec_thickness_um", "报价厚度超出需求公差");
        }
        if (specs.containsKey("height_mm")) {
            var expectedHeight = number(specs.get("height_mm"), "需求高度");
            var heightRaw = fields.get("height_mm");
            if (heightRaw == null) {
                heightRaw = dynamicSpecValue(quote, "高度", "height");
            }
            var actualHeight = number(heightRaw, "报价高度");
            boolean height = within(actualHeight, expectedHeight, tolerance);
            checks.add(Map.of(
                    "field", "height_mm", "expected", decimal(expectedHeight),
                    "actual", decimal(actualHeight), "tolerance", decimal(tolerance), "passed", height));
            if (!height) {
                exclude(exclusions, "spec_height_mm", "报价高度超出需求公差");
            }
        }
        return checks;
    }

    private List<Map<String, Object>> dynamicChecks(
            ProcurementTask task,
            ProcurementQuote quote,
            List<Map<String, String>> exclusions,
            List<String> warnings) {
        var checks = new ArrayList<Map<String, Object>>();
        var actualSpecs = map(quote.getExtracted().get("specifications"));
        var fields = fieldValues(quote);
        task.getSpecifications().forEach((key, raw) -> {
            var expected = map(raw);
            var actualEntry = dynamicActual(actualSpecs, fields, key, expected);
            var actual = actualEntry.value();
            var match = text(expected.getOrDefault("match", "exact"));
            boolean passed;
            if ("number".equals(text(expected.get("type")))) {
                var actualNumber = convertedNumber(actual, actualEntry.unit(), text(expected.get("unit")));
                passed = actualNumber != null && switch (match) {
                    case "tolerance" -> within(actualNumber, number(expected.get("value"), key), number(expected.get("tolerance"), key));
                    case "range" -> actualNumber.compareTo(number(expected.get("min"), key)) >= 0
                            && actualNumber.compareTo(number(expected.get("max"), key)) <= 0;
                    case "gte" -> actualNumber.compareTo(number(expected.get("value"), key)) >= 0;
                    case "lte" -> actualNumber.compareTo(number(expected.get("value"), key)) <= 0;
                    default -> actualNumber.compareTo(number(expected.get("value"), key)) == 0;
                };
            } else if ("boolean".equals(text(expected.get("type")))) {
                passed = Objects.equals(expected.get("value"), actual);
            } else {
                passed = isColorSpecification(key, expected)
                        ? normalizeColor(expected.get("value")).equals(normalizeColor(actual))
                        : normalized(expected.get("value")).equals(normalized(actual));
            }
            var check = new LinkedHashMap<String, Object>();
            check.put("field", key);
            check.put("label", text(expected.getOrDefault("label", key)));
            check.put("expected", expectedValue(expected));
            check.put("actual", actual == null ? "未识别" : text(actual));
            check.put("tolerance", text(expected.getOrDefault("tolerance", match)));
            check.put("match", match);
            check.put("priority", text(expected.getOrDefault("priority", "hard")));
            check.put("passed", passed);
            checks.add(check);
            if (!passed) {
                if ("preference".equals(expected.get("priority"))) {
                    warnings.add(check.get("label") + " 未满足偏好");
                } else {
                    exclude(exclusions, "spec_" + key, check.get("label") + " 不符合采购需求");
                }
            }
        });
        return checks;
    }

    private Object dynamicSpecValue(ProcurementQuote quote, String zhLabel, String key) {
        var actualSpecs = map(quote.getExtracted().get("specifications"));
        for (var entry : actualSpecs.entrySet()) {
            var candidate = map(entry.getValue());
            var label = text(candidate.getOrDefault("label", entry.getKey()));
            var identity = normalized(String.valueOf(entry.getKey())) + " " + normalized(label);
            if (identity.contains(normalized(zhLabel)) || identity.contains(key)) {
                return candidate.get("value");
            }
        }
        return null;
    }

    private DynamicActual dynamicActual(
            Map<String, Object> actualSpecs,
            Map<String, Object> fields,
            String key,
            Map<String, Object> expected) {
        if (actualSpecs.containsKey(key)) {
            return actual(actualSpecs.get(key), "");
        }
        var wantedLabel = normalized(expected.getOrDefault("label", key));
        for (var entry : actualSpecs.entrySet()) {
            var candidate = map(entry.getValue());
            var candidateLabel = normalized(candidate.getOrDefault("label", entry.getKey()));
            if (candidateLabel.equals(wantedLabel)) {
                return actual(entry.getValue(), "");
            }
        }
        var identity = normalized(key + " " + expected.getOrDefault("label", key));
        if (identity.contains("厚度") || identity.contains("thickness")) {
            return actual(fields.get("thickness_um"), "um");
        }
        if (identity.contains("宽度") || identity.contains("width")) {
            return actual(fields.get("width_mm"), "mm");
        }
        if (identity.contains("长度") || identity.contains("length")) {
            return actual(fields.get("length_mm"), "mm");
        }
        if (identity.contains("材质") || identity.contains("material")) {
            return actual(fields.get("material"), "");
        }
        if (identity.contains("印刷") || identity.contains("printcolor")) {
            return actual(fields.get("print_colors"), "");
        }
        if (identity.contains("颜色") || identity.contains("color")) {
            return actual(fields.get("color"), "");
        }
        return actual(fields.get(key), "");
    }

    private DynamicActual actual(Object raw, String defaultUnit) {
        var entry = map(raw);
        return entry.isEmpty()
                ? new DynamicActual(raw, defaultUnit)
                : new DynamicActual(entry.get("value"), text(entry.getOrDefault("unit", defaultUnit)));
    }

    private BigDecimal convertedNumber(Object value, String actualUnit, String expectedUnit) {
        if (value == null) {
            return null;
        }
        try {
            var number = new BigDecimal(text(value));
            var actualFactor = unitFactor(actualUnit.isBlank() ? expectedUnit : actualUnit);
            var expectedFactor = unitFactor(expectedUnit);
            if (actualFactor == null || expectedFactor == null) {
                return normalized(actualUnit).equals(normalized(expectedUnit)) ? number : null;
            }
            return number.multiply(actualFactor).divide(expectedFactor, 24, RoundingMode.HALF_UP);
        } catch (RuntimeException error) {
            return null;
        }
    }

    private BigDecimal unitFactor(String unit) {
        return switch (normalized(unit)) {
            case "", "mm", "毫米" -> BigDecimal.ONE;
            case "um", "μm", "µm", "微米" -> new BigDecimal("0.001");
            case "cm", "厘米" -> new BigDecimal("10");
            case "m", "米" -> new BigDecimal("1000");
            default -> null;
        };
    }

    private record DynamicActual(Object value, String unit) {}

    private String expectedValue(Map<String, Object> expected) {
        if (expected.containsKey("value")) {
            return text(expected.get("value"));
        }
        return text(expected.get("min")) + ".." + text(expected.get("max"));
    }

    private void exactCheck(
            List<Map<String, Object>> checks,
            List<Map<String, String>> exclusions,
            String field,
            Object expected,
            Object actual) {
        boolean passed = normalized(expected).equals(normalized(actual));
        checks.add(Map.of(
                "field", field, "expected", text(expected), "actual", text(actual),
                "tolerance", "exact", "passed", passed));
        if (!passed) {
            exclude(exclusions, "spec_" + field, field + " 不符合采购需求");
        }
    }

    private String normalizeColor(Object value) {
        var text = normalized(value);
        return switch (text) {
            case "白色", "white" -> "white";
            case "黑色", "black" -> "black";
            case "红色", "red" -> "red";
            case "蓝色", "blue" -> "blue";
            case "绿色", "green" -> "green";
            case "黄色", "yellow" -> "yellow";
            case "灰色", "grey", "gray" -> "gray";
            case "透明", "透明色", "transparent", "clear" -> "transparent";
            default -> text;
        };
    }

    private boolean isColorSpecification(String key, Map<String, Object> expected) {
        var identity = normalized(key + " " + expected.getOrDefault("label", key));
        return identity.contains("color") || identity.contains("颜色") || identity.contains("色彩");
    }

    private boolean matchesItem(String expected, String description) {
        var expectedIdentity = canonicalItem(expected);
        return expectedIdentity != null && expectedIdentity.equals(canonicalItem(description));
    }

    private void itemIdentityCheck(
            ProcurementTask task,
            Map<String, Object> fields,
            List<Map<String, String>> exclusions,
            List<Map<String, Object>> specChecks) {
        var requested = task.getItemName();
        if (requested.isBlank()) {
            return;
        }
        var description = text(fields.get("item_description"));
        var expected = canonicalItem(requested);
        if (expected == null) {
            specChecks.add(Map.of(
                    "field", "item_identity", "expected", requested,
                    "actual", description, "tolerance", "exact", "passed", false));
            exclude(exclusions, "item_identity",
                    "无法复核物料一致性（需求物料“" + requested + "”不在可识别范围内）");
            return;
        }
        boolean passed = expected.equals(canonicalItem(description));
        specChecks.add(Map.of(
                "field", "item_identity", "expected", requested,
                "actual", description, "tolerance", "exact", "passed", passed));
        if (!passed) {
            exclude(exclusions, "item_identity",
                    "报价物料“" + (description.isBlank() ? "未识别" : description) + "”与需求“" + requested + "”不一致");
        }
    }

    private void exactCanonicalCheck(
            List<Map<String, Object>> checks,
            List<Map<String, String>> exclusions,
            String field,
            String label,
            Object expectedRaw,
            Object actualRaw) {
        String expected = "material".equals(field) ? canonicalMaterial(expectedRaw) : canonicalColor(expectedRaw);
        String actual = "material".equals(field) ? canonicalMaterial(actualRaw) : canonicalColor(actualRaw);
        if (expected == null) {
            if (!text(expectedRaw).isBlank()) {
                checks.add(Map.of(
                        "field", field, "expected", text(expectedRaw),
                        "actual", actual == null ? "未识别" : actual,
                        "tolerance", "exact", "passed", false));
                exclude(exclusions, "spec_" + field,
                        "无法复核" + label + "一致性（需求值“" + text(expectedRaw) + "”不在可识别范围内）");
            }
            return;
        }
        boolean passed = actual != null && actual.equals(expected);
        checks.add(Map.of(
                "field", field, "expected", expected,
                "actual", actual == null ? "未识别" : actual,
                "tolerance", "exact", "passed", passed));
        if (!passed) {
            exclude(exclusions, "spec_" + field,
                    label + " " + (actual == null ? "未识别" : actual) + " 不符合需求 " + expected);
        }
    }

    private String canonicalMaterial(Object value) {
        return canonicalAlias(value, Map.of(
                "PE", List.of("pe", "聚乙烯", "polyethylene"),
                "PVC", List.of("pvc", "聚氯乙烯", "polyvinylchloride"),
                "PP", List.of("pp", "聚丙烯", "polypropylene"),
                "bopp", List.of("bopp", "双向拉伸聚丙烯"),
                "PET", List.of("pet", "聚对苯二甲酸乙二醇"),
                "PLA", List.of("pla", "聚乳酸"),
                "corrugated", List.of("瓦楞", "corrugated", "cardboard"),
                "kraft", List.of("牛皮", "kraft"),
                "coated_paper", List.of("铜版纸", "coatedpaper", "artpaper")));
    }

    private String canonicalColor(Object value) {
        return canonicalAlias(value, Map.of(
                "white", List.of("白色", "白", "white"),
                "black", List.of("黑色", "黑", "black"),
                "transparent", List.of("透明", "transparent", "clear"),
                "red", List.of("红色", "红", "red"),
                "blue", List.of("蓝色", "蓝", "blue"),
                "kraft", List.of("牛皮色", "牛皮", "牛卡", "kraft")));
    }

    private String canonicalAlias(Object value, Map<String, List<String>> aliases) {
        var raw = text(value).strip().toLowerCase(Locale.ROOT);
        if (raw.isBlank()) {
            return null;
        }
        for (var entry : aliases.entrySet()) {
            for (var alias : entry.getValue()) {
                if (Pattern.compile("(?<![a-z])" + Pattern.quote(alias) + "(?![a-z])").matcher(raw).find()) {
                    return entry.getKey();
                }
            }
        }
        return null;
    }

    private String canonicalItem(Object value) {
        var raw = text(value).strip().toLowerCase(Locale.ROOT);
        var item = raw.replaceAll("\\s+", "");
        if (List.of("快递袋", "快递包装袋", "mailer", "mailingbag", "courierbag").stream()
                .anyMatch(item::contains)) {
            return "mailer";
        }
        if (List.of("垃圾袋", "trashbag", "garbagebag", "binliner").stream()
                .anyMatch(item::contains)) {
            return "trash_bag";
        }
        if (List.of("气泡膜", "气泡袋", "气泡垫", "bubblewrap", "bubblefilm", "bubble").stream()
                .anyMatch(item::contains)) {
            return "bubble";
        }
        if (List.of("缠绕膜", "拉伸膜", "stretchfilm", "stretchwrap", "stretch").stream()
                .anyMatch(item::contains)) {
            return "stretch";
        }
        if (List.of("封箱胶带", "胶带", "tape").stream()
                .anyMatch(item::contains)) {
            return "tape";
        }
        if (List.of("珍珠棉", "epe", "pefoam", "foam").stream()
                .anyMatch(item::contains)) {
            return "foam";
        }
        if (List.of("不干胶标签", "热敏标签", "标签", "不干胶", "thermallabel", "sticker", "label").stream()
                .anyMatch(item::contains)) {
            return "label";
        }
        if (List.of("纸箱", "包装箱", "carton", "corrugated").stream()
                .anyMatch(item::contains)) {
            return "carton";
        }
        if (Pattern.compile("\\bbox(?:es)?\\b").matcher(raw).find()) {
            return "carton";
        }
        return null;
    }

    private boolean within(BigDecimal actual, BigDecimal expected, BigDecimal tolerance) {
        return actual.subtract(expected).abs().compareTo(tolerance) <= 0;
    }

    private void exclude(List<Map<String, String>> exclusions, String code, String message) {
        exclusions.add(Map.of("code", code, "message", message));
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> map(Object value) {
        return value instanceof Map<?, ?> raw ? (Map<String, Object>) raw : Map.of();
    }

    private Map<String, Object> fieldValues(ProcurementQuote quote) {
        var result = new LinkedHashMap<String, Object>();
        map(quote.getExtracted().get("fields")).forEach((key, value) -> {
            var entry = map(value);
            result.put(key, entry.isEmpty() ? value : entry.get("value"));
        });
        return result;
    }

    private BigDecimal number(Object value, String label) {
        if (value == null || value instanceof Boolean) {
            throw invalidQuote(label + "不是有效数值");
        }
        try {
            var number = new BigDecimal(String.valueOf(value));
            if (number.precision() > 60 || number.scale() > 1000 || number.scale() < -1000) {
                throw invalidQuote(label + "精度超出安全范围");
            }
            return number;
        } catch (NumberFormatException error) {
            throw invalidQuote(label + "不是有效数值");
        }
    }

    private BigDecimal positive(Object value, String label) {
        var number = number(value, label);
        if (number.signum() <= 0) {
            throw invalidQuote(label + "必须大于 0");
        }
        return number;
    }

    private int integer(Object value) {
        return value == null ? 0 : number(value, "整数").intValueExact();
    }

    private ApiException invalidQuote(String message) {
        return new ApiException(HttpStatus.UNPROCESSABLE_ENTITY, "invalid_quote", message);
    }

    private String baseCurrency(ProcurementTask task) {
        return text(task.getConstraints().getOrDefault("base_currency", "CNY")).toUpperCase(Locale.ROOT);
    }

    private String normalized(Object value) {
        return text(value).strip().toLowerCase(Locale.ROOT).replaceAll("\\s+", "");
    }

    private String text(Object value) {
        return value == null ? "" : String.valueOf(value);
    }

    private String decimal(BigDecimal value) {
        return CanonicalJson.decimal(value);
    }

    private String money(BigDecimal value) {
        return value.setScale(MONEY.scale(), RoundingMode.HALF_UP).toPlainString();
    }

    private String unitMoney(BigDecimal value) {
        return value.setScale(UNIT_MONEY.scale(), RoundingMode.HALF_UP).toPlainString();
    }

    public record Calculation(
            String inputSha256,
            Map<String, Object> canonicalInput,
            Map<String, Object> result) {}

    private record QuoteResult(
            String quoteId,
            String supplierName,
            boolean eligible,
            List<Map<String, String>> exclusionReasons,
            List<String> warnings,
            List<Map<String, Object>> specChecks,
            String quoteCurrency,
            String baseCurrency,
            BigDecimal fxRate,
            BigDecimal quotedPrice,
            BigDecimal priceBasis,
            BigDecimal normalizedUnit,
            BigDecimal goodsBeforeTax,
            BigDecimal tax,
            BigDecimal freight,
            BigDecimal landedQuote,
            BigDecimal landedTotal,
            BigDecimal landedUnit,
            BigDecimal moq,
            int leadDays,
            BigDecimal taxRate,
            boolean taxIncluded,
            boolean shippingIncluded,
            boolean supportsInvoice,
            String paymentTerms,
            String validUntil,
            String description) {

        Map<String, Object> toMap(Integer rank, String score) {
            var value = new LinkedHashMap<String, Object>();
            value.put("quote_id", quoteId);
            value.put("supplier_name", supplierName);
            value.put("eligible", eligible);
            value.put("exclusion_reasons", exclusionReasons);
            value.put("warnings", warnings);
            value.put("match", Map.of(
                    "item", "",
                    "quoted_description", description,
                    "passed", exclusionReasons.stream().noneMatch(item -> item.get("code").startsWith("spec_") || "item_identity".equals(item.get("code"))),
                    "spec_checks", specChecks));
            var commercial = new LinkedHashMap<String, Object>();
            commercial.put("moq", decimalValue(moq));
            commercial.put("lead_time_days", leadDays);
            commercial.put("tax_rate", decimalValue(taxRate));
            commercial.put("tax_included", taxIncluded);
            commercial.put("shipping_included", shippingIncluded);
            commercial.put("supports_invoice", supportsInvoice);
            commercial.put("payment_terms", paymentTerms.isBlank() ? null : paymentTerms);
            commercial.put("valid_until", validUntil.isBlank() ? null : validUntil);
            value.put("commercial", commercial);
            value.put("cost", Map.ofEntries(
                    Map.entry("quote_currency", quoteCurrency),
                    Map.entry("base_currency", baseCurrency),
                    Map.entry("fx_rate", decimalValue(fxRate)),
                    Map.entry("quoted_price", decimalValue(quotedPrice)),
                    Map.entry("price_basis", decimalValue(priceBasis)),
                    Map.entry("normalized_unit_quote_currency", unitValue(normalizedUnit)),
                    Map.entry("goods_before_tax_quote_currency", moneyValue(goodsBeforeTax)),
                    Map.entry("tax_quote_currency", moneyValue(tax)),
                    Map.entry("freight_quote_currency", moneyValue(freight)),
                    Map.entry("landed_total_quote_currency", moneyValue(landedQuote)),
                    Map.entry("landed_total_base", moneyValue(landedTotal)),
                    Map.entry("landed_unit_base", unitValue(landedUnit))));
            value.put("rank", rank);
            value.put("score", score);
            return value;
        }

        private String decimalValue(BigDecimal value) { return CanonicalJson.decimal(value); }
        private String moneyValue(BigDecimal value) { return value.setScale(2, RoundingMode.HALF_UP).toPlainString(); }
        private String unitValue(BigDecimal value) { return value.setScale(4, RoundingMode.HALF_UP).toPlainString(); }
    }
}
