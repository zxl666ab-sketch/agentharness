package com.caijiatai.procurement.task;

import java.util.Optional;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.jpa.repository.Modifying;
import jakarta.persistence.LockModeType;

public interface ProcurementTaskRepository extends JpaRepository<ProcurementTask, String> {
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select task from ProcurementTask task where task.id = :id")
    Optional<ProcurementTask> lockById(String id);

    java.util.List<ProcurementTask> findAllByOrderByUpdatedAtDesc(Pageable pageable);

    @Modifying(clearAutomatically = true, flushAutomatically = true)
    @Query("update ProcurementTask task set task.retryable = true, task.retryMessage = :message "
            + "where task.id = :id")
    int markRetryable(String id, String message);

    @Modifying(clearAutomatically = true, flushAutomatically = true)
    @Query("update ProcurementTask task set task.retryable = false, task.retryMessage = null "
            + "where task.id = :id")
    int clearRetryable(String id);
}
