package com.caijiatai.procurement.cache;

import java.util.Optional;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

/** Redis 禁用时的无锁回退（冻结设计 4.9：禁用则回退无锁路径）。 */
@Component
@ConditionalOnProperty(prefix = "app.redis", name = "enabled", havingValue = "false", matchIfMissing = true)
public final class NoopDecisionLock implements DecisionLock {
    @Override
    public Optional<LockHandle> acquire(String taskId) {
        return Optional.of(NoopHandle.INSTANCE);
    }

    private enum NoopHandle implements LockHandle {
        INSTANCE;

        @Override
        public void close() {
            // 无锁路径
        }
    }
}
