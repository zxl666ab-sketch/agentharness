package com.caijiatai.procurement.agent;

import jakarta.persistence.LockModeType;
import java.time.Instant;
import java.util.List;
import java.util.Optional;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.jpa.repository.QueryHints;
import jakarta.persistence.QueryHint;
import org.springframework.data.repository.query.Param;

public interface AgentCommandRepository extends JpaRepository<AgentCommand, String> {
    boolean existsByAggregateIdAndOperationTypeAndStatusIn(
            String aggregateId, String operationType, List<String> statuses);

    Optional<AgentCommand> findFirstByAggregateIdAndOperationTypeOrderByAcceptedAtAsc(
            String aggregateId, String operationType);

    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select command from AgentCommand command where command.operationId = :operationId")
    Optional<AgentCommand> lockById(@Param("operationId") String operationId);
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @QueryHints(@QueryHint(name = "jakarta.persistence.lock.timeout", value = "-2"))
    @Query("select command from AgentCommand command where command.status in ('pending', 'accepted') "
            + "and command.nextAttemptAt <= current_timestamp order by command.acceptedAt")
    List<AgentCommand> lockDispatchable(Pageable pageable);

    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @QueryHints(@QueryHint(name = "jakarta.persistence.lock.timeout", value = "-2"))
    @Query("select command from AgentCommand command where command.status = 'published' "
            + "and command.publishedAt < :cutoff order by command.acceptedAt")
    List<AgentCommand> lockPublishedStale(@org.springframework.data.repository.query.Param("cutoff") java.time.Instant cutoff,
            Pageable pageable);

    @Modifying
    @Query("update AgentCommand command "
            + "set command.nextAttemptAt = current_timestamp, command.acceptedAt = current_timestamp "
            + "where command.operationId = :operationId")
    int alignTimestampsToDbClock(@Param("operationId") String operationId);
}
