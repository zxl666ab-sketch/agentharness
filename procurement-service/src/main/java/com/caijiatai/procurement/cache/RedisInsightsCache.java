package com.caijiatai.procurement.cache;

import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.function.Supplier;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Component;
import tools.jackson.databind.ObjectMapper;

/**
 * Redis 看板缓存（冻结设计 4.9）：key {@code cache:insights:{name}}，TTL 60s，
 * 写操作主动失效；Redis 不可用时直接走 loader（退化不缓存）。
 */
@Component
@ConditionalOnProperty(prefix = "app.redis", name = "enabled", havingValue = "true")
public final class RedisInsightsCache implements InsightsCache {
    static final Duration TTL = Duration.ofSeconds(60);
    private static final String KEY_PREFIX = "cache:insights:";

    private final StringRedisTemplate redis;
    private final ObjectMapper mapper;

    public RedisInsightsCache(StringRedisTemplate redis, ObjectMapper mapper) {
        this.redis = redis;
        this.mapper = mapper;
    }

    @Override
    public Map<String, Object> getOrLoad(String name, Supplier<Map<String, Object>> loader) {
        try {
            var raw = redis.opsForValue().get(key(name));
            if (raw != null) {
                return mapper.readValue(raw, Map.class);
            }
        } catch (RuntimeException ignored) {
            // Redis 不可用：退化直读
        }
        var value = loader.get();
        try {
            redis.opsForValue().set(key(name), mapper.writeValueAsString(value), TTL);
        } catch (RuntimeException ignored) {
            // 写缓存失败不影响读路径
        }
        return value;
    }

    @Override
    public List<Map<String, Object>> getListOrLoad(String name, Supplier<List<Map<String, Object>>> loader) {
        try {
            var raw = redis.opsForValue().get(key(name));
            if (raw != null) {
                return mapper.readValue(raw, List.class);
            }
        } catch (RuntimeException ignored) {
            // Redis 不可用：退化直读
        }
        var value = loader.get();
        try {
            redis.opsForValue().set(key(name), mapper.writeValueAsString(value), TTL);
        } catch (RuntimeException ignored) {
            // 写缓存失败不影响读路径
        }
        return value;
    }

    @Override
    public void evictAll() {
        try {
            Set<String> keys = redis.keys(KEY_PREFIX + "*");
            if (keys != null && !keys.isEmpty()) {
                redis.delete(keys);
            }
        } catch (RuntimeException ignored) {
            // 失效失败由 TTL 兜底
        }
    }

    private String key(String name) {
        return KEY_PREFIX + name;
    }
}
