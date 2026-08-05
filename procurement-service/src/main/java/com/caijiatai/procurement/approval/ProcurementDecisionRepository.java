package com.caijiatai.procurement.approval;

import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;

public interface ProcurementDecisionRepository extends JpaRepository<ProcurementDecision, String> {
    Optional<ProcurementDecision> findByTaskId(String taskId);
    Optional<ProcurementDecision> findByPendingDecisionId(String pendingDecisionId);
    Optional<ProcurementDecision> findByQuoteId(String quoteId);
}
