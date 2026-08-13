package com.caijiatai.procurement.order;

import jakarta.persistence.LockModeType;
import java.util.List;
import java.util.Optional;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface OrderRepository extends JpaRepository<PurchaseOrder, String> {
    Optional<PurchaseOrder> findByTaskId(String taskId);

    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select order from PurchaseOrder order where order.id = :id")
    Optional<PurchaseOrder> lockById(@Param("id") String id);

    Page<PurchaseOrder> findAllByOrderByCreatedAtDesc(Pageable pageable);

    Page<PurchaseOrder> findByStatusOrderByCreatedAtDesc(String status, Pageable pageable);

    List<PurchaseOrder> findByStatusAndUpdatedAtBefore(String status, java.time.Instant before);

    long countByStatus(String status);
}
