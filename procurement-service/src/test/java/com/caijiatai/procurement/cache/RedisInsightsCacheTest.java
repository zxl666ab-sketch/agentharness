package com.caijiatai.procurement.cache;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.Test;
import org.springframework.data.redis.core.StringRedisTemplate;
import tools.jackson.databind.ObjectMapper;

class RedisInsightsCacheTest {
    @SuppressWarnings("unchecked")
    private final org.springframework.data.redis.core.ValueOperations<String, String> values =
            mock(org.springframework.data.redis.core.ValueOperations.class);
    private final StringRedisTemplate redis = mock(StringRedisTemplate.class);
    private final java.util.concurrent.ConcurrentHashMap<String, String> store =
            new java.util.concurrent.ConcurrentHashMap<>();
    private final RedisInsightsCache cache = new RedisInsightsCache(redis, new ObjectMapper());

    {
        when(redis.opsForValue()).thenReturn(values);
        when(values.get(any())).thenAnswer(invocation -> store.get(invocation.getArgument(0)));
        org.mockito.Mockito.doAnswer(invocation -> {
            store.put(invocation.getArgument(0), invocation.getArgument(1));
            return null;
        }).when(values).set(any(String.class), any(String.class), any(java.time.Duration.class));
    }

    @Test
    void getOrLoadCachesUntilEvicted() {
        var calls = new AtomicInteger();

        var first = cache.getOrLoad("overview", () -> {
            calls.incrementAndGet();
            return Map.of("tasks", 3);
        });
        var second = cache.getOrLoad("overview", () -> {
            calls.incrementAndGet();
            return Map.of("tasks", 3);
        });

        assertThat(first).isEqualTo(Map.of("tasks", 3));
        assertThat(second).isEqualTo(Map.of("tasks", 3));
        assertThat(calls.get()).isEqualTo(1);
    }

    @Test
    void getOrLoadServesFromCacheWhenHit() throws Exception {
        var mapper = new ObjectMapper();
        store.put("cache:insights:overview", mapper.writeValueAsString(Map.of("tasks", 9)));

        var value = cache.getOrLoad("overview", () -> Map.of("tasks", 0));

        assertThat(value.get("tasks")).isEqualTo(9);
    }

    @Test
    void evictAllDeletesAllInsightsKeys() {
        when(redis.keys("cache:insights:*")).thenReturn(java.util.Set.of(
                "cache:insights:overview", "cache:insights:trend:6"));

        cache.evictAll();

        verify(redis).delete(java.util.Set.of("cache:insights:overview", "cache:insights:trend:6"));
    }

    @Test
    void getListOrLoadCachesListValues() {
        var calls = new AtomicInteger();

        cache.getListOrLoad("categories", () -> {
            calls.incrementAndGet();
            return List.of(Map.of("category", "a", "count", 1));
        });
        cache.getListOrLoad("categories", () -> {
            calls.incrementAndGet();
            return List.of(Map.of("category", "a", "count", 1));
        });

        assertThat(calls.get()).isEqualTo(1);
    }

    @Test
    void degradesToLoaderWhenRedisUnavailable() {
        var failingValues = mock(org.springframework.data.redis.core.ValueOperations.class);
        when(failingValues.get(any())).thenThrow(new RuntimeException("connection refused"));
        org.mockito.Mockito.doThrow(new RuntimeException("connection refused"))
                .when(failingValues).set(any(String.class), any(String.class), any(java.time.Duration.class));
        var failingRedis = mock(StringRedisTemplate.class);
        when(failingRedis.opsForValue()).thenReturn(failingValues);
        var failingCache = new RedisInsightsCache(failingRedis, new ObjectMapper());
        var calls = new AtomicInteger();

        var value = failingCache.getOrLoad("overview", () -> {
            calls.incrementAndGet();
            return Map.of("tasks", 5);
        });

        assertThat(value).isEqualTo(Map.of("tasks", 5));
        assertThat(calls.get()).isEqualTo(1);
    }

    @Test
    void noopCacheAlwaysLoadsDirectly() {
        var cache = new NoopInsightsCache();
        var calls = new AtomicInteger();

        cache.getOrLoad("overview", () -> {
            calls.incrementAndGet();
            return Map.of("tasks", 1);
        });
        cache.getOrLoad("overview", () -> {
            calls.incrementAndGet();
            return Map.of("tasks", 1);
        });
        cache.evictAll();

        assertThat(calls.get()).isEqualTo(2);
    }

    @org.junit.jupiter.api.Test
    void listCacheRoundTripsThroughJson() throws Exception {
        var mapper = new ObjectMapper();
        store.put("cache:insights:trend:6",
                mapper.writeValueAsString(List.of(Map.of("month", "2026-08", "task_count", 2))));

        var value = cache.getListOrLoad("trend:6", List::of);

        assertThat(value).hasSize(1);
        assertThat(value.getFirst().get("task_count")).isEqualTo(2);
    }
}
