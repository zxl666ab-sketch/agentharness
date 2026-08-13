package com.caijiatai.procurement.cache;

import java.util.List;
import java.util.Map;
import java.util.function.Supplier;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

/** Redis 禁用时的缓存回退：loader 直读直写（不缓存）。 */
@Component
@ConditionalOnProperty(prefix = "app.redis", name = "enabled", havingValue = "false", matchIfMissing = true)
public final class NoopInsightsCache implements InsightsCache {
    @Override
    public Map<String, Object> getOrLoad(String name, Supplier<Map<String, Object>> loader) {
        return loader.get();
    }

    @Override
    public List<Map<String, Object>> getListOrLoad(String name, Supplier<List<Map<String, Object>>> loader) {
        return loader.get();
    }

    @Override
    public void evictAll() {
        // 无缓存无需失效
    }
}
