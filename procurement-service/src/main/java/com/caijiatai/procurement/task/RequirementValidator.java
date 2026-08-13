package com.caijiatai.procurement.task;

import com.caijiatai.procurement.api.ApiException;
import java.math.BigDecimal;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import org.springframework.http.HttpStatus;

/** One validation boundary for both human-confirmed and Agent-produced requirements. */
public final class RequirementValidator {
    private RequirementValidator() {}

    public static void validate(
            int schemaVersion,
            String category,
            String itemName,
            BigDecimal quantity,
            String unit,
            Map<String, Object> specifications,
            BigDecimal sizeToleranceMm,
            BigDecimal thicknessToleranceUm,
            BigDecimal maxLandedUnitCost) {
        if (schemaVersion < 1 || schemaVersion > 2) {
            throw bad("invalid_schema_version", "仅支持采购需求 schema_version 1 或 2");
        }
        if (itemName == null || itemName.isBlank() || itemName.length() > 200) {
            throw bad("invalid_item_name", "品名不能为空且不得超过 200 个字符");
        }
        if (specifications == null) {
            throw bad("invalid_specifications", "采购需求必须提供规格字段");
        }
        if (schemaVersion == 1) {
            validateLegacy(category, unit, itemName, specifications);
        } else {
            validateDynamic(category, unit, specifications);
        }
        if (quantity == null || quantity.signum() <= 0 || quantity.compareTo(new BigDecimal("100000000")) > 0) {
            throw bad("invalid_quantity", "采购数量必须大于 0 且不得超过 1 亿");
        }
        if (schemaVersion == 1) {
            validateLegacyDimensions(itemName, specifications);
        }
        if (sizeToleranceMm != null) {
            rangeDecimal(sizeToleranceMm, "尺寸公差", BigDecimal.ZERO, new BigDecimal("100"));
        }
        if (thicknessToleranceUm != null) {
            rangeDecimal(thicknessToleranceUm, "厚度公差", BigDecimal.ZERO, new BigDecimal("5000"));
        }
        if (maxLandedUnitCost != null && maxLandedUnitCost.signum() <= 0) {
            throw bad("invalid_constraints", "到货单价上限必须大于 0");
        }
    }

    private static void validateLegacy(
            String category, String unit, String itemName, Map<String, Object> specifications) {
        var required = List.of("width_mm", "length_mm", "thickness_um", "material", "color", "print_colors");
        if (!specifications.keySet().containsAll(required)) {
            throw bad("invalid_v1_specifications", "V1 包装需求缺少固定规格字段");
        }
        if (!"ecommerce_packaging".equals(category)) {
            throw bad("invalid_category", "V1 采购域仅支持 ecommerce_packaging");
        }
        if (!"piece".equals(unit)) {
            throw bad("invalid_unit", "V1 采购单位仅支持 piece");
        }
    }

    private static void validateLegacyDimensions(String itemName, Map<String, Object> specifications) {
        positiveDecimal(specifications.get("width_mm"), "宽度", new BigDecimal("10000"));
        positiveDecimal(specifications.get("length_mm"), "长度", new BigDecimal("10000000"));
        positiveDecimal(specifications.get("thickness_um"), "厚度", new BigDecimal("5000"));
        if (specifications.get("print_colors") != null) {
            int printColors = integerValue(specifications.get("print_colors"), "印刷色数");
            if (printColors < 0 || printColors > 12) {
                throw bad("invalid_print_colors", "印刷色数必须在 0 到 12 之间");
            }
        }
        if (specifications.containsKey("height_mm")) {
            positiveDecimal(specifications.get("height_mm"), "高度", new BigDecimal("10000"));
        } else if (requiresHeight(itemName)) {
            throw bad("invalid_specifications", "纸箱采购规格必须提供高度 height_mm");
        }
    }

