package com.caijiatai.procurement.approval;

import java.util.List;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;

public interface PendingDecisionRepository extends JpaRepository<PendingDecision, String> {
    Optional<PendingDecision> findByOperationId(String operationId);
    List<PendingDecision> findByTaskIdAndStatusIn(String taskId, List<String> statuses);
}
