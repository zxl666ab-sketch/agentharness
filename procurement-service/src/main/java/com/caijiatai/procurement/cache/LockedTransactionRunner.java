package com.caijiatai.procurement.cache;

import com.caijiatai.procurement.api.ApiException;
import java.util.Objects;
import java.util.function.Supplier;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.support.TransactionTemplate;

/**
 * 编程式分布式锁与事务生命周期编排器：
 * 严格保证分布式锁的生命周期包裹 Spring 事务生命周期（锁范围 ⊃ 事务范围），
 * 避免方法返回出块先释放锁、但底层事务因异步提交延迟导致并发脏读与状态漂移。
 */
@Component
public class LockedTransactionRunner {
    private final DecisionLock decisionLock;
    private final TransactionTemplate transactionTemplate;

    public LockedTransactionRunner(DecisionLock decisionLock, PlatformTransactionManager transactionManager) {
        this.decisionLock = Objects.requireNonNull(decisionLock, "decisionLock must not be null");
        this.transactionTemplate = new TransactionTemplate(transactionManager);
        this.transactionTemplate.setPropagationBehavior(TransactionDefinition.PROPAGATION_REQUIRES_NEW);
    }

    public <T> T executeInLockAndTx(String taskId, Supplier<T> action) {
        var lockOpt = decisionLock.acquire(taskId);
        if (lockOpt.isEmpty()) {
            throw new ApiException(HttpStatus.CONFLICT, "lock_conflict", "任务正在并发处理中，请稍后重试: " + taskId);
        }
        try (var lock = lockOpt.get()) {
            // 事务提交必须在锁作用域之内完成
            return transactionTemplate.execute(status -> action.get());
        }
    }

    public void runInLockAndTx(String taskId, Runnable action) {
        executeInLockAndTx(taskId, () -> {
            action.run();
            return null;
        });
    }
}
