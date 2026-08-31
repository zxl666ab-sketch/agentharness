package com.caijiatai.procurement.agent;

import static org.assertj.core.api.Assertions.assertThat;

import java.math.BigDecimal;
import org.junit.jupiter.api.Test;

class ModelPricingServiceTest {
    private static final String PRICING_JSON = """
            {"hy3":{"input_per_million_usd":1,"output_per_million_usd":2,"cached_input_per_million_usd":0.5},
             "tiny":{"input_per_million_usd":0.1,"output_per_million_usd":0.2}}
            """;

    @Test
    void parsesExactAndFallbackLookup() {
        var service = new ModelPricingService(PRICING_JSON);
        assertThat(service.priced("hy3")).isTrue();
        assertThat(service.priced("tiny")).isTrue();
        assertThat(service.priced("mystery-model")).isFalse();

        var wildcard = new ModelPricingService("{\"*\":{\"input_per_million_usd\":1,\"output_per_million_usd\":2}}");
        assertThat(wildcard.priced("anything")).isTrue();
        // 精确键优先于 * 兜底：1M 输入 token × $1/百万 = $1.0000000000（若误用 * 键会是 $9）
        var mixed = new ModelPricingService("{\"*\":{\"input_per_million_usd\":9,\"output_per_million_usd\":9},"
                + "\"hy3\":{\"input_per_million_usd\":1,\"output_per_million_usd\":2}}");
        assertThat(mixed.costUsd("hy3", 1_000_000, 0, 0)).contains(new BigDecimal("1.0000000000"));
    }

    @Test
    void costMirrorsPythonFormulaWithCacheDiscount() {
        var service = new ModelPricingService(PRICING_JSON);
        // input 1000（其中 600 命中缓存按 0.5 计）+ output 500 × 2 → (400×1 + 600×0.5 + 500×2)/1e6 = 0.0017
        assertThat(service.costUsd("hy3", 1000, 600, 500)).contains(new BigDecimal("0.0017000000"));
        // 未配置缓存价的模型：缓存不打折，全额按输入价
        assertThat(service.costUsd("tiny", 1000, 600, 500)).contains(new BigDecimal("0.0002000000"));
        // cached 超过 input 时钳制到 input（与 Python min 一致）
        assertThat(service.costUsd("hy3", 100, 999, 0)).contains(new BigDecimal("0.0000500000"));
    }

    @Test
    void malformedOrMissingEnvIsHonestUnpriced() {
        var broken = new ModelPricingService("{not json");
        assertThat(broken.configured()).isFalse();
        assertThat(broken.parseError()).isPresent();
        assertThat(broken.costUsd("hy3", 100, 0, 100)).isEmpty();

        var empty = new ModelPricingService("");
        assertThat(empty.configured()).isFalse();
        assertThat(empty.parseError()).isEmpty();

        // 只有输入价没有输出价 → known()=false → 未计价，绝不部分计价
        var half = new ModelPricingService("{\"hy3\":{\"input_per_million_usd\":1}}");
        assertThat(half.priced("hy3")).isFalse();
    }

    @Test
    void snapshotExposesPricesForConfigEndpoint() {
        var snapshot = new ModelPricingService(PRICING_JSON).snapshot();
        assertThat(snapshot).containsKeys("hy3", "tiny");
        assertThat(snapshot.get("hy3").get("cached_input_per_million_usd"))
                .isEqualTo(new BigDecimal("0.5"));
    }
}
