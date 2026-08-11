package com.caijiatai.procurement.agent;

import java.util.List;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

public interface RuntimeEventRepository extends JpaRepository<RuntimeEvent, Long> {
    List<RuntimeEvent> findByGlobalSeqGreaterThanOrderByGlobalSeqAsc(long after, Pageable pageable);

    List<RuntimeEvent> findByRunId(String runId, Pageable pageable);

    RuntimeEvent findFirstByTypeOrderByGlobalSeqDesc(String type);

    boolean existsByGlobalSeq(long globalSeq);

    @Modifying
    @Query("update RuntimeEvent event set event.payload = :payload where event.globalSeq = :globalSeq")
    int updatePayload(@Param("globalSeq") long globalSeq, @Param("payload") java.util.Map<String, Object> payload);
}
