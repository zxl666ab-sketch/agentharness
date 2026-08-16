package com.caijiatai.procurement.quote;

import java.util.List;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;

public interface ProcurementQuoteRepository extends JpaRepository<ProcurementQuote, String> {
    List<ProcurementQuote> findByTaskIdOrderByCreatedAtAsc(String taskId);
    Optional<ProcurementQuote> findByIdAndTaskId(String id, String taskId);
    boolean existsByTaskIdAndSourceArtifactId(String taskId, String sourceArtifactId);
    long countByTaskId(String taskId);
    java.util.List<ProcurementQuote> findAllByOrderByCreatedAtDesc();
    java.util.List<ProcurementQuote> findByTaskIdInOrderByCreatedAtAsc(java.util.Collection<String> taskIds);
    java.util.List<ProcurementQuote> findBySupplierNameOrderByCreatedAtAsc(String supplierName);
}