    private static void validateDynamic(
            String category, String unit, Map<String, Object> specifications) {
        if (category == null || category.isBlank() || category.length() > 100) {
            throw bad("invalid_category", "V2 采购品类不能为空且不得超过 100 个字符");
        }
        if (unit == null || unit.isBlank() || unit.length() > 50) {
            throw bad("invalid_unit", "V2 采购单位不能为空且不得超过 50 个字符");
        }
        if (specifications.isEmpty()) {
            throw bad("invalid_specifications", "V2 采购需求至少需要一项动态规格");
        }
        specifications.forEach((key, raw) -> validateDynamicSpec(key, raw));
    }

    private static void validateDynamicSpec(String key, Object raw) {
        if (key == null || key.isBlank() || key.length() > 100 || !(raw instanceof Map<?, ?>)) {
            throw bad("invalid_dynamic_spec", "动态规格结构无效：" + key);
        }
        var spec = map(raw);
        var label = String.valueOf(spec.getOrDefault("label", ""));
        var type = String.valueOf(spec.get("type"));
        var match = String.valueOf(spec.get("match"));
        var priority = String.valueOf(spec.get("priority"));
        if (label.isBlank() || label.length() > 100) {
            throw bad("invalid_dynamic_spec", "动态规格名称无效：" + key);
        }
        if (!List.of("number", "text", "boolean").contains(type)) {
            throw bad("invalid_dynamic_spec", "动态规格类型无效：" + key);
        }
        if (!List.of("exact", "tolerance", "range", "gte", "lte").contains(match)) {
            throw bad("invalid_dynamic_spec", "动态规格匹配方式无效：" + key);
        }
        if (!List.of("hard", "preference").contains(priority)) {
            throw bad("invalid_dynamic_spec", "动态规格优先级无效：" + key);
        }
        if ("number".equals(type)) {
            validateDynamicNumber(key, label, match, spec);
            return;
        }
        if (!"exact".equals(match)) {
            throw bad("invalid_dynamic_spec", "文本和布尔规格仅支持完全一致：" + key);
        }
        var value = spec.get("value");
        if ("boolean".equals(type) && !(value instanceof Boolean)) {
            throw bad("invalid_dynamic_spec", "布尔规格值无效：" + key);
        }
        if ("text".equals(type)
                && (value == null || String.valueOf(value).isBlank() || String.valueOf(value).length() > 500)) {
            throw bad("invalid_dynamic_spec", "文本规格值无效：" + key);
        }
    }

    private static void validateDynamicNumber(
            String key, String label, String match, Map<String, Object> spec) {
        var unit = String.valueOf(spec.getOrDefault("unit", ""));
        if (unit.length() > 30) {
            throw bad("invalid_dynamic_spec", "动态规格单位过长：" + key);
        }
        if ("range".equals(match)) {
            var min = dynamicDecimal(spec.get("min"), key);
            var max = dynamicDecimal(spec.get("max"), key);
            if (min.compareTo(max) > 0) {
                throw bad("invalid_dynamic_spec", "动态规格范围上下限倒置：" + key);
            }
            validateDimensionBound(key, label, unit, min);
            validateDimensionBound(key, label, unit, max);
            return;
        }
        var value = dynamicDecimal(spec.get("value"), key);
        validateDimensionBound(key, label, unit, value);
        if ("tolerance".equals(match)) {
            var tolerance = dynamicDecimal(spec.get("tolerance"), key);
            if (tolerance.signum() < 0) {
                throw bad("invalid_dynamic_spec", "动态规格公差不得为负数：" + key);
            }
        }
    }

    private static BigDecimal dynamicDecimal(Object value, String key) {
        if (value == null || String.valueOf(value).isBlank()) {
            throw bad("invalid_dynamic_spec", "动态数值规格缺少值：" + key);
        }
        try {
            var number = new BigDecimal(String.valueOf(value));
            if (number.precision() > 60 || number.scale() > 1000 || number.scale() < -1000
                    || number.abs().compareTo(new BigDecimal("1000000000000000000")) > 0) {
                throw bad("invalid_dynamic_spec", "动态数值规格超出安全范围：" + key);
            }
            return number;
        } catch (NumberFormatException error) {
            throw bad("invalid_dynamic_spec", "动态数值规格不是有效数字：" + key);
        }
    }

