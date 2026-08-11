package com.caijiatai.procurement.cache;

import java.util.Map;
import java.util.Optional;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

@Component
@ConditionalOnProperty(prefix = "app.redis", name = "enabled", havingValue = "false", matchIfMissing = true)
public final class NoopTaskContextCache implements TaskContextCache {
    @Override
    public Optional<Map<String, Object>> get(String taskId, int generation) {
        return Optional.empty();
    }

    @Override
    public void put(String taskId, int generation, Map<String, Object> context) {
        // degraded: cache unavailable, reads go straight to the database
    }

    @Override
    public void evict(String taskId) {
        // nothing to evict
    }
}
