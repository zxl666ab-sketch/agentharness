package com.caijiatai.procurement.ai;

import java.util.List;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;

public interface AiTaskRecordRepository extends JpaRepository<AiTaskRecord, String> {
    List<AiTaskRecord> findByAiTaskIdOrderByAttemptAscSequenceAscCreatedAtAsc(String aiTaskId);

    boolean existsByAiTaskIdAndAttemptAndSequence(String aiTaskId, int attempt, int sequence);

    Optional<AiTaskRecord> findFirstByAiTaskIdAndAttemptOrderBySequenceDesc(
            String aiTaskId, int attempt);
}
