package com.caijiatai.procurement.ai;

import java.util.Collection;
import java.util.List;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;

public interface AiTaskRecordRepository extends JpaRepository<AiTaskRecord, String> {
    List<AiTaskRecord> findByAiTaskIdOrderByAttemptAscSequenceAscCreatedAtAsc(String aiTaskId);

    List<AiTaskRecord> findByAiTaskIdAndAttemptAndStatusIn(
            String aiTaskId, int attempt, Collection<AiStepStatus> statuses);

    List<AiTaskRecord> findByAiTaskIdAndStatusIn(
            String aiTaskId, Collection<AiStepStatus> statuses);

    boolean existsByAiTaskIdAndAttemptAndStep(
            String aiTaskId, int attempt, AiTaskStep step);

    boolean existsByAiTaskIdAndAttemptAndSequence(String aiTaskId, int attempt, int sequence);

    Optional<AiTaskRecord> findFirstByAiTaskIdAndAttemptOrderBySequenceDesc(
            String aiTaskId, int attempt);
}
