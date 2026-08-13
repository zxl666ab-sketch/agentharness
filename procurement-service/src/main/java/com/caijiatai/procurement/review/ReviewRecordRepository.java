package com.caijiatai.procurement.review;

import jakarta.persistence.LockModeType;
import java.util.List;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;

public interface ReviewRecordRepository
        extends JpaRepository<ReviewRecord, String>, JpaSpecificationExecutor<ReviewRecord> {
    Optional<ReviewRecord> findByAiResultId(String aiResultId);
    Optional<ReviewRecord> findByPendingDecisionId(String pendingDecisionId);
    List<ReviewRecord> findByBusinessIdOrderByCreatedAtAsc(String businessId);
    List<ReviewRecord> findByBusinessIdAndStatus(String businessId, ReviewStatus status);

    long countByStatus(ReviewStatus status);

    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select review from ReviewRecord review where review.id = :id")
    Optional<ReviewRecord> lockById(String id);
}
