package com.caijiatai.procurement.agent;

import java.time.Instant;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

@Component
public class IdempotencyRecordCleaner {

    private static final Logger log = LoggerFactory.getLogger(IdempotencyRecordCleaner.class);
    private final IdempotencyRecordRepository repository;

    public IdempotencyRecordCleaner(IdempotencyRecordRepository repository) {
        this.repository = repository;
    }

    @Scheduled(cron = "0 0 3 * * ?") // Every night at 03:00 AM
    @Transactional
    public void purgeExpiredRecords() {
        try {
            int deleted = repository.deleteExpiredRecords(Instant.now());
            if (deleted > 0) {
                log.info("Purged {} expired idempotency records from database", deleted);
            }
        } catch (Exception e) {
            log.error("Failed to purge expired idempotency records", e);
        }
    }
}
