package com.caijiatai.procurement.ai;

import java.util.List;
import java.util.Optional;
import jakarta.persistence.LockModeType;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;

public interface AiTaskRepository extends JpaRepository<AiTask, String>, JpaSpecificationExecutor<AiTask> {
    Optional<AiTask> findByOperationId(String operationId);

    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select task from AiTask task where task.operationId = :operationId")
    Optional<AiTask> lockByOperationId(String operationId);

    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select task from AiTask task where task.id = :id")
    Optional<AiTask> lockById(String id);

    Optional<AiTask> findByBusinessIdAndGenerationAndTaskTypeAndIdempotencyKey(
            String businessId,
            int generation,
            AiTaskType taskType,
            String idempotencyKey);

    List<AiTask> findByBusinessIdOrderByCreatedAtDesc(String businessId);

    List<AiTask> findByBusinessIdAndStaleFalse(String businessId);

    long countByStatus(AiTaskStatus status);

    long countByStaleTrue();
}
