package com.caijiatai.procurement.quote;

import java.util.List;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;

public interface ProcurementQuoteRepository extends JpaRepository<ProcurementQuote, String> {
    List<ProcurementQuote> findByTaskIdOrderByCreatedAtAsc(String taskId);
    Optional<ProcurementQuote> findByIdAndTaskId(String id, String taskId);
    long countByTaskId(String taskId);
    java.util.List<ProcurementQuote> findBySupplierNameOrderByCreatedAtAsc(String supplierName);
}
