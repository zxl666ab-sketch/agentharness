package com.caijiatai.procurement.agent;

import java.time.Instant;
import java.util.List;
import java.util.Optional;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface RuntimeEventRepository extends JpaRepository<RuntimeEvent, Long> {
    List<RuntimeEvent> findByGlobalSeqGreaterThanOrderByGlobalSeqAsc(long after, Pageable pageable);

    List<RuntimeEvent> findByRunId(String runId, Pageable pageable);

    Optional<RuntimeEvent> findFirstByRunIdOrderByGlobalSeqAsc(String runId);

    long countByRunId(String runId);

    long countByRunIdAndType(String runId, String type);

    /** Full per-run turn scan for cost accounting (not subject to the 100-row read window). */
    List<RuntimeEvent> findByRunIdAndType(String runId, String type);

    /** Bounded platform-wide turn scan for the cost panel (see CostService). */
    List<RuntimeEvent> findTop50000ByTypeOrderByGlobalSeqAsc(String type);

    // Freshness lookups must order by occurredAt: the agent-side global_seq counter can
    // regress after Kafka retention prunes the topic (LIVE-1), so "highest seq" is not
    // necessarily the "newest" event.
    Optional<RuntimeEvent> findFirstByTypeOrderByOccurredAtDesc(String type);

    List<RuntimeEvent> findTop10ByTypeStartingWithOrderByOccurredAtDesc(String typePrefix);

    /** Bounded recent window for /api/runs discovery (see RuntimeQueryService.runs()). */
    List<RuntimeEvent> findTop20000ByOrderByGlobalSeqDesc();

    /** Dedup key includes occurredAt so a regressed global_seq cannot silently drop a new event. */
    boolean existsByGlobalSeqAndOccurredAt(long globalSeq, Instant occurredAt);

    boolean existsByRunIdAndTypeAndOccurredAt(String runId, String type, Instant occurredAt);

    boolean existsByGlobalSeq(long globalSeq);

    @Query("select coalesce(max(event.globalSeq), 0) from RuntimeEvent event")
    long maxGlobalSeq();

    @Modifying
    @Query("update RuntimeEvent event set event.payload = :payload where event.globalSeq = :globalSeq")
    int updatePayload(@Param("globalSeq") long globalSeq, @Param("payload") java.util.Map<String, Object> payload);
}
