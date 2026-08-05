package com.caijiatai.procurement.comparison;

import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;

public interface ComparisonSnapshotRepository extends JpaRepository<ComparisonSnapshot, String> {
    Optional<ComparisonSnapshot> findByIdAndTaskId(String id, String taskId);
    Optional<ComparisonSnapshot> findFirstByTaskIdOrderBySnapshotVersionDesc(String taskId);
}
