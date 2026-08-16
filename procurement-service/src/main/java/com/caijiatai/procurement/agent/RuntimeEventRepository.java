package com.caijiatai.procurement.agent;

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

    RuntimeEvent findFirstByTypeOrderByGlobalSeqDesc(String type);

    List<RuntimeEvent> findTop10ByTypeStartingWithOrderByGlobalSeqDesc(String typePrefix);

    boolean existsByGlobalSeq(long globalSeq);

    @Modifying
    @Query("update RuntimeEvent event set event.payload = :payload where event.globalSeq = :globalSeq")
    int updatePayload(@Param("globalSeq") long globalSeq, @Param("payload") java.util.Map<String, Object> payload);
}
