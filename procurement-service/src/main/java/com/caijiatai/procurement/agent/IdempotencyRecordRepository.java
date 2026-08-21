package com.caijiatai.procurement.agent;

import java.time.Instant;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface IdempotencyRecordRepository
        extends JpaRepository<IdempotencyRecord, IdempotencyRecord.Key> {

    @Modifying
    @Query("DELETE FROM IdempotencyRecord r WHERE r.expiresAt < :now")
    int deleteExpiredRecords(@Param("now") Instant now);
}
