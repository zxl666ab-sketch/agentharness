package com.caijiatai.procurement.cache;

import java.time.Duration;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.stereotype.Component;

/**
 * Redis 分布式锁实现（冻结设计 4.9）：
 * key {@code lock:decision:{taskId}}，SETNX + value=请求标识（UUID）+ 过期 10s；
 * 释放用 Lua 脚本校验持有者（只释放自己的锁，防止误删他人锁）。
 * 业务执行超过 10s 锁过期由乐观锁（version）兜底。
 */
@Component
@ConditionalOnProperty(prefix = "app.redis", name = "enabled", havingValue = "true")
public final class RedisDecisionLock implements DecisionLock {
    static final Duration TTL = Duration.ofSeconds(10);
    static final DefaultRedisScript<Long> RELEASE_SCRIPT = new DefaultRedisScript<>(
            "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
            Long.class);

    private final StringRedisTemplate redis;

    public RedisDecisionLock(StringRedisTemplate redis) {
        this.redis = redis;
    }

    @Override
    public Optional<LockHandle> acquire(String taskId) {
        var requestId = UUID.randomUUID().toString();
        try {
            Boolean acquired = redis.opsForValue().setIfAbsent(key(taskId), requestId, TTL);
            if (!Boolean.TRUE.equals(acquired)) {
                return Optional.empty();
            }
            return Optional.of(new HeldHandle(taskId, requestId));
        } catch (RuntimeException error) {
            // Redis 不可用：回退无锁路径（与 NoopTaskContextCache 同思路）
            return Optional.of(NoopHandle.INSTANCE);
        }
    }

    private String key(String taskId) {
        return "lock:decision:" + taskId;
    }

    private final class HeldHandle implements LockHandle {
        private final String taskId;
        private final String requestId;

        private HeldHandle(String taskId, String requestId) {
            this.taskId = taskId;
            this.requestId = requestId;
        }

        @Override
        public void close() {
            try {
                // 条件释放：仅当 key 的 value 仍是自己的请求标识时才删除
                redis.execute(RELEASE_SCRIPT, List.of(key(taskId)), requestId);
            } catch (RuntimeException ignored) {
                // 释放失败由 TTL 兜底
            }
        }
    }

    private enum NoopHandle implements LockHandle {
        INSTANCE;

        @Override
        public void close() {
            // 无锁路径
        }
    }
}
