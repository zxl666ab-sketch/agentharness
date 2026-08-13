package com.caijiatai.procurement.cache;

import java.util.List;
import java.util.Map;
import java.util.function.Supplier;

/**
 * 看板/统计缓存抽象（冻结设计 4.9）：key {@code cache:insights:{name}}，TTL 60s；
 * 写操作（新增报价/审批/订单流转/供应商变更）时 {@link #evictAll()} 主动失效。
 */
public interface InsightsCache {
    Map<String, Object> getOrLoad(String name, Supplier<Map<String, Object>> loader);

    List<Map<String, Object>> getListOrLoad(String name, Supplier<List<Map<String, Object>>> loader);

    void evictAll();
}
