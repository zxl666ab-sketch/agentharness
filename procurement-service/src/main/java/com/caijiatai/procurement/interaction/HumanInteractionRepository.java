package com.caijiatai.procurement.interaction;

import jakarta.persistence.LockModeType;
import java.util.List;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;

public interface HumanInteractionRepository extends JpaRepository<HumanInteraction, String> {
    List<HumanInteraction> findByTaskIdOrderByCreatedAtDesc(String taskId);
    Optional<HumanInteraction> findByTaskIdAndGenerationAndQuestionFingerprint(
            String taskId, int generation, String questionFingerprint);
    Optional<HumanInteraction> findByOperationId(String operationId);

    @Query("select interaction.taskId from HumanInteraction interaction where interaction.id = :id")
    Optional<String> findTaskIdById(String id);

    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select interaction from HumanInteraction interaction where interaction.id = :id")
    Optional<HumanInteraction> lockById(String id);

    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select interaction from HumanInteraction interaction "
            + "where interaction.taskId = :taskId and interaction.status = 'WAITING'")
    List<HumanInteraction> lockWaitingByTaskId(String taskId);
}
