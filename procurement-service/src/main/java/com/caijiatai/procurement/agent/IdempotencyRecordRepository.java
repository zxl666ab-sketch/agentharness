package com.caijiatai.procurement.agent;

import org.springframework.data.jpa.repository.JpaRepository;

public interface IdempotencyRecordRepository
        extends JpaRepository<IdempotencyRecord, IdempotencyRecord.Key> {}
