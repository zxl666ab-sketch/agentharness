package com.caijiatai.procurement.cache;

import java.util.Map;
import java.util.Optional;

/** Task context cache abstraction; Redis-backed with automatic degradation. */
public interface TaskContextCache {
    Optional<Map<String, Object>> get(String taskId, int generation);

    void put(String taskId, int generation, Map<String, Object> context);

    void evict(String taskId);
}
