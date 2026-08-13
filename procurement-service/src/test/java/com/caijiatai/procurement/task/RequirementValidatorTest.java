package com.caijiatai.procurement.task;

import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.caijiatai.procurement.api.ApiException;
import java.math.BigDecimal;
import java.util.LinkedHashMap;
import java.util.Map;
import org.junit.jupiter.api.Test;

class RequirementValidatorTest {
    @Test
    void acceptsV2DynamicDimensionsWithConvertibleUnits() {
        RequirementValidator.validate(
                2, "ecommerce_packaging", "BOPP透明封箱胶带",
                new BigDecimal("3000"), "roll", tapeSpecifications(),
                null, null, new BigDecimal("4.20"));
    }

    @Test
    void rejectsMalformedV2Ranges() {
        var specs = new LinkedHashMap<>(tapeSpecifications());
        specs.put("length", Map.of(
                "label", "长度", "type", "number", "unit", "m",
                "match", "range", "priority", "hard", "min", "100", "max", "50"));

        assertThatThrownBy(() -> RequirementValidator.validate(
                2, "ecommerce_packaging", "BOPP透明封箱胶带",
                new BigDecimal("3000"), "roll", specs,
                null, null, new BigDecimal("4.20")))
                .isInstanceOfSatisfying(ApiException.class, error ->
                        org.assertj.core.api.Assertions.assertThat(error.code())
                                .isEqualTo("invalid_dynamic_spec"));
    }

    static Map<String, Object> tapeSpecifications() {
        var specs = new LinkedHashMap<String, Object>();
        specs.put("width", number("宽度", "48", "mm"));
        specs.put("length", number("长度", "100", "m"));
        specs.put("thickness", number("厚度", "50", "µm"));
        specs.put("material", text("材质", "BOPP"));
        specs.put("color", text("颜色", "透明"));
        specs.put("print_colors", number("印刷色数", "0", ""));
        return specs;
    }

    private static Map<String, Object> number(String label, String value, String unit) {
        return Map.of(
                "label", label, "type", "number", "value", value, "unit", unit,
                "match", "exact", "priority", "hard");
    }

    private static Map<String, Object> text(String label, String value) {
        return Map.of(
                "label", label, "type", "text", "value", value,
                "match", "exact", "priority", "hard");
    }
}
