package com.caijiatai.procurement.cache;

import java.util.Optional;

/**
 * 审批分布式锁抽象（冻结设计 4.9）：SETNX + 请求标识 + Lua 条件释放，
 * Redis 禁用或不可用时回退无锁路径（Noop），业务超时由乐观锁（version）兜底。
 */
public interface DecisionLock {
    /**
     * 获取锁：成功返回句柄（finally 中 close 释放）；对方持有时返回 empty（409）；
     * Redis 不可用返回空操作句柄（降级无锁）。
     */
    Optional<LockHandle> acquire(String taskId);

    interface LockHandle extends AutoCloseable {
        @Override
        void close();
    }

    /** 空操作句柄：Redis 不可用或禁用时的回退。 */
    LockHandle NOOP_HANDLE = () -> {
        // 无锁路径
    };
}
