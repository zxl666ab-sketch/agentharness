package com.caijiatai.procurement.invoice;

import jakarta.persistence.LockModeType;
import java.util.List;
import java.util.Optional;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface InvoiceRepository extends JpaRepository<Invoice, String> {
    Page<Invoice> findAllByOrderByCreatedAtDesc(Pageable pageable);

    Page<Invoice> findByStatusOrderByCreatedAtDesc(String status, Pageable pageable);

    Page<Invoice> findByOrderIdOrderByCreatedAtDesc(String orderId, Pageable pageable);

    List<Invoice> findByOrderIdOrderByCreatedAtAsc(String orderId);

    Optional<Invoice> findByInvoiceNo(String invoiceNo);

    long countByOrderIdAndStatusIn(String orderId, List<String> statuses);

    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select invoice from Invoice invoice where invoice.id = :id")
    Optional<Invoice> lockById(@Param("id") String id);
}
