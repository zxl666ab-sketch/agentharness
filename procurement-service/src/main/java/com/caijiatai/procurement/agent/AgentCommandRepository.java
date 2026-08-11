package com.caijiatai.procurement.agent;

import jakarta.persistence.LockModeType;
import java.time.Instant;
import java.util.List;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.jpa.repository.QueryHints;
import jakarta.persistence.QueryHint;
import org.springframework.data.repository.query.Param;

public interface AgentCommandRepository extends JpaRepository<AgentCommand, String> {
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @QueryHints(@QueryHint(name = "jakarta.persistence.lock.timeout", value = "-2"))
    @Query("select command from AgentCommand command where command.status in ('pending', 'accepted') "
            + "and command.nextAttemptAt <= current_timestamp order by command.acceptedAt")
    List<AgentCommand> lockDispatchable(Pageable pageable);

    @Modifying
    @Query("update AgentCommand command "
            + "set command.nextAttemptAt = current_timestamp, command.acceptedAt = current_timestamp "
            + "where command.operationId = :operationId")
    int alignTimestampsToDbClock(@Param("operationId") String operationId);
}
