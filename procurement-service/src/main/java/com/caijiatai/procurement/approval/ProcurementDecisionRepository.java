package com.caijiatai.procurement.approval;

import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;
import java.util.List;

public interface ProcurementDecisionRepository extends JpaRepository<ProcurementDecision, String> {
    List<ProcurementDecision> findByRunIdOrderByCreatedAtAsc(String runId);
    Optional<ProcurementDecision> findByTaskId(String taskId);
    Optional<ProcurementDecision> findByPendingDecisionId(String pendingDecisionId);
    Optional<ProcurementDecision> findByQuoteId(String quoteId);
    java.util.List<ProcurementDecision> findByQuoteIdIn(java.util.Collection<String> quoteIds);
    java.util.List<ProcurementDecision> findByTaskIdIn(java.util.Collection<String> taskIds);
}
