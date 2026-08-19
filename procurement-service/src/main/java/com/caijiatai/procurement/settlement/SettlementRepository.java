package com.caijiatai.procurement.settlement;

import jakarta.persistence.LockModeType;
import java.util.List;
import java.util.Optional;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface SettlementRepository extends JpaRepository<PurchaseSettlement, String> {
    Optional<PurchaseSettlement> findByOrderId(String orderId);

    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select settlement from PurchaseSettlement settlement where settlement.id = :id")
    Optional<PurchaseSettlement> lockById(@Param("id") String id);

    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select settlement from PurchaseSettlement settlement where settlement.orderId = :orderId")
    Optional<PurchaseSettlement> lockByOrderId(@Param("orderId") String orderId);

    Page<PurchaseSettlement> findAllByOrderByCreatedAtDesc(Pageable pageable);

    Page<PurchaseSettlement> findByStatusOrderByCreatedAtDesc(String status, Pageable pageable);

    List<PurchaseSettlement> findByStatusAndUpdatedAtBefore(String status, java.time.Instant before);

    long countByStatus(String status);
}
