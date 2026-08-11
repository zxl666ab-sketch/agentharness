package com.caijiatai.procurement.cache;

import java.time.Duration;
import java.util.Map;
import java.util.Optional;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Component;
import tools.jackson.databind.ObjectMapper;

@Component
@ConditionalOnProperty(prefix = "app.redis", name = "enabled", havingValue = "true")
public final class RedisTaskContextCache implements TaskContextCache {
    static final Duration TTL = Duration.ofMinutes(10);

    private final StringRedisTemplate redis;
    private final ObjectMapper mapper;

    public RedisTaskContextCache(StringRedisTemplate redis, ObjectMapper mapper) {
        this.redis = redis;
        this.mapper = mapper;
    }

    @Override
    public Optional<Map<String, Object>> get(String taskId, int generation) {
        try {
            var raw = redis.opsForValue().get(key(taskId, generation));
            if (raw == null) {
                return Optional.empty();
            }
            return Optional.ofNullable(mapper.readValue(raw, Map.class));
        } catch (RuntimeException error) {
            return Optional.empty(); // Redis unavailable: degrade to direct read
        }
    }

    @Override
    public void put(String taskId, int generation, Map<String, Object> context) {
        try {
            redis.opsForValue().set(key(taskId, generation), mapper.writeValueAsString(context), TTL);
        } catch (RuntimeException error) {
            // degrade silently
        }
    }

    @Override
    public void evict(String taskId) {
        try {
            var keys = redis.keys("ctx:task:" + taskId + ":v*");
            if (keys != null && !keys.isEmpty()) {
                redis.delete(keys);
            }
        } catch (RuntimeException error) {
            // degrade silently
        }
    }

    private String key(String taskId, int generation) {
        return "ctx:task:" + taskId + ":v" + generation;
    }
}
