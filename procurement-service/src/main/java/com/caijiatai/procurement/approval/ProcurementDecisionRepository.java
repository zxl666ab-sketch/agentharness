package com.caijiatai.procurement.approval;

import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;
import java.util.List;

public interface ProcurementDecisionRepository extends JpaRepository<ProcurementDecision, String> {
    List<ProcurementDecision> findByRunIdOrderByCreatedAtAsc(String runId);
    Optional<ProcurementDecision> findByTaskId(String taskId);
    Optional<ProcurementDecision> findByPendingDecisionId(String pendingDecisionId);
    Optional<ProcurementDecision> findByQuoteId(String quoteId);
}
