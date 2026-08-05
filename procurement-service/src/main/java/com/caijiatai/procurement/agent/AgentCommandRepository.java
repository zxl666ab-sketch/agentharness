package com.caijiatai.procurement.agent;

import jakarta.persistence.LockModeType;
import java.time.Instant;
import java.util.List;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;

public interface AgentCommandRepository extends JpaRepository<AgentCommand, String> {
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select command from AgentCommand command where command.status in ('pending', 'accepted') "
            + "and command.nextAttemptAt <= :now order by command.acceptedAt")
    List<AgentCommand> lockDispatchable(Instant now, Pageable pageable);
}