    private static void validateDimensionBound(
            String key, String label, String unit, BigDecimal value) {
        var identity = (key + " " + label).toLowerCase(Locale.ROOT).replaceAll("[\\s_-]+", "");
        String kind = null;
        BigDecimal maxMillimetres = null;
        if (identity.contains("thickness") || identity.contains("厚度")) {
            kind = "thickness";
            maxMillimetres = new BigDecimal("5");
        } else if (identity.contains("width") || identity.contains("宽度")) {
            kind = "width";
            maxMillimetres = new BigDecimal("10000");
        } else if (identity.contains("height") || identity.contains("高度")) {
            kind = "height";
            maxMillimetres = new BigDecimal("10000");
        } else if (identity.contains("length") || identity.contains("长度")) {
            kind = "length";
            maxMillimetres = new BigDecimal("10000000");
        }
        if (kind == null) {
            return;
        }
        if (value.signum() <= 0) {
            throw bad("invalid_dynamic_spec", "尺寸动态规格必须大于 0：" + key);
        }
        var normalizedUnit = unit.strip().toLowerCase(Locale.ROOT);
        if (normalizedUnit.isBlank()) {
            normalizedUnit = "thickness".equals(kind) ? "um" : "mm";
        }
        var factor = switch (normalizedUnit) {
            case "mm", "毫米" -> BigDecimal.ONE;
            case "um", "μm", "µm", "微米" -> new BigDecimal("0.001");
            case "cm", "厘米" -> new BigDecimal("10");
            case "m", "米" -> new BigDecimal("1000");
            default -> null;
        };
        if (factor == null) {
            throw bad("invalid_dynamic_spec", "尺寸动态规格单位无效：" + key);
        }
        if (value.multiply(factor).compareTo(maxMillimetres) > 0) {
            throw bad("invalid_dynamic_spec", "尺寸动态规格超过安全上限：" + key);
        }
    }

    private static void positiveDecimal(Object value, String label, BigDecimal max) {
        if (value == null || String.valueOf(value).isBlank()) {
            throw bad("invalid_specifications", "缺少规格字段：" + label);
        }
        try {
            var number = new BigDecimal(String.valueOf(value));
            if (number.signum() <= 0) throw bad("invalid_specifications", label + "必须大于 0");
            if (number.compareTo(max) > 0) {
                throw bad("invalid_specifications", label + "超过上限 " + max.toPlainString());
            }
        } catch (NumberFormatException error) {
            throw bad("invalid_specifications", label + "不是有效数值");
        }
    }

    private static void rangeDecimal(BigDecimal value, String label, BigDecimal min, BigDecimal max) {
        if (value.compareTo(min) < 0 || value.compareTo(max) > 0) {
            throw bad("invalid_constraints", label + "必须在 " + min.toPlainString() + " 到 " + max.toPlainString() + " 之间");
        }
    }

    private static int integerValue(Object value, String label) {
        try {
            return new BigDecimal(String.valueOf(value)).intValueExact();
        } catch (RuntimeException error) {
            throw bad("invalid_" + label, label + "必须是整数");
        }
    }

    private static boolean requiresHeight(String itemName) {
        var text = itemName == null ? "" : itemName.toLowerCase(Locale.ROOT);
        if (text.contains("胶带") || text.matches(".*\\btape\\b.*")) return false;
        if (List.of("纸箱", "包装箱", "carton", "corrugated").stream().anyMatch(text::contains)) return true;
        return text.matches(".*\\bbox(?:es)?\\b.*") && !text.contains("tape");
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> map(Object value) {
        return value instanceof Map<?, ?> raw ? (Map<String, Object>) raw : Map.of();
    }

    private static ApiException bad(String code, String message) {
        return new ApiException(HttpStatus.BAD_REQUEST, code, message);
    }
}
