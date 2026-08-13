package com.caijiatai.procurement.cache;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.time.Duration;
import java.util.List;
import java.util.Optional;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.ValueOperations;

class RedisDecisionLockTest {
    @SuppressWarnings("unchecked")
    private final ValueOperations<String, String> values = mock(ValueOperations.class);
    private final StringRedisTemplate redis = mock(StringRedisTemplate.class);

    private RedisDecisionLock lock() {
        when(redis.opsForValue()).thenReturn(values);
        return new RedisDecisionLock(redis);
    }

    @Test
    void acquiresWithSetNxAndTenSecondTtl() {
        when(values.setIfAbsent(eq("lock:decision:t1"), any(String.class), eq(Duration.ofSeconds(10))))
                .thenReturn(true);
        var lock = lock();

        var handle = lock.acquire("t1");

        assertThat(handle).isPresent();
        verify(values).setIfAbsent(eq("lock:decision:t1"), any(String.class), eq(Duration.ofSeconds(10)));
    }

    @Test
    void returnsEmptyWhenAnotherRequestHoldsTheLock() {
        when(values.setIfAbsent(eq("lock:decision:t1"), any(String.class), any(Duration.class)))
                .thenReturn(false);
        var lock = lock();

        assertThat(lock.acquire("t1")).isEmpty();
    }

    @Test
    void releaseRunsConditionalLuaWithOwnRequestId() {
        when(values.setIfAbsent(eq("lock:decision:t1"), any(String.class), any(Duration.class)))
                .thenReturn(true);
        var lock = lock();
        var handle = lock.acquire("t1").orElseThrow();

        handle.close();

        var captor = ArgumentCaptor.forClass(List.class);
        verify(redis).execute(eq(RedisDecisionLock.RELEASE_SCRIPT), captor.capture(), any(String.class));
        assertThat(captor.getValue()).containsExactly("lock:decision:t1");
    }

    @Test
    void degradesToNoopHandleWhenRedisUnavailable() {
        when(values.setIfAbsent(any(String.class), any(String.class), any(Duration.class)))
                .thenThrow(new RuntimeException("connection refused"));
        var lock = lock();

        var handle = lock.acquire("t1");

        assertThat(handle).isPresent();
        handle.get().close(); // 不应抛出
        verify(redis, never()).execute(any(org.springframework.data.redis.core.script.RedisScript.class),
                anyList(), any(String.class));
    }

    @Test
    void releaseIsSafeWhenRedisFails() {
        when(values.setIfAbsent(any(String.class), any(String.class), any(Duration.class)))
                .thenReturn(true);
        when(redis.execute(any(), anyList(), any(String.class)))
                .thenThrow(new RuntimeException("connection refused"));
        var lock = lock();
        var handle = lock.acquire("t1").orElseThrow();

        handle.close(); // 释放失败由 TTL 兜底，不应抛出
    }

    @Test
    void noopLockAlwaysAcquiresWithoutRedis() {
        var lock = new NoopDecisionLock();

        var handle = lock.acquire("t1");

        assertThat(handle).isPresent();
        handle.get().close();
        assertThat(lock.acquire("t2")).isPresent();
    }
}
