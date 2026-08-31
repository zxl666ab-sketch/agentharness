package com.caijiatai.procurement.agent;

import com.fasterxml.jackson.databind.JsonNode;
import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Optional;
import org.springframework.stereotype.Service;

/**
 * 模型定价（成本面板唯一计价真源，Java 侧）。
 *
 * <p>配置来自环境变量 {@code PROCUREMENT_MODEL_PRICING}：JSON 对象，键为模型名
 * （与 runtime {@code model_turn_end} 事件的 model 字段一致，{@code "*"} 为兜底键），
 * 值为 {@code {input_per_million_usd, output_per_million_usd, cached_input_per_million_usd}}
 * （美元/百万 token）。
 *
 * <p>公式与 Python runtime {@code _cost_usd} 完全同口径：缓存命中从输入 token 中扣除后
 * 按缓存价单独计价；未配置缓存价时不打折（按全价输入计）。输入与输出价同时存在才算"已知价"。
 *
 * <p>未配置价格时如实标记未计价，绝不解释为免费（README「模型配置」节既定口径）。
 */
@Service
public final class ModelPricingService {
    public static final String ENV_KEY = "PROCUREMENT_MODEL_PRICING";
    private static final BigDecimal MILLION = BigDecimal.valueOf(1_000_000L);

    /** 单模型每百万 token 单价；cached 可为 null（表示缓存不打折）。 */
    public record Price(BigDecimal inputPerMillionUsd, BigDecimal outputPerMillionUsd,
            BigDecimal cachedInputPerMillionUsd) {
        public boolean known() {
            return inputPerMillionUsd != null && outputPerMillionUsd != null;
        }
    }

    private final Map<String, Price> byModel;
    private final String parseError;

    public ModelPricingService() {
        this(System.getenv(ENV_KEY));
    }

    /** 包私有构造：测试直接注入 env 原文。Jackson 2 无托管 bean（Boot4 默认 Jackson 3），自建实例。 */
    ModelPricingService(String rawEnv) {
        var mapper = new com.fasterxml.jackson.databind.ObjectMapper();
        var parsed = new LinkedHashMap<String, Price>();
        String error = null;
        if (rawEnv != null && !rawEnv.isBlank()) {
            try {
                JsonNode root = mapper.readTree(rawEnv);
                if (!root.isObject()) {
                    error = ENV_KEY + " 必须是 JSON 对象";
                } else {
                    root.fields().forEachRemaining(entry -> {
                        JsonNode node = entry.getValue();
                        parsed.put(entry.getKey(), new Price(
                                decimal(node.get("input_per_million_usd")),
                                decimal(node.get("output_per_million_usd")),
                                decimal(node.get("cached_input_per_million_usd"))));
                    });
                }
            } catch (Exception exception) {
                error = ENV_KEY + " 解析失败：" + exception.getMessage();
            }
        }
        this.byModel = Map.copyOf(parsed);
        this.parseError = error;
    }

    private static BigDecimal decimal(JsonNode node) {
        if (node == null || node.isNull()) {
            return null;
        }
        try {
            return new BigDecimal(node.asText());
        } catch (NumberFormatException exception) {
            return null;
        }
    }

    /** 精确模型名优先，其次 "*" 兜底；都没有则未计价。 */
    public Optional<Price> priceFor(String model) {
        var exact = byModel.get(model == null ? "" : model);
        if (exact != null) {
            return Optional.of(exact);
        }
        return Optional.ofNullable(byModel.get("*"));
    }

    public boolean priced(String model) {
        return priceFor(model).filter(Price::known).isPresent();
    }

    /**
     * 与 Python {@code _cost_usd} 同口径的单模型成本；未知价返回 empty。
     * 结果保留 10 位小数（与 Python round(cost, 10) 一致）。
     */
    public Optional<BigDecimal> costUsd(String model, long inputTokens, long cachedInputTokens,
            long outputTokens) {
        var optional = priceFor(model).filter(Price::known);
        if (optional.isEmpty()) {
            return Optional.empty();
        }
        var price = optional.get();
        long cached = Math.max(0, Math.min(cachedInputTokens, Math.max(0, inputTokens)));
        BigDecimal inputCost;
        if (cached > 0 && price.cachedInputPerMillionUsd() != null) {
            inputCost = BigDecimal.valueOf(inputTokens - cached).multiply(price.inputPerMillionUsd())
                    .add(BigDecimal.valueOf(cached).multiply(price.cachedInputPerMillionUsd()));
        } else {
            inputCost = BigDecimal.valueOf(Math.max(0, inputTokens)).multiply(price.inputPerMillionUsd());
        }
        var total = inputCost.add(BigDecimal.valueOf(Math.max(0, outputTokens)).multiply(price.outputPerMillionUsd()))
                .divide(MILLION, 10, RoundingMode.HALF_UP);
        return Optional.of(total);
    }

    /** 脱敏后的定价快照（价格本身不敏感，供 config/costs 端点展示）。 */
    public Map<String, Map<String, Object>> snapshot() {
        var result = new LinkedHashMap<String, Map<String, Object>>();
        byModel.forEach((model, price) -> {
            var value = new LinkedHashMap<String, Object>();
            value.put("input_per_million_usd", price.inputPerMillionUsd());
            value.put("output_per_million_usd", price.outputPerMillionUsd());
            value.put("cached_input_per_million_usd", price.cachedInputPerMillionUsd());
            result.put(model, value);
        });
        return result;
    }

    public boolean configured() {
        return !byModel.isEmpty();
    }

    public Optional<String> parseError() {
        return Optional.ofNullable(parseError);
    }
}
