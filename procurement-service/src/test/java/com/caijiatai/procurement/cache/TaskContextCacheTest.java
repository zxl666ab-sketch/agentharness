package com.caijiatai.procurement.cache;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import java.util.Map;
import org.junit.jupiter.api.Test;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.ValueOperations;
import tools.jackson.databind.ObjectMapper;

class TaskContextCacheTest {
    @Test
    void noopAlwaysMissesAndEvictsSilently() {
        var cache = new NoopTaskContextCache();
        assertThat(cache.get("t", 1)).isEmpty();
        cache.put("t", 1, Map.of("k", "v"));
        cache.evict("t");
    }

    @Test
    void redisCacheDegradesWhenRedisIsUnavailable() {
        var redis = mock(StringRedisTemplate.class);
        var values = mock(ValueOperations.class);
        when(redis.opsForValue()).thenReturn(values);
        when(values.get(anyString())).thenThrow(new RuntimeException("redis down"));
        var cache = new RedisTaskContextCache(redis, new ObjectMapper());

        assertThat(cache.get("t", 1)).isEmpty();
        cache.put("t", 1, Map.of("k", "v")); // must not throw
        cache.evict("t"); // must not throw
    }

    @Test
    void redisCacheRoundTrip() {
        var redis = mock(StringRedisTemplate.class);
        var values = mock(ValueOperations.class);
        when(redis.opsForValue()).thenReturn(values);
        when(values.get("ctx:task:t:v1")).thenReturn("{\"k\":\"v\"}");
        var cache = new RedisTaskContextCache(redis, new ObjectMapper());

        assertThat(cache.get("t", 1)).contains(Map.of("k", "v"));
    }

    @Test
    void redisCacheReturnsEmptyForMalformedJson() {
        var redis = mock(StringRedisTemplate.class);
        var values = mock(ValueOperations.class);
        when(redis.opsForValue()).thenReturn(values);
        when(values.get(anyString())).thenReturn("not-json");
        var cache = new RedisTaskContextCache(redis, new ObjectMapper());

        assertThat(cache.get("t", 1)).isEmpty();
    }
}
